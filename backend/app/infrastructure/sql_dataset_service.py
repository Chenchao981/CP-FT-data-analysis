from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Event, Lock
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
    DatasetCapabilityDrilldownContext,
    DatasetCapabilityStatistics,
    DatasetChartData,
    DatasetComparisonItem,
    DatasetComparisonRequest,
    DatasetComparisonResult,
    DatasetDescriptiveStatistics,
    DatasetDetailMeasurement,
    DatasetDetailPage,
    DatasetDetailRow,
    DatasetEvidenceSampling,
    DatasetHistogramBin,
    DatasetHistogramStatistics,
    DatasetMeasurementAggregateContext,
    DatasetMeasurementEvidence,
    DatasetMeasurementStatusCount,
    DatasetNormalFitPoint,
    DatasetNormalFitStatistics,
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
from app.infrastructure.formal_spec_resolver import (
    FormalSpecResolution,
    resolve_released_formal_spec,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService
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
    "tester_ids": 50,
    "program_versions": 50,
    "test_conditions": 50,
}
_PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS = 2_000_000
_PARAMETER_ANALYSIS_CONTRACT_VERSION = "PARAMETER_ANALYSIS_V1"
_BOX_PLOT_METHOD = "TUKEY_BOX_V1"
_HISTOGRAM_METHOD = "EQUAL_WIDTH_HISTOGRAM_V1"
_NORMAL_FIT_METHOD = "NORMAL_FIT_MLE_V1"
_BOX_OUTLIER_EVIDENCE_LIMIT = 200
_DISTRIBUTION_OOS_EVIDENCE_LIMIT = 100
_SUPPORTED_CAPABILITY_METHODS = frozenset(
    {"CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1"}
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


@dataclass(slots=True)
class _ParameterAnalysisFlight:
    completed: Event
    result: DatasetParameterAnalysisResult | None = None
    error: BaseException | None = None


_PARAMETER_ANALYSIS_FLIGHT_LOCK = Lock()
_PARAMETER_ANALYSIS_FLIGHTS: dict[tuple[int, str], _ParameterAnalysisFlight] = {}


def _raise_parameter_analysis_flight_error(error: BaseException) -> None:
    """Raise a fresh exception so concurrent waiters never share traceback state."""

    if isinstance(error, DomainError):
        raise DomainError(
            error.code,
            error.message,
            error.status_code,
            details=[dict(item) for item in error.details],
        ) from error
    try:
        cloned = type(error)(*error.args)
    except Exception as exc:  # pragma: no cover - defensive for exotic exceptions
        raise RuntimeError("coalesced parameter analysis failed") from exc
    raise cloned from error


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
    tester_ids: tuple[str, ...] = (),
    program_versions: tuple[str, ...] = (),
    test_conditions: tuple[str, ...] = (),
) -> str:
    payload = {
        "lot_ids": sorted(lot_ids),
        "wafer_ids": sorted(wafer_ids),
        "bin_codes": sorted(bin_codes),
        "overall_results": sorted(overall_results),
        "source_ids": sorted(source_ids),
        "tester_ids": sorted(tester_ids),
        "program_versions": sorted(program_versions),
        "test_conditions": sorted(test_conditions),
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


def _measurement_evidence(
    row: Mapping[str, Any], *, parameter: str
) -> DatasetMeasurementEvidence:
    measurement_id = int(row["measurement_id"])
    unit_id = int(row["unit_id"])
    if measurement_id <= 0 or unit_id <= 0:
        raise DomainError(
            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
            f"parameter {parameter} has an invalid evidence identity",
            409,
        )
    value = _optional_finite_float(
        row["value_numeric"], field=f"{parameter} evidence value"
    )
    if value is None:
        raise DomainError(
            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
            f"parameter {parameter} has evidence without a numeric value",
            409,
        )
    raw_status = str(row.get("spec_status") or "NO_SPEC")
    if raw_status not in {"IN_SPEC", "OUT_OF_SPEC", "NO_SPEC"}:
        raise DomainError(
            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
            f"parameter {parameter} has an invalid evidence Spec status",
            409,
        )
    return DatasetMeasurementEvidence(
        measurement_id=measurement_id,
        value=value,
        drilldown_key=f"UNIT:{unit_id}",
        spec_status=raw_status,
    )


def _histogram_spec_region(
    lower: float,
    upper: float,
    *,
    lsl: float | None,
    usl: float | None,
    lower_operator: str | None = None,
    upper_operator: str | None = None,
    upper_inclusive: bool = False,
) -> str:
    if lsl is None and usl is None:
        return "NO_SPEC"
    lower_op = lower_operator or ">="
    upper_op = upper_operator or "<="
    entirely_below = lsl is not None and (
        upper < lsl or (upper == lsl and (lower_op == ">" or not upper_inclusive))
    )
    entirely_above = usl is not None and (
        lower > usl or (lower == usl and upper_op == "<")
    )
    if entirely_below or entirely_above:
        return "OUT_OF_SPEC"
    lower_inside = lsl is None or lower > lsl or (lower == lsl and lower_op == ">=")
    upper_inside = (
        usl is None
        or upper < usl
        or (upper == usl and upper_op == "<=" and upper_inclusive)
        or (upper == usl and not upper_inclusive)
    )
    if lower_inside and upper_inside:
        return "IN_SPEC"
    return "CROSSES_SPEC"


def _value_spec_status(
    value: float,
    *,
    lsl: float | None,
    usl: float | None,
    lower_operator: str | None = None,
    upper_operator: str | None = None,
) -> str:
    if lsl is None and usl is None:
        return "NO_SPEC"
    lower_oos = lsl is not None and (
        value <= lsl if (lower_operator or ">=") == ">" else value < lsl
    )
    upper_oos = usl is not None and (
        value >= usl if (upper_operator or "<=") == "<" else value > usl
    )
    if lower_oos or upper_oos:
        return "OUT_OF_SPEC"
    return "IN_SPEC"


def _normal_fit_statistics(
    *,
    sample_count: int,
    minimum_sample_size: int = 2,
    mean: float | None,
    sample_stddev: float | None,
    minimum: float | None,
    maximum: float | None,
) -> DatasetNormalFitStatistics:
    """Return a bounded MLE normal curve without changing the observed population."""
    if (
        sample_count < minimum_sample_size
        or mean is None
        or sample_stddev is None
        or minimum is None
        or maximum is None
    ):
        return DatasetNormalFitStatistics(
            status="NOT_APPLICABLE",
            reason_code="NORMAL_FIT_MINIMUM_SAMPLE_NOT_MET",
            sample_count=sample_count,
            mean=mean,
            standard_deviation=None,
            points=(),
        )
    standard_deviation = sample_stddev * math.sqrt((sample_count - 1) / sample_count)
    if not math.isfinite(standard_deviation) or standard_deviation <= 0:
        return DatasetNormalFitStatistics(
            status="NOT_APPLICABLE",
            reason_code="NORMAL_FIT_ZERO_VARIANCE",
            sample_count=sample_count,
            mean=mean,
            standard_deviation=(
                standard_deviation if math.isfinite(standard_deviation) else None
            ),
            points=(),
        )
    if maximum < minimum:
        raise DomainError(
            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
            "normal-fit input range is inverted",
            409,
        )
    lower = min(minimum, mean - 4.0 * standard_deviation)
    upper = max(maximum, mean + 4.0 * standard_deviation)
    step = (upper - lower) / 100.0
    coefficient = 1.0 / (standard_deviation * math.sqrt(2.0 * math.pi))
    points = tuple(
        DatasetNormalFitPoint(
            x=lower + step * index,
            probability_density=coefficient
            * math.exp(
                -0.5 * ((lower + step * index - mean) / standard_deviation) ** 2
            ),
        )
        for index in range(101)
    )
    return DatasetNormalFitStatistics(
        status="AVAILABLE",
        reason_code=None,
        sample_count=sample_count,
        mean=mean,
        standard_deviation=standard_deviation,
        points=points,
    )


def _analysis_filter_sql(
    *,
    lot_ids: tuple[str, ...] = (),
    wafer_ids: tuple[str, ...] = (),
    bin_codes: tuple[str, ...] = (),
    overall_results: tuple[str, ...] = (),
    source_run_ids: tuple[int, ...] | None = None,
    tester_ids: tuple[str, ...] = (),
    program_versions: tuple[str, ...] = (),
    condition_item_ids: tuple[int, ...] | None = None,
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
    if tester_ids:
        clauses.append("tr.tester_id IN :tester_ids")
        parameters["tester_ids"] = tester_ids
        expanding.append("tester_ids")
    if program_versions:
        clauses.append(
            "EXISTS(SELECT 1 FROM mdm.test_program_version filter_pv "
            "WHERE filter_pv.program_version_id=tr.program_version_id "
            "AND filter_pv.version_code IN :program_versions)"
        )
        parameters["program_versions"] = program_versions
        expanding.append("program_versions")
    if condition_item_ids is not None:
        if condition_item_ids:
            clauses.append(
                "EXISTS(SELECT 1 FROM test.measurement condition_m "
                "WHERE condition_m.unit_id=ur.unit_id "
                "AND condition_m.test_item_id IN :condition_item_ids)"
            )
            parameters["condition_item_ids"] = condition_item_ids
            expanding.append("condition_item_ids")
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


def _comparison_scope_cte(
    refs: tuple[Any, ...],
) -> tuple[str, dict[str, object]]:
    selects: list[str] = []
    parameters: dict[str, object] = {}
    for ordinal, ref in enumerate(refs, start=1):
        dataset_key = f"compare_dataset_id_{ordinal}"
        version_key = f"compare_version_no_{ordinal}"
        ordinal_key = f"compare_ordinal_{ordinal}"
        selects.append(
            f"SELECT :{dataset_key} AS dataset_id,"
            f":{version_key} AS version_no,"
            f":{ordinal_key} AS ordinal_no"
        )
        parameters[dataset_key] = int(ref.dataset_id)
        parameters[version_key] = int(ref.version_no)
        parameters[ordinal_key] = ordinal
    return " UNION ALL ".join(selects), parameters


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
    return f"RUN-{int(row['run_id'])}"


def _resolve_analysis_parameter_identities(
    identity_rows: tuple[Mapping[str, Any], ...],
    *,
    dataset_id: int,
    version_no: int,
    parameter_names: tuple[str, ...],
) -> tuple[dict[str, tuple[object, ...]], dict[str, tuple[int, ...]]]:
    grouped_rows: dict[str, list[Mapping[str, Any]]] = {}
    available_by_program: dict[int, set[str]] = {}
    requested = set(parameter_names)
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
        if name not in requested:
            continue
        grouped_rows.setdefault(name, []).append(row)
        available_by_program[program_id].add(name)

    missing = sorted(
        {
            name
            for name in parameter_names
            if name not in grouped_rows
            or any(name not in available for available in available_by_program.values())
        }
    )
    if missing:
        raise DomainError(
            "ANALYSIS_PARAMETER_INCOMPATIBLE",
            "one or more parameters are unavailable after run-level filters",
            409,
            details=[
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "parameters": missing,
                }
            ],
        )

    signatures: dict[str, tuple[object, ...]] = {}
    allowed_test_item_ids: dict[str, tuple[int, ...]] = {}
    for name in parameter_names:
        rows = grouped_rows[name]
        definitions_by_program: dict[int, set[tuple[str, int]]] = {}
        for row in rows:
            step_code = str(row["step_code"] or "").strip().upper()
            if not step_code or row["sequence_no"] is None:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "selected parameter has an incomplete step identity",
                    409,
                    details=[
                        {
                            "dataset_id": dataset_id,
                            "version_no": version_no,
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
                len(definitions) != 1 for definitions in definitions_by_program.values()
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
                        "dataset_id": dataset_id,
                        "version_no": version_no,
                        "parameters": [name],
                    }
                ],
            )
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
                _optional_finite_float(row["program_lsl"], field=f"{name} program LSL"),
                _optional_finite_float(row["program_usl"], field=f"{name} program USL"),
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
                        "dataset_id": dataset_id,
                        "version_no": version_no,
                        "parameters": [name],
                    }
                ],
            )
        item_ids = tuple(sorted({int(row["test_item_id"]) for row in rows}))
        if not item_ids:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "selected parameter has no exact test-item identity",
                409,
            )
        allowed_test_item_ids[name] = item_ids
        signatures[name] = (
            next(iter(normalized_step_codes)),
            next(iter(sequence_nos)),
            next(iter(canonical_codes)),
            *next(iter(parameter_signatures)),
        )
    return signatures, allowed_test_item_ids


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
        rule_service: SqlAnalysisRuleService | None = None,
    ) -> None:
        self._engine = engine
        self._rules = rule_service or SqlAnalysisRuleService(engine)

    @staticmethod
    def _validate_rule_parameters(
        parameters: Mapping[str, Any],
        *,
        algorithm_code: str,
    ) -> dict[str, Any]:
        if parameters.get("missing_value_policy") != "EXCLUDE_AND_COUNT":
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "parameter analysis requires EXCLUDE_AND_COUNT missing-value policy",
                409,
            )
        if parameters.get("retest_policy") != "EACH_ATTEMPT":
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "parameter analysis currently requires EACH_ATTEMPT retest policy",
                409,
            )
        if parameters.get("outlier_policy") != "MARK_ONLY":
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "parameter analysis currently requires MARK_ONLY outlier policy",
                409,
            )
        minimum = parameters.get("minimum_sample_size")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 2:
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "approved rule is missing a valid minimum_sample_size",
                409,
            )
        if algorithm_code == _BOX_PLOT_METHOD:
            multiplier = parameters.get("whisker_multiplier")
            if (
                not isinstance(multiplier, (int, float))
                or isinstance(multiplier, bool)
                or not math.isfinite(float(multiplier))
                or float(multiplier) <= 0
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved Tukey rule is missing a valid whisker_multiplier",
                    409,
                )
        if algorithm_code == _HISTOGRAM_METHOD:
            bin_count = parameters.get("histogram_bin_count")
            if (
                not isinstance(bin_count, int)
                or isinstance(bin_count, bool)
                or not (5 <= bin_count <= 100)
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved Histogram rule is missing a valid histogram_bin_count",
                    409,
                )
        if algorithm_code in _SUPPORTED_CAPABILITY_METHODS:
            expected_dimension = (
                "RUN" if algorithm_code == "CPK_POOLED_WITHIN_RUN_V1" else "LOT_WAFER"
            )
            if (
                parameters.get("sigma_definition") != "POOLED_WITHIN"
                or parameters.get("subgroup_dimension") != expected_dimension
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved Cpk rule has an incompatible sigma or subgroup contract",
                    409,
                )
            risk_metric = parameters.get("capability_risk_metric")
            risk_threshold = parameters.get("capability_risk_threshold")
            if (risk_metric is None) != (risk_threshold is None):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved Cpk risk metric and threshold must be supplied together",
                    409,
                )
            if risk_metric is not None and (
                risk_metric not in {"CPK", "PPK", "MIN_CPK_PPK"}
                or not isinstance(risk_threshold, (int, float))
                or isinstance(risk_threshold, bool)
                or not math.isfinite(float(risk_threshold))
                or float(risk_threshold) <= 0
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved Cpk risk policy is invalid",
                    409,
                )
        return dict(parameters)

    def _resolve_parameter_analysis_rules(
        self,
        request: DatasetParameterAnalysisRequest,
        contexts: tuple[Mapping[str, Any], ...],
    ) -> dict[str, dict[str, Any]]:
        requested = set(request.analyses)
        references: dict[str, tuple[str, str, str]] = {}
        if DatasetParameterAnalysisType.BOX_PLOT in requested:
            references[DatasetParameterAnalysisType.BOX_PLOT.value] = (
                request.box_plot.rule_code or "",
                request.box_plot.version_code or "",
                _BOX_PLOT_METHOD,
            )
        if DatasetParameterAnalysisType.HISTOGRAM in requested:
            references[DatasetParameterAnalysisType.HISTOGRAM.value] = (
                request.histogram.rule_code or "",
                request.histogram.version_code or "",
                _HISTOGRAM_METHOD,
            )
        if DatasetParameterAnalysisType.NORMAL_FIT in requested:
            references[DatasetParameterAnalysisType.NORMAL_FIT.value] = (
                request.normal_fit.rule_code or "",
                request.normal_fit.version_code or "",
                _NORMAL_FIT_METHOD,
            )
        if DatasetParameterAnalysisType.CAPABILITY in requested:
            method = request.capability.method
            if method is None or method.value not in _SUPPORTED_CAPABILITY_METHODS:
                raise DomainError(
                    "ANALYSIS_RULE_VERSION_REQUIRED",
                    "Capability requires one supported explicit method and rule version",
                    409,
                )
            references[DatasetParameterAnalysisType.CAPABILITY.value] = (
                request.capability.rule_code or "",
                request.capability.version_code or "",
                method.value,
            )

        resolved: dict[str, dict[str, Any]] = {}
        for analysis, (rule_code, version_code, algorithm_code) in references.items():
            resolved_parameters: dict[str, Any] | None = None
            for context in contexts:
                for parameter in request.parameters:
                    current = self._rules.approved_rule_parameters(
                        rule_code=rule_code,
                        version_code=version_code,
                        test_stage=str(context["test_stage"]),
                        expected_algorithm_code=algorithm_code,
                        supplier_id=(
                            int(context["supplier_id"])
                            if context.get("supplier_id") is not None
                            else None
                        ),
                        product_id=(
                            int(context["product_id"])
                            if context.get("product_id") is not None
                            else None
                        ),
                        parameter=parameter,
                    )
                    normalized = self._validate_rule_parameters(
                        current, algorithm_code=algorithm_code
                    )
                    if resolved_parameters is None:
                        resolved_parameters = normalized
                    elif resolved_parameters != normalized:
                        raise DomainError(
                            "ANALYSIS_RULE_CONTRACT_INVALID",
                            "one exact rule version resolved to inconsistent parameters",
                            409,
                        )
            if resolved_parameters is None:
                raise DomainError(
                    "ANALYSIS_RULE_NOT_APPROVED",
                    "requested parameter-analysis rule has no approved scope",
                    409,
                )
            resolved[analysis] = {
                "rule_code": rule_code,
                "version_code": version_code,
                "algorithm_code": algorithm_code,
                "parameters": resolved_parameters,
            }
        return resolved

    def assert_parameter_analysis_rules_approved(
        self, request: DatasetParameterAnalysisRequest
    ) -> None:
        with self._engine.connect() as connection:
            contexts = tuple(
                self._version_context(
                    connection, reference.dataset_id, reference.version_no, lock=False
                )
                for reference in request.datasets
            )
        self._resolve_parameter_analysis_rules(request, contexts)

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
        scope_sql, scope_parameters = _comparison_scope_cte(refs)
        parameter_signatures: dict[str, set[tuple[object, ...]]] = {
            name: set() for name in request.parameters
        }
        parameter_presence: dict[str, int] = {name: 0 for name in request.parameters}
        with self._engine.connect() as connection:
            context_rows = tuple(
                connection.execute(
                    text(
                        "/* COMPARE_CONTEXT_SET */ WITH selected_datasets AS ("
                        + scope_sql
                        + ") SELECT selected.ordinal_no,dv.dataset_version_id,"
                        "dv.dataset_id,dv.version_no,dv.input_batch_id,"
                        "dv.canonical_model_version,dv.status,dv.is_current,dv.unit_count,"
                        "d.supplier_id,d.product_id,d.test_stage,"
                        "COALESCE(product_enrichment.value_text,p.product_name) "
                        "AS product_name,dv.spec_set_id "
                        "FROM selected_datasets selected "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_id=selected.dataset_id "
                        "AND dv.version_no=selected.version_no "
                        "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                        "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                        "OUTER APPLY(SELECT TOP (1) fe.value_text FROM "
                        "ingestion.field_enrichment fe WHERE "
                        "fe.import_batch_id=dv.input_batch_id "
                        "AND fe.source_file_id IS NULL "
                        "AND fe.test_stage=d.test_stage "
                        "AND fe.field_code='PRODUCT_CODE' "
                        "AND fe.action='FILL' AND fe.is_current=1 "
                        "ORDER BY fe.enrichment_id DESC) product_enrichment "
                        "ORDER BY selected.ordinal_no"
                    ),
                    scope_parameters,
                )
                .mappings()
                .all()
            )
            if len(context_rows) != len(refs):
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            contexts = tuple(
                sorted(context_rows, key=lambda row: int(row["ordinal_no"]))
            )
            for ordinal, (ref, context) in enumerate(
                zip(refs, contexts, strict=True), start=1
            ):
                if (
                    int(context["ordinal_no"]) != ordinal
                    or int(context["dataset_id"]) != ref.dataset_id
                    or int(context["version_no"]) != ref.version_no
                ):
                    raise DomainError(
                        "ANALYSIS_DATASET_CONTEXT_INVALID",
                        "比较分析 Dataset 上下文顺序或身份无效",
                        409,
                    )
                if str(context["status"]) != "PUBLISHED" or not bool(
                    context["is_current"]
                ):
                    raise DomainError(
                        "ANALYSIS_VERSION_NOT_CURRENT",
                        "比较分析只允许选择当前已发布的正式版本",
                        409,
                    )
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
            aggregate_rows = tuple(
                connection.execute(
                    _statement(
                        "/* COMPARE_UNIT_AGGREGATE_SET */ WITH selected_datasets AS ("
                        + scope_sql
                        + "),unit_aggregate AS (SELECT selected.ordinal_no,"
                        "COUNT_BIG(*) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                        "SUM(CASE WHEN ur.overall_result='UNKNOWN' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                        "SUM(CASE WHEN ur.overall_result='ABORT' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count "
                        "FROM selected_datasets selected "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_id=selected.dataset_id "
                        "AND dv.version_no=selected.version_no "
                        "JOIN dataset.dataset_version_run dvr "
                        "ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr "
                        "ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE 1=1" + filter_sql + " GROUP BY selected.ordinal_no) "
                        "SELECT selected.ordinal_no,selected.dataset_id,"
                        "selected.version_no,ISNULL(aggregate.unit_count,0) "
                        "AS unit_count,ISNULL(aggregate.pass_count,0) AS pass_count,"
                        "ISNULL(aggregate.fail_count,0) AS fail_count,"
                        "ISNULL(aggregate.unknown_count,0) AS unknown_count,"
                        "ISNULL(aggregate.abort_count,0) AS abort_count "
                        "FROM selected_datasets selected "
                        "LEFT JOIN unit_aggregate aggregate "
                        "ON aggregate.ordinal_no=selected.ordinal_no "
                        "ORDER BY selected.ordinal_no",
                        expanding,
                    ),
                    {**scope_parameters, **filter_parameters},
                )
                .mappings()
                .all()
            )
            if len(aggregate_rows) != len(refs):
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "Dataset 聚合行数与请求范围不一致",
                    409,
                )
            aggregates = {int(row["ordinal_no"]): row for row in aggregate_rows}

            resolved_by_ordinal: dict[
                int, tuple[dict[str, tuple[object, ...]], dict[str, tuple[int, ...]]]
            ] = {}
            selected_test_item_ids: tuple[int, ...] = ()
            statistic_rows: tuple[Mapping[str, Any], ...] = ()
            if request.parameters:
                parameter_names = tuple(request.parameters)
                identity_clauses: list[str] = []
                identity_parameters: dict[str, object] = {
                    **scope_parameters,
                    "analysis_parameters": parameter_names,
                }
                identity_expanding = ["analysis_parameters"]
                if request.lot_ids:
                    identity_clauses.append("tr.lot_id IN :identity_lot_ids")
                    identity_parameters["identity_lot_ids"] = tuple(request.lot_ids)
                    identity_expanding.append("identity_lot_ids")
                identity_rows = tuple(
                    connection.execute(
                        _statement(
                            "/* COMPARE_PARAMETER_IDENTITY_SET */ "
                            "WITH selected_datasets AS ("
                            + scope_sql
                            + ") SELECT selected.ordinal_no,selected.dataset_id,"
                            "selected.version_no,tr.run_id,"
                            "tr.program_version_id AS run_program_version_id,"
                            "tid.test_item_id,tid.program_version_id,tid.step_code,"
                            "tid.sequence_no,tid.raw_item_name,"
                            "tid.canonical_parameter_code,tid.unit_code,"
                            "tid.program_lsl,tid.program_usl,tid.condition_json "
                            "FROM selected_datasets selected "
                            "JOIN dataset.dataset_version dv "
                            "ON dv.dataset_id=selected.dataset_id "
                            "AND dv.version_no=selected.version_no "
                            "JOIN dataset.dataset_version_run dvr "
                            "ON dvr.dataset_version_id=dv.dataset_version_id "
                            "JOIN test.test_run tr "
                            "ON tr.processing_run_id=dvr.processing_run_id "
                            "LEFT JOIN mdm.test_item_definition tid "
                            "ON tid.program_version_id=tr.program_version_id "
                            "AND tid.is_analysis_parameter=1 "
                            "AND tid.raw_item_name IN :analysis_parameters "
                            "WHERE 1=1"
                            + (
                                " AND " + " AND ".join(identity_clauses)
                                if identity_clauses
                                else ""
                            )
                            + " ORDER BY selected.ordinal_no,tr.run_id,"
                            "tid.raw_item_name",
                            tuple(identity_expanding),
                        ),
                        identity_parameters,
                    )
                    .mappings()
                    .all()
                )
                identities_by_ordinal: dict[int, list[Mapping[str, Any]]] = {
                    ordinal: [] for ordinal in range(1, len(refs) + 1)
                }
                for row in identity_rows:
                    identities_by_ordinal[int(row["ordinal_no"])].append(row)
                for ordinal, ref in enumerate(refs, start=1):
                    resolved = _resolve_analysis_parameter_identities(
                        tuple(identities_by_ordinal[ordinal]),
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        parameter_names=parameter_names,
                    )
                    resolved_by_ordinal[ordinal] = resolved
                    resolved_signatures, _ = resolved
                    for name, signature in resolved_signatures.items():
                        parameter_signatures.setdefault(name, set()).add(signature)

                selected_test_item_ids = tuple(
                    sorted(
                        {
                            test_item_id
                            for _, allowed_by_name in resolved_by_ordinal.values()
                            for item_ids in allowed_by_name.values()
                            for test_item_id in item_ids
                        }
                    )
                )
                if not selected_test_item_ids:
                    raise DomainError(
                        "ANALYSIS_PARAMETER_INCOMPATIBLE",
                        "所选参数没有可验证的 Test Item 身份",
                        409,
                    )

                statistic_rows = tuple(
                    connection.execute(
                        _statement(
                            "/* COMPARE_PARAMETER_AGGREGATE_SET */ "
                            "WITH selected_datasets AS ("
                            + scope_sql
                            + ") SELECT selected.ordinal_no,selected.dataset_id,"
                            "selected.version_no,m.test_item_id,tid.raw_item_name,"
                            "COUNT_BIG(*) AS row_count,"
                            "SUM(CASE WHEN m.value_numeric IS NULL "
                            "THEN CONVERT(bigint,1) ELSE 0 END) AS missing_count,"
                            "MIN(m.value_numeric) AS minimum,"
                            "MAX(m.value_numeric) AS maximum,"
                            "SUM(m.value_numeric) AS numeric_sum "
                            "FROM selected_datasets selected "
                            "JOIN dataset.dataset_version dv "
                            "ON dv.dataset_id=selected.dataset_id "
                            "AND dv.version_no=selected.version_no "
                            "JOIN dataset.dataset_version_run dvr "
                            "ON dvr.dataset_version_id=dv.dataset_version_id "
                            "JOIN test.test_run tr "
                            "ON tr.processing_run_id=dvr.processing_run_id "
                            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                            "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                            "JOIN mdm.test_item_definition tid "
                            "ON tid.test_item_id=m.test_item_id "
                            "AND tid.program_version_id=tr.program_version_id "
                            "WHERE 1=1"
                            + filter_sql
                            + " AND m.test_item_id IN :analysis_test_item_ids "
                            "GROUP BY selected.ordinal_no,selected.dataset_id,"
                            "selected.version_no,m.test_item_id,tid.raw_item_name "
                            "ORDER BY selected.ordinal_no,tid.raw_item_name,"
                            "m.test_item_id",
                            expanding + ("analysis_test_item_ids",),
                        ),
                        {
                            **scope_parameters,
                            **filter_parameters,
                            "analysis_test_item_ids": selected_test_item_ids,
                        },
                    )
                    .mappings()
                    .all()
                )

            statistics_by_ordinal: dict[int, tuple[DatasetParameterStatistic, ...]] = {
                ordinal: () for ordinal in range(1, len(refs) + 1)
            }
            if request.parameters:
                grouped_statistics: dict[int, dict[str, dict[str, Any]]] = defaultdict(
                    dict
                )
                for row in statistic_rows:
                    ordinal = int(row["ordinal_no"])
                    name = str(row["raw_item_name"])
                    resolved_signatures, allowed_test_item_ids = resolved_by_ordinal[
                        ordinal
                    ]
                    if name not in allowed_test_item_ids or int(
                        row["test_item_id"]
                    ) not in set(allowed_test_item_ids[name]):
                        continue
                    row_count = int(row["row_count"] or 0)
                    missing_count = int(row["missing_count"] or 0)
                    if row_count < 0 or missing_count < 0 or missing_count > row_count:
                        raise DomainError(
                            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                            f"parameter {name} has invalid aggregate counts",
                            409,
                        )
                    bucket = grouped_statistics[ordinal].setdefault(
                        name,
                        {
                            "row_count": 0,
                            "missing_count": 0,
                            "minimums": [],
                            "maximums": [],
                            "numeric_sums": [],
                        },
                    )
                    bucket["row_count"] += row_count
                    bucket["missing_count"] += missing_count
                    minimum = _optional_finite_float(
                        row["minimum"], field=f"{name} minimum"
                    )
                    maximum = _optional_finite_float(
                        row["maximum"], field=f"{name} maximum"
                    )
                    numeric_sum = _optional_finite_float(
                        row["numeric_sum"], field=f"{name} numeric sum"
                    )
                    if minimum is not None:
                        bucket["minimums"].append(minimum)
                    if maximum is not None:
                        bucket["maximums"].append(maximum)
                    if numeric_sum is not None:
                        bucket["numeric_sums"].append(numeric_sum)

                for ordinal in range(1, len(refs) + 1):
                    resolved_signatures, _ = resolved_by_ordinal[ordinal]
                    statistics: list[DatasetParameterStatistic] = []
                    present = grouped_statistics.get(ordinal, {})
                    for name in sorted(present, key=str.casefold):
                        bucket = present[name]
                        row_count = int(bucket["row_count"])
                        missing_count = int(bucket["missing_count"])
                        measured_count = row_count - missing_count
                        signature = resolved_signatures[name]
                        statistics.append(
                            DatasetParameterStatistic(
                                name=name,
                                unit=signature[3],
                                lsl=signature[4],
                                usl=signature[5],
                                test_condition=signature[6],
                                measured_count=measured_count,
                                missing_count=missing_count,
                                minimum=(
                                    min(bucket["minimums"])
                                    if bucket["minimums"]
                                    else None
                                ),
                                maximum=(
                                    max(bucket["maximums"])
                                    if bucket["maximums"]
                                    else None
                                ),
                                average=(
                                    math.fsum(bucket["numeric_sums"]) / measured_count
                                    if measured_count
                                    else None
                                ),
                            )
                        )
                    statistics_by_ordinal[ordinal] = tuple(statistics)
                    for name in present:
                        parameter_presence[name] = parameter_presence.get(name, 0) + 1

            items: list[DatasetComparisonItem] = []
            for ordinal, (ref, context) in enumerate(
                zip(refs, contexts, strict=True), start=1
            ):
                product_name = (
                    str(context["product_name"]).strip() or None
                    if context["product_name"] is not None
                    else None
                )
                aggregate = aggregates.get(ordinal)
                if aggregate is None:
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "Dataset 聚合缺少请求范围",
                        409,
                    )
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
                        parameter_statistics=statistics_by_ordinal[ordinal],
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

    @staticmethod
    def _analysis_condition_item_ids(
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        test_conditions: tuple[str, ...],
    ) -> tuple[int, ...] | None:
        if not test_conditions:
            return None
        rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT tid.test_item_id,tid.raw_item_name,"
                    "tid.condition_json FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                    "AND tid.is_analysis_parameter=1"
                ),
                {"dataset_id": dataset_id, "version_no": version_no},
            )
            .mappings()
            .all()
        )
        selected = set(test_conditions)
        return tuple(
            sorted(
                int(row["test_item_id"])
                for row in rows
                if _condition_text(
                    row["condition_json"], parameter=str(row["raw_item_name"])
                )
                in selected
            )
        )

    def _analysis_identity_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        lot_ids: tuple[str, ...],
        source_run_ids: tuple[int, ...] | None,
        tester_ids: tuple[str, ...],
        program_versions: tuple[str, ...],
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
        if tester_ids:
            clauses.append("tr.tester_id IN :identity_tester_ids")
            params["identity_tester_ids"] = tester_ids
            expanding.append("identity_tester_ids")
        if program_versions:
            clauses.append("pv.version_code IN :identity_program_versions")
            params["identity_program_versions"] = program_versions
            expanding.append("identity_program_versions")
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
                    "LEFT JOIN mdm.test_program_version pv "
                    "ON pv.program_version_id=tr.program_version_id "
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

    def _analysis_multi_context_identity_rows(
        self,
        connection: Connection,
        *,
        refs: tuple[Any, ...],
        lot_ids: tuple[str, ...],
        tester_ids: tuple[str, ...],
        program_versions: tuple[str, ...],
        parameter_names: tuple[str, ...],
    ) -> tuple[
        tuple[Mapping[str, Any], ...],
        tuple[tuple[Mapping[str, Any], ...], ...],
    ]:
        """Resolve every multi-Dataset context and parameter identity in one read.

        This path is intentionally limited to scopes whose source/test-condition
        filters do not require per-Dataset metadata decoding.  Callers retain the
        established per-Dataset path for those filters.
        """

        scope_sql, parameters = _comparison_scope_cte(refs)
        parameters["analysis_parameters"] = parameter_names
        identity_clauses: list[str] = []
        expanding = ["analysis_parameters"]
        if lot_ids:
            identity_clauses.append("tr.lot_id IN :identity_lot_ids")
            parameters["identity_lot_ids"] = lot_ids
            expanding.append("identity_lot_ids")
        if tester_ids:
            identity_clauses.append("tr.tester_id IN :identity_tester_ids")
            parameters["identity_tester_ids"] = tester_ids
            expanding.append("identity_tester_ids")
        if program_versions:
            identity_clauses.append("pv.version_code IN :identity_program_versions")
            parameters["identity_program_versions"] = program_versions
            expanding.append("identity_program_versions")
        identity_filter = (
            " WHERE " + " AND ".join(identity_clauses) if identity_clauses else ""
        )
        rows = (
            connection.execute(
                _statement(
                    ";WITH /* MULTI_PARAMETER_CONTEXT_IDENTITIES */ requested_scope AS ("
                    + scope_sql
                    + "), contexts AS ("
                    "SELECT rs.ordinal_no,dv.dataset_version_id,"
                    "access_b.import_batch_id AS access_batch_id,dv.dataset_id,"
                    "dv.version_no,dv.input_batch_id,dv.canonical_model_version,"
                    "dv.status,dv.is_current,dv.unit_count,d.supplier_id,d.product_id,"
                    "d.test_stage,COALESCE(product_enrichment.value_text,p.product_name) "
                    "AS product_name,dv.spec_set_id FROM requested_scope rs "
                    "LEFT JOIN dataset.dataset_version dv ON dv.dataset_id=rs.dataset_id "
                    "AND dv.version_no=rs.version_no "
                    "LEFT JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN ingestion.import_batch access_b ON "
                    "access_b.import_batch_id=dv.input_batch_id "
                    "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                    "OUTER APPLY(SELECT TOP (1) fe.value_text FROM "
                    "ingestion.field_enrichment fe WHERE "
                    "fe.import_batch_id=dv.input_batch_id "
                    "AND fe.source_file_id IS NULL AND fe.test_stage=d.test_stage "
                    "AND fe.field_code='PRODUCT_CODE' AND fe.action='FILL' "
                    "AND fe.is_current=1 ORDER BY fe.enrichment_id DESC) "
                    "product_enrichment), identity_rows AS ("
                    "SELECT rs.ordinal_no,tr.run_id,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.test_item_id,tid.program_version_id,tid.step_code,"
                    "tid.sequence_no,tid.raw_item_name,tid.canonical_parameter_code,"
                    "tid.unit_code,tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM requested_scope rs "
                    "JOIN dataset.dataset_version dv ON dv.dataset_id=rs.dataset_id "
                    "AND dv.version_no=rs.version_no "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_program_version pv "
                    "ON pv.program_version_id=tr.program_version_id "
                    "LEFT JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "AND tid.is_analysis_parameter=1 "
                    "AND tid.raw_item_name IN :analysis_parameters"
                    + identity_filter
                    + ") SELECT c.ordinal_no,c.dataset_version_id,c.access_batch_id,"
                    "c.dataset_id,"
                    "c.version_no,c.input_batch_id,c.canonical_model_version,c.status,"
                    "c.is_current,c.unit_count,c.supplier_id,c.product_id,c.test_stage,"
                    "c.product_name,c.spec_set_id,i.run_id,i.run_program_version_id,"
                    "i.test_item_id,i.program_version_id,i.step_code,i.sequence_no,"
                    "i.raw_item_name,i.canonical_parameter_code,i.unit_code,"
                    "i.program_lsl,i.program_usl,i.condition_json FROM contexts c "
                    "LEFT JOIN identity_rows i ON i.ordinal_no=c.ordinal_no "
                    "ORDER BY c.ordinal_no,i.run_id,i.raw_item_name",
                    tuple(expanding),
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        context_fields = (
            "dataset_version_id",
            "dataset_id",
            "version_no",
            "input_batch_id",
            "canonical_model_version",
            "status",
            "is_current",
            "unit_count",
            "supplier_id",
            "product_id",
            "test_stage",
            "product_name",
            "spec_set_id",
        )
        identity_fields = (
            "run_id",
            "run_program_version_id",
            "test_item_id",
            "program_version_id",
            "step_code",
            "sequence_no",
            "raw_item_name",
            "canonical_parameter_code",
            "unit_code",
            "program_lsl",
            "program_usl",
            "condition_json",
        )
        contexts_by_ordinal: dict[int, Mapping[str, Any]] = {}
        identities_by_ordinal: dict[int, list[Mapping[str, Any]]] = {
            ordinal: [] for ordinal in range(1, len(refs) + 1)
        }
        for row in rows:
            ordinal = int(row["ordinal_no"])
            if ordinal not in identities_by_ordinal:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset identity query returned an unknown scope",
                    409,
                )
            if row["dataset_version_id"] is not None and row["access_batch_id"] is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            context = {field: row[field] for field in context_fields}
            previous = contexts_by_ordinal.setdefault(ordinal, context)
            if previous != context:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset identity query returned conflicting contexts",
                    409,
                )
            if row["run_id"] is not None:
                identities_by_ordinal[ordinal].append(
                    {field: row[field] for field in identity_fields}
                )

        contexts: list[Mapping[str, Any]] = []
        identity_groups: list[tuple[Mapping[str, Any], ...]] = []
        for ordinal in range(1, len(refs) + 1):
            context = contexts_by_ordinal.get(ordinal)
            if context is None or context["dataset_version_id"] is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            contexts.append(context)
            identity_groups.append(tuple(identities_by_ordinal[ordinal]))
        return tuple(contexts), tuple(identity_groups)

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

    def _analysis_multi_descriptive_rows(
        self,
        connection: Connection,
        *,
        scopes: tuple[
            tuple[
                Any,
                str,
                dict[str, object],
                tuple[str, ...],
                tuple[int, ...],
            ],
            ...,
        ],
    ) -> dict[int, tuple[int, int, tuple[Mapping[str, Any], ...]]]:
        """Aggregate exact descriptive statistics for all selected Datasets.

        The query keeps a distinct filtered-unit branch and exact resolved test-item
        set for every Dataset.  It therefore removes N+1 round trips without
        merging Dataset identities or weakening per-Dataset reconciliation.
        """

        if not scopes:
            return {}
        requested_scope: list[str] = []
        filtered_units: list[str] = []
        item_predicates: list[str] = []
        parameters: dict[str, object] = {}
        expanding: list[str] = []
        for ordinal, (
            ref,
            filter_sql,
            filter_parameters,
            filter_expanding,
            test_item_ids,
        ) in enumerate(scopes, start=1):
            ordinal_name = f"batch_ordinal_{ordinal}"
            dataset_name = f"batch_dataset_id_{ordinal}"
            version_name = f"batch_version_no_{ordinal}"
            item_name = f"batch_test_item_ids_{ordinal}"
            parameters[ordinal_name] = ordinal
            parameters[dataset_name] = int(ref.dataset_id)
            parameters[version_name] = int(ref.version_no)
            parameters[item_name] = test_item_ids
            expanding.append(item_name)
            requested_scope.append(f"SELECT :{ordinal_name} AS ordinal_no")

            scoped_filter_sql = filter_sql
            for name, value in filter_parameters.items():
                scoped_name = f"batch_{name}_{ordinal}"
                scoped_filter_sql = scoped_filter_sql.replace(
                    f":{name}", f":{scoped_name}"
                )
                parameters[scoped_name] = value
                if name in filter_expanding:
                    expanding.append(scoped_name)
            filtered_units.append(
                f"SELECT :{ordinal_name} AS ordinal_no,ur.unit_id,"
                "tr.program_version_id FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr "
                "ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                f"WHERE dv.dataset_id=:{dataset_name} "
                f"AND dv.version_no=:{version_name}" + scoped_filter_sql
            )
            item_predicates.append(
                f"(fu.ordinal_no=:{ordinal_name} AND m.test_item_id IN :{item_name})"
            )

        status_columns = ",".join(
            "SUM(CASE WHEN sm.measurement_status='"
            + status
            + "' THEN CONVERT(bigint,1) ELSE 0 END) AS status_"
            + status.lower()
            for status in _MEASUREMENT_STATUSES
        )
        rows = (
            connection.execute(
                _statement(
                    ";WITH /* MULTI_PARAMETER_DESCRIPTIVE */ requested_scope AS ("
                    + " UNION ALL ".join(requested_scope)
                    + "), filtered_units AS ("
                    + " UNION ALL ".join(filtered_units)
                    + "), unit_counts AS ("
                    "SELECT ordinal_no,COUNT_BIG(*) AS matched_unit_count "
                    "FROM filtered_units GROUP BY ordinal_no), "
                    "selected_measurements AS ("
                    "SELECT fu.ordinal_no,tid.raw_item_name,m.measurement_status,"
                    "m.value_numeric FROM filtered_units fu "
                    "JOIN test.measurement m ON m.unit_id=fu.unit_id "
                    "JOIN mdm.test_item_definition tid "
                    "ON tid.test_item_id=m.test_item_id "
                    "AND tid.program_version_id=fu.program_version_id WHERE "
                    + " OR ".join(item_predicates)
                    + "), aggregates AS ("
                    "SELECT sm.ordinal_no,sm.raw_item_name,COUNT_BIG(*) AS row_count,"
                    "SUM(CASE WHEN sm.measurement_status='MEASURED' "
                    "AND sm.value_numeric IS NOT NULL THEN CONVERT(bigint,1) "
                    "ELSE 0 END) AS numeric_count,"
                    + status_columns
                    + ",MIN(CASE WHEN sm.measurement_status='MEASURED' "
                    "THEN sm.value_numeric END) AS minimum,"
                    "MAX(CASE WHEN sm.measurement_status='MEASURED' "
                    "THEN sm.value_numeric END) AS maximum,"
                    "AVG(CASE WHEN sm.measurement_status='MEASURED' "
                    "THEN sm.value_numeric END) AS average,"
                    "STDEV(CASE WHEN sm.measurement_status='MEASURED' "
                    "THEN sm.value_numeric END) AS sample_stddev "
                    "FROM selected_measurements sm "
                    "GROUP BY sm.ordinal_no,sm.raw_item_name) "
                    "SELECT rs.ordinal_no,ISNULL(uc.matched_unit_count,0) "
                    "AS matched_unit_count,a.raw_item_name,a.row_count,a.numeric_count,"
                    + ",".join(
                        f"a.status_{status.lower()}" for status in _MEASUREMENT_STATUSES
                    )
                    + ",a.minimum,a.maximum,a.average,a.sample_stddev "
                    "FROM requested_scope rs LEFT JOIN unit_counts uc "
                    "ON uc.ordinal_no=rs.ordinal_no LEFT JOIN aggregates a "
                    "ON a.ordinal_no=rs.ordinal_no "
                    "ORDER BY rs.ordinal_no,a.raw_item_name",
                    tuple(expanding),
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        grouped: dict[int, dict[str, Any]] = {
            ordinal: {"matched": None, "rows": []}
            for ordinal in range(1, len(scopes) + 1)
        }
        for row in rows:
            ordinal = int(row["ordinal_no"])
            if ordinal not in grouped:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset aggregate returned an unknown scope",
                    409,
                )
            matched = int(row["matched_unit_count"] or 0)
            previous = grouped[ordinal]["matched"]
            if previous is not None and int(previous) != matched:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset aggregate returned conflicting unit counts",
                    409,
                )
            grouped[ordinal]["matched"] = matched
            if row["raw_item_name"] is not None:
                grouped[ordinal]["rows"].append(row)

        result: dict[int, tuple[int, int, tuple[Mapping[str, Any], ...]]] = {}
        for ordinal, values in grouped.items():
            if values["matched"] is None:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset aggregate omitted a requested scope",
                    409,
                )
            aggregate_rows = tuple(values["rows"])
            candidate = sum(int(row["row_count"] or 0) for row in aggregate_rows)
            matched = int(values["matched"])
            if matched < 0 or candidate < 0:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "parameter-analysis aggregate returned invalid counts",
                    409,
                )
            if candidate > _PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS:
                ref = scopes[ordinal - 1][0]
                raise DomainError(
                    "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED",
                    "parameter analysis exceeds the bounded measurement workload",
                    422,
                    details=[
                        {
                            "dataset_id": int(ref.dataset_id),
                            "version_no": int(ref.version_no),
                            "actual": candidate,
                            "limit": _PARAMETER_ANALYSIS_MAX_CANDIDATE_MEASUREMENTS,
                        }
                    ],
                )
            result[ordinal] = (matched, candidate, aggregate_rows)
        return result

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
        whisker_multiplier: float,
    ) -> tuple[Mapping[str, Any], ...]:
        rows = (
            connection.execute(
                _statement(
                    ";WITH numeric_values AS ("
                    "SELECT tid.raw_item_name,m.measurement_id,m.unit_id,m.value_numeric,"
                    "tid.program_lsl,tid.program_usl FROM dataset.dataset_version dv "
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
                    "MIN(CASE WHEN value_numeric>=q1-:whisker_multiplier*(q3-q1) THEN value_numeric END) "
                    "AS lower_whisker,"
                    "MAX(CASE WHEN value_numeric<=q3+:whisker_multiplier*(q3-q1) THEN value_numeric END) "
                    "AS upper_whisker,"
                    "SUM(CASE WHEN value_numeric<q1-:whisker_multiplier*(q3-q1) "
                    "OR value_numeric>q3+:whisker_multiplier*(q3-q1) "
                    "THEN CONVERT(bigint,1) ELSE 0 END) AS outlier_count "
                    "FROM quartiles GROUP BY raw_item_name ORDER BY raw_item_name",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    "whisker_multiplier": whisker_multiplier,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_box_outlier_rows(
        self,
        connection: Connection,
        *,
        dataset_id: int,
        version_no: int,
        filter_sql: str,
        filter_parameters: dict[str, object],
        expanding: tuple[str, ...],
        test_item_ids: tuple[int, ...],
        whisker_multiplier: float,
    ) -> tuple[Mapping[str, Any], ...]:
        half_limit = _BOX_OUTLIER_EVIDENCE_LIMIT // 2
        rows = (
            connection.execute(
                _statement(
                    ";WITH /* BOX_OUTLIER_EVIDENCE */ numeric_values AS ("
                    "SELECT tid.raw_item_name,m.measurement_id,m.unit_id,m.value_numeric "
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
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                    "quartiles AS (SELECT raw_item_name,measurement_id,unit_id,value_numeric,"
                    "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY value_numeric) "
                    "OVER(PARTITION BY raw_item_name) AS q1,"
                    "PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY value_numeric) "
                    "OVER(PARTITION BY raw_item_name) AS q3 FROM numeric_values),"
                    "outliers AS (SELECT raw_item_name,measurement_id,unit_id,value_numeric "
                    "FROM quartiles WHERE value_numeric<q1-:whisker_multiplier*(q3-q1) "
                    "OR value_numeric>q3+:whisker_multiplier*(q3-q1)),"
                    "ranked AS (SELECT raw_item_name,measurement_id,unit_id,value_numeric,"
                    "COUNT_BIG(*) OVER(PARTITION BY raw_item_name) AS original_points,"
                    "ROW_NUMBER() OVER(PARTITION BY raw_item_name "
                    "ORDER BY value_numeric,measurement_id) AS low_rank,"
                    "ROW_NUMBER() OVER(PARTITION BY raw_item_name "
                    "ORDER BY value_numeric DESC,measurement_id DESC) AS high_rank "
                    "FROM outliers) "
                    "SELECT raw_item_name,measurement_id,unit_id,value_numeric,"
                    "'NO_SPEC' AS spec_status,original_points FROM ranked "
                    "WHERE low_rank<=:outlier_half_limit OR high_rank<=:outlier_half_limit "
                    "ORDER BY raw_item_name,value_numeric,measurement_id",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    "whisker_multiplier": whisker_multiplier,
                    "outlier_half_limit": half_limit,
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return tuple(rows)

    def _analysis_distribution_evidence_rows(
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
                    ";WITH /* DISTRIBUTION_EVIDENCE */ distribution_values AS ("
                    "SELECT tid.raw_item_name,m.measurement_id,m.unit_id,m.value_numeric,"
                    "CONVERT(bit,0) AS is_oos,'NO_SPEC' AS spec_status "
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
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                    "ranked AS (SELECT raw_item_name,measurement_id,unit_id,value_numeric,"
                    "is_oos,spec_status,COUNT_BIG(*) OVER(PARTITION BY raw_item_name) AS original_points,"
                    "ROW_NUMBER() OVER(PARTITION BY raw_item_name ORDER BY value_numeric,measurement_id) AS value_rank,"
                    "ROW_NUMBER() OVER(PARTITION BY raw_item_name,is_oos ORDER BY measurement_id) AS class_rank "
                    "FROM distribution_values),"
                    "selected AS (SELECT *,"
                    "CONVERT(bigint,(original_points+3)/4) AS q1_rank,"
                    "CONVERT(bigint,(original_points+1)/2) AS median_rank,"
                    "CONVERT(bigint,(3*original_points+3)/4) AS q3_rank FROM ranked) "
                    "SELECT raw_item_name,measurement_id,unit_id,value_numeric,spec_status,original_points "
                    "FROM selected WHERE value_rank IN (1,q1_rank,median_rank,q3_rank,original_points) "
                    "OR (is_oos=1 AND class_rank<=:oos_evidence_limit) "
                    "ORDER BY raw_item_name,value_numeric,measurement_id",
                    expanding + ("analysis_test_item_ids",),
                ),
                {
                    "dataset_id": dataset_id,
                    "version_no": version_no,
                    "analysis_test_item_ids": test_item_ids,
                    "oos_evidence_limit": _DISTRIBUTION_OOS_EVIDENCE_LIMIT,
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
                    "SELECT tid.raw_item_name,m.measurement_id,m.unit_id,m.value_numeric "
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
                    "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                    "bounds AS (SELECT raw_item_name,MIN(value_numeric) AS range_min,"
                    "MAX(value_numeric) AS range_max FROM numeric_values GROUP BY raw_item_name),"
                    "bucketed AS (SELECT v.raw_item_name,"
                    "v.value_numeric,b.range_min,b.range_max,"
                    "CASE WHEN b.range_min=b.range_max THEN 0 "
                    "WHEN v.value_numeric=b.range_max THEN :histogram_bin_count-1 "
                    "ELSE CONVERT(int,FLOOR((v.value_numeric-b.range_min)*"
                    ":histogram_bin_count/NULLIF(b.range_max-b.range_min,0))) END "
                    "AS bin_index FROM numeric_values v JOIN bounds b "
                    "ON b.raw_item_name=v.raw_item_name) "
                    "SELECT raw_item_name,range_min,range_max,bin_index,"
                    "COUNT_BIG(*) AS bin_value_count "
                    "FROM bucketed GROUP BY raw_item_name,range_min,range_max,bin_index "
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
                "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
                "LEFT JOIN mdm.spec_binding sb ON 1=0 "
                "LEFT JOIN mdm.scope_priority sp ON 1=0 "
            )
        else:
            spec_joins = (
                "LEFT JOIN mdm.spec_binding sb ON "
                "(sb.program_version_id IS NULL OR sb.program_version_id=tr.program_version_id) "
                "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
                "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
                "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
                "AND (sb.effective_from_utc IS NULL "
                "OR sb.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (sb.effective_to_utc IS NULL "
                "OR sb.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.scope_priority sp ON sp.scope_code=sb.scope_code "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
                "AND ss.status='RELEASED' "
                "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        rows = (
            connection.execute(
                _statement(
                    "SELECT DISTINCT tr.run_id,tr.test_stage,COALESCE(tr.started_at_utc,pr.started_at_utc) AS event_at_utc,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.program_version_id AS item_program_version_id,"
                    "tid.test_item_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "tid.raw_item_name,sb.spec_binding_id,sp.priority AS scope_priority,ss.spec_set_id,ss.version_code,"
                    "si.spec_item_id,si.unit_code,"
                    "si.lsl,si.usl,si.lower_operator,si.upper_operator,si.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
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
        formal_spec: FormalSpecResolution | None = None,
        subgroup_rows: tuple[Mapping[str, Any], ...],
        rule_code: str | None,
        capability_method: str | None = None,
        minimum_sample_size: int = 2,
        risk_metric: str | None = None,
        risk_threshold: float | None = None,
        parameters_sha256: str | None = None,
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
        formal_spec = formal_spec or resolve_released_formal_spec(
            spec_rows,
            parameter=parameter,
            identity_unit=identity.unit,
            identity_condition=identity.test_condition,
        )
        reasons = list(formal_spec.reason_codes)
        spec_set_ids = formal_spec.spec_set_ids
        lsl = formal_spec.lsl
        usl = formal_spec.usl
        if lsl is None and usl is None:
            if "FORMAL_SPEC_LIMIT_MISSING" not in reasons:
                reasons.append("FORMAL_SPEC_LIMIT_MISSING")
            spec_mode = None
        elif lsl is None:
            spec_mode = "UPPER_ONLY"
        elif usl is None:
            spec_mode = "LOWER_ONLY"
        else:
            spec_mode = "TWO_SIDED"

        sample_count = int(statistics["numeric_count"])
        if sample_count < minimum_sample_size:
            reasons.append("CAPABILITY_MINIMUM_SAMPLE_NOT_MET")
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
        if rule_code is None or capability_method is None:
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
                risk_metric=risk_metric,
                risk_threshold=risk_threshold,
                parameters_sha256=parameters_sha256,
            ),
            spec_set_ids if formal_spec.resolved else (),
            "RELEASED_SPEC" if formal_spec.resolved else "UNRESOLVED",
        )

    @staticmethod
    def _descriptive_batch_eligible(
        request: DatasetParameterAnalysisRequest,
    ) -> bool:
        return (
            1 <= len(request.datasets) <= 8
            and {item.value for item in request.analyses}
            == {DatasetParameterAnalysisType.DESCRIPTIVE.value}
            and not request.filters.source_ids
            and not request.filters.test_conditions
        )

    def analyze_parameters(
        self, request: DatasetParameterAnalysisRequest
    ) -> DatasetParameterAnalysisResult:
        """Coalesce only concurrent identical bounded descriptive reads."""

        if not self._descriptive_batch_eligible(request):
            return self._analyze_parameters_uncached(request)
        request_key = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        key = (id(self._engine), request_key)
        with _PARAMETER_ANALYSIS_FLIGHT_LOCK:
            flight = _PARAMETER_ANALYSIS_FLIGHTS.get(key)
            owner = flight is None
            if flight is None:
                flight = _ParameterAnalysisFlight(Event())
                _PARAMETER_ANALYSIS_FLIGHTS[key] = flight
        if not owner:
            flight.completed.wait()
            if flight.error is not None:
                _raise_parameter_analysis_flight_error(flight.error)
            if flight.result is None:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "coalesced parameter analysis completed without a result",
                    409,
                )
            return flight.result
        try:
            flight.result = self._analyze_parameters_uncached(request)
            return flight.result
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with _PARAMETER_ANALYSIS_FLIGHT_LOCK:
                if _PARAMETER_ANALYSIS_FLIGHTS.get(key) is flight:
                    del _PARAMETER_ANALYSIS_FLIGHTS[key]
                flight.completed.set()

    def _analyze_parameters_uncached(
        self, request: DatasetParameterAnalysisRequest
    ) -> DatasetParameterAnalysisResult:
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
        tester_ids = _normalized_filter_values(
            tuple(request.filters.tester_ids), field="tester_ids"
        )
        program_versions = _normalized_filter_values(
            tuple(request.filters.program_versions), field="program_versions"
        )
        test_conditions = _normalized_filter_values(
            tuple(request.filters.test_conditions), field="test_conditions"
        )
        overall_results = _normalized_filter_values(
            tuple(item.value for item in request.filters.overall_results),
            field="overall_results",
        )
        parameter_names = _normalized_filter_values(
            tuple(request.parameters), field="parameters"
        )
        analysis_types = {item.value for item in request.analyses}
        spec_overlay_requested = bool(
            analysis_types.intersection(
                {
                    DatasetParameterAnalysisType.HISTOGRAM.value,
                    DatasetParameterAnalysisType.NORMAL_FIT.value,
                    DatasetParameterAnalysisType.CAPABILITY.value,
                }
            )
        )
        refs = tuple(request.datasets)
        use_descriptive_batch = self._descriptive_batch_eligible(request)
        contexts: list[Mapping[str, Any]] = []
        batched_identity_groups: tuple[tuple[Mapping[str, Any], ...], ...] = ()
        work: list[
            tuple[
                object,
                Mapping[str, Any],
                tuple[int, ...] | None,
                tuple[int, ...] | None,
                dict[str, DatasetAnalysisParameterIdentity],
                dict[str, tuple[object, ...]],
                dict[str, tuple[int, ...]],
            ]
        ] = []
        with self._engine.connect() as connection:
            if use_descriptive_batch:
                batched_contexts, batched_identity_groups = (
                    self._analysis_multi_context_identity_rows(
                        connection,
                        refs=refs,
                        lot_ids=lot_ids,
                        tester_ids=tester_ids,
                        program_versions=program_versions,
                        parameter_names=parameter_names,
                    )
                )
                contexts.extend(batched_contexts)
            else:
                for ref in refs:
                    contexts.append(
                        self._version_context(
                            connection, ref.dataset_id, ref.version_no, lock=False
                        )
                    )
            for context in contexts:
                if str(context["status"]) != "PUBLISHED" or not bool(
                    context["is_current"]
                ):
                    raise DomainError(
                        "ANALYSIS_VERSION_NOT_CURRENT",
                        "parameter analysis only allows Current Published Dataset Versions",
                        409,
                    )
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

            resolved_rules = self._resolve_parameter_analysis_rules(
                request, tuple(contexts)
            )
            box_rule = resolved_rules.get(DatasetParameterAnalysisType.BOX_PLOT.value)
            histogram_rule = resolved_rules.get(
                DatasetParameterAnalysisType.HISTOGRAM.value
            )
            normal_fit_rule = resolved_rules.get(
                DatasetParameterAnalysisType.NORMAL_FIT.value
            )
            capability_rule = resolved_rules.get(
                DatasetParameterAnalysisType.CAPABILITY.value
            )
            capability_method = (
                str(capability_rule["algorithm_code"])
                if capability_rule is not None
                else None
            )

            all_signatures: dict[str, set[tuple[object, ...]]] = {
                name: set() for name in parameter_names
            }
            for ordinal, (ref, context) in enumerate(
                zip(refs, contexts, strict=True), start=1
            ):
                if use_descriptive_batch:
                    source_run_ids = None
                    identity_rows = batched_identity_groups[ordinal - 1]
                    condition_item_ids = None
                else:
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
                        tester_ids=tester_ids,
                        program_versions=program_versions,
                        parameter_names=parameter_names,
                    )
                    condition_item_ids = self._analysis_condition_item_ids(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        test_conditions=test_conditions,
                    )
                signatures, allowed_test_item_ids = (
                    _resolve_analysis_parameter_identities(
                        identity_rows,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        parameter_names=parameter_names,
                    )
                )
                identities: dict[str, DatasetAnalysisParameterIdentity] = {}
                for name in parameter_names:
                    signature = signatures[name]
                    all_signatures[name].add(signature)
                    context_spec_id = context["spec_set_id"]
                    identities[name] = DatasetAnalysisParameterIdentity(
                        name=name,
                        canonical_parameter_code=signature[2],
                        unit=signature[3],
                        program_lsl=signature[4],
                        program_usl=signature[5],
                        test_condition=signature[6],
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
                        condition_item_ids,
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

            batched_descriptive: dict[
                int, tuple[int, int, tuple[Mapping[str, Any], ...]]
            ] = {}
            descriptive_preflight: dict[int, tuple[int, int]] = {}
            if use_descriptive_batch:
                batch_scopes: list[
                    tuple[
                        Any,
                        str,
                        dict[str, object],
                        tuple[str, ...],
                        tuple[int, ...],
                    ]
                ] = []
                for (
                    ref,
                    _,
                    source_run_ids,
                    condition_item_ids,
                    _,
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
                        tester_ids=tester_ids,
                        program_versions=program_versions,
                        condition_item_ids=condition_item_ids,
                    )
                    batch_scopes.append(
                        (
                            ref,
                            filter_sql,
                            filter_parameters,
                            expanding,
                            selected_test_item_ids,
                        )
                    )
                if len(refs) == 1:
                    (
                        ref,
                        filter_sql,
                        filter_parameters,
                        expanding,
                        selected_test_item_ids,
                    ) = batch_scopes[0]
                    descriptive_preflight[1] = self._analysis_preflight(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                    )
                batched_descriptive = self._analysis_multi_descriptive_rows(
                    connection, scopes=tuple(batch_scopes)
                )

            items: list[DatasetParameterAnalysisItem] = []
            formal_compatibility_signatures: dict[str, set[tuple[object, ...]]] = {
                name: set() for name in parameter_names
            }
            unresolved_formal_parameters: set[str] = set()
            for ordinal, (
                ref,
                context,
                source_run_ids,
                condition_item_ids,
                identities,
                _,
                allowed_test_item_ids,
            ) in enumerate(work, start=1):
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
                    tester_ids=tester_ids,
                    program_versions=program_versions,
                    condition_item_ids=condition_item_ids,
                )
                if use_descriptive_batch:
                    try:
                        (
                            batch_matched_units,
                            batch_candidate_measurements,
                            aggregate_rows,
                        ) = batched_descriptive[ordinal]
                    except KeyError as exc:
                        raise DomainError(
                            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                            "multi-Dataset aggregate omitted a requested scope",
                            409,
                        ) from exc
                    if len(refs) == 1:
                        matched_units, candidate_measurements = descriptive_preflight[
                            ordinal
                        ]
                        if (
                            matched_units != batch_matched_units
                            or candidate_measurements != batch_candidate_measurements
                        ):
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                "parameter preflight and aggregate counts do not reconcile",
                                409,
                            )
                    else:
                        matched_units = batch_matched_units
                        candidate_measurements = batch_candidate_measurements
                else:
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
                        whisker_multiplier=float(
                            box_rule["parameters"]["whisker_multiplier"]
                        ),
                    )
                    if DatasetParameterAnalysisType.BOX_PLOT.value in analysis_types
                    else ()
                )
                box_outlier_rows = (
                    self._analysis_box_outlier_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                        whisker_multiplier=float(
                            box_rule["parameters"]["whisker_multiplier"]
                        ),
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
                        bin_count=int(
                            histogram_rule["parameters"]["histogram_bin_count"]
                        ),
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
                    if analysis_types.intersection(
                        {
                            DatasetParameterAnalysisType.HISTOGRAM.value,
                            DatasetParameterAnalysisType.NORMAL_FIT.value,
                            DatasetParameterAnalysisType.CAPABILITY.value,
                        }
                    )
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
                        rule_code=capability_method,
                    )
                    if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types
                    and capability_rule is not None
                    else ()
                )
                distribution_evidence_rows = (
                    self._analysis_distribution_evidence_rows(
                        connection,
                        dataset_id=ref.dataset_id,
                        version_no=ref.version_no,
                        filter_sql=filter_sql,
                        filter_parameters=filter_parameters,
                        expanding=expanding,
                        test_item_ids=selected_test_item_ids,
                    )
                    if analysis_types.intersection(
                        {
                            DatasetParameterAnalysisType.NORMAL_FIT.value,
                            DatasetParameterAnalysisType.CAPABILITY.value,
                        }
                    )
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
                box_outliers_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in box_outlier_rows:
                    box_outliers_by_name.setdefault(
                        str(row["raw_item_name"]), []
                    ).append(row)
                histogram_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in histogram_rows:
                    histogram_by_name.setdefault(str(row["raw_item_name"]), []).append(
                        row
                    )
                spec_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in spec_rows:
                    spec_by_name.setdefault(str(row["raw_item_name"]), []).append(row)
                evidence_by_name: dict[str, list[Mapping[str, Any]]] = {}
                for row in distribution_evidence_rows:
                    evidence_by_name.setdefault(str(row["raw_item_name"]), []).append(
                        row
                    )

                parameter_results: list[DatasetParameterAnalysis] = []
                for name in parameter_names:
                    stats = internal_statistics[name]
                    identity = identities[name]
                    formal_spec = (
                        resolve_released_formal_spec(
                            tuple(spec_by_name.get(name, ())),
                            parameter=name,
                            identity_unit=identity.unit,
                            identity_condition=identity.test_condition,
                        )
                        if spec_overlay_requested
                        else None
                    )
                    if formal_spec is not None:
                        identity = replace(
                            identity,
                            spec_set_ids=formal_spec.spec_set_ids,
                            limit_source=(
                                "RELEASED_SPEC"
                                if formal_spec.resolved
                                else "UNRESOLVED"
                            ),
                            formal_lsl=formal_spec.lsl,
                            formal_usl=formal_spec.usl,
                            formal_lower_operator=formal_spec.lower_operator,
                            formal_upper_operator=formal_spec.upper_operator,
                            formal_spec_status=formal_spec.status,
                            formal_spec_reason_codes=formal_spec.reason_codes,
                            formal_spec_versions=formal_spec.spec_versions,
                        )
                        if len(refs) > 1:
                            if not formal_spec.resolved:
                                unresolved_formal_parameters.add(name)
                            else:
                                formal_compatibility_signatures[name].add(
                                    (
                                        formal_spec.spec_set_ids,
                                        identity.unit,
                                        identity.test_condition,
                                        formal_spec.lsl,
                                        formal_spec.usl,
                                        formal_spec.lower_operator,
                                        formal_spec.upper_operator,
                                    )
                                )
                    raw_distribution_evidence = evidence_by_name.get(name, [])
                    observed_evidence = tuple(
                        _measurement_evidence(row, parameter=name)
                        for row in raw_distribution_evidence
                    )
                    evidence_original = (
                        int(raw_distribution_evidence[0]["original_points"])
                        if raw_distribution_evidence
                        else 0
                    )
                    if (
                        raw_distribution_evidence
                        and evidence_original != stats["numeric_count"]
                    ):
                        raise DomainError(
                            "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                            f"parameter {name} distribution evidence does not reconcile",
                            409,
                        )
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
                        box_minimum_sample = int(
                            box_rule["parameters"]["minimum_sample_size"]
                        )
                        if stats["numeric_count"] < box_minimum_sample:
                            box_row = None
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
                        raw_outlier_evidence = box_outliers_by_name.get(name, [])
                        outlier_evidence = tuple(
                            _measurement_evidence(row, parameter=name)
                            for row in raw_outlier_evidence
                        )
                        if len(outlier_evidence) > _BOX_OUTLIER_EVIDENCE_LIMIT:
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                f"parameter {name} exceeded bounded outlier evidence",
                                409,
                            )
                        evidence_original = (
                            int(raw_outlier_evidence[0]["original_points"])
                            if raw_outlier_evidence
                            else 0
                        )
                        if evidence_original != outlier_count:
                            raise DomainError(
                                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                f"parameter {name} outlier evidence does not reconcile",
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
                            outlier_evidence=outlier_evidence,
                            outlier_sampling=DatasetEvidenceSampling(
                                sampled=len(outlier_evidence) < outlier_count,
                                method="EXTREME_BOTH_TAILS_BY_VALUE_THEN_MEASUREMENT_ID_V1",
                                original_points=outlier_count,
                                returned_points=len(outlier_evidence),
                            ),
                        )

                    histogram = None
                    if DatasetParameterAnalysisType.HISTOGRAM.value in analysis_types:
                        rows = histogram_by_name.get(name, [])
                        histogram_bin_count = int(
                            histogram_rule["parameters"]["histogram_bin_count"]
                        )
                        histogram_minimum_sample = int(
                            histogram_rule["parameters"]["minimum_sample_size"]
                        )
                        if stats["numeric_count"] < histogram_minimum_sample:
                            rows = []
                        if not rows:
                            if stats["numeric_count"] >= histogram_minimum_sample:
                                raise DomainError(
                                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                                    f"parameter {name} is missing requested histogram aggregates",
                                    409,
                                )
                            histogram = DatasetHistogramStatistics(
                                bin_count=0,
                                requested_bin_count=histogram_bin_count,
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
                                1 if range_min == range_max else histogram_bin_count
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
                            built_bins: list[DatasetHistogramBin] = []
                            for index, count in enumerate(counts):
                                lower_bound = (
                                    range_min + width * index
                                    if actual_bin_count > 1
                                    else range_min
                                )
                                upper_bound = (
                                    range_max
                                    if index == actual_bin_count - 1
                                    else range_min + width * (index + 1)
                                )
                                built_bins.append(
                                    DatasetHistogramBin(
                                        index=index,
                                        lower_bound=lower_bound,
                                        upper_bound=upper_bound,
                                        count=count,
                                        lower_inclusive=True,
                                        upper_inclusive=index == actual_bin_count - 1,
                                        spec_region=_histogram_spec_region(
                                            lower_bound,
                                            upper_bound,
                                            lsl=None,
                                            usl=None,
                                            upper_inclusive=index
                                            == actual_bin_count - 1,
                                        ),
                                        aggregate_drilldown_context=(
                                            DatasetMeasurementAggregateContext(
                                                dataset_id=ref.dataset_id,
                                                version_no=ref.version_no,
                                                parameter=name,
                                                lower_bound=lower_bound,
                                                upper_bound=upper_bound,
                                                lower_inclusive=True,
                                                upper_inclusive=index
                                                == actual_bin_count - 1,
                                            )
                                            if count > 0
                                            else None
                                        ),
                                    )
                                )
                            bins = tuple(built_bins)
                            histogram = DatasetHistogramStatistics(
                                bin_count=actual_bin_count,
                                requested_bin_count=histogram_bin_count,
                                range_min=range_min,
                                range_max=range_max,
                                bins=bins,
                            )

                    normal_fit = (
                        _normal_fit_statistics(
                            sample_count=stats["numeric_count"],
                            minimum_sample_size=int(
                                normal_fit_rule["parameters"]["minimum_sample_size"]
                            ),
                            mean=stats["average"],
                            sample_stddev=stats["sample_stddev"],
                            minimum=stats["minimum"],
                            maximum=stats["maximum"],
                        )
                        if DatasetParameterAnalysisType.NORMAL_FIT.value
                        in analysis_types
                        else None
                    )
                    if normal_fit is not None:
                        normal_fit = replace(
                            normal_fit,
                            observed_evidence=observed_evidence,
                            evidence_sampling=DatasetEvidenceSampling(
                                sampled=len(observed_evidence) < stats["numeric_count"],
                                method="BOUNDED_QUANTILE_EVIDENCE_BY_MEASUREMENT_ID_V1",
                                original_points=stats["numeric_count"],
                                returned_points=len(observed_evidence),
                            ),
                        )
                    capability = None
                    if DatasetParameterAnalysisType.CAPABILITY.value in analysis_types:
                        capability, spec_set_ids, limit_source = (
                            self._analysis_capability_result(
                                parameter=name,
                                identity=identity,
                                statistics=stats,
                                spec_rows=tuple(spec_by_name.get(name, ())),
                                formal_spec=formal_spec,
                                subgroup_rows=subgroup_rows,
                                rule_code=(
                                    f"{capability_rule['rule_code']}:"
                                    f"{capability_rule['version_code']}"
                                ),
                                capability_method=capability_method,
                                minimum_sample_size=int(
                                    capability_rule["parameters"]["minimum_sample_size"]
                                ),
                                risk_metric=capability_rule["parameters"].get(
                                    "capability_risk_metric"
                                ),
                                risk_threshold=capability_rule["parameters"].get(
                                    "capability_risk_threshold"
                                ),
                                parameters_sha256=hashlib.sha256(
                                    json.dumps(
                                        capability_rule["parameters"],
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ).encode("utf-8")
                                ).hexdigest(),
                            )
                        )
                        identity = replace(
                            identity,
                            spec_set_ids=spec_set_ids,
                            limit_source=limit_source,
                        )
                        capability = replace(
                            capability,
                            drilldown_context=DatasetCapabilityDrilldownContext(
                                dataset_id=ref.dataset_id,
                                version_no=ref.version_no,
                                parameter=name,
                            ),
                        )
                    effective_lsl = formal_spec.lsl if formal_spec is not None else None
                    effective_usl = formal_spec.usl if formal_spec is not None else None
                    effective_lower_operator = (
                        formal_spec.lower_operator if formal_spec is not None else None
                    )
                    effective_upper_operator = (
                        formal_spec.upper_operator if formal_spec is not None else None
                    )
                    if histogram is not None:
                        histogram = replace(
                            histogram,
                            bins=tuple(
                                replace(
                                    bin_item,
                                    spec_region=_histogram_spec_region(
                                        bin_item.lower_bound,
                                        bin_item.upper_bound,
                                        lsl=effective_lsl,
                                        usl=effective_usl,
                                        lower_operator=effective_lower_operator,
                                        upper_operator=effective_upper_operator,
                                        upper_inclusive=bin_item.upper_inclusive,
                                    ),
                                )
                                for bin_item in histogram.bins
                            ),
                        )
                    if normal_fit is not None:
                        normal_fit = replace(
                            normal_fit,
                            observed_evidence=tuple(
                                replace(
                                    point,
                                    spec_status=_value_spec_status(
                                        point.value,
                                        lsl=effective_lsl,
                                        usl=effective_usl,
                                        lower_operator=effective_lower_operator,
                                        upper_operator=effective_upper_operator,
                                    ),
                                )
                                for point in normal_fit.observed_evidence
                            ),
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
                            normal_fit=normal_fit,
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
                            tester_ids=tester_ids,
                            program_versions=program_versions,
                            test_conditions=test_conditions,
                            matched_unit_count=matched_units,
                            candidate_measurement_count=candidate_measurements,
                        ),
                        parameters=tuple(parameter_results),
                    )
                )
            if (
                len(refs) > 1
                and spec_overlay_requested
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
            sorted(
                {
                    spec_version
                    for item in items
                    for parameter in item.parameters
                    for spec_version in parameter.identity.formal_spec_versions
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
                    tester_ids=tester_ids,
                    program_versions=program_versions,
                    test_conditions=test_conditions,
                ),
                filter_hash=_parameter_analysis_filter_hash(
                    lot_ids=lot_ids,
                    wafer_ids=wafer_ids,
                    bin_codes=bin_codes,
                    overall_results=overall_results,
                    source_ids=source_ids,
                    tester_ids=tester_ids,
                    program_versions=program_versions,
                    test_conditions=test_conditions,
                ),
            ),
            rule_context=DatasetParameterAnalysisRuleContext(
                spec_versions=spec_versions,
                bin_mapping_versions=(),
                evaluation_rule_versions=tuple(
                    f"RULE:{rule['rule_code']}:{rule['version_code']}:"
                    f"{rule['algorithm_code']}"
                    for rule in resolved_rules.values()
                ),
                capability_rule_code=(
                    str(capability_rule["rule_code"])
                    if capability_rule is not None
                    else None
                ),
                capability_rule_approval_status=(
                    "NOT_REQUESTED" if capability_rule is None else "APPROVED"
                ),
            ),
            capabilities=tuple(
                DatasetParameterAnalysisCapability(
                    code=analysis.value,
                    status=("AVAILABLE"),
                    reason_code=(None),
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
            warnings=(),
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
            detail_test_item_ids: tuple[int, ...] = ()
            if parameters:
                identity_rows = self._analysis_identity_rows(
                    connection,
                    dataset_id=dataset_id,
                    version_no=version_no,
                    lot_ids=lot_ids,
                    source_run_ids=None,
                    tester_ids=(),
                    program_versions=(),
                    parameter_names=parameters,
                )
                _, allowed_test_item_ids = _resolve_analysis_parameter_identities(
                    identity_rows,
                    dataset_id=dataset_id,
                    version_no=version_no,
                    parameter_names=parameters,
                )
                detail_test_item_ids = tuple(
                    sorted(
                        {
                            test_item_id
                            for item_ids in allowed_test_item_ids.values()
                            for test_item_id in item_ids
                        }
                    )
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
                            "WHERE m.unit_id IN :unit_ids "
                            "AND m.test_item_id IN :detail_test_item_ids "
                            "ORDER BY m.unit_id,tid.sequence_no",
                            ("unit_ids", "detail_test_item_ids"),
                        ),
                        {
                            "unit_ids": unit_ids,
                            "detail_test_item_ids": detail_test_item_ids,
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
            source_records.append(
                {
                    "run_id": int(row["run_id"]),
                    "lot_id": str(row["lot_id"]),
                    "source_id": _run_source_identity(row),
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
            "SELECT DISTINCT tr.run_id,tr.program_version_id AS run_program_version_id,"
            "tid.test_item_id,tid.program_version_id,tid.step_code,tid.sequence_no,"
            "tid.raw_item_name,tid.canonical_parameter_code,tid.unit_code,"
            "tid.program_lsl,tid.program_usl,tid.condition_json "
            + version_join
            + "LEFT JOIN mdm.test_item_definition tid "
            "ON tid.program_version_id=tr.program_version_id "
            "AND tid.is_analysis_parameter=1 "
            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
            "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
            + source_filter
            + "ORDER BY tr.run_id,tid.sequence_no"
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
        available_by_program: dict[int, set[str]] = {}
        for row in parameter_rows:
            program_version_id = row["run_program_version_id"]
            if program_version_id is None:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "selected FT run has no program-version identity",
                    409,
                )
            available_by_program.setdefault(int(program_version_id), set())
            if row["raw_item_name"] is not None:
                available_by_program[int(program_version_id)].add(
                    str(row["raw_item_name"])
                )
        common_names = (
            tuple(sorted(set.intersection(*available_by_program.values())))
            if available_by_program
            else ()
        )
        resolved_signatures, allowed_test_item_ids = (
            _resolve_analysis_parameter_identities(
                tuple(parameter_rows),
                dataset_id=int(context["dataset_id"]),
                version_no=int(context["version_no"]),
                parameter_names=common_names,
            )
            if common_names
            else ({}, {})
        )
        names = set(common_names)
        selected_parameter = parameters["parameter"]
        if selected_parameter is not None and selected_parameter not in names:
            raise DomainError(
                "FT_PARAMETER_NOT_FOUND", "selected FT parameter was not found", 404
            )
        point_rows: list[Mapping[str, Any]] = []
        total_count = 0
        if selected_parameter:
            selected_test_item_ids = allowed_test_item_ids[selected_parameter]
            point_params = {
                **parameters,
                **source_parameters,
                "analysis_test_item_ids": selected_test_item_ids,
            }
            count_sql = (
                "SELECT COUNT_BIG(*) "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "AND tid.program_version_id=tr.program_version_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND m.test_item_id IN :analysis_test_item_ids"
            )
            point_expanding = (
                ("source_run_ids", "analysis_test_item_ids")
                if selected_source
                else ("analysis_test_item_ids",)
            )
            count_statement = _statement(count_sql, point_expanding)
            total_count = int(
                connection.execute(
                    count_statement,
                    point_params,
                ).scalar_one()
            )
            stride = max(1, (total_count + 9_999) // 10_000)
            points_sql = (
                ";WITH points AS (SELECT tr.run_id,ur.unit_sequence,tr.lot_id,"
                "m.value_numeric,m.measurement_status,"
                "tid.program_lsl AS lsl,tid.program_usl AS usl,"
                "ROW_NUMBER() OVER(ORDER BY tr.run_id,ur.unit_sequence,ur.unit_id) AS rn "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "AND tid.program_version_id=tr.program_version_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND m.test_item_id IN :analysis_test_item_ids) "
                "SELECT run_id,unit_sequence,lot_id,value_numeric,measurement_status "
                "FROM points WHERE (rn-1)%:stride=0 OR "
                "(value_numeric IS NOT NULL AND ((lsl IS NOT NULL AND value_numeric<lsl) "
                "OR (usl IS NOT NULL AND value_numeric>usl))) "
                "ORDER BY run_id,unit_sequence"
            )
            points_statement = _statement(points_sql, point_expanding)
            point_rows = (
                connection.execute(
                    points_statement,
                    {**point_params, "stride": stride},
                )
                .mappings()
                .all()
            )
        parameter_options = []
        for name in sorted(
            common_names,
            key=lambda item: int(resolved_signatures[item][1]),
        ):
            signature = resolved_signatures[name]
            parameter_options.append(
                FtParameterOption(
                    name=name,
                    unit=signature[3],
                    lsl=signature[4],
                    usl=signature[5],
                    test_condition=signature[6],
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
