from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    BinCountPoint,
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetAnalysisParameterIdentity,
    DatasetBoxPlotStatistics,
    DatasetCapabilityStatistics,
    DatasetChartData,
    DatasetComparisonItem,
    DatasetComparisonRequest,
    DatasetComparisonResult,
    DatasetDescriptiveStatistics,
    DatasetDetailMeasurement,
    DatasetDetailPage,
    DatasetDetailRow,
    DatasetHistogramBin,
    DatasetHistogramStatistics,
    DatasetMeasurementStatusCount,
    DatasetParameterAnalysis,
    DatasetParameterAnalysisCapability,
    DatasetParameterAnalysisContextFilterSummary,
    DatasetParameterAnalysisCounts,
    DatasetParameterAnalysisDatasetContext,
    DatasetParameterAnalysisFilterSummary,
    DatasetParameterAnalysisItem,
    DatasetParameterAnalysisNormalizedFilters,
    DatasetParameterAnalysisRequest,
    DatasetParameterAnalysisResolvedDataset,
    DatasetParameterAnalysisResult,
    DatasetParameterAnalysisRuleContext,
    DatasetParameterAnalysisSamplingSummary,
    DatasetParameterAnalysisType,
    DatasetParameterStatistic,
    DatasetRecord,
    DatasetResultSummary,
    DatasetVersionRecord,
    DqGateResult,
    FtParameterOption,
    FtParameterPoint,
    GateReason,
    PublishDatasetVersionRequest,
    WaferMapPoint,
    WaferOption,
    WaferYieldPoint,
)
from app.infrastructure.sql_visibility import (
    current_dataset_read_scope_sql,
    visibility_parameters,
)

_MAX_SQL_SERVER_OFFSET = 2_147_483_647
_DETAIL_FILTER_LIMITS = {
    "lot_ids": 50,
    "wafer_ids": 100,
    "bin_codes": 50,
    "parameters": 20,
    "overall_results": 4,
    "source_ids": 50,
}
_PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS = 2_000_000
_PARAMETER_ANALYSIS_CONTRACT_VERSION = "PARAMETER_ANALYSIS_V1"
_BOX_PLOT_METHOD = "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1"
_HISTOGRAM_METHOD = "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1"
_SUPPORTED_PARAMETER_ANALYSIS_RULE_CODES = frozenset(
    {
        _BOX_PLOT_METHOD,
        _HISTOGRAM_METHOD,
        "CPK_POOLED_WITHIN_RUN_V1",
        "CPK_POOLED_WITHIN_LOT_WAFER_V1",
    }
)
_MEASUREMENT_STATUSES = (
    "MEASURED",
    "OVER_RANGE",
    "UNDER_RANGE",
    "NOT_TESTED",
    "MISSING",
    "INVALID",
    "NOT_APPLICABLE",
)


def _dataset(row: Mapping[str, Any]) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=int(row["dataset_id"]),
        dataset_code=str(row["dataset_code"]),
        dataset_name=str(row["dataset_name"]),
        dataset_type=str(row["dataset_type"]),
        test_stage=str(row["test_stage"]),
        supplier_id=row["supplier_id"],
        product_id=row["product_id"],
        owner_user_id=int(row["owner_user_id"]),
    )


def _version(row: Mapping[str, Any], *, run_count: int) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_version_id=int(row["dataset_version_id"]),
        dataset_id=int(row["dataset_id"]),
        version_no=int(row["version_no"]),
        input_batch_id=int(row["input_batch_id"]),
        canonical_model_version=str(row["canonical_model_version"]),
        status=str(row["status"]),
        is_current=bool(row["is_current"]),
        run_count=run_count,
    )


def _wafer_yield(row: Mapping[str, Any]) -> WaferYieldPoint:
    pass_count = int(row["pass_count"] or 0)
    fail_count = int(row["fail_count"] or 0)
    unknown_count = int(row["unknown_count"] or 0)
    abort_count = int(row["abort_count"] or 0)
    known_yield_denominator = pass_count + fail_count
    return WaferYieldPoint(
        lot_id=str(row["lot_id"]),
        wafer_id=str(row["wafer_id"] or ""),
        unit_count=int(row["unit_count"]),
        pass_count=pass_count,
        fail_count=fail_count,
        unknown_count=unknown_count,
        abort_count=abort_count,
        known_yield_denominator=known_yield_denominator,
        yield_rate=(pass_count / known_yield_denominator)
        if known_yield_denominator
        else None,
    )


def _normalized_filter_values(
    values: tuple[str, ...], *, field: str
) -> tuple[str, ...]:
    limit = _DETAIL_FILTER_LIMITS[field]
    if len(values) > limit:
        raise DomainError(
            "ANALYSIS_FILTER_LIMIT_EXCEEDED",
            f"{field} exceeds the maximum of {limit} values",
            422,
        )
    normalized = tuple(str(value).strip() for value in values)
    if any(not value or len(value) > 200 for value in normalized):
        raise DomainError(
            "ANALYSIS_FILTER_INVALID",
            f"{field} contains an empty or oversized value",
            422,
        )
    if len(normalized) != len(set(normalized)):
        raise DomainError(
            "ANALYSIS_FILTER_INVALID",
            f"{field} contains duplicate values",
            422,
        )
    return normalized


def _parameter_analysis_filter_hash(
    *,
    lot_ids: tuple[str, ...],
    wafer_ids: tuple[str, ...],
    bin_codes: tuple[str, ...],
    overall_results: tuple[str, ...],
    source_ids: tuple[str, ...],
) -> str:
    payload = {
        "lot_ids": sorted(lot_ids),
        "wafer_ids": sorted(wafer_ids),
        "bin_codes": sorted(bin_codes),
        "overall_results": sorted(overall_results),
        "source_ids": sorted(source_ids),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _condition_text(value: object, *, parameter: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has invalid test-condition metadata",
            409,
        ) from exc
    if not isinstance(decoded, dict):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has invalid test-condition metadata",
            409,
        )
    supported_keys = {"text", "bias1", "bias2"}
    if any(not isinstance(key, str) for key in decoded) or not set(decoded).issubset(
        supported_keys
    ):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has unsupported test-condition metadata",
            409,
        )
    normalized: dict[str, str] = {}
    for key in ("text", "bias1", "bias2"):
        raw_value = decoded.get(key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            raise DomainError(
                "ANALYSIS_SPEC_CONTRACT_INVALID",
                f"parameter {parameter} has invalid test-condition metadata",
                409,
            )
        normalized_value = " ".join(raw_value.split())
        if normalized_value:
            normalized[key] = normalized_value
    if not normalized:
        return None
    if set(normalized) == {"text"}:
        return normalized["text"]
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _optional_finite_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID",
            f"{field} is not numeric",
            409,
        ) from exc
    if not math.isfinite(numeric):
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID",
            f"{field} is not a finite numeric value",
            409,
        )
    return numeric


def _analysis_filter_sql(
    *,
    lot_ids: tuple[str, ...] = (),
    wafer_ids: tuple[str, ...] = (),
    bin_codes: tuple[str, ...] = (),
    overall_results: tuple[str, ...] = (),
    source_run_ids: tuple[int, ...] | None = None,
) -> tuple[str, dict[str, object], tuple[str, ...]]:
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    expanding: list[str] = []
    if lot_ids:
        clauses.append("tr.lot_id IN :lot_ids")
        parameters["lot_ids"] = lot_ids
        expanding.append("lot_ids")
    if wafer_ids:
        clauses.append("COALESCE(ur.wafer_id,tr.wafer_id) IN :wafer_ids")
        parameters["wafer_ids"] = wafer_ids
        expanding.append("wafer_ids")
    if bin_codes:
        clauses.append("COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') IN :bin_codes")
        parameters["bin_codes"] = bin_codes
        expanding.append("bin_codes")
    if overall_results:
        clauses.append("ur.overall_result IN :overall_results")
        parameters["overall_results"] = overall_results
        expanding.append("overall_results")
    if source_run_ids is not None:
        if source_run_ids:
            clauses.append("tr.run_id IN :source_run_ids")
            parameters["source_run_ids"] = source_run_ids
            expanding.append("source_run_ids")
        else:
            clauses.append("1=0")
    return (
        " AND " + " AND ".join(clauses) if clauses else "",
        parameters,
        tuple(expanding),
    )


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _run_source_identity(row: Mapping[str, Any]) -> str:
    metadata: dict[str, Any] = {}
    try:
        decoded = json.loads(row.get("metadata_json") or "{}")
        if isinstance(decoded, dict):
            metadata = decoded
    except (TypeError, ValueError):
        metadata = {}
    source_id = str(metadata.get("source_id") or "").strip()
    if source_id:
        return source_id
    tester_id = str(row.get("tester_id") or "").strip()
    return tester_id or f"RUN-{int(row['run_id'])}"


def _capability_side(
    *, mean: float, limit: float | None, sigma: float, lower: bool
) -> float | None:
    if limit is None:
        return None
    value = (mean - limit) / (3.0 * sigma) if lower else (limit - mean) / (3.0 * sigma)
    return value if math.isfinite(value) else None


def _combined_capability(
    lower_index: float | None, upper_index: float | None
) -> float | None:
    available = tuple(
        value for value in (lower_index, upper_index) if value is not None
    )
    return min(available) if available else None


class SqlDatasetService:
    def __init__(
        self,
        engine: Engine,
        *,
        approved_parameter_analysis_rule_codes: frozenset[str] = frozenset(),
    ) -> None:
        self._engine = engine
        normalized_rules = frozenset(
            str(rule_code).strip()
            for rule_code in approved_parameter_analysis_rule_codes
        )
        unknown_rules = normalized_rules - _SUPPORTED_PARAMETER_ANALYSIS_RULE_CODES
        if unknown_rules:
            raise ValueError(
                "approved parameter-analysis rules contain an unknown rule code"
            )
        self._approved_parameter_analysis_rule_codes = normalized_rules

    def assert_parameter_analysis_rules_approved(
        self, request: DatasetParameterAnalysisRequest
    ) -> None:
        analyses = set(request.analyses)
        required_rules: set[str] = set()
        if DatasetParameterAnalysisType.BOX_PLOT in analyses:
            required_rules.add(_BOX_PLOT_METHOD)
        if DatasetParameterAnalysisType.HISTOGRAM in analyses:
            required_rules.add(_HISTOGRAM_METHOD)
        if request.capability.rule_code is not None:
            required_rules.add(request.capability.rule_code.value)
        missing_rules = sorted(
            required_rules - self._approved_parameter_analysis_rule_codes
        )
        if missing_rules:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "one or more requested parameter-analysis rules have no approved "
                "server-side activation",
                409,
                details=[{"rule_code": rule_code} for rule_code in missing_rules],
            )

    def list_datasets(self, principal: Principal) -> tuple[DatasetRecord, ...]:
        params = visibility_parameters(principal)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT d.dataset_id,d.dataset_code,d.dataset_name,d.dataset_type,d.test_stage,"
                        "d.supplier_id,d.product_id,d.owner_user_id FROM dataset.dataset d "
                        "LEFT JOIN dataset.dataset_version access_dv ON "
                        "access_dv.dataset_id=d.dataset_id AND "
                        "access_dv.status='PUBLISHED' AND access_dv.is_current=1 "
                        "LEFT JOIN ingestion.import_batch access_b ON "
                        "access_b.import_batch_id=access_dv.input_batch_id WHERE "
                        + current_dataset_read_scope_sql(
                            dataset_alias="d",
                            version_alias="access_dv",
                            batch_alias="access_b",
                        )
                        + " "
                        "ORDER BY d.dataset_id DESC"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return tuple(_dataset(row) for row in rows)

    def assert_dataset_access(
        self,
        dataset_id: int,
        principal: Principal,
        mode: str = "READ",
        *,
        version_no: int | None = None,
    ) -> None:
        normalized_mode = mode.strip().upper()
        if normalized_mode not in {"READ", "WRITE"}:
            raise ValueError("dataset access mode must be READ or WRITE")
        parameters: dict[str, object] = visibility_parameters(principal) | {
            "dataset_id": dataset_id
        }
        joins = ""
        scope = "(:is_admin=1 OR d.owner_user_id=:user_id)"
        if normalized_mode == "READ":
            version_predicate = (
                "access_dv.dataset_id=d.dataset_id AND "
                "access_dv.version_no=:access_version_no"
                if version_no is not None
                else "access_dv.dataset_id=d.dataset_id AND "
                "access_dv.status='PUBLISHED' AND access_dv.is_current=1"
            )
            joins = (
                " LEFT JOIN dataset.dataset_version access_dv ON "
                + version_predicate
                + " LEFT JOIN ingestion.import_batch access_b ON "
                "access_b.import_batch_id=access_dv.input_batch_id"
            )
            scope = current_dataset_read_scope_sql(
                dataset_alias="d",
                version_alias="access_dv",
                batch_alias="access_b",
            )
            if version_no is not None:
                parameters["access_version_no"] = version_no
        with self._engine.connect() as connection:
            found = connection.execute(
                text(
                    "SELECT TOP (1) d.dataset_id FROM dataset.dataset d "
                    + joins
                    + " WHERE d.dataset_id=:dataset_id AND ("
                    + scope
                    + ")"
                ),
                parameters,
            ).scalar_one_or_none()
        if found is None:
            raise DomainError("DATASET_ACCESS_DENIED", "无权访问该数据集", 403)

    def create_dataset(self, request: CreateDatasetRequest) -> DatasetRecord:
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        text(
                            "INSERT dataset.dataset("
                            "dataset_code,dataset_name,dataset_type,test_stage,supplier_id,"
                            "product_id,project_code,owner_user_id) OUTPUT "
                            "INSERTED.dataset_id,INSERTED.dataset_code,INSERTED.dataset_name,"
                            "INSERTED.dataset_type,INSERTED.test_stage,INSERTED.supplier_id,"
                            "INSERTED.product_id,INSERTED.owner_user_id VALUES("
                            ":dataset_code,:dataset_name,:dataset_type,:test_stage,:supplier_id,"
                            ":product_id,:project_code,:owner_user_id)"
                        ),
                        request.model_dump(mode="json"),
                    )
                    .mappings()
                    .one()
                )
            return _dataset(row)
        except IntegrityError as exc:
            raise DomainError(
                code="DATASET_IDENTITY_INVALID",
                message="dataset code already exists or an explicit owner/MDM identity is invalid",
                status_code=409,
            ) from exc

    def get_chart_data(
        self,
        dataset_id: int,
        version_no: int,
        lot_id: str | None = None,
        wafer_id: str | None = None,
        source_id: str | None = None,
        parameter: str | None = None,
    ) -> DatasetChartData:
        if wafer_id and not lot_id:
            raise DomainError(
                "LOT_REQUIRED_FOR_WAFER",
                "wafer chart selection requires an explicit lot identity",
                422,
            )
        parameters = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "lot_id": lot_id,
            "wafer_id": wafer_id,
            "source_id": source_id,
            "parameter": parameter,
        }
        lot_filter = " AND (:lot_id IS NULL OR tr.lot_id=:lot_id)"
        wafer_filter = " AND (:wafer_id IS NULL OR tr.wafer_id=:wafer_id)"
        version_join = (
            " FROM dataset.dataset_version dv "
            "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        )
        with self._engine.connect() as connection:
            context = self._version_context(
                connection, dataset_id, version_no, lock=False
            )
            if str(context["status"]) != "PUBLISHED" or not bool(context["is_current"]):
                raise DomainError(
                    "ANALYSIS_VERSION_NOT_CURRENT",
                    "图表只允许查看当前已发布的正式版本",
                    409,
                )
            if str(context["test_stage"]) == "FT":
                return self._get_ft_chart_data(
                    connection,
                    context,
                    parameters,
                    version_join,
                )
            option_rows = (
                connection.execute(
                    text(
                        "SELECT DISTINCT tr.lot_id,tr.wafer_id"
                        + version_join
                        + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                        "AND tr.wafer_id IS NOT NULL ORDER BY tr.lot_id,tr.wafer_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            yield_rows = (
                connection.execute(
                    text(
                        "SELECT tr.lot_id,tr.wafer_id,COUNT_BIG(*) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                        "SUM(CASE WHEN ur.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                        "SUM(CASE WHEN ur.overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count "
                        + version_join
                        + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                        + lot_filter
                        + " GROUP BY tr.lot_id,tr.wafer_id ORDER BY tr.lot_id,tr.wafer_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            bin_rows = (
                connection.execute(
                    text(
                        "SELECT ISNULL(ur.soft_bin,'UNKNOWN') AS soft_bin,COUNT_BIG(*) AS unit_count "
                        + version_join
                        + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                        + lot_filter
                        + wafer_filter
                        + " GROUP BY ur.soft_bin ORDER BY unit_count DESC,soft_bin"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            map_rows: list[Mapping[str, Any]] = []
            if lot_id and wafer_id:
                map_rows = (
                    connection.execute(
                        text(
                            "SELECT ur.x_coord,ur.y_coord,ur.soft_bin,ur.overall_result "
                            + version_join
                            + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                            "AND tr.lot_id=:lot_id AND tr.wafer_id=:wafer_id "
                            "AND ur.x_coord IS NOT NULL AND ur.y_coord IS NOT NULL "
                            "ORDER BY ur.y_coord,ur.x_coord"
                        ),
                        parameters,
                    )
                    .mappings()
                    .all()
                )
        total_bins = sum(int(row["unit_count"]) for row in bin_rows)
        wafer_yield = tuple(_wafer_yield(row) for row in yield_rows)
        return DatasetChartData(
            dataset_id=dataset_id,
            version_no=version_no,
            test_stage=str(context["test_stage"]),
            product_name=context["product_name"],
            selected_lot_id=lot_id,
            selected_wafer_id=wafer_id,
            selected_source_id=None,
            selected_parameter=None,
            lot_options=tuple(dict.fromkeys(str(row["lot_id"]) for row in option_rows)),
            wafer_options=tuple(
                WaferOption(str(row["lot_id"]), str(row["wafer_id"]))
                for row in option_rows
            ),
            source_options=(),
            parameter_options=(),
            wafer_yield=wafer_yield,
            bin_counts=tuple(
                BinCountPoint(
                    soft_bin=str(row["soft_bin"]),
                    unit_count=int(row["unit_count"]),
                    percent=int(row["unit_count"]) / total_bins if total_bins else 0.0,
                )
                for row in bin_rows
            ),
            wafer_map=tuple(
                WaferMapPoint(
                    x=int(row["x_coord"]),
                    y=int(row["y_coord"]),
                    soft_bin=str(row["soft_bin"])
                    if row["soft_bin"] is not None
                    else None,
                    result=str(row["overall_result"]),
                )
                for row in map_rows
            ),
            ft_parameter_points=(),
            ft_total_point_count=0,
            ft_sampled=False,
        )

    def compare(self, request: DatasetComparisonRequest) -> DatasetComparisonResult:
        refs = tuple(request.datasets)
        contexts: list[Mapping[str, Any]] = []
        items: list[DatasetComparisonItem] = []
        parameter_signatures: dict[str, set[tuple[object, ...]]] = {
            name: set() for name in request.parameters
        }
        parameter_presence: dict[str, int] = {name: 0 for name in request.parameters}
        with self._engine.connect() as connection:
            for ref in refs:
                context = self._version_context(
                    connection, ref.dataset_id, ref.version_no, lock=False
                )
                if str(context["status"]) != "PUBLISHED" or not bool(
                    context["is_current"]
                ):
                    raise DomainError(
                        "ANALYSIS_VERSION_NOT_CURRENT",
                        "比较分析只允许选择当前已发布的正式版本",
                        409,
                    )
                contexts.append(context)
            stages = {str(context["test_stage"]) for context in contexts}
            if len(stages) != 1:
                raise DomainError(
                    "ANALYSIS_STAGE_INCOMPATIBLE",
                    "CP 与 FT 数据不能放入同一次比较",
                    409,
                )
            stage = next(iter(stages))
            if len(contexts) > 1 and stage == "CP":
                spec_ids = {context["spec_set_id"] for context in contexts}
                if None in spec_ids or len(spec_ids) != 1:
                    raise DomainError(
                        "ANALYSIS_SPEC_INCOMPATIBLE",
                        "所选 CP 数据的 Spec 不一致或无法证明一致，已阻止合并比较",
                        409,
                    )

            filter_sql, filter_parameters, expanding = _analysis_filter_sql(
                lot_ids=tuple(request.lot_ids),
                wafer_ids=tuple(request.wafer_ids),
                bin_codes=tuple(request.bin_codes),
            )
            version_join = (
                " FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            )
            for ref, context in zip(refs, contexts, strict=True):
                product_name = (
                    str(context["product_name"]).strip() or None
                    if context["product_name"] is not None
                    else None
                )
                parameters: dict[str, object] = {
                    "dataset_id": ref.dataset_id,
                    "version_no": ref.version_no,
                    **filter_parameters,
                }
                aggregate = (
                    connection.execute(
                        _statement(
                            "SELECT COUNT_BIG(*) AS unit_count,"
                            "SUM(CASE WHEN ur.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                            "SUM(CASE WHEN ur.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                            "SUM(CASE WHEN ur.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                            "SUM(CASE WHEN ur.overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count"
                            + version_join
                            + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                            + filter_sql,
                            expanding,
                        ),
                        parameters,
                    )
                    .mappings()
                    .one()
                )
                statistics: list[DatasetParameterStatistic] = []
                if request.parameters:
                    parameter_params = {
                        **parameters,
                        "analysis_parameters": tuple(request.parameters),
                    }
                    parameter_expanding = expanding + ("analysis_parameters",)
                    rows = (
                        connection.execute(
                            _statement(
                                "SELECT tid.raw_item_name,tid.unit_code,tid.program_lsl,"
                                "tid.program_usl,tid.condition_json,COUNT_BIG(*) AS row_count,"
                                "SUM(CASE WHEN m.value_numeric IS NULL THEN CONVERT(bigint,1) ELSE 0 END) AS missing_count,"
                                "MIN(m.value_numeric) AS minimum,MAX(m.value_numeric) AS maximum,"
                                "AVG(m.value_numeric) AS average"
                                + version_join
                                + "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                                + filter_sql
                                + " AND tid.raw_item_name IN :analysis_parameters "
                                "GROUP BY tid.raw_item_name,tid.unit_code,tid.program_lsl,"
                                "tid.program_usl,tid.condition_json ORDER BY tid.raw_item_name",
                                parameter_expanding,
                            ),
                            parameter_params,
                        )
                        .mappings()
                        .all()
                    )
                    present: set[str] = set()
                    for row in rows:
                        name = str(row["raw_item_name"])
                        present.add(name)
                        condition = _condition_text(
                            row["condition_json"], parameter=name
                        )
                        unit = (
                            str(row["unit_code"]).strip() or None
                            if row["unit_code"] is not None
                            else None
                        )
                        signature = (
                            unit,
                            _optional_finite_float(
                                row["program_lsl"], field=f"{name} LSL"
                            ),
                            _optional_finite_float(
                                row["program_usl"], field=f"{name} USL"
                            ),
                            condition,
                        )
                        row_count = int(row["row_count"] or 0)
                        missing_count = int(row["missing_count"] or 0)
                        if (
                            row_count < 0
                            or missing_count < 0
                            or missing_count > row_count
                        ):
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                f"parameter {name} has invalid aggregate counts",
                                409,
                            )
                        parameter_signatures.setdefault(name, set()).add(signature)
                        statistics.append(
                            DatasetParameterStatistic(
                                name=name,
                                unit=signature[0],
                                lsl=signature[1],
                                usl=signature[2],
                                test_condition=condition,
                                measured_count=row_count - missing_count,
                                missing_count=missing_count,
                                minimum=_optional_finite_float(
                                    row["minimum"], field=f"{name} minimum"
                                ),
                                maximum=_optional_finite_float(
                                    row["maximum"], field=f"{name} maximum"
                                ),
                                average=_optional_finite_float(
                                    row["average"], field=f"{name} average"
                                ),
                            )
                        )
                    for name in present:
                        parameter_presence[name] = parameter_presence.get(name, 0) + 1
                unit_count = int(aggregate["unit_count"] or 0)
                passed = int(aggregate["pass_count"] or 0)
                failed = int(aggregate["fail_count"] or 0)
                unknown = int(aggregate["unknown_count"] or 0)
                aborted = int(aggregate["abort_count"] or 0)
                counts = (unit_count, passed, failed, unknown, aborted)
                if any(value < 0 for value in counts) or (
                    passed + failed + unknown + aborted != unit_count
                ):
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "PASS/FAIL/UNKNOWN/ABORT counts do not reconcile to units",
                        409,
                    )
                known = passed + failed
                items.append(
                    DatasetComparisonItem(
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        test_stage=stage,
                        product_name=product_name,
                        unit_count=unit_count,
                        pass_count=passed,
                        fail_count=failed,
                        unknown_count=unknown,
                        abort_count=aborted,
                        known_yield_denominator=known,
                        yield_rate=passed / known if known else None,
                        parameter_statistics=tuple(statistics),
                    )
                )

        if request.parameters:
            incompatible = [
                name
                for name in request.parameters
                if parameter_presence.get(name, 0) != len(refs)
                or len(parameter_signatures.get(name, set())) != 1
            ]
            if incompatible:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "所选参数在各 Dataset 中缺失，或单位/Spec/测试条件不一致",
                    409,
                    details=[{"parameters": incompatible}],
                )
        compatibility = (
            "SINGLE_DATASET"
            if len(refs) == 1
            else "COMPATIBLE"
            if (stage == "CP" or request.parameters)
            else "NOT_EVALUATED"
        )
        return DatasetComparisonResult(
            test_stage=stage,
            spec_compatibility=compatibility,
            lot_ids=tuple(request.lot_ids),
            wafer_ids=tuple(request.wafer_ids),
            bin_codes=tuple(request.bin_codes),
            parameters=tuple(request.parameters),
            items=tuple(items),
        )

    def _analysis_source_run_ids(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        source_ids: tuple[str, ...],
    ) -> tuple[int, ...] | None:
        if not source_ids:
            return None
        rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT tr.run_id,tr.tester_id,tr.metadata_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                    "ORDER BY tr.run_id"
                ),
                {"dataset_id": dataset_id, "version_no": version_no},
            )
            .mappings()
            .all()
        )
        selected = set(source_ids)
        return tuple(
            int(row["run_id"]) for row in rows if _run_source_identity(row) in selected
        )

    def _analysis_identity_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        lot_ids: tuple[str, ...],
        source_run_ids: tuple[int, ...] | None,
        parameter_names: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        clauses: list[str] = []
        params: dict[str, object] = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "analysis_parameters": parameter_names,
        }
        expanding = ["analysis_parameters"]
        if lot_ids:
            clauses.append("tr.lot_id IN :identity_lot_ids")
            params["identity_lot_ids"] = lot_ids
            expanding.append("identity_lot_ids")
        if source_run_ids is not None:
            if source_run_ids:
                clauses.append("tr.run_id IN :identity_source_run_ids")
                params["identity_source_run_ids"] = source_run_ids
                expanding.append("identity_source_run_ids")
            else:
                clauses.append("1=0")
        rows = (
            connection.execute(
                _statement(
                    "SELECT DISTINCT tr.run_id,tr.program_version_id AS run_program_version_id,"
                    "tid.test_item_id,tid.program_version_id,tid.step_code,tid.sequence_no,"
                    "tid.raw_item_name,tid.canonical_parameter_code,tid.unit_code,"
                    "tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "AND tid.is_analysis_parameter=1 "
                    "AND tid.raw_item_name IN :analysis_parameters "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + (" AND " + " AND ".join(clauses) if clauses else "")
                    + " ORDER BY tr.run_id,tid.raw_item_name",
                    tuple(expanding),
                ),
                params,
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_preflight(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
    ) -> tuple[int, int]:
        parameters = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "analysis_test_item_ids": test_item_ids,
            **filter_parameters,
        }
        row = (
            connection.execute(
                _statement(
                    ";WITH filtered_units AS ("
                    "SELECT ur.unit_id,tr.program_version_id FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + "), selected_measurements AS ("
                    "SELECT m.measurement_id FROM filtered_units fu "
                    "JOIN test.measurement m ON m.unit_id=fu.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=fu.program_version_id "
                    "WHERE m.test_item_id IN :analysis_test_item_ids) "
                    "SELECT (SELECT COUNT_BIG(*) FROM filtered_units) AS matched_unit_count,"
                    "(SELECT COUNT_BIG(*) FROM selected_measurements) "
                    "AS candidate_measurement_count",
                    expanding + ("analysis_test_item_ids",),
                ),
                parameters,
            )
            .mappings()
            .one()
        )
        matched = int(row["matched_unit_count"] or 0)
        candidate = int(row["candidate_measurement_count"] or 0)
        if matched < 0 or candidate < 0:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "parameter-analysis preflight returned invalid counts",
                409,
            )
        if candidate > _PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS:
            raise DomainError(
                "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED",
                "parameter analysis exceeds the bounded measurement workload",
                422,
                details=[
                    {
                        "dataset_id": dataset_id,
                        "version_no": version_no,
                        "actual": candidate,
                        "limit": _PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS,
                    }
                ],
            )
        return matched, candidate

    def _analysis_aggregate_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        status_columns = ",".join(
            "SUM(CASE WHEN m.measurement_status='"
            + status
            + "' THEN CONVERT(bigint,1) ELSE 0 END) AS status_"
            + status.lower()
            for status in _MEASUREMENT_STATUSES
        )
        rows = (
            connection.execute(
                _statement(
                    "SELECT tid.raw_item_name,COUNT_BIG(*) AS row_count,"
                    "SUM(CASE WHEN m.measurement_status='MEASURED' "
                    "AND m.value_numeric IS NOT NULL THEN CONVERT(bigint,1) ELSE 0 END) "
                    "AS numeric_count,"
                    + status_columns
                    + ",MIN(CASE WHEN m.measurement_status='MEASURED' "
                    "THEN m.value_numeric END) AS minimum,"
                    "MAX(CASE WHEN m.measurement_status='MEASURED' "
                    "THEN m.value_numeric END) AS maximum,"
                    "AVG(CASE WHEN m.measurement_status='MEASURED' "
                    "THEN m.value_numeric END) AS average,"
                    "STDEV(CASE WHEN m.measurement_status='MEASURED' "
                    "THEN m.value_numeric END) AS sample_stddev "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + " AND m.test_item_id IN :analysis_test_item_ids "
                    "GROUP BY tid.raw_item_name ORDER BY tid.raw_item_name",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_box_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        rows = (
            connection.execute(
                _statement(
                    ";WITH numeric_values AS ("
                    "SELECT tid.raw_item_name,m.value_numeric FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + " AND m.test_item_id IN :analysis_test_item_ids "
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                    "quartiles AS (SELECT raw_item_name,value_numeric,"
                    "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY value_numeric) "
                    "OVER(PARTITION BY raw_item_name) AS q1,"
                    "PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY value_numeric) "
                    "OVER(PARTITION BY raw_item_name) AS median,"
                    "PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY value_numeric) "
                    "OVER(PARTITION BY raw_item_name) AS q3 FROM numeric_values) "
                    "SELECT raw_item_name,MIN(value_numeric) AS minimum,MAX(q1) AS q1,"
                    "MAX(median) AS median,MAX(q3) AS q3,MAX(value_numeric) AS maximum,"
                    "MIN(CASE WHEN value_numeric>=q1-1.5*(q3-q1) THEN value_numeric END) "
                    "AS lower_whisker,"
                    "MAX(CASE WHEN value_numeric<=q3+1.5*(q3-q1) THEN value_numeric END) "
                    "AS upper_whisker,"
                    "SUM(CASE WHEN value_numeric<q1-1.5*(q3-q1) "
                    "OR value_numeric>q3+1.5*(q3-q1) "
                    "THEN CONVERT(bigint,1) ELSE 0 END) AS outlier_count "
                    "FROM quartiles GROUP BY raw_item_name ORDER BY raw_item_name",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_histogram_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
        bin_count: int,
    ) -> tuple[Mapping[str, Any], ...]:
        rows = (
            connection.execute(
                _statement(
                    ";WITH numeric_values AS ("
                    "SELECT tid.raw_item_name,m.value_numeric FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + " AND m.test_item_id IN :analysis_test_item_ids "
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                    "bounds AS (SELECT raw_item_name,MIN(value_numeric) AS range_min,"
                    "MAX(value_numeric) AS range_max FROM numeric_values GROUP BY raw_item_name),"
                    "bucketed AS (SELECT v.raw_item_name,b.range_min,b.range_max,"
                    "CASE WHEN b.range_min=b.range_max THEN 0 "
                    "WHEN v.value_numeric=b.range_max THEN :histogram_bin_count-1 "
                    "ELSE CONVERT(int,FLOOR((v.value_numeric-b.range_min)*"
                    ":histogram_bin_count/NULLIF(b.range_max-b.range_min,0))) END "
                    "AS bin_index FROM numeric_values v JOIN bounds b "
                    "ON b.raw_item_name=v.raw_item_name) "
                    "SELECT raw_item_name,range_min,range_max,bin_index,"
                    "COUNT_BIG(*) AS bin_value_count FROM bucketed "
                    "GROUP BY raw_item_name,range_min,range_max,bin_index "
                    "ORDER BY raw_item_name,bin_index",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    "histogram_bin_count": bin_count,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_spec_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        test_stage: str,
        dataset_spec_set_id: int | None,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        if test_stage == "CP":
            spec_joins = (
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=:dataset_spec_set_id "
                "AND ss.status='RELEASED' "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        else:
            spec_joins = (
                "LEFT JOIN mdm.spec_binding sb ON sb.active=1 "
                "AND sb.program_version_id=tr.program_version_id "
                "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
                "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
                "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
                "AND ss.status='RELEASED' "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        rows = (
            connection.execute(
                _statement(
                    "SELECT DISTINCT tr.run_id,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.program_version_id AS item_program_version_id,"
                    "tid.test_item_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "tid.raw_item_name,ss.spec_set_id,si.spec_item_id,si.unit_code,"
                    "si.lsl,si.usl,si.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=tr.program_version_id "
                    + spec_joins
                    + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + " AND m.test_item_id IN :analysis_test_item_ids "
                    "ORDER BY tid.raw_item_name,tr.run_id,tid.test_item_id,ss.spec_set_id",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "dataset_spec_set_id": dataset_spec_set_id,
                    "analysis_test_item_ids": test_item_ids,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_subgroup_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
        rule_code: str,
    ) -> tuple[Mapping[str, Any], ...]:
        if rule_code == "CPK_POOLED_WITHIN_RUN_V1":
            subgroup_expression = "CONVERT(nvarchar(64),tr.run_id)"
            identity_complete_expression = "1"
        elif rule_code == "CPK_POOLED_WITHIN_LOT_WAFER_V1":
            subgroup_expression = (
                "COALESCE(tr.lot_id,N'')+N'|'+COALESCE(ur.wafer_id,tr.wafer_id,N'')"
            )
            identity_complete_expression = (
                "CASE WHEN "
                "NULLIF(LTRIM(RTRIM(tr.lot_id)),N'') IS NULL OR "
                "NULLIF(LTRIM(RTRIM(COALESCE(ur.wafer_id,tr.wafer_id))),N'') IS NULL "
                "THEN 0 ELSE 1 END"
            )
        else:
            raise DomainError(
                "ANALYSIS_CAPABILITY_RULE_INVALID",
                "unsupported Cpk subgroup rule",
                422,
            )
        rows = (
            connection.execute(
                _statement(
                    "SELECT tid.raw_item_name,"
                    + subgroup_expression
                    + " AS subgroup_key,MIN("
                    + identity_complete_expression
                    + ") AS subgroup_identity_complete,COUNT_BIG(*) AS subgroup_count,"
                    "STDEV(m.value_numeric) AS subgroup_stddev "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + filter_sql
                    + " AND m.test_item_id IN :analysis_test_item_ids "
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL "
                    "GROUP BY tid.raw_item_name,"
                    + subgroup_expression
                    + " ORDER BY tid.raw_item_name,subgroup_key",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_capability_result(
        self,
        *,
        parameter: str,
        identity: DatasetAnalysisParameterIdentity,
        statistics: Mapping[str, Any],
        spec_rows: tuple[Mapping[str, Any], ...],
        subgroup_rows: tuple[Mapping[str, Any], ...],
        rule_code: str | None,
    ) -> tuple[DatasetCapabilityStatistics, tuple[int, ...], str]:
        if rule_code is None:
            return (
                DatasetCapabilityStatistics(
                    status="NOT_ELIGIBLE",
                    ppk_status="NOT_REQUESTED",
                    cpk_status="NOT_REQUESTED",
                    reason_codes=("CAPABILITY_RULE_REQUIRED",),
                    spec_mode=None,
                    lsl=None,
                    usl=None,
                    sample_count=int(statistics["numeric_count"]),
                    subgroup_count=0,
                    overall_sigma=None,
                    within_sigma=None,
                    ppl=None,
                    ppu=None,
                    ppk=None,
                    cpl=None,
                    cpu=None,
                    cpk=None,
                    rule_code=None,
                ),
                identity.spec_set_ids,
                "NOT_EVALUATED",
            )
        reasons: list[str] = []
        covered_spec_set_ids: set[int] = set()
        spec_signatures: set[tuple[object, ...]] = set()
        scope_rows: dict[tuple[object, ...], list[Mapping[str, Any]]] = {}
        for row in spec_rows:
            scope_key = (
                row["run_id"],
                row["run_program_version_id"],
                row["item_program_version_id"],
                row["test_item_id"],
                row["lot_id"],
                row["wafer_id"],
            )
            scope_rows.setdefault(scope_key, []).append(row)
        scope_uncovered = False
        scope_ambiguous = False
        for scope_key, rows in scope_rows.items():
            if scope_key[1] is None or scope_key[1] != scope_key[2]:
                scope_uncovered = True
                continue
            covered_rows = tuple(
                row
                for row in rows
                if row["spec_set_id"] is not None and row["spec_item_id"] is not None
            )
            if not covered_rows:
                scope_uncovered = True
                continue
            scope_spec_ids = {int(row["spec_set_id"]) for row in covered_rows}
            scope_signatures = {
                (
                    str(row["unit_code"]).strip() or None
                    if row["unit_code"] is not None
                    else None,
                    _optional_finite_float(row["lsl"], field=f"{parameter} formal LSL"),
                    _optional_finite_float(row["usl"], field=f"{parameter} formal USL"),
                    _condition_text(
                        row["condition_json"], parameter=f"{parameter} formal spec"
                    ),
                )
                for row in covered_rows
            }
            if len(scope_spec_ids) != 1 or len(scope_signatures) != 1:
                scope_ambiguous = True
                continue
            covered_spec_set_ids.update(scope_spec_ids)
            spec_signatures.update(scope_signatures)
        spec_set_ids = tuple(sorted(covered_spec_set_ids))
        formal_signature: tuple[object, ...] | None = None
        if not spec_rows:
            reasons.append("FORMAL_RELEASED_SPEC_NOT_FOUND")
        elif scope_uncovered:
            reasons.append("FORMAL_SPEC_SCOPE_NOT_COVERED")
        if scope_ambiguous or len(spec_signatures) > 1 or len(spec_set_ids) > 1:
            reasons.append("SPEC_CONTEXT_AMBIGUOUS")
        if not reasons and len(spec_signatures) == 1 and len(spec_set_ids) == 1:
            formal_signature = next(iter(spec_signatures))
            if (
                formal_signature[0] != identity.unit
                or formal_signature[3] != identity.test_condition
            ):
                reasons.append("SPEC_CONTEXT_AMBIGUOUS")
                formal_signature = None

        lsl = formal_signature[1] if formal_signature is not None else None
        usl = formal_signature[2] if formal_signature is not None else None
        if lsl is not None and usl is not None and float(lsl) > float(usl):
            raise DomainError(
                "ANALYSIS_SPEC_CONTRACT_INVALID",
                f"parameter {parameter} has reversed formal specification limits",
                409,
            )
        if lsl is None and usl is None:
            reasons.append("FORMAL_SPEC_LIMIT_MISSING")
            spec_mode = None
        elif lsl is None:
            spec_mode = "UPPER_ONLY"
        elif usl is None:
            spec_mode = "LOWER_ONLY"
        else:
            spec_mode = "TWO_SIDED"

        sample_count = int(statistics["numeric_count"])
        status_counts: Mapping[str, int] = statistics["status_counts"]
        if int(status_counts.get("OVER_RANGE", 0)) or int(
            status_counts.get("UNDER_RANGE", 0)
        ):
            reasons.append("CENSORED_MEASUREMENTS_PRESENT")
        if int(status_counts.get("MEASURED", 0)) != sample_count:
            reasons.append("MEASURED_VALUE_MISSING")
        mean = statistics["average"]
        overall_sigma = statistics["sample_stddev"]
        if mean is None:
            reasons.append("PPK_MEAN_UNAVAILABLE")
        if overall_sigma is None or float(overall_sigma) <= 0.0:
            reasons.append("PPK_OVERALL_SIGMA_NOT_POSITIVE")

        ppk_blockers = tuple(dict.fromkeys(reasons))
        ppl = ppu = ppk = None
        if not ppk_blockers:
            mean_value = float(mean)
            sigma_value = float(overall_sigma)
            ppl = _capability_side(
                mean=mean_value,
                limit=float(lsl) if lsl is not None else None,
                sigma=sigma_value,
                lower=True,
            )
            ppu = _capability_side(
                mean=mean_value,
                limit=float(usl) if usl is not None else None,
                sigma=sigma_value,
                lower=False,
            )
            ppk = _combined_capability(ppl, ppu)
        ppk_status = "ELIGIBLE" if ppk is not None else "NOT_ELIGIBLE"

        cpl = cpu = cpk = within_sigma = None
        subgroup_count = 0
        cpk_reasons: list[str] = []
        if rule_code is None:
            raise AssertionError("capability rule gate was not applied")
        else:
            cpk_reasons.extend(ppk_blockers)
            selected_subgroups = tuple(
                row for row in subgroup_rows if str(row["raw_item_name"]) == parameter
            )
            subgroup_count = len(selected_subgroups)
            counts: list[int] = []
            variance_numerator = 0.0
            variance_denominator = 0
            for row in selected_subgroups:
                count = int(row["subgroup_count"] or 0)
                counts.append(count)
                if not bool(row.get("subgroup_identity_complete", 1)):
                    cpk_reasons.append("CPK_SUBGROUP_IDENTITY_MISSING")
                if count < 2:
                    cpk_reasons.append("CPK_SUBGROUP_INSUFFICIENT_DF")
                    continue
                sigma = _optional_finite_float(
                    row["subgroup_stddev"],
                    field=f"{parameter} subgroup standard deviation",
                )
                if sigma is None:
                    cpk_reasons.append("CPK_SUBGROUP_SIGMA_UNAVAILABLE")
                    continue
                if sigma < 0.0:
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        f"parameter {parameter} subgroup standard deviation is negative",
                        409,
                    )
                variance_numerator += (count - 1) * sigma * sigma
                variance_denominator += count - 1
            if selected_subgroups and sum(counts) != sample_count:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    f"parameter {parameter} subgroup counts do not reconcile",
                    409,
                )
            if variance_denominator > 0:
                within_sigma = math.sqrt(variance_numerator / variance_denominator)
                if not math.isfinite(within_sigma) or within_sigma <= 0.0:
                    within_sigma = None
                    cpk_reasons.append("CPK_WITHIN_SIGMA_NOT_POSITIVE")
            else:
                cpk_reasons.append("CPK_WITHIN_SIGMA_NOT_POSITIVE")
            cpk_reasons = list(dict.fromkeys(cpk_reasons))
            if not cpk_reasons and within_sigma is not None:
                mean_value = float(mean)
                cpl = _capability_side(
                    mean=mean_value,
                    limit=float(lsl) if lsl is not None else None,
                    sigma=within_sigma,
                    lower=True,
                )
                cpu = _capability_side(
                    mean=mean_value,
                    limit=float(usl) if usl is not None else None,
                    sigma=within_sigma,
                    lower=False,
                )
                cpk = _combined_capability(cpl, cpu)
            cpk_status = "ELIGIBLE" if cpk is not None else "NOT_ELIGIBLE"

        combined_reasons = tuple(dict.fromkeys((*ppk_blockers, *cpk_reasons)))
        if ppk_status == "NOT_ELIGIBLE":
            status = "NOT_ELIGIBLE"
        elif cpk_status == "NOT_ELIGIBLE":
            status = "PARTIAL"
        else:
            status = "ELIGIBLE"
        return (
            DatasetCapabilityStatistics(
                status=status,
                ppk_status=ppk_status,
                cpk_status=cpk_status,
                reason_codes=combined_reasons,
                spec_mode=spec_mode,
                lsl=float(lsl) if lsl is not None else None,
                usl=float(usl) if usl is not None else None,
                sample_count=sample_count,
                subgroup_count=subgroup_count,
                overall_sigma=float(overall_sigma)
                if overall_sigma is not None
                else None,
                within_sigma=within_sigma,
                ppl=ppl,
                ppu=ppu,
                ppk=ppk,
                cpl=cpl,
                cpu=cpu,
                cpk=cpk,
                rule_code=rule_code,
            ),
            spec_set_ids if formal_signature is not None else (),
            "RELEASED_SPEC" if formal_signature is not None else "UNRESOLVED",
        )

    def analyze_parameters(
        self, request: DatasetParameterAnalysisRequest
    ) -> DatasetParameterAnalysisResult:
        self.assert_parameter_analysis_rules_approved(request)
        lot_ids = _normalized_filter_values(
            tuple(request.filters.lot_ids), field="lot_ids"
        )
        wafer_ids = _normalized_filter_values(
            tuple(request.filters.wafer_ids), field="wafer_ids"
        )
        bin_codes = _normalized_filter_values(
            tuple(request.filters.bin_codes), field="bin_codes"
        )
        source_ids = _normalized_filter_values(
            tuple(request.filters.source_ids), field="source_ids"
        )
        overall_results = _normalized_filter_values(
            tuple(item.value for item in request.filters.overall_results),
            field="overall_results",
        )
        parameter_names = _normalized_filter_values(
            tuple(request.parameters), field="parameters"
        )
        analysis_types = {item.value for item in request.analyses}
        rule_code = (
            request.capability.rule_code.value
            if request.capability.rule_code is not None
            else None
        )
        refs = tuple(request.datasets)
        contexts: list[Mapping[str, Any]] = []
        work: list[
            tuple[
                object,
                Mapping[str, Any],
                tuple[int, ...] | None,
                dict[str, DatasetAnalysisParameterIdentity],
                dict[str, tuple[object, ...]],
                dict[str, tuple[int, ...]],
            ]
        ] = []
        with self._engine.connect() as connection:
            for ref in refs:
                context = self._version_context(
                    connection, ref.dataset_id, ref.version_no, lock=False
                )
                if str(context["status"]) != "PUBLISHED" or not bool(
                    context["is_current"]
                ):
                    raise DomainError(
                        "ANALYSIS_VERSION_NOT_CURRENT",
                        "parameter analysis only allows Current Published Dataset Versions",
                        409,
                    )
                contexts.append(context)
            stages = {str(context["test_stage"]) for context in contexts}
            if len(stages) != 1:
                raise DomainError(
                    "ANALYSIS_STAGE_INCOMPATIBLE",
                    "CP and FT datasets cannot be combined in one parameter analysis",
                    409,
                )
            stage = next(iter(stages))
            if stage not in {"CP", "FT"}:
                raise DomainError(
                    "ANALYSIS_STAGE_INCOMPATIBLE",
                    "parameter analysis currently supports CP and FT datasets only",
                    409,
                )
            if len(contexts) > 1 and stage == "CP":
                spec_ids = {context["spec_set_id"] for context in contexts}
                if None in spec_ids or len(spec_ids) != 1:
                    raise DomainError(
                        "ANALYSIS_SPEC_INCOMPATIBLE",
                        "selected CP datasets do not have provably compatible specifications",
                        409,
                    )

            all_signatures: dict[str, set[tuple[object, ...]]] = {
                name: set() for name in parameter_names
            }
            for ref, context in zip(refs, contexts, strict=True):
                source_run_ids = self._analysis_source_run_ids(
                    connection,
                    dataset_id=ref.dataset_id,
                    version_no=ref.version_no,
                    source_ids=source_ids,
                )
                identity_rows = self._analysis_identity_rows(
                    connection,
                    dataset_id=ref.dataset_id,
                    version_no=ref.version_no,
                    lot_ids=lot_ids,
                    source_run_ids=source_run_ids,
                    parameter_names=parameter_names,
                )
                grouped_rows: dict[str, list[Mapping[str, Any]]] = {}
                available_by_program: dict[int, set[str]] = {}
                for row in identity_rows:
                    run_program_version_id = row["run_program_version_id"]
                    if run_program_version_id is None:
                        raise DomainError(
                            "ANALYSIS_PARAMETER_INCOMPATIBLE",
                            "selected run has no program-version identity",
                            409,
                        )
                    program_id = int(run_program_version_id)
                    available_by_program.setdefault(program_id, set())
                    if row["raw_item_name"] is None:
                        continue
                    name = str(row["raw_item_name"])
                    grouped_rows.setdefault(name, []).append(row)
                    available_by_program[program_id].add(name)
                identities: dict[str, DatasetAnalysisParameterIdentity] = {}
                signatures: dict[str, tuple[object, ...]] = {}
                allowed_test_item_ids: dict[str, tuple[int, ...]] = {}
                missing = sorted(
                    {
                        name
                        for name in parameter_names
                        if name not in grouped_rows
                        or any(
                            name not in available
                            for available in available_by_program.values()
                        )
                    }
                )
                if missing:
                    raise DomainError(
                        "ANALYSIS_PARAMETER_INCOMPATIBLE",
                        "one or more parameters are unavailable after run-level filters",
                        409,
                        details=[
                            {
                                "dataset_id": ref.dataset_id,
                                "version_no": ref.version_no,
                                "parameters": missing,
                            }
                        ],
                    )
                for name in parameter_names:
                    rows = grouped_rows[name]
                    definitions_by_program: dict[int, set[tuple[str, int]]] = {}
                    for row in rows:
                        step_code = str(row["step_code"] or "").strip().upper()
                        if not step_code:
                            raise DomainError(
                                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                                "selected parameter has an empty step identity",
                                409,
                                details=[
                                    {
                                        "dataset_id": ref.dataset_id,
                                        "version_no": ref.version_no,
                                        "parameters": [name],
                                    }
                                ],
                            )
                        definitions_by_program.setdefault(
                            int(row["program_version_id"]), set()
                        ).add((step_code, int(row["sequence_no"])))
                    normalized_step_codes = {
                        step_code
                        for definitions in definitions_by_program.values()
                        for step_code, _ in definitions
                    }
                    sequence_nos = {
                        sequence_no
                        for definitions in definitions_by_program.values()
                        for _, sequence_no in definitions
                    }
                    if (
                        any(
                            len(definitions) != 1
                            for definitions in definitions_by_program.values()
                        )
                        or len(normalized_step_codes) != 1
                        or len(sequence_nos) != 1
                    ):
                        raise DomainError(
                            "ANALYSIS_PARAMETER_INCOMPATIBLE",
                            "selected raw parameter name does not resolve to one stable step identity",
                            409,
                            details=[
                                {
                                    "dataset_id": ref.dataset_id,
                                    "version_no": ref.version_no,
                                    "parameters": [name],
                                }
                            ],
                        )
                    stable_step_code = next(iter(normalized_step_codes))
                    stable_sequence_no = next(iter(sequence_nos))
                    canonical_codes = {
                        str(row["canonical_parameter_code"]).strip() or None
                        if row["canonical_parameter_code"] is not None
                        else None
                        for row in rows
                    }
                    parameter_signatures = {
                        (
                            str(row["unit_code"]).strip() or None
                            if row["unit_code"] is not None
                            else None,
                            _optional_finite_float(
                                row["program_lsl"], field=f"{name} program LSL"
                            ),
                            _optional_finite_float(
                                row["program_usl"], field=f"{name} program USL"
                            ),
                            _condition_text(row["condition_json"], parameter=name),
                        )
                        for row in rows
                    }
                    if len(canonical_codes) != 1 or len(parameter_signatures) != 1:
                        raise DomainError(
                            "ANALYSIS_PARAMETER_INCOMPATIBLE",
                            "selected parameter has conflicting identity metadata",
                            409,
                            details=[
                                {
                                    "dataset_id": ref.dataset_id,
                                    "version_no": ref.version_no,
                                    "parameters": [name],
                                }
                            ],
                        )
                    signature = next(iter(parameter_signatures))
                    canonical_parameter_code = next(iter(canonical_codes))
                    item_ids = tuple(sorted({int(row["test_item_id"]) for row in rows}))
                    if not item_ids:
                        raise DomainError(
                            "ANALYSIS_PARAMETER_INCOMPATIBLE",
                            "selected parameter has no exact test-item identity",
                            409,
                        )
                    allowed_test_item_ids[name] = item_ids
                    cross_dataset_signature = (
                        stable_step_code,
                        stable_sequence_no,
                        canonical_parameter_code,
                        *signature,
                    )
                    signatures[name] = cross_dataset_signature
                    all_signatures[name].add(cross_dataset_signature)
                    context_spec_id = context["spec_set_id"]
                    identities[name] = DatasetAnalysisParameterIdentity(
                        name=name,
                        canonical_parameter_code=canonical_parameter_code,
                        unit=signature[0],
                        program_lsl=signature[1],
                        program_usl=signature[2],
                        test_condition=signature[3],
                        spec_set_ids=(int(context_spec_id),)
                        if context_spec_id is not None
                        else (),
                        limit_source="PROGRAM_METADATA",
                    )
                work.append(
                    (
                        ref,
                        context,
                        source_run_ids,
                        identities,
                        signatures,
                        allowed_test_item_ids,
                    )
                )

            incompatible = [
                name
                for name, signatures in all_signatures.items()
                if len(signatures) != 1
            ]
            if incompatible:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "selected parameters have incompatible unit, limits, or test conditions",
                    409,
                    details=[{"parameters": incompatible}],
                )

            items: list[DatasetParameterAnalysisItem] = []
            formal_compatibility_signatures: dict[str, set[tuple[object, ...]]] = {
                name: set() for name in parameter_names
            }
            unresolved_formal_parameters: set[str] = set()
            for (
                ref,
                context,
                source_run_ids,
                identities,
                _,
                allowed_test_item_ids,
            ) in work:
                selected_test_item_ids = tuple(
                    sorted(
                        {
                            item_id
                            for item_ids in allowed_test_item_ids.values()
                            for item_id in item_ids
                        }
                    )
                )
                filter_sql, filter_parameters, expanding = _analysis_filter_sql(
                    lot_ids=lot_ids,
                    wafer_ids=wafer_ids,
                    bin_codes=bin_codes,
                    overall_results=overall_results,
                    source_run_ids=source_run_ids,
                )
                matched_units, candidate_measurements = self._analysis_preflight(
                    connection,
                    dataset_id=ref.dataset_id,
                    version_no=ref.version_no,
                    filter_sql=filter_sql,
                    filter_parameters=filter_parameters,
                    expanding=expanding,
                    test_item_ids=selected_test_item_ids,
                )
                aggregate_rows = self._analysis_aggregate_rows(
                    connection,
                    dataset_id=ref.dataset_id,
                    version_no=ref.version_no,
                    filter_sql=filter_sql,
                    filter_parameters=filter_parameters,
                    expanding=expanding,
                    test_item_ids=selected_test_item_ids,
                )
                box_rows = (
                    self._analysis_box_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                    )
                    if DatasetParameterAnalysisType.BOX_PLOT.value in analysis_types
                    else ()
                )
                histogram_rows = (
                    self._analysis_histogram_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                        bin_count=request.histogram.bin_count,
                    )
                    if DatasetParameterAnalysisType.HISTOGRAM.value in analysis_types
                    else ()
                )
                spec_rows = (
                    self._analysis_spec_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        test_stage=str(context["test_stage"]),
                        dataset_spec_set_id=(
                            int(context["spec_set_id"])
                            if context["spec_set_id"] is not None
                            else None
                        ),
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                    )
                    if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types
                    and rule_code is not None
                    else ()
                )
                subgroup_rows = (
                    self._analysis_subgroup_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                        rule_code=rule_code,
                    )
                    if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types
                    and rule_code is not None
                    else ()
                )

                aggregate_by_name = {
                    str(row["raw_item_name"]): row for row in aggregate_rows
                }
                if any(name not in parameter_names for name in aggregate_by_name):
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "parameter aggregate returned an unrequested parameter",
                        409,
                    )
                internal_statistics: dict[str, dict[str, Any]] = {}
                aggregate_total = 0
                for name in parameter_names:
                    row = aggregate_by_name.get(name)
                    if row is None:
                        row_count = numeric_count = 0
                        status_counts = {status: 0 for status in _MEASUREMENT_STATUSES}
                        minimum = maximum = average = sample_stddev = None
                    else:
                        row_count = int(row["row_count"] or 0)
                        numeric_count = int(row["numeric_count"] or 0)
                        status_counts = {
                            status: int(row[f"status_{status.lower()}"] or 0)
                            for status in _MEASUREMENT_STATUSES
                        }
                        minimum = _optional_finite_float(
                            row["minimum"], field=f"{name} minimum"
                        )
                        maximum = _optional_finite_float(
                            row["maximum"], field=f"{name} maximum"
                        )
                        average = _optional_finite_float(
                            row["average"], field=f"{name} average"
                        )
                        sample_stddev = _optional_finite_float(
                            row["sample_stddev"],
                            field=f"{name} sample standard deviation",
                        )
                    if (
                        row_count < 0
                        or numeric_count < 0
                        or numeric_count > row_count
                        or sum(status_counts.values()) != row_count
                    ):
                        raise DomainError(
                            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                            f"parameter {name} has invalid aggregate counts",
                            409,
                        )
                    aggregate_total += row_count
                    internal_statistics[name] = {
                        "row_count": row_count,
                        "numeric_count": numeric_count,
                        "status_counts": status_counts,
                        "minimum": minimum,
                        "maximum": maximum,
                        "average": average,
                        "sample_stddev": sample_stddev,
                    }
                if aggregate_total != candidate_measurements:
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "parameter measurement counts do not reconcile to preflight",
                        409,
                    )

                box_by_name = {str(row["raw_item_name"]): row for row in box_rows}
                histogram_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in histogram_rows:
                    histogram_by_name.setdefault(str(row["raw_item_name"]), []).append(
                        row
                    )
                spec_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in spec_rows:
                    spec_by_name.setdefault(str(row["raw_item_name"]), []).append(row)

                parameter_results: list[DatasetParameterAnalysis] = []
                for name in parameter_names:
                    stats = internal_statistics[name]
                    descriptive = (
                        DatasetDescriptiveStatistics(
                            row_count=stats["row_count"],
                            numeric_count=stats["numeric_count"],
                            excluded_count=stats["row_count"] - stats["numeric_count"],
                            minimum=stats["minimum"],
                            maximum=stats["maximum"],
                            average=stats["average"],
                            sample_stddev=stats["sample_stddev"],
                        )
                        if DatasetParameterAnalysisType.DESCRIPTIVE.value
                        in analysis_types
                        else None
                    )
                    box_plot = None
                    box_row = box_by_name.get(name)
                    if (
                        DatasetParameterAnalysisType.BOX_PLOT.value in analysis_types
                        and stats["numeric_count"] > 0
                        and box_row is None
                    ):
                        raise DomainError(
                            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                            f"parameter {name} is missing requested box-plot aggregates",
                            409,
                        )
                    if box_row is not None:
                        values = {
                            field: _optional_finite_float(
                                box_row[field], field=f"{name} box {field}"
                            )
                            for field in (
                                "minimum",
                                "q1",
                                "median",
                                "q3",
                                "maximum",
                                "lower_whisker",
                                "upper_whisker",
                            )
                        }
                        if any(value is None for value in values.values()):
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                f"parameter {name} has incomplete box-plot aggregates",
                                409,
                            )
                        ordered = tuple(
                            float(values[field])
                            for field in ("minimum", "q1", "median", "q3", "maximum")
                        )
                        lower_whisker = float(values["lower_whisker"])
                        upper_whisker = float(values["upper_whisker"])
                        outlier_count = int(box_row["outlier_count"] or 0)
                        if (
                            tuple(sorted(ordered)) != ordered
                            or not ordered[0]
                            <= lower_whisker
                            <= upper_whisker
                            <= ordered[-1]
                            or outlier_count < 0
                            or outlier_count > stats["numeric_count"]
                        ):
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                f"parameter {name} has invalid box-plot aggregates",
                                409,
                            )
                        box_plot = DatasetBoxPlotStatistics(
                            minimum=ordered[0],
                            q1=ordered[1],
                            median=ordered[2],
                            q3=ordered[3],
                            maximum=ordered[4],
                            lower_whisker=lower_whisker,
                            upper_whisker=upper_whisker,
                            outlier_count=outlier_count,
                            method=_BOX_PLOT_METHOD,
                        )

                    histogram = None
                    if DatasetParameterAnalysisType.HISTOGRAM.value in analysis_types:
                        rows = histogram_by_name.get(name, [])
                        if not rows:
                            if stats["numeric_count"] > 0:
                                raise DomainError(
                                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                    f"parameter {name} is missing requested histogram aggregates",
                                    409,
                                )
                            histogram = DatasetHistogramStatistics(
                                bin_count=0,
                                requested_bin_count=request.histogram.bin_count,
                                range_min=None,
                                range_max=None,
                                bins=(),
                            )
                        else:
                            range_pairs = {
                                (
                                    _optional_finite_float(
                                        row["range_min"],
                                        field=f"{name} histogram minimum",
                                    ),
                                    _optional_finite_float(
                                        row["range_max"],
                                        field=f"{name} histogram maximum",
                                    ),
                                )
                                for row in rows
                            }
                            if len(range_pairs) != 1 or None in next(iter(range_pairs)):
                                raise DomainError(
                                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                    f"parameter {name} has invalid histogram bounds",
                                    409,
                                )
                            range_min, range_max = (
                                float(value) for value in next(iter(range_pairs))
                            )
                            actual_bin_count = (
                                1
                                if range_min == range_max
                                else request.histogram.bin_count
                            )
                            counts = [0] * actual_bin_count
                            for row in rows:
                                index = int(row["bin_index"])
                                count = int(row["bin_value_count"] or 0)
                                if index < 0 or index >= actual_bin_count or count < 0:
                                    raise DomainError(
                                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                        f"parameter {name} has invalid histogram buckets",
                                        409,
                                    )
                                counts[index] += count
                            if sum(counts) != stats["numeric_count"]:
                                raise DomainError(
                                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                    f"parameter {name} histogram does not reconcile",
                                    409,
                                )
                            width = (
                                (range_max - range_min) / actual_bin_count
                                if actual_bin_count > 1
                                else 0.0
                            )
                            bins = tuple(
                                DatasetHistogramBin(
                                    index=index,
                                    lower_bound=(
                                        range_min + width * index
                                        if actual_bin_count > 1
                                        else range_min
                                    ),
                                    upper_bound=(
                                        range_max
                                        if index == actual_bin_count - 1
                                        else range_min + width * (index + 1)
                                    ),
                                    count=count,
                                    lower_inclusive=True,
                                    upper_inclusive=index == actual_bin_count - 1,
                                )
                                for index, count in enumerate(counts)
                            )
                            histogram = DatasetHistogramStatistics(
                                bin_count=actual_bin_count,
                                requested_bin_count=request.histogram.bin_count,
                                range_min=range_min,
                                range_max=range_max,
                                bins=bins,
                            )

                    capability = None
                    identity = identities[name]
                    if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types:
                        capability, spec_set_ids, limit_source = (
                            self._analysis_capability_result(
                                parameter=name,
                                identity=identity,
                                statistics=stats,
                                spec_rows=tuple(spec_by_name.get(name, ())),
                                subgroup_rows=subgroup_rows,
                                rule_code=rule_code,
                            )
                        )
                        identity = DatasetAnalysisParameterIdentity(
                            name=identity.name,
                            canonical_parameter_code=identity.canonical_parameter_code,
                            unit=identity.unit,
                            program_lsl=identity.program_lsl,
                            program_usl=identity.program_usl,
                            test_condition=identity.test_condition,
                            spec_set_ids=spec_set_ids,
                            limit_source=limit_source,
                        )
                        if len(refs) > 1:
                            formal_blockers = {
                                "FORMAL_RELEASED_SPEC_NOT_FOUND",
                                "FORMAL_SPEC_SCOPE_NOT_COVERED",
                                "SPEC_CONTEXT_AMBIGUOUS",
                                "FORMAL_SPEC_LIMIT_MISSING",
                            }
                            if formal_blockers.intersection(capability.reason_codes):
                                unresolved_formal_parameters.add(name)
                            else:
                                formal_compatibility_signatures[name].add(
                                    (
                                        identity.unit,
                                        identity.test_condition,
                                        capability.lsl,
                                        capability.usl,
                                    )
                                )
                    parameter_results.append(
                        DatasetParameterAnalysis(
                            identity=identity,
                            status_counts=tuple(
                                DatasetMeasurementStatusCount(
                                    status=status,
                                    count=stats["status_counts"][status],
                                )
                                for status in _MEASUREMENT_STATUSES
                            ),
                            descriptive=descriptive,
                            box_plot=box_plot,
                            histogram=histogram,
                            capability=capability,
                        )
                    )
                items.append(
                    DatasetParameterAnalysisItem(
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        test_stage=str(context["test_stage"]),
                        group_key=f"DATASET:{ref.dataset_id}:VERSION:{ref.version_no}",
                        filter_summary=DatasetParameterAnalysisFilterSummary(
                            lot_ids=lot_ids,
                            wafer_ids=wafer_ids,
                            bin_codes=bin_codes,
                            overall_results=overall_results,
                            source_ids=source_ids,
                            matched_unit_count=matched_units,
                            candidate_measurement_count=candidate_measurements,
                        ),
                        parameters=tuple(parameter_results),
                    )
                )
            if (
                len(refs) > 1
                and DatasetParameterAnalysisType.CAPABILITY.value in analysis_types
                and rule_code is not None
                and (
                    unresolved_formal_parameters
                    or any(
                        len(signatures) != 1
                        for signatures in formal_compatibility_signatures.values()
                    )
                )
            ):
                incompatible_specs = sorted(
                    unresolved_formal_parameters
                    | {
                        name
                        for name, signatures in formal_compatibility_signatures.items()
                        if len(signatures) != 1
                    }
                )
                raise DomainError(
                    "ANALYSIS_SPEC_INCOMPATIBLE",
                    "selected datasets do not have compatible unique formal specifications",
                    409,
                    details=[{"parameters": incompatible_specs}],
                )
        included_unit_count = sum(
            item.filter_summary.matched_unit_count for item in items
        )
        input_unit_count = max(
            included_unit_count,
            sum(int(context.get("unit_count") or 0) for context in contexts),
        )
        missing_measurement_count = sum(
            status.count
            for item in items
            for parameter in item.parameters
            for status in parameter.status_counts
            if status.status == "MISSING"
        )
        spec_versions = tuple(
            f"SPEC_SET:{spec_set_id}"
            for spec_set_id in sorted(
                {
                    spec_set_id
                    for item in items
                    for parameter in item.parameters
                    for spec_set_id in parameter.identity.spec_set_ids
                }
            )
        )
        return DatasetParameterAnalysisResult(
            contract_version=_PARAMETER_ANALYSIS_CONTRACT_VERSION,
            group_by=request.group_by.value,
            compatibility="SINGLE_DATASET" if len(refs) == 1 else "COMPATIBLE",
            dataset_context=DatasetParameterAnalysisDatasetContext(
                resolved_datasets=tuple(
                    DatasetParameterAnalysisResolvedDataset(
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                    )
                    for ref in refs
                ),
                test_stage=stage,
                current_published_verified=True,
            ),
            filter_summary=DatasetParameterAnalysisContextFilterSummary(
                normalized_filters=DatasetParameterAnalysisNormalizedFilters(
                    lot_ids=lot_ids,
                    wafer_ids=wafer_ids,
                    bin_codes=bin_codes,
                    overall_results=overall_results,
                    source_ids=source_ids,
                ),
                filter_hash=_parameter_analysis_filter_hash(
                    lot_ids=lot_ids,
                    wafer_ids=wafer_ids,
                    bin_codes=bin_codes,
                    overall_results=overall_results,
                    source_ids=source_ids,
                ),
            ),
            rule_context=DatasetParameterAnalysisRuleContext(
                spec_versions=spec_versions,
                bin_mapping_versions=(),
                evaluation_rule_versions=tuple(
                    method
                    for method in (
                        _BOX_PLOT_METHOD
                        if DatasetParameterAnalysisType.BOX_PLOT.value in analysis_types
                        else None,
                        _HISTOGRAM_METHOD
                        if DatasetParameterAnalysisType.HISTOGRAM.value
                        in analysis_types
                        else None,
                        rule_code,
                    )
                    if method is not None
                ),
                capability_rule_code=rule_code,
                capability_rule_approval_status=(
                    "NOT_REQUESTED" if rule_code is None else "APPROVED"
                ),
            ),
            capabilities=tuple(
                DatasetParameterAnalysisCapability(
                    code=analysis.value,
                    status=(
                        "GATED"
                        if analysis == DatasetParameterAnalysisType.CAPABILITY
                        and rule_code is None
                        else "AVAILABLE"
                    ),
                    reason_code=(
                        "CAPABILITY_RULE_REQUIRED"
                        if analysis == DatasetParameterAnalysisType.CAPABILITY
                        and rule_code is None
                        else None
                    ),
                )
                for analysis in request.analyses
            ),
            counts=DatasetParameterAnalysisCounts(
                input_units=input_unit_count,
                included_units=included_unit_count,
                excluded_units=input_unit_count - included_unit_count,
                missing_measurements=missing_measurement_count,
            ),
            sampling_summary=DatasetParameterAnalysisSamplingSummary(
                sampled=False,
                method=None,
                original_points=0,
                returned_points=0,
                preserved_out_of_spec_points=0,
            ),
            warnings=(
                ("CAPABILITY_RULE_REQUIRED",)
                if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types
                and rule_code is None
                else ()
            ),
            computed_at=datetime.now(timezone.utc).isoformat(),
            items=tuple(items),
        )

    def get_detail_page(
        self,
        dataset_id: int,
        version_no: int,
        *,
        page: int,
        page_size: int,
        lot_ids: tuple[str, ...] = (),
        wafer_ids: tuple[str, ...] = (),
        bin_codes: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> DatasetDetailPage:
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page < 1
            or page_size < 1
            or page_size > 200
            or (page - 1) * page_size > _MAX_SQL_SERVER_OFFSET
        ):
            raise DomainError(
                "ANALYSIS_PAGE_INVALID", "明细页码或每页行数超出允许范围", 422
            )
        lot_ids = _normalized_filter_values(lot_ids, field="lot_ids")
        wafer_ids = _normalized_filter_values(wafer_ids, field="wafer_ids")
        bin_codes = _normalized_filter_values(bin_codes, field="bin_codes")
        parameters = _normalized_filter_values(parameters, field="parameters")
        filter_sql, filter_parameters, expanding = _analysis_filter_sql(
            lot_ids=lot_ids, wafer_ids=wafer_ids, bin_codes=bin_codes
        )
        params: dict[str, object] = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "offset": (page - 1) * page_size,
            "page_size": page_size,
            **filter_parameters,
        }
        version_join = (
            " FROM dataset.dataset_version dv "
            "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        )
        unit_from = version_join + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
        version_where = "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
        with self._engine.connect() as connection:
            context = self._version_context(
                connection, dataset_id, version_no, lock=False
            )
            if str(context["status"]) != "PUBLISHED" or not bool(context["is_current"]):
                raise DomainError(
                    "ANALYSIS_VERSION_NOT_CURRENT",
                    "只能查看当前已发布正式版本的明细",
                    409,
                )
            lot_options = tuple(
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT tr.lot_id"
                        + version_join
                        + version_where
                        + " AND tr.lot_id IS NOT NULL"
                        + " ORDER BY tr.lot_id"
                    ),
                    params,
                ).all()
            )
            wafer_options = tuple(
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id"
                        + unit_from
                        + version_where
                        + " AND COALESCE(ur.wafer_id,tr.wafer_id) IS NOT NULL "
                        "ORDER BY wafer_id"
                    ),
                    params,
                ).all()
            )
            bin_options = tuple(
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') AS bin_code"
                        + unit_from
                        + version_where
                        + " ORDER BY bin_code"
                    ),
                    params,
                ).all()
            )
            parameter_options = tuple(
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT tid.raw_item_name"
                        + version_join
                        + "JOIN mdm.test_item_definition tid ON tid.program_version_id=tr.program_version_id "
                        + version_where
                        + " AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL "
                        "ORDER BY tid.raw_item_name"
                    ),
                    params,
                ).all()
            )
            unavailable_parameters = tuple(
                parameter
                for parameter in parameters
                if parameter not in set(parameter_options)
            )
            if unavailable_parameters:
                raise DomainError(
                    "ANALYSIS_PARAMETER_NOT_FOUND",
                    "one or more selected parameters are unavailable in this version",
                    422,
                    details=[{"parameters": list(unavailable_parameters)}],
                )
            total = int(
                connection.execute(
                    _statement(
                        "SELECT COUNT_BIG(*)" + unit_from + version_where + filter_sql,
                        expanding,
                    ),
                    params,
                ).scalar_one()
            )
            unit_rows = (
                connection.execute(
                    _statement(
                        "SELECT ur.unit_id,ur.logical_unit_key,tr.lot_id,"
                        "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                        "ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                        "ur.overall_result,ur.source_row_no"
                        + unit_from
                        + version_where
                        + filter_sql
                        + " ORDER BY tr.run_id,COALESCE(ur.unit_sequence,ur.unit_id),ur.unit_id "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY",
                        expanding,
                    ),
                    params,
                )
                .mappings()
                .all()
            )
            measurements_by_unit: dict[int, list[DatasetDetailMeasurement]] = {}
            if unit_rows and parameters:
                unit_ids = tuple(int(row["unit_id"]) for row in unit_rows)
                measurement_rows = (
                    connection.execute(
                        _statement(
                            "SELECT m.unit_id,tid.raw_item_name,m.value_numeric,m.value_text,"
                            "m.measurement_status,tid.unit_code,tid.program_lsl,tid.program_usl "
                            "FROM test.measurement m JOIN mdm.test_item_definition tid "
                            "ON tid.test_item_id=m.test_item_id "
                            "WHERE m.unit_id IN :unit_ids AND tid.raw_item_name IN :detail_parameters "
                            "ORDER BY m.unit_id,tid.sequence_no",
                            ("unit_ids", "detail_parameters"),
                        ),
                        {
                            "unit_ids": unit_ids,
                            "detail_parameters": parameters,
                        },
                    )
                    .mappings()
                    .all()
                )
                for row in measurement_rows:
                    unit_id = int(row["unit_id"])
                    measurements_by_unit.setdefault(unit_id, []).append(
                        DatasetDetailMeasurement(
                            parameter=str(row["raw_item_name"]),
                            value_numeric=_optional_finite_float(
                                row["value_numeric"],
                                field=f"{row['raw_item_name']} measurement",
                            ),
                            value_text=str(row["value_text"])
                            if row["value_text"] is not None
                            else None,
                            status=str(row["measurement_status"]),
                            unit=str(row["unit_code"])
                            if row["unit_code"] is not None
                            else None,
                            lsl=_optional_finite_float(
                                row["program_lsl"],
                                field=f"{row['raw_item_name']} LSL",
                            ),
                            usl=_optional_finite_float(
                                row["program_usl"],
                                field=f"{row['raw_item_name']} USL",
                            ),
                        )
                    )
        return DatasetDetailPage(
            dataset_id=dataset_id,
            version_no=version_no,
            test_stage=str(context["test_stage"]),
            page=page,
            page_size=page_size,
            total=total,
            lot_options=lot_options,
            wafer_options=wafer_options,
            bin_options=bin_options,
            parameter_options=parameter_options,
            items=tuple(
                DatasetDetailRow(
                    unit_id=int(row["unit_id"]),
                    logical_unit_key=str(row["logical_unit_key"]),
                    lot_id=(str(row["lot_id"]) if row["lot_id"] is not None else None),
                    wafer_id=str(row["wafer_id"])
                    if row["wafer_id"] is not None
                    else None,
                    x=int(row["x_coord"]) if row["x_coord"] is not None else None,
                    y=int(row["y_coord"]) if row["y_coord"] is not None else None,
                    soft_bin=str(row["soft_bin"])
                    if row["soft_bin"] is not None
                    else None,
                    hard_bin=str(row["hard_bin"])
                    if row["hard_bin"] is not None
                    else None,
                    overall_result=str(row["overall_result"]),
                    source_row_no=int(row["source_row_no"])
                    if row["source_row_no"] is not None
                    else None,
                    measurements=tuple(
                        measurements_by_unit.get(int(row["unit_id"]), ())
                    ),
                )
                for row in unit_rows
            ),
        )

    def _get_ft_chart_data(
        self,
        connection: Connection,
        context: Mapping[str, Any],
        parameters: dict[str, Any],
        version_join: str,
    ) -> DatasetChartData:
        all_option_rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT tr.run_id,tr.lot_id,tr.tester_id,tr.program_version_id,tr.metadata_json"
                    + version_join
                    + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                    "ORDER BY tr.lot_id,tr.tester_id,tr.run_id"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        lot_options = tuple(
            dict.fromkeys(str(row["lot_id"]) for row in all_option_rows)
        )
        option_rows = tuple(
            row
            for row in all_option_rows
            if parameters["lot_id"] is None
            or str(row["lot_id"]) == parameters["lot_id"]
        )
        source_records = []
        for row in option_rows:
            metadata: dict[str, Any] = {}
            try:
                decoded = json.loads(row["metadata_json"] or "{}")
                if isinstance(decoded, dict):
                    metadata = decoded
            except (TypeError, ValueError):
                metadata = {}
            source_identity = str(metadata.get("source_id") or "").strip()
            if not source_identity:
                source_identity = str(row["tester_id"] or f"RUN-{row['run_id']}")
            source_records.append(
                {
                    "run_id": int(row["run_id"]),
                    "lot_id": str(row["lot_id"]),
                    "source_id": source_identity,
                }
            )
        source_records.sort(
            key=lambda item: (item["lot_id"], item["source_id"], item["run_id"])
        )
        source_options = tuple(
            dict.fromkeys(str(row["source_id"]) for row in source_records)
        )
        selected_source = parameters["source_id"]
        if selected_source and selected_source not in source_options:
            raise DomainError(
                "FT_SOURCE_NOT_FOUND", "selected FT source was not found", 404
            )
        selected_run_ids = tuple(
            int(row["run_id"])
            for row in source_records
            if not selected_source or row["source_id"] == selected_source
        )
        source_filter = ""
        source_parameters: dict[str, Any] = {}
        if selected_source:
            source_filter = "AND tr.run_id IN :source_run_ids "
            source_parameters["source_run_ids"] = selected_run_ids

        parameter_statement = text(
            "SELECT DISTINCT tid.sequence_no,tid.raw_item_name,"
            "tid.unit_code,tid.program_lsl AS lsl,tid.program_usl AS usl,tid.condition_json "
            + version_join
            + "JOIN mdm.test_item_definition tid ON tid.program_version_id=tr.program_version_id "
            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
            "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
            + source_filter
            + "AND tid.is_analysis_parameter=1 ORDER BY tid.sequence_no"
        )
        if selected_source:
            parameter_statement = parameter_statement.bindparams(
                bindparam("source_run_ids", expanding=True)
            )
        parameter_rows = (
            connection.execute(
                parameter_statement,
                {
                    **parameters,
                    **source_parameters,
                },
            )
            .mappings()
            .all()
        )
        names = {str(row["raw_item_name"]) for row in parameter_rows}
        selected_parameter = parameters["parameter"]
        if selected_parameter is not None and selected_parameter not in names:
            raise DomainError(
                "FT_PARAMETER_NOT_FOUND", "selected FT parameter was not found", 404
            )
        point_rows: list[Mapping[str, Any]] = []
        total_count = 0
        if selected_parameter:
            point_params = {
                **parameters,
                **source_parameters,
            }
            count_statement = text(
                "SELECT COUNT_BIG(*) "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND tid.raw_item_name=:parameter"
            )
            if selected_source:
                count_statement = count_statement.bindparams(
                    bindparam("source_run_ids", expanding=True)
                )
            total_count = int(
                connection.execute(
                    count_statement,
                    point_params,
                ).scalar_one()
            )
            stride = max(1, (total_count + 9_999) // 10_000)
            points_statement = text(
                ";WITH points AS (SELECT tr.run_id,ur.unit_sequence,tr.lot_id,"
                "m.value_numeric,m.measurement_status,"
                "tid.program_lsl AS lsl,tid.program_usl AS usl,"
                "ROW_NUMBER() OVER(ORDER BY tr.run_id,ur.unit_sequence,ur.unit_id) AS rn "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND tid.raw_item_name=:parameter) "
                "SELECT run_id,unit_sequence,lot_id,value_numeric,measurement_status "
                "FROM points WHERE (rn-1)%:stride=0 OR "
                "(value_numeric IS NOT NULL AND ((lsl IS NOT NULL AND value_numeric<lsl) "
                "OR (usl IS NOT NULL AND value_numeric>usl))) "
                "ORDER BY run_id,unit_sequence"
            )
            if selected_source:
                points_statement = points_statement.bindparams(
                    bindparam("source_run_ids", expanding=True)
                )
            point_rows = (
                connection.execute(
                    points_statement,
                    {**point_params, "stride": stride},
                )
                .mappings()
                .all()
            )
        parameter_groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in parameter_rows:
            parameter_groups.setdefault(str(row["raw_item_name"]), []).append(row)
        parameter_options = []
        for name, rows in sorted(
            parameter_groups.items(),
            key=lambda item: min(int(row["sequence_no"]) for row in item[1]),
        ):
            units = {str(row["unit_code"]) for row in rows if row["unit_code"]}
            if len(units) > 1:
                raise DomainError(
                    "FT_PARAMETER_UNIT_CONFLICT",
                    f"FT parameter {name} has conflicting units across source runs",
                    409,
                )
            lsl_values = {row["lsl"] for row in rows}
            usl_values = {row["usl"] for row in rows}
            condition_texts = set()
            for row in rows:
                try:
                    condition = json.loads(row["condition_json"] or "{}")
                except (TypeError, ValueError):
                    condition = {}
                condition_texts.add(condition.get("text"))
            parameter_options.append(
                FtParameterOption(
                    name=name,
                    unit=next(iter(units)) if units else None,
                    lsl=float(next(iter(lsl_values)))
                    if len(lsl_values) == 1 and None not in lsl_values
                    else None,
                    usl=float(next(iter(usl_values)))
                    if len(usl_values) == 1 and None not in usl_values
                    else None,
                    test_condition=next(iter(condition_texts))
                    if len(condition_texts) == 1
                    else "多源文件测试条件不同，请选择单一源文件",
                )
            )
        source_by_run = {
            int(row["run_id"]): str(row["source_id"]) for row in source_records
        }
        return DatasetChartData(
            dataset_id=int(context["dataset_id"]),
            version_no=int(context["version_no"]),
            test_stage="FT",
            product_name=context["product_name"],
            selected_lot_id=parameters["lot_id"],
            selected_wafer_id=None,
            selected_source_id=parameters["source_id"],
            selected_parameter=selected_parameter,
            lot_options=lot_options,
            wafer_options=(),
            source_options=source_options,
            parameter_options=tuple(parameter_options),
            wafer_yield=(),
            bin_counts=(),
            wafer_map=(),
            ft_parameter_points=tuple(
                FtParameterPoint(
                    sequence=int(row["unit_sequence"]),
                    lot_id=str(row["lot_id"]),
                    source_id=source_by_run[int(row["run_id"])],
                    value=float(row["value_numeric"])
                    if row["value_numeric"] is not None
                    else None,
                    status=str(row["measurement_status"]),
                )
                for row in point_rows
            ),
            ft_total_point_count=total_count,
            ft_sampled=len(point_rows) < total_count,
        )

    def create_version(
        self, dataset_id: int, request: CreateDatasetVersionRequest
    ) -> DatasetVersionRecord:
        with self._engine.begin() as connection:
            dataset_exists = connection.execute(
                text(
                    "SELECT dataset_id FROM dataset.dataset WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset_id"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            if dataset_exists is None:
                raise DomainError("DATASET_NOT_FOUND", "dataset was not found", 404)
            batch_exists = connection.execute(
                text(
                    "SELECT import_batch_id FROM ingestion.import_batch "
                    "WHERE import_batch_id=:input_batch_id"
                ),
                {"input_batch_id": request.input_batch_id},
            ).scalar_one_or_none()
            if batch_exists is None:
                raise DomainError(
                    "INPUT_BATCH_NOT_FOUND", "input batch was not found", 404
                )
            found_runs = self._find_runs(connection, request.processing_run_ids)
            missing = sorted(set(request.processing_run_ids) - found_runs)
            if missing:
                raise DomainError(
                    "PROCESSING_RUN_NOT_FOUND",
                    "one or more processing runs were not found",
                    404,
                    details=[{"processing_run_ids": missing}],
                )

            version_no = int(
                connection.execute(
                    text(
                        "SELECT ISNULL(MAX(version_no),0)+1 FROM dataset.dataset_version "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE dataset_id=:dataset_id"
                    ),
                    {"dataset_id": dataset_id},
                ).scalar_one()
            )
            supersedes = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version "
                    "WHERE dataset_id=:dataset_id AND status='PUBLISHED' AND is_current=1"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            row = (
                connection.execute(
                    text(
                        "INSERT dataset.dataset_version("
                        "dataset_id,version_no,input_batch_id,canonical_model_version,status,"
                        "is_current,supersedes_dataset_version_id,metadata_json) OUTPUT "
                        "INSERTED.dataset_version_id,INSERTED.dataset_id,INSERTED.version_no,"
                        "INSERTED.input_batch_id,INSERTED.canonical_model_version,"
                        "INSERTED.status,INSERTED.is_current VALUES("
                        ":dataset_id,:version_no,:input_batch_id,:canonical_model_version,"
                        "'VALIDATING',0,:supersedes,:metadata_json)"
                    ),
                    {
                        "dataset_id": dataset_id,
                        "version_no": version_no,
                        "input_batch_id": request.input_batch_id,
                        "canonical_model_version": request.canonical_model_version,
                        "supersedes": supersedes,
                        "metadata_json": json.dumps(
                            {"run_count": len(request.processing_run_ids)},
                            separators=(",", ":"),
                        ),
                    },
                )
                .mappings()
                .one()
            )
            version_id = int(row["dataset_version_id"])
            connection.execute(
                text(
                    "INSERT dataset.dataset_version_run("
                    "dataset_version_id,processing_run_id,run_role,ordinal_no) VALUES("
                    ":dataset_version_id,:processing_run_id,'PRIMARY',:ordinal_no)"
                ),
                [
                    {
                        "dataset_version_id": version_id,
                        "processing_run_id": run_id,
                        "ordinal_no": ordinal,
                    }
                    for ordinal, run_id in enumerate(
                        request.processing_run_ids, start=1
                    )
                ],
            )
        return _version(row, run_count=len(request.processing_run_ids))

    @staticmethod
    def _find_runs(connection: Connection, run_ids: list[int]) -> set[int]:
        found: set[int] = set()
        for offset in range(0, len(run_ids), 500):
            chunk = run_ids[offset : offset + 500]
            placeholders = ",".join(f":run_{index}" for index in range(len(chunk)))
            params = {f"run_{index}": value for index, value in enumerate(chunk)}
            rows = connection.execute(
                text(
                    "SELECT processing_run_id FROM ingestion.processing_run "
                    f"WHERE processing_run_id IN ({placeholders})"
                ),
                params,
            ).all()
            found.update(int(row[0]) for row in rows)
        return found

    def _version_context(
        self,
        connection: Connection,
        dataset_id: int,
        version_no: int,
        *,
        lock: bool,
        principal: Principal | None = None,
    ) -> Mapping[str, Any]:
        lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        access_clause = ""
        parameters: dict[str, object] = {
            "dataset_id": dataset_id,
            "version_no": version_no,
        }
        if principal is not None:
            access_clause = " AND " + current_dataset_read_scope_sql(
                dataset_alias="d",
                version_alias="dv",
                batch_alias="access_b",
            )
            parameters.update(visibility_parameters(principal))
        row = (
            connection.execute(
                text(
                    "SELECT dv.dataset_version_id,dv.dataset_id,dv.version_no,"
                    "dv.input_batch_id,dv.canonical_model_version,dv.status,dv.is_current,"
                    "dv.unit_count,"
                    "d.supplier_id,d.product_id,d.test_stage,"
                    "COALESCE(product_enrichment.value_text,p.product_name) AS product_name,"
                    "dv.spec_set_id "
                    f"FROM dataset.dataset_version dv{lock_hint} "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "JOIN ingestion.import_batch access_b ON "
                    "access_b.import_batch_id=dv.input_batch_id "
                    "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                    "OUTER APPLY(SELECT TOP (1) fe.value_text FROM "
                    "ingestion.field_enrichment fe WHERE "
                    "fe.import_batch_id=dv.input_batch_id AND fe.source_file_id IS NULL "
                    "AND fe.test_stage=d.test_stage AND fe.field_code='PRODUCT_CODE' "
                    "AND fe.action='FILL' AND fe.is_current=1 "
                    "ORDER BY fe.enrichment_id DESC) product_enrichment "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    + access_clause
                ),
                parameters,
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
            )
        return row

    def _evaluate(
        self,
        connection: Connection,
        dataset_id: int,
        version_no: int,
        *,
        lock: bool,
        principal: Principal | None = None,
    ) -> DqGateResult:
        context = self._version_context(
            connection,
            dataset_id,
            version_no,
            lock=lock,
            principal=principal,
        )
        version_id = int(context["dataset_version_id"])
        run_rows = (
            connection.execute(
                text(
                    "SELECT pr.processing_run_id,pr.source_file_id,pr.status,"
                    "ISNULL(pr.unit_count_output,0) AS unit_count,"
                    "ISNULL(pr.measurement_count_output,0) AS measurement_count,"
                    "CASE WHEN pj.import_batch_id=:input_batch_id OR EXISTS("
                    " SELECT 1 FROM ingestion.source_file_receipt sfr "
                    " LEFT JOIN ingestion.import_batch_file ibf ON ibf.receipt_id=sfr.receipt_id "
                    " WHERE sfr.source_file_id=pr.source_file_id AND "
                    " (sfr.import_batch_id=:input_batch_id OR ibf.import_batch_id=:input_batch_id)"
                    ") THEN 1 ELSE 0 END AS lineage_matches "
                    "FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
                    "JOIN ingestion.processing_job pj ON pj.job_id=pr.job_id "
                    "WHERE dvr.dataset_version_id=:version_id"
                ),
                {"version_id": version_id, "input_batch_id": context["input_batch_id"]},
            )
            .mappings()
            .all()
        )
        reasons: list[GateReason] = []
        if not run_rows:
            reasons.append(
                GateReason(
                    "NO_PROCESSING_RUN", 1, "dataset version has no processing run"
                )
            )
        not_ready = sum(row["status"] not in {"READY", "PUBLISHED"} for row in run_rows)
        if not_ready:
            reasons.append(
                GateReason("RUN_NOT_READY", not_ready, "processing runs are not ready")
            )
        bad_lineage = sum(not bool(row["lineage_matches"]) for row in run_rows)
        if bad_lineage:
            reasons.append(
                GateReason(
                    "INPUT_LINEAGE_MISMATCH",
                    bad_lineage,
                    "processing runs are not attributable to the declared input batch",
                )
            )
        duplicate_sources = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT pr.source_file_id FROM "
                    "dataset.dataset_version_run dvr JOIN ingestion.processing_run pr "
                    "ON pr.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id GROUP BY pr.source_file_id "
                    "HAVING COUNT(*)>1) duplicates"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )
        if duplicate_sources:
            reasons.append(
                GateReason(
                    "DUPLICATE_SOURCE_RUN",
                    duplicate_sources,
                    "multiple processing runs reference the same immutable source",
                )
            )
        identity_mismatches = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id AND ("
                    "NOT EXISTS(SELECT 1 FROM test.test_run tr WHERE tr.processing_run_id=pr.processing_run_id) OR "
                    "EXISTS(SELECT 1 FROM test.test_run tr WHERE tr.processing_run_id=pr.processing_run_id AND ("
                    "tr.test_stage<>:test_stage OR (:supplier_id IS NOT NULL AND tr.supplier_id<>:supplier_id) OR "
                    "(:product_id IS NOT NULL AND (tr.product_id IS NULL OR tr.product_id<>:product_id)))))"
                ),
                {
                    "version_id": version_id,
                    "test_stage": context["test_stage"],
                    "supplier_id": context["supplier_id"],
                    "product_id": context["product_id"],
                },
            ).scalar_one()
        )
        if identity_mismatches:
            reasons.append(
                GateReason(
                    "DATASET_IDENTITY_MISMATCH",
                    identity_mismatches,
                    "canonical run identity does not match the dataset scope",
                )
            )
        blocking_issues = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.data_quality_issue dqi ON dqi.processing_run_id=dvr.processing_run_id "
                    "JOIN ingestion.data_quality_rule dqr ON dqr.rule_id=dqi.rule_id "
                    "LEFT JOIN ingestion.dq_rule_version dqrv ON dqrv.dq_rule_version_id=dqi.dq_rule_version_id "
                    "WHERE dvr.dataset_version_id=:version_id AND ("
                    "(dqi.resolution_status='OPEN' AND (dqi.severity='BLOCKER' OR "
                    "ISNULL(dqrv.is_blocking,dqr.is_blocking)=1)) OR "
                    "(dqi.resolution_status='WAIVED' AND dqi.severity='BLOCKER'))"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )
        if blocking_issues:
            reasons.append(
                GateReason(
                    "BLOCKING_DQ_ISSUE",
                    blocking_issues,
                    "open blocking quality issues prevent publication",
                )
            )
        return DqGateResult(
            dataset_id=dataset_id,
            version_no=version_no,
            status="PASS" if not reasons else "BLOCKED",
            run_count=len(run_rows),
            unit_count=sum(int(row["unit_count"]) for row in run_rows),
            measurement_count=sum(int(row["measurement_count"]) for row in run_rows),
            reasons=tuple(reasons),
        )

    def evaluate_gate(
        self,
        dataset_id: int,
        version_no: int,
        principal: Principal,
    ) -> DqGateResult:
        with self._engine.connect() as connection:
            return self._evaluate(
                connection,
                dataset_id,
                version_no,
                lock=False,
                principal=principal,
            )

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord:
        with self._engine.begin() as connection:
            context = self._version_context(
                connection, dataset_id, version_no, lock=True
            )
            version_id = int(context["dataset_version_id"])
            run_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM dataset.dataset_version_run "
                        "WHERE dataset_version_id=:version_id"
                    ),
                    {"version_id": version_id},
                ).scalar_one()
            )
            if context["status"] == "PUBLISHED" and bool(context["is_current"]):
                return _version(context, run_count=run_count)
            if context["status"] not in {"DRAFT", "VALIDATING"}:
                raise DomainError(
                    "DATASET_VERSION_NOT_PUBLISHABLE",
                    "dataset version is not in a publishable state",
                    409,
                )
            user_status = connection.execute(
                text("SELECT status FROM iam.app_user WHERE user_id=:user_id"),
                {"user_id": request.published_by},
            ).scalar_one_or_none()
            if user_status != "ACTIVE":
                raise DomainError(
                    "PUBLISHER_NOT_ACTIVE",
                    "publisher must be an active application user",
                    409,
                )
            gate = self._evaluate(connection, dataset_id, version_no, lock=False)
            if gate.status != "PASS":
                raise DomainError(
                    "DQ_GATE_BLOCKED",
                    "dataset version cannot be published because the DQ gate is blocked",
                    409,
                    details=[
                        {
                            "code": reason.code,
                            "count": reason.count,
                            "message": reason.message,
                        }
                        for reason in gate.reasons
                    ],
                )
            previous_id = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset_id AND status='PUBLISHED' AND is_current=1"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            if previous_id is not None and int(previous_id) != version_id:
                previous_run_rows = (
                    connection.execute(
                        text(
                            "SELECT pr.processing_run_id,pr.status,pr.is_current,"
                            "CASE WHEN EXISTS(SELECT 1 "
                            "FROM dataset.dataset_version_run other_dvr "
                            "JOIN dataset.dataset_version other_dv "
                            "ON other_dv.dataset_version_id=other_dvr.dataset_version_id "
                            "WHERE other_dvr.processing_run_id=pr.processing_run_id "
                            "AND other_dv.dataset_version_id<>:previous_id "
                            "AND other_dv.status='PUBLISHED' AND other_dv.is_current=1) "
                            "THEN 1 ELSE 0 END AS has_other_current "
                            "FROM dataset.dataset_version_run dvr WITH (UPDLOCK,HOLDLOCK) "
                            "JOIN ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK) "
                            "ON pr.processing_run_id=dvr.processing_run_id "
                            "WHERE dvr.dataset_version_id=:previous_id "
                            "ORDER BY dvr.ordinal_no,pr.processing_run_id"
                        ),
                        {"previous_id": previous_id},
                    )
                    .mappings()
                    .all()
                )
                if not previous_run_rows or any(
                    row["status"] != "PUBLISHED" or not bool(row["is_current"])
                    for row in previous_run_rows
                ):
                    raise DomainError(
                        "DATASET_PREVIOUS_RUN_CONFLICT",
                        "previous Current Dataset Version has inconsistent Processing Runs",
                        409,
                    )
                previous_runs_to_supersede = sum(
                    not bool(row["has_other_current"]) for row in previous_run_rows
                )
                previous_updated = connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='SUPERSEDED',is_current=0 "
                        "WHERE dataset_version_id=:previous_id "
                        "AND status='PUBLISHED' AND is_current=1"
                    ),
                    {"previous_id": previous_id},
                )
                if previous_updated.rowcount != 1:
                    raise DomainError(
                        "DATASET_PREVIOUS_VERSION_CONFLICT",
                        "previous Current Dataset Version changed during publication",
                        409,
                    )
                previous_runs_updated = connection.execute(
                    text(
                        "UPDATE pr SET pr.status='SUPERSEDED',pr.is_current=0 "
                        "FROM ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN dataset.dataset_version_run dvr "
                        "ON dvr.processing_run_id=pr.processing_run_id "
                        "WHERE dvr.dataset_version_id=:previous_id "
                        "AND pr.status='PUBLISHED' AND pr.is_current=1 "
                        "AND NOT EXISTS(SELECT 1 "
                        "FROM dataset.dataset_version_run other_dvr "
                        "JOIN dataset.dataset_version other_dv "
                        "ON other_dv.dataset_version_id=other_dvr.dataset_version_id "
                        "WHERE other_dvr.processing_run_id=pr.processing_run_id "
                        "AND other_dv.status='PUBLISHED' AND other_dv.is_current=1)"
                    ),
                    {"previous_id": previous_id},
                )
                if previous_runs_updated.rowcount != previous_runs_to_supersede:
                    raise DomainError(
                        "DATASET_PREVIOUS_RUN_CONFLICT",
                        "previous Current Processing Runs changed during publication",
                        409,
                    )
            connection.execute(
                text(
                    "UPDATE pr SET pr.status='PUBLISHED',pr.is_current=1 "
                    "FROM ingestion.processing_run pr JOIN dataset.dataset_version_run dvr "
                    "ON dvr.processing_run_id=pr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id"
                ),
                {"version_id": version_id},
            )
            row = (
                connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='PUBLISHED',is_current=1,"
                        "row_count=:unit_count,unit_count=:unit_count,"
                        "measurement_count=:measurement_count,published_by=:published_by,"
                        "published_at_utc=SYSUTCDATETIME(),supersedes_dataset_version_id=:previous_id "
                        "OUTPUT INSERTED.dataset_version_id,INSERTED.dataset_id,INSERTED.version_no,"
                        "INSERTED.input_batch_id,INSERTED.canonical_model_version,"
                        "INSERTED.status,INSERTED.is_current "
                        "WHERE dataset_version_id=:version_id"
                    ),
                    {
                        "unit_count": gate.unit_count,
                        "measurement_count": gate.measurement_count,
                        "published_by": request.published_by,
                        "previous_id": previous_id,
                        "version_id": version_id,
                    },
                )
                .mappings()
                .one()
            )
        return _version(row, run_count=run_count)

    def get_summary(
        self,
        dataset_id: int,
        version_no: int,
        principal: Principal,
    ) -> DatasetResultSummary:
        access_clause = " AND " + current_dataset_read_scope_sql(
            dataset_alias="d",
            version_alias="dv",
            batch_alias="access_b",
        )
        parameters: dict[str, object] = visibility_parameters(principal) | {
            "dataset_id": dataset_id,
            "version_no": version_no,
        }
        with self._engine.connect() as connection:
            base = (
                connection.execute(
                    text(
                        "SELECT d.dataset_code,d.dataset_name,dv.status,dv.is_current,"
                        "COUNT(DISTINCT dvr.processing_run_id) AS run_count,"
                        "COUNT(DISTINCT tr.lot_id) AS lot_count,"
                        "COUNT(DISTINCT CASE WHEN tr.wafer_id IS NOT NULL "
                        "THEN tr.lot_id+'|'+tr.wafer_id END) AS wafer_count,"
                        "COUNT(ur.unit_id) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' THEN 1 ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' THEN 1 ELSE 0 END) AS fail_count "
                        "FROM dataset.dataset d JOIN dataset.dataset_version dv "
                        "ON dv.dataset_id=d.dataset_id "
                        "JOIN ingestion.import_batch access_b ON "
                        "access_b.import_batch_id=dv.input_batch_id "
                        "LEFT JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "LEFT JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "LEFT JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE d.dataset_id=:dataset_id AND dv.version_no=:version_no"
                        + access_clause
                        + " "
                        "GROUP BY d.dataset_code,d.dataset_name,dv.status,dv.is_current"
                    ),
                    parameters,
                )
                .mappings()
                .one_or_none()
            )
            if base is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            measurement_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    ),
                    {"dataset_id": dataset_id, "version_no": version_no},
                ).scalar_one()
            )
            bin_rows = (
                connection.execute(
                    text(
                        "SELECT ur.soft_bin,COUNT_BIG(*) AS unit_count FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                        "AND ur.soft_bin IS NOT NULL "
                        "GROUP BY ur.soft_bin ORDER BY ur.soft_bin"
                    ),
                    {"dataset_id": dataset_id, "version_no": version_no},
                )
                .mappings()
                .all()
            )
        unit_count = int(base["unit_count"] or 0)
        classified_pass_count = int(base["pass_count"] or 0)
        classified_fail_count = int(base["fail_count"] or 0)
        classified_count = classified_pass_count + classified_fail_count
        pass_count = classified_pass_count if classified_count else None
        fail_count = classified_fail_count if classified_count else None
        return DatasetResultSummary(
            dataset_id=dataset_id,
            dataset_code=str(base["dataset_code"]),
            dataset_name=str(base["dataset_name"]),
            version_no=version_no,
            version_status=str(base["status"]),
            is_current=bool(base["is_current"]),
            run_count=int(base["run_count"] or 0),
            lot_count=int(base["lot_count"] or 0),
            wafer_count=int(base["wafer_count"] or 0),
            unit_count=unit_count,
            pass_count=pass_count,
            fail_count=fail_count,
            yield_rate=(
                classified_pass_count / classified_count if classified_count else None
            ),
            measurement_count=measurement_count,
            bin_counts={
                str(row["soft_bin"]): int(row["unit_count"]) for row in bin_rows
            },
        )
