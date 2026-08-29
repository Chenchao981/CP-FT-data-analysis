from __future__ import annotations

import json
import math
from collections.abc import Mapping
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
    DatasetChartData,
    DatasetComparisonItem,
    DatasetComparisonRequest,
    DatasetComparisonResult,
    DatasetDetailMeasurement,
    DatasetDetailPage,
    DatasetDetailRow,
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

_CURRENT_DATA_READ_GRANT = """
EXISTS(
    SELECT 1
    FROM iam.user_role scope_ur
    JOIN iam.role scope_r
      ON scope_r.role_id=scope_ur.role_id AND scope_r.active=1
    JOIN iam.data_scope_grant scope_g
      ON scope_g.role_id=scope_ur.role_id AND scope_g.user_id IS NULL
    WHERE scope_ur.user_id=:user_id
      AND scope_g.scope_type='GLOBAL'
      AND scope_g.scope_key=N'TMS_CURRENT_DATA'
      AND scope_g.permission_mode='READ'
      AND (scope_g.expires_at_utc IS NULL
           OR scope_g.expires_at_utc>SYSUTCDATETIME())
)
"""

_CURRENT_PUBLISHED_DATASET_VERSION = """
EXISTS(
    SELECT 1
    FROM dataset.dataset_version access_dv
    WHERE access_dv.dataset_id=d.dataset_id
      AND access_dv.status='PUBLISHED'
      AND access_dv.is_current=1
)
"""

_CURRENT_PUBLISHED_REQUESTED_VERSION = """
EXISTS(
    SELECT 1
    FROM dataset.dataset_version access_dv
    WHERE access_dv.dataset_id=d.dataset_id
      AND access_dv.version_no=:access_version_no
      AND access_dv.status='PUBLISHED'
      AND access_dv.is_current=1
)
"""

_MAX_SQL_SERVER_OFFSET = 2_147_483_647
_DETAIL_FILTER_LIMITS = {
    "lot_ids": 50,
    "wafer_ids": 100,
    "bin_codes": 50,
    "parameters": 20,
}


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
    raw_text = decoded.get("text")
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has invalid test-condition metadata",
            409,
        )
    normalized = " ".join(raw_text.split())
    return normalized or None


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


class SqlDatasetService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_datasets(self, principal: Principal) -> tuple[DatasetRecord, ...]:
        params = {
            "user_id": principal.user_id,
            "is_admin": "SYSTEM_ADMIN" in principal.roles,
        }
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT d.dataset_id,d.dataset_code,d.dataset_name,d.dataset_type,d.test_stage,"
                        "d.supplier_id,d.product_id,d.owner_user_id FROM dataset.dataset d "
                        "WHERE :is_admin=1 OR d.owner_user_id=:user_id OR ("
                        + _CURRENT_DATA_READ_GRANT
                        + " AND "
                        + _CURRENT_PUBLISHED_DATASET_VERSION
                        + ") "
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
        scope = ":is_admin=1 OR d.owner_user_id=:user_id"
        if normalized_mode == "READ":
            current_version_scope = (
                _CURRENT_PUBLISHED_REQUESTED_VERSION
                if version_no is not None
                else _CURRENT_PUBLISHED_DATASET_VERSION
            )
            scope += (
                " OR ("
                + _CURRENT_DATA_READ_GRANT
                + " AND "
                + current_version_scope
                + ")"
            )
        parameters: dict[str, object] = {
            "dataset_id": dataset_id,
            "user_id": principal.user_id,
            "is_admin": "SYSTEM_ADMIN" in principal.roles,
        }
        if normalized_mode == "READ" and version_no is not None:
            parameters["access_version_no"] = version_no
        with self._engine.connect() as connection:
            found = connection.execute(
                text(
                    "SELECT TOP (1) d.dataset_id FROM dataset.dataset d "
                    "WHERE d.dataset_id=:dataset_id AND (" + scope + ")"
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
            if str(context["status"]) != "PUBLISHED" or not bool(
                context["is_current"]
            ):
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
        version_where = (
            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
        )
        with self._engine.connect() as connection:
            context = self._version_context(connection, dataset_id, version_no, lock=False)
            if str(context["status"]) != "PUBLISHED" or not bool(
                context["is_current"]
            ):
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
                    lot_id=(
                        str(row["lot_id"]) if row["lot_id"] is not None else None
                    ),
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
                    measurements=tuple(measurements_by_unit.get(int(row["unit_id"]), ())),
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
            access_clause = (
                " AND (:is_admin=1 OR d.owner_user_id=:user_id OR ("
                + _CURRENT_DATA_READ_GRANT
                + " AND dv.status='PUBLISHED' AND dv.is_current=1))"
            )
            parameters.update(
                {
                    "user_id": principal.user_id,
                    "is_admin": "SYSTEM_ADMIN" in principal.roles,
                }
            )
        row = (
            connection.execute(
                text(
                    "SELECT dv.dataset_version_id,dv.dataset_id,dv.version_no,"
                    "dv.input_batch_id,dv.canonical_model_version,dv.status,dv.is_current,"
                    "d.supplier_id,d.product_id,d.test_stage,"
                    "COALESCE(product_enrichment.value_text,p.product_name) AS product_name,"
                    "dv.spec_set_id "
                    f"FROM dataset.dataset_version dv{lock_hint} "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
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
                connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='SUPERSEDED',is_current=0 "
                        "WHERE dataset_version_id=:previous_id"
                    ),
                    {"previous_id": previous_id},
                )
            connection.execute(
                text(
                    "UPDATE prior SET prior.status='SUPERSEDED',prior.is_current=0 "
                    "FROM ingestion.processing_run prior JOIN ingestion.processing_run target "
                    "ON target.source_file_id=prior.source_file_id "
                    "JOIN dataset.dataset_version_run dvr ON dvr.processing_run_id=target.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id AND prior.status='PUBLISHED' "
                    "AND prior.is_current=1 AND prior.processing_run_id<>target.processing_run_id"
                ),
                {"version_id": version_id},
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
        access_clause = (
            " AND (:is_admin=1 OR d.owner_user_id=:user_id OR ("
            + _CURRENT_DATA_READ_GRANT
            + " AND dv.status='PUBLISHED' AND dv.is_current=1))"
        )
        parameters: dict[str, object] = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "user_id": principal.user_id,
            "is_admin": "SYSTEM_ADMIN" in principal.roles,
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
            yield_rate=(classified_pass_count / classified_count if classified_count else None),
            measurement_count=measurement_count,
            bin_counts={
                str(row["soft_bin"]): int(row["unit_count"]) for row in bin_rows
            },
        )
