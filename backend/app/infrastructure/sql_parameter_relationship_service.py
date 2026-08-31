from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from itertools import combinations_with_replacement
from threading import Event, Lock
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsCounts,
    AnalyticsDatasetContext,
    AnalyticsFilterSummary,
    AnalyticsNormalizedFilters,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
)
from app.domain.parameter_relationship import (
    ParameterCorrelationResult,
    ParameterRelationshipAnalysis,
    ParameterRelationshipGroupBy,
    ParameterRelationshipIdentity,
    ParameterRelationshipItem,
    ParameterRelationshipRequest,
    ParameterRelationshipResult,
    ParameterScatterPoint,
    ParameterTrendPoint,
)
from app.infrastructure.formal_spec_resolver import (
    FormalSpecResolution,
    resolve_released_formal_spec,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService

_CONTRACT_VERSION = "PARAMETER_RELATIONSHIP_V1"
_SAMPLING_METHOD = "DETERMINISTIC_SCOPE_STRIDE_PRESERVE_FORMAL_SPEC_OOS_V2"
_MULTI_SAMPLING_METHOD = "DETERMINISTIC_SCOPE_HASH_STRIDE_PRESERVE_FORMAL_SPEC_OOS_V1"
_PEARSON_METHOD = "PEARSON_PAIRWISE_V1"
_TREND_ORDER_BASIS = (
    "DATASET_ORDINAL_THEN_RUN_SOURCE_TIME_THEN_RUN_ID_THEN_UNIT_SEQUENCE_THEN_UNIT_ID"
)


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _finite_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID", f"{field} is not numeric", 409
        ) from exc
    if not math.isfinite(result):
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID", f"{field} is not finite", 409
        )
    return result


def _iso_datetime(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat()
    return str(value)


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
    if not isinstance(decoded, dict) or not set(decoded).issubset(
        {"text", "bias1", "bias2"}
    ):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has unsupported test-condition metadata",
            409,
        )
    normalized: dict[str, str] = {}
    for key in ("text", "bias1", "bias2"):
        raw = decoded.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise DomainError(
                "ANALYSIS_SPEC_CONTRACT_INVALID",
                f"parameter {parameter} has invalid test-condition metadata",
                409,
            )
        compact = " ".join(raw.split())
        if compact:
            normalized[key] = compact
    if not normalized:
        return None
    if set(normalized) == {"text"}:
        return normalized["text"]
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _source_identity(row: Mapping[str, Any]) -> str:
    metadata: Mapping[str, Any] = {}
    try:
        decoded = json.loads(row.get("metadata_json") or "{}")
        if isinstance(decoded, dict):
            metadata = decoded
    except (TypeError, ValueError):
        metadata = {}
    explicit = str(metadata.get("source_id") or "").strip()
    return explicit or f"RUN-{int(row['run_id'])}"


def _normalized_filters(
    request: ParameterRelationshipRequest,
) -> AnalyticsNormalizedFilters:
    filters = request.filters
    return AnalyticsNormalizedFilters(
        lot_ids=tuple(sorted(filters.lot_ids)),
        wafer_ids=tuple(sorted(filters.wafer_ids)),
        bin_codes=tuple(sorted(filters.bin_codes)),
        overall_results=tuple(sorted(item.value for item in filters.overall_results)),
        source_ids=tuple(sorted(filters.source_ids)),
        tester_ids=tuple(sorted(filters.tester_ids)),
        program_versions=tuple(sorted(filters.program_versions)),
        test_conditions=tuple(sorted(filters.test_conditions)),
    )


def _filter_summary(request: ParameterRelationshipRequest) -> AnalyticsFilterSummary:
    normalized = _normalized_filters(request)
    filter_hash = hashlib.sha256(
        json.dumps(
            asdict(normalized),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    parameters = tuple(sorted(request.parameters))
    context_hash = hashlib.sha256(
        json.dumps(
            {
                "datasets": sorted(
                    (item.dataset_id, item.version_no) for item in request.datasets
                ),
                "filter_hash": filter_hash,
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return AnalyticsFilterSummary(
        normalized_filters=normalized,
        parameters=parameters,
        filter_hash=filter_hash,
        context_hash=context_hash,
    )


@dataclass(frozen=True, slots=True)
class _ResolvedIdentity:
    identity: ParameterRelationshipIdentity
    signature: tuple[object, ...]
    test_item_ids: tuple[int, ...]
    test_item_program_ids: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _DatasetWork:
    dataset_ordinal: int
    context: Mapping[str, Any]
    source_rows: tuple[Mapping[str, Any], ...]
    identities: dict[str, _ResolvedIdentity]
    formal_specs: dict[str, FormalSpecResolution]
    filter_sql: str
    filter_parameters: dict[str, object]
    expanding: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PointScope:
    kind: str
    work: _DatasetWork
    parameter: str
    y_parameter: str | None
    candidate_count: int
    out_of_spec_count: int

    @property
    def in_spec_count(self) -> int:
        return self.candidate_count - self.out_of_spec_count


@dataclass(frozen=True, slots=True)
class _TrendCandidate:
    work: _DatasetWork
    group_key: str
    parameter: str
    row: Mapping[str, Any]
    value: float
    out_of_spec: bool


@dataclass(slots=True)
class _RelationshipFlight:
    completed: Event
    result: ParameterRelationshipResult | None = None
    error: BaseException | None = None


_RELATIONSHIP_FLIGHT_LOCK = Lock()
_RELATIONSHIP_FLIGHTS: dict[tuple[type, int, str], _RelationshipFlight] = {}


def _raise_relationship_flight_error(error: BaseException) -> None:
    """Raise a fresh exception so concurrent waiters never share traceback state."""

    if isinstance(error, DomainError):
        raise DomainError(
            error.code,
            error.message,
            error.status_code,
            details=deepcopy(error.details),
        ) from None
    if not isinstance(error, Exception):
        raise RuntimeError(  # noqa: TRY004 - never replay process-control exceptions
            "coalesced relationship analysis was interrupted"
        ) from None
    try:
        cloned = type(error)(*error.args)
    except Exception as exc:  # pragma: no cover - defensive for exotic exceptions
        raise RuntimeError("coalesced relationship analysis failed") from exc
    raise cloned from None


def _allocate_budgets(scopes: list[_PointScope], budget: int) -> list[int]:
    allocations = [0] * len(scopes)
    remaining = budget
    active = [index for index, scope in enumerate(scopes) if scope.in_spec_count]
    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        next_active: list[int] = []
        for index in active:
            capacity = scopes[index].in_spec_count - allocations[index]
            assigned = min(capacity, share, remaining)
            allocations[index] += assigned
            remaining -= assigned
            if allocations[index] < scopes[index].in_spec_count:
                next_active.append(index)
            if remaining == 0:
                break
        active = next_active
    return allocations


def _correlation(
    *,
    count: int,
    sum_x: float,
    sum_y: float,
    sum_x2: float,
    sum_y2: float,
    sum_xy: float,
    minimum_sample_size: int = 2,
) -> tuple[float | None, str, str | None]:
    if count < minimum_sample_size:
        return None, "NOT_ELIGIBLE", "CORRELATION_INSUFFICIENT_PAIRS"
    x_term = count * sum_x2 - sum_x * sum_x
    y_term = count * sum_y2 - sum_y * sum_y
    if x_term <= 0 or y_term <= 0:
        return None, "NOT_ELIGIBLE", "CORRELATION_ZERO_VARIANCE"
    value = (count * sum_xy - sum_x * sum_y) / math.sqrt(x_term * y_term)
    if not math.isfinite(value):
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID",
            "correlation calculation produced a non-finite result",
            409,
        )
    return max(-1.0, min(1.0, value)), "ELIGIBLE", None


class SqlParameterRelationshipService:
    """Read-only, exact-identity relationship analysis for formal Datasets."""

    def __init__(
        self,
        engine: Engine,
        *,
        rule_service: SqlAnalysisRuleService | None = None,
    ) -> None:
        self._engine = engine
        self._rules = rule_service or SqlAnalysisRuleService(engine)

    def _resolve_correlation_rule(
        self,
        request: ParameterRelationshipRequest,
        contexts: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any] | None:
        if ParameterRelationshipAnalysis.CORRELATION not in set(request.analyses):
            return None
        method = request.correlation.method
        rule_code = request.correlation.rule_code
        version_code = request.correlation.version_code
        if method is None or rule_code is None or version_code is None:
            raise DomainError(
                "ANALYSIS_RULE_VERSION_REQUIRED",
                "correlation requires an explicit method, rule_code and version_code",
                409,
            )
        if method.value != _PEARSON_METHOD:
            raise DomainError(
                "ANALYSIS_RULE_VERSION_REQUIRED",
                "correlation method is not supported by this implementation",
                409,
            )
        resolved: dict[str, Any] | None = None
        for context in contexts:
            for parameter in request.parameters:
                current = self._rules.approved_rule_parameters(
                    rule_code=rule_code,
                    version_code=version_code,
                    test_stage=str(context["test_stage"]),
                    expected_algorithm_code=method.value,
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
                if resolved is None:
                    resolved = dict(current)
                elif resolved != dict(current):
                    raise DomainError(
                        "ANALYSIS_RULE_CONTRACT_INVALID",
                        "one exact correlation rule resolved to inconsistent parameters",
                        409,
                    )
        if resolved is None:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "the requested correlation rule has no approved scope",
                409,
            )
        if (
            resolved.get("missing_value_policy") != "PAIRWISE_EXCLUDE_AND_COUNT"
            or resolved.get("retest_policy") != "EACH_ATTEMPT"
            or resolved.get("outlier_policy") != "MARK_ONLY"
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "correlation rule requires pairwise exclusion, each attempt and mark-only outliers",
                409,
            )
        minimum = resolved.get("minimum_sample_size")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 2:
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "correlation rule is missing a valid minimum_sample_size",
                409,
            )
        return resolved

    @staticmethod
    def _context_rows(
        connection: Connection, request: ParameterRelationshipRequest
    ) -> tuple[Mapping[str, Any], ...]:
        rows: list[Mapping[str, Any]] = []
        for reference in request.datasets:
            row = (
                connection.execute(
                    text(
                        "/* REL_CONTEXT */ SELECT dv.dataset_version_id,dv.dataset_id,"
                        "dv.version_no,dv.status,dv.is_current,dv.spec_set_id,dv.input_batch_id,"
                        "d.dataset_name,d.test_stage,d.supplier_id,d.product_id,p.product_name,"
                        "ss.version_code AS spec_version "
                        "FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                        "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                        "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                        "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    ),
                    {
                        "dataset": reference.dataset_id,
                        "version": reference.version_no,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            if str(row["status"]) != "PUBLISHED" or not bool(row["is_current"]):
                raise DomainError(
                    "ANALYSIS_VERSION_NOT_CURRENT",
                    "relationship analysis only accepts Current Published versions",
                    409,
                )
            rows.append(row)
        return SqlParameterRelationshipService._validate_context_rows(tuple(rows))

    @staticmethod
    def _validate_context_rows(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[Mapping[str, Any], ...]:
        stages = {str(row["test_stage"]) for row in rows}
        if len(stages) != 1 or not stages.issubset({"CP", "FT"}):
            raise DomainError(
                "ANALYSIS_STAGE_INCOMPATIBLE",
                "one relationship request must contain only CP or only FT datasets",
                409,
            )
        if len(rows) > 1 and next(iter(stages)) == "CP":
            spec_ids = {row["spec_set_id"] for row in rows}
            if None in spec_ids or len(spec_ids) != 1:
                raise DomainError(
                    "ANALYSIS_SPEC_INCOMPATIBLE",
                    "selected CP datasets do not have one proven compatible Spec",
                    409,
                )
        return rows

    @staticmethod
    def _multi_context_identity_rows(
        connection: Connection, request: ParameterRelationshipRequest
    ) -> tuple[
        tuple[Mapping[str, Any], ...],
        tuple[tuple[Mapping[str, Any], ...], ...],
    ]:
        """Resolve eight unfiltered Dataset contexts and identities in one read."""

        requested_scope: list[str] = []
        parameters: dict[str, object] = {
            "relationship_parameters": request.parameters,
        }
        for ordinal, reference in enumerate(request.datasets, start=1):
            parameters[f"multi_ordinal_{ordinal}"] = ordinal
            parameters[f"multi_dataset_{ordinal}"] = int(reference.dataset_id)
            parameters[f"multi_version_{ordinal}"] = int(reference.version_no)
            requested_scope.append(
                f"SELECT :multi_ordinal_{ordinal} AS ordinal_no,"
                f":multi_dataset_{ordinal} AS dataset_id,"
                f":multi_version_{ordinal} AS version_no"
            )
        rows = tuple(
            connection.execute(
                _statement(
                    ";WITH /* REL_MULTI_CONTEXT_IDENTITIES */ requested_scope AS ("
                    + " UNION ALL ".join(requested_scope)
                    + "), contexts AS (SELECT rs.ordinal_no,dv.dataset_version_id,"
                    "dv.dataset_id,dv.version_no,dv.status,dv.is_current,"
                    "dv.spec_set_id,dv.input_batch_id,d.dataset_name,d.test_stage,"
                    "d.supplier_id,d.product_id,p.product_name,"
                    "ss.version_code AS spec_version FROM requested_scope rs "
                    "LEFT JOIN dataset.dataset_version dv ON dv.dataset_id=rs.dataset_id "
                    "AND dv.version_no=rs.version_no "
                    "LEFT JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                    "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id),"
                    "identity_rows AS (SELECT rs.ordinal_no,tr.run_id,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.test_item_id,tid.program_version_id,tid.step_code,"
                    "tid.sequence_no,tid.raw_item_name,"
                    "tid.canonical_parameter_code,tid.unit_code,"
                    "tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM requested_scope rs JOIN dataset.dataset_version dv "
                    "ON dv.dataset_id=rs.dataset_id AND dv.version_no=rs.version_no "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "AND tid.is_analysis_parameter=1 "
                    "AND tid.raw_item_name IN :relationship_parameters) "
                    "SELECT c.ordinal_no,c.dataset_version_id,c.dataset_id,"
                    "c.version_no,c.status,c.is_current,c.spec_set_id,"
                    "c.input_batch_id,c.dataset_name,c.test_stage,c.supplier_id,"
                    "c.product_id,c.product_name,c.spec_version,i.run_id,"
                    "i.run_program_version_id,i.test_item_id,i.program_version_id,"
                    "i.step_code,i.sequence_no,i.raw_item_name,"
                    "i.canonical_parameter_code,i.unit_code,i.program_lsl,"
                    "i.program_usl,i.condition_json FROM contexts c "
                    "LEFT JOIN identity_rows i ON i.ordinal_no=c.ordinal_no "
                    "ORDER BY c.ordinal_no,i.run_id,i.raw_item_name",
                    ("relationship_parameters",),
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
            "status",
            "is_current",
            "spec_set_id",
            "input_batch_id",
            "dataset_name",
            "test_stage",
            "supplier_id",
            "product_id",
            "product_name",
            "spec_version",
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
        contexts: dict[int, Mapping[str, Any]] = {}
        identities: dict[int, list[Mapping[str, Any]]] = {
            ordinal: [] for ordinal in range(1, len(request.datasets) + 1)
        }
        for row in rows:
            ordinal = int(row["ordinal_no"])
            if ordinal not in identities:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset identity query returned an unknown scope",
                    409,
                )
            context = {field: row[field] for field in context_fields}
            previous = contexts.setdefault(ordinal, context)
            if previous != context:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset identity query returned conflicting contexts",
                    409,
                )
            if row["run_id"] is not None:
                identities[ordinal].append(
                    {field: row[field] for field in identity_fields}
                )

        ordered_contexts: list[Mapping[str, Any]] = []
        ordered_identities: list[tuple[Mapping[str, Any], ...]] = []
        for ordinal in range(1, len(request.datasets) + 1):
            context = contexts.get(ordinal)
            if context is None or context["dataset_version_id"] is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            if str(context["status"]) != "PUBLISHED" or not bool(context["is_current"]):
                raise DomainError(
                    "ANALYSIS_VERSION_NOT_CURRENT",
                    "relationship analysis only accepts Current Published versions",
                    409,
                )
            ordered_contexts.append(context)
            ordered_identities.append(tuple(identities[ordinal]))
        return (
            SqlParameterRelationshipService._validate_context_rows(
                tuple(ordered_contexts)
            ),
            tuple(ordered_identities),
        )

    @staticmethod
    def _dataset_context(
        rows: tuple[Mapping[str, Any], ...],
    ) -> AnalyticsDatasetContext:
        return AnalyticsDatasetContext(
            resolved_datasets=tuple(
                AnalyticsResolvedDataset(
                    dataset_id=int(row["dataset_id"]),
                    version_no=int(row["version_no"]),
                    dataset_name=str(row["dataset_name"]),
                    test_stage=str(row["test_stage"]),
                    product_name=(
                        str(row["product_name"])
                        if row["product_name"] is not None
                        else None
                    ),
                )
                for row in rows
            ),
            test_stage=str(rows[0]["test_stage"]),
            current_published_verified=True,
        )

    @staticmethod
    def _source_rows(
        connection: Connection, context: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                text(
                    "/* REL_SOURCE */ SELECT DISTINCT tr.run_id,tr.metadata_json,"
                    "tr.tester_id,tr.program_version_id,"
                    "pv.version_code AS program_version "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_program_version pv "
                    "ON pv.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                },
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _selected_run_ids(
        request: ParameterRelationshipRequest,
        source_rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[int, ...] | None:
        if not request.filters.source_ids:
            return None
        selected = set(request.filters.source_ids)
        return tuple(
            sorted(
                int(row["run_id"])
                for row in source_rows
                if _source_identity(row) in selected
            )
        )

    @staticmethod
    def _condition_item_ids(
        connection: Connection,
        context: Mapping[str, Any],
        request: ParameterRelationshipRequest,
    ) -> tuple[int, ...] | None:
        if not request.filters.test_conditions:
            return None
        rows = (
            connection.execute(
                text(
                    "/* REL_CONDITION_ITEMS */ SELECT DISTINCT tid.test_item_id,"
                    "tid.raw_item_name,tid.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version "
                    "AND tid.is_analysis_parameter=1"
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                },
            )
            .mappings()
            .all()
        )
        selected = set(request.filters.test_conditions)
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

    @staticmethod
    def _run_filter_sql(
        request: ParameterRelationshipRequest,
        *,
        source_run_ids: tuple[int, ...] | None,
    ) -> tuple[str, dict[str, object], tuple[str, ...]]:
        clauses: list[str] = []
        parameters: dict[str, object] = {}
        expanding: list[str] = []
        values = (
            (
                "identity_lot_ids",
                tuple(request.filters.lot_ids),
                "tr.lot_id IN :identity_lot_ids",
            ),
            (
                "identity_tester_ids",
                tuple(request.filters.tester_ids),
                "tr.tester_id IN :identity_tester_ids",
            ),
            (
                "identity_program_versions",
                tuple(request.filters.program_versions),
                "pv.version_code IN :identity_program_versions",
            ),
        )
        for name, selected, clause in values:
            if selected:
                clauses.append(clause)
                parameters[name] = selected
                expanding.append(name)
        if source_run_ids is not None:
            if source_run_ids:
                clauses.append("tr.run_id IN :identity_source_run_ids")
                parameters["identity_source_run_ids"] = source_run_ids
                expanding.append("identity_source_run_ids")
            else:
                clauses.append("1=0")
        return (
            " AND " + " AND ".join(clauses) if clauses else "",
            parameters,
            tuple(expanding),
        )

    def _identity_rows(
        self,
        connection: Connection,
        context: Mapping[str, Any],
        request: ParameterRelationshipRequest,
        *,
        source_run_ids: tuple[int, ...] | None,
    ) -> tuple[Mapping[str, Any], ...]:
        run_filter, run_parameters, run_expanding = self._run_filter_sql(
            request, source_run_ids=source_run_ids
        )
        return tuple(
            connection.execute(
                _statement(
                    "/* REL_IDENTITY */ SELECT DISTINCT tr.run_id,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.test_item_id,tid.program_version_id,tid.step_code,"
                    "tid.sequence_no,tid.raw_item_name,"
                    "tid.canonical_parameter_code,tid.unit_code,"
                    "tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_program_version pv "
                    "ON pv.program_version_id=tr.program_version_id "
                    "LEFT JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "AND tid.is_analysis_parameter=1 "
                    "AND tid.raw_item_name IN :relationship_parameters "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + run_filter
                    + " ORDER BY tr.run_id,tid.raw_item_name",
                    ("relationship_parameters", *run_expanding),
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    "relationship_parameters": request.parameters,
                    **run_parameters,
                },
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _resolve_identities(
        rows: tuple[Mapping[str, Any], ...],
        context: Mapping[str, Any],
        parameters: tuple[str, ...],
    ) -> dict[str, _ResolvedIdentity]:
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        available_by_program: dict[int, set[str]] = {}
        selected = set(parameters)
        for row in rows:
            run_program = row["run_program_version_id"]
            if run_program is None:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "selected run has no program-version identity",
                    409,
                )
            program_id = int(run_program)
            available_by_program.setdefault(program_id, set())
            if row["raw_item_name"] is None:
                continue
            name = str(row["raw_item_name"])
            if name in selected:
                grouped[name].append(row)
                available_by_program[program_id].add(name)
        missing = sorted(
            name
            for name in parameters
            if name not in grouped
            or any(name not in names for names in available_by_program.values())
        )
        if missing:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "one or more relationship parameters are unavailable in every selected program",
                409,
                details=[
                    {
                        "dataset_id": int(context["dataset_id"]),
                        "version_no": int(context["version_no"]),
                        "parameters": missing,
                    }
                ],
            )
        resolved: dict[str, _ResolvedIdentity] = {}
        for name in parameters:
            parameter_rows = grouped[name]
            definitions_by_program: dict[int, set[tuple[str, int]]] = defaultdict(set)
            for row in parameter_rows:
                step = str(row["step_code"] or "").strip().upper()
                if not step or row["sequence_no"] is None:
                    raise DomainError(
                        "ANALYSIS_PARAMETER_INCOMPATIBLE",
                        f"parameter {name} has incomplete step identity",
                        409,
                    )
                definitions_by_program[int(row["program_version_id"])].add(
                    (step, int(row["sequence_no"]))
                )
            steps = {
                item[0]
                for definitions in definitions_by_program.values()
                for item in definitions
            }
            sequences = {
                item[1]
                for definitions in definitions_by_program.values()
                for item in definitions
            }
            canonical = {
                str(row["canonical_parameter_code"]).strip() or None
                if row["canonical_parameter_code"] is not None
                else None
                for row in parameter_rows
            }
            identity_contexts = {
                (
                    str(row["unit_code"]).strip() or None
                    if row["unit_code"] is not None
                    else None,
                    _condition_text(row["condition_json"], parameter=name),
                )
                for row in parameter_rows
            }
            program_limits = {
                (
                    _finite_float(row["program_lsl"], field=f"{name} program LSL"),
                    _finite_float(row["program_usl"], field=f"{name} program USL"),
                )
                for row in parameter_rows
            }
            if (
                any(len(items) != 1 for items in definitions_by_program.values())
                or len(steps) != 1
                or len(sequences) != 1
                or len(canonical) != 1
                or len(identity_contexts) != 1
            ):
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    f"parameter {name} has ambiguous exact identity",
                    409,
                )
            ids = tuple(sorted({int(row["test_item_id"]) for row in parameter_rows}))
            if not ids:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    f"parameter {name} has no exact test_item_id",
                    409,
                )
            item_programs = tuple(
                sorted(
                    {
                        (int(row["test_item_id"]), int(row["program_version_id"]))
                        for row in parameter_rows
                    }
                )
            )
            if len({item_id for item_id, _ in item_programs}) != len(item_programs):
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    f"parameter {name} has one test_item_id in multiple programs",
                    409,
                )
            step = next(iter(steps))
            sequence = next(iter(sequences))
            canonical_code = next(iter(canonical))
            unit, condition = next(iter(identity_contexts))
            lsl, usl = (
                next(iter(program_limits)) if len(program_limits) == 1 else (None, None)
            )
            signature = (
                step,
                sequence,
                canonical_code,
                unit,
                condition,
            )
            resolved[name] = _ResolvedIdentity(
                identity=ParameterRelationshipIdentity(
                    name=name,
                    canonical_parameter_code=canonical_code,
                    step_code=step,
                    sequence_no=sequence,
                    unit=unit,
                    program_lsl=lsl,
                    program_usl=usl,
                    test_condition=condition,
                ),
                signature=signature,
                test_item_ids=ids,
                test_item_program_ids=item_programs,
            )
        return resolved

    def _formal_spec_rows(
        self,
        connection: Connection,
        work: _DatasetWork,
        *,
        unit_scope_required: bool,
    ) -> tuple[Mapping[str, Any], ...]:
        stage = str(work.context["test_stage"])
        if stage == "CP":
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
                "AND sp.active=1 "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
                "AND ss.status='RELEASED' "
                "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        if unit_scope_required:
            scope_join = (
                self._base_join() + "JOIN ingestion.processing_run pr "
                "ON pr.processing_run_id=dvr.processing_run_id "
                + "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid "
                "ON tid.test_item_id=m.test_item_id "
                "AND tid.program_version_id=tr.program_version_id "
            )
            measurement_scope = " AND m.test_item_id IN :selected_test_item_ids "
            marker = "/* REL_FORMAL_SPEC */ "
        else:
            scope_join = (
                " FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN ingestion.processing_run pr "
                "ON pr.processing_run_id=dvr.processing_run_id "
                "JOIN test.test_run tr "
                "ON tr.processing_run_id=dvr.processing_run_id "
                "LEFT JOIN mdm.test_program_version pv "
                "ON pv.program_version_id=tr.program_version_id "
                "JOIN mdm.test_item_definition tid "
                "ON tid.program_version_id=tr.program_version_id "
                "AND tid.is_analysis_parameter=1 "
                "AND tid.test_item_id IN :selected_test_item_ids "
            )
            measurement_scope = (
                " AND EXISTS(SELECT 1 FROM test.unit_result spec_ur "
                "JOIN test.measurement spec_m ON spec_m.unit_id=spec_ur.unit_id "
                "AND spec_m.test_item_id=tid.test_item_id "
                "WHERE spec_ur.run_id=tr.run_id) "
            )
            marker = "/* REL_FORMAL_SPEC_SEEK */ "
        return tuple(
            connection.execute(
                _statement(
                    marker
                    + "SELECT DISTINCT tr.run_id,tr.test_stage,COALESCE(tr.started_at_utc,pr.started_at_utc) AS event_at_utc,"
                    "tr.program_version_id AS run_program_version_id,"
                    "tid.program_version_id AS item_program_version_id,"
                    "tid.test_item_id,tr.lot_id,tid.raw_item_name,"
                    "sb.spec_binding_id,sp.priority AS scope_priority,ss.spec_set_id,ss.version_code,si.spec_item_id,"
                    "si.unit_code,si.lsl,si.usl,si.lower_operator,si.upper_operator,si.condition_json "
                    + scope_join
                    + spec_joins
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + measurement_scope
                    + "ORDER BY tid.raw_item_name,tr.run_id,tr.lot_id,tid.test_item_id,"
                    "ss.spec_set_id",
                    (*work.expanding, "selected_test_item_ids"),
                ),
                {
                    "dataset": int(work.context["dataset_id"]),
                    "version": int(work.context["version_no"]),
                    "dataset_spec_set_id": work.context.get("spec_set_id"),
                    "selected_test_item_ids": self._all_test_item_ids(work.identities),
                    **work.filter_parameters,
                },
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _filter_sql(
        request: ParameterRelationshipRequest,
        *,
        source_run_ids: tuple[int, ...] | None,
        condition_item_ids: tuple[int, ...] | None,
    ) -> tuple[str, dict[str, object], tuple[str, ...]]:
        filters = request.filters
        clauses: list[str] = []
        parameters: dict[str, object] = {}
        expanding: list[str] = []
        values = (
            ("lot_ids", tuple(filters.lot_ids), "tr.lot_id IN :lot_ids"),
            (
                "wafer_ids",
                tuple(filters.wafer_ids),
                "COALESCE(ur.wafer_id,tr.wafer_id) IN :wafer_ids",
            ),
            (
                "bin_codes",
                tuple(filters.bin_codes),
                "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') IN :bin_codes",
            ),
            (
                "overall_results",
                tuple(item.value for item in filters.overall_results),
                "ur.overall_result IN :overall_results",
            ),
            ("tester_ids", tuple(filters.tester_ids), "tr.tester_id IN :tester_ids"),
            (
                "program_versions",
                tuple(filters.program_versions),
                "pv.version_code IN :program_versions",
            ),
        )
        for name, selected, clause in values:
            if selected:
                clauses.append(clause)
                parameters[name] = selected
                expanding.append(name)
        if source_run_ids is not None:
            if source_run_ids:
                clauses.append("tr.run_id IN :source_run_ids")
                parameters["source_run_ids"] = source_run_ids
                expanding.append("source_run_ids")
            else:
                clauses.append("1=0")
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

    @staticmethod
    def _base_join() -> str:
        return (
            " FROM dataset.dataset_version dv "
            "JOIN dataset.dataset_version_run dvr "
            "ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr "
            "ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            "LEFT JOIN mdm.test_program_version pv "
            "ON pv.program_version_id=tr.program_version_id "
        )

    @staticmethod
    def _all_test_item_ids(identities: dict[str, _ResolvedIdentity]) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    item_id
                    for identity in identities.values()
                    for item_id in identity.test_item_ids
                }
            )
        )

    def _counts(
        self, connection: Connection, work: _DatasetWork
    ) -> tuple[int, int, int, int, int, int, int]:
        context = work.context
        selected_ids = self._all_test_item_ids(work.identities)
        row = (
            connection.execute(
                _statement(
                    ";WITH filtered_units AS (SELECT ur.unit_id,ur.overall_result"
                    + self._base_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + ") /* REL_COUNTS */ SELECT "
                    "(SELECT COUNT_BIG(*)"
                    + self._base_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version) "
                    "AS input_units,COUNT_BIG(*) AS included_units,"
                    "SUM(CASE WHEN fu.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                    "SUM(CASE WHEN fu.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                    "SUM(CASE WHEN fu.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                    "SUM(CASE WHEN fu.overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count,"
                    "(SELECT COUNT_BIG(*) FROM filtered_units selected_fu "
                    "JOIN test.measurement selected_m "
                    "ON selected_m.unit_id=selected_fu.unit_id "
                    "WHERE selected_m.test_item_id IN :selected_test_item_ids) "
                    "AS selected_measurements,"
                    "(SELECT COUNT_BIG(*) FROM filtered_units missing_fu "
                    "JOIN test.measurement missing_m "
                    "ON missing_m.unit_id=missing_fu.unit_id "
                    "WHERE missing_m.test_item_id IN :selected_test_item_ids "
                    "AND missing_m.value_numeric IS NULL) AS null_measurements "
                    "FROM filtered_units fu",
                    (*work.expanding, "selected_test_item_ids"),
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    "selected_test_item_ids": selected_ids,
                    **work.filter_parameters,
                },
            )
            .mappings()
            .one()
        )
        input_units = int(row["input_units"] or 0)
        included_units = int(row["included_units"] or 0)
        counts = tuple(
            int(row[name] or 0)
            for name in ("pass_count", "fail_count", "unknown_count", "abort_count")
        )
        if sum(counts) != included_units:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "relationship result counts do not reconcile to included units",
                409,
            )
        selected_measurements = int(row["selected_measurements"] or 0)
        null_measurements = int(row["null_measurements"] or 0)
        expected = included_units * len(work.identities)
        missing = expected - selected_measurements + null_measurements
        if (
            input_units < included_units
            or selected_measurements < 0
            or selected_measurements > expected
            or null_measurements < 0
            or null_measurements > selected_measurements
            or missing < 0
        ):
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "relationship measurement counts are invalid",
                409,
            )
        return input_units, included_units, *counts, missing

    def _multi_counts_and_duplicate(
        self,
        connection: Connection,
        works: list[_DatasetWork],
    ) -> dict[int, tuple[int, int, int, int, int, int, int]]:
        """Reconcile eight unfiltered scopes and guard duplicates in one read."""

        scope_rows: list[str] = []
        item_rows: list[str] = []
        parameters: dict[str, object] = {}
        for work in works:
            ordinal = work.dataset_ordinal
            parameters[f"multi_count_ordinal_{ordinal}"] = ordinal
            parameters[f"multi_count_dataset_version_{ordinal}"] = int(
                work.context["dataset_version_id"]
            )
            parameters[f"multi_count_dataset_{ordinal}"] = int(
                work.context["dataset_id"]
            )
            parameters[f"multi_count_version_{ordinal}"] = int(
                work.context["version_no"]
            )
            scope_rows.append(
                f"SELECT :multi_count_ordinal_{ordinal} AS ordinal_no,"
                f":multi_count_dataset_version_{ordinal} AS dataset_version_id,"
                f":multi_count_dataset_{ordinal} AS dataset_id,"
                f":multi_count_version_{ordinal} AS version_no"
            )
            for item_index, test_item_id in enumerate(
                self._all_test_item_ids(work.identities), start=1
            ):
                name = f"multi_count_item_{ordinal}_{item_index}"
                parameters[name] = test_item_id
                item_rows.append(
                    f"SELECT :multi_count_ordinal_{ordinal} AS ordinal_no,"
                    f":{name} AS test_item_id"
                )
        if not scope_rows or not item_rows:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "multi-Dataset relationship scope has no exact parameter identity",
                409,
            )
        rows = tuple(
            connection.execute(
                text(
                    ";WITH /* REL_MULTI_COUNTS_DUPLICATE */ requested_scope AS ("
                    + " UNION ALL ".join(scope_rows)
                    + "), selected_items AS ("
                    + " UNION ALL ".join(item_rows)
                    + "), filtered_units AS (SELECT rs.ordinal_no,ur.unit_id,"
                    "ur.overall_result FROM requested_scope rs "
                    "JOIN dataset.dataset_version dv ON dv.dataset_id=rs.dataset_id "
                    "AND dv.version_no=rs.version_no "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id),"
                    "unit_counts AS (SELECT ordinal_no,COUNT_BIG(*) AS input_units,"
                    "SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                    "SUM(CASE WHEN overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                    "SUM(CASE WHEN overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                    "SUM(CASE WHEN overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count "
                    "FROM filtered_units GROUP BY ordinal_no),"
                    "measurement_per_key AS (SELECT fu.ordinal_no,fu.unit_id,"
                    "m.test_item_id,COUNT_BIG(*) AS row_count,"
                    "SUM(CASE WHEN m.value_numeric IS NULL THEN CONVERT(bigint,1) ELSE 0 END) AS null_count "
                    "FROM filtered_units fu JOIN test.measurement m "
                    "ON m.unit_id=fu.unit_id JOIN selected_items si "
                    "ON si.ordinal_no=fu.ordinal_no "
                    "AND si.test_item_id=m.test_item_id "
                    "GROUP BY fu.ordinal_no,fu.unit_id,m.test_item_id),"
                    "measurement_counts AS (SELECT ordinal_no,"
                    "SUM(row_count) AS selected_measurements,"
                    "SUM(null_count) AS null_measurements,"
                    "MAX(CASE WHEN row_count>1 THEN 1 ELSE 0 END) AS has_duplicate "
                    "FROM measurement_per_key GROUP BY ordinal_no) "
                    "SELECT rs.ordinal_no,COALESCE(uc.input_units,0) AS input_units,"
                    "COALESCE(uc.input_units,0) AS included_units,"
                    "COALESCE(uc.pass_count,0) AS pass_count,"
                    "COALESCE(uc.fail_count,0) AS fail_count,"
                    "COALESCE(uc.unknown_count,0) AS unknown_count,"
                    "COALESCE(uc.abort_count,0) AS abort_count,"
                    "COALESCE(mc.selected_measurements,0) AS selected_measurements,"
                    "COALESCE(mc.null_measurements,0) AS null_measurements,"
                    "COALESCE(mc.has_duplicate,0) AS has_duplicate "
                    "FROM requested_scope rs LEFT JOIN unit_counts uc "
                    "ON uc.ordinal_no=rs.ordinal_no LEFT JOIN measurement_counts mc "
                    "ON mc.ordinal_no=rs.ordinal_no ORDER BY rs.ordinal_no"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        by_ordinal: dict[int, tuple[int, int, int, int, int, int, int]] = {}
        works_by_ordinal = {work.dataset_ordinal: work for work in works}
        for row in rows:
            ordinal = int(row["ordinal_no"])
            work = works_by_ordinal.get(ordinal)
            if work is None or ordinal in by_ordinal:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset count query returned an invalid scope",
                    409,
                )
            if bool(row["has_duplicate"]):
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "a unit has duplicate measurements for one exact parameter identity",
                    409,
                )
            input_units = int(row["input_units"] or 0)
            included_units = int(row["included_units"] or 0)
            counts = tuple(
                int(row[name] or 0)
                for name in (
                    "pass_count",
                    "fail_count",
                    "unknown_count",
                    "abort_count",
                )
            )
            selected_measurements = int(row["selected_measurements"] or 0)
            null_measurements = int(row["null_measurements"] or 0)
            expected = included_units * len(work.identities)
            missing = expected - selected_measurements + null_measurements
            if (
                sum(counts) != included_units
                or input_units != included_units
                or selected_measurements < 0
                or selected_measurements > expected
                or null_measurements < 0
                or null_measurements > selected_measurements
                or missing < 0
            ):
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset relationship counts do not reconcile",
                    409,
                )
            by_ordinal[ordinal] = (
                input_units,
                included_units,
                *counts,
                missing,
            )
        if set(by_ordinal) != set(works_by_ordinal):
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "multi-Dataset count query omitted a selected scope",
                409,
            )
        return by_ordinal

    def _assert_no_duplicate_measurements(
        self, connection: Connection, work: _DatasetWork
    ) -> None:
        duplicate = connection.execute(
            _statement(
                "/* REL_DUPLICATE */ SELECT TOP (1) m.unit_id,m.test_item_id,"
                "COUNT_BIG(*) AS duplicate_count"
                + self._base_join()
                + "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                + work.filter_sql
                + " AND m.test_item_id IN :selected_test_item_ids "
                "GROUP BY m.unit_id,m.test_item_id HAVING COUNT_BIG(*)>1",
                (*work.expanding, "selected_test_item_ids"),
            ),
            {
                "dataset": int(work.context["dataset_id"]),
                "version": int(work.context["version_no"]),
                "selected_test_item_ids": self._all_test_item_ids(work.identities),
                **work.filter_parameters,
            },
        ).first()
        if duplicate is not None:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "a unit has duplicate measurements for one exact parameter identity",
                409,
            )

    @staticmethod
    def _point_parameters(
        work: _DatasetWork,
        *,
        x_parameter: str,
        y_parameter: str | None = None,
    ) -> dict[str, object]:
        x_spec = work.formal_specs.get(x_parameter)
        parameters: dict[str, object] = {
            "dataset": int(work.context["dataset_id"]),
            "version": int(work.context["version_no"]),
            "x_test_item_ids": work.identities[x_parameter].test_item_ids,
            "x_lsl": x_spec.lsl if x_spec is not None else None,
            "x_usl": x_spec.usl if x_spec is not None else None,
            "x_lower_operator": x_spec.lower_operator if x_spec is not None else None,
            "x_upper_operator": x_spec.upper_operator if x_spec is not None else None,
            **work.filter_parameters,
        }
        if y_parameter is not None:
            y_spec = work.formal_specs.get(y_parameter)
            parameters["y_test_item_ids"] = work.identities[y_parameter].test_item_ids
            parameters["y_lsl"] = y_spec.lsl if y_spec is not None else None
            parameters["y_usl"] = y_spec.usl if y_spec is not None else None
            parameters["y_lower_operator"] = (
                y_spec.lower_operator if y_spec is not None else None
            )
            parameters["y_upper_operator"] = (
                y_spec.upper_operator if y_spec is not None else None
            )
        return parameters

    @staticmethod
    def _scatter_join() -> str:
        return (
            "JOIN test.measurement mx ON mx.unit_id=ur.unit_id "
            "JOIN mdm.test_item_definition tx ON tx.test_item_id=mx.test_item_id "
            "AND tx.program_version_id=tr.program_version_id "
            "JOIN test.measurement my ON my.unit_id=ur.unit_id "
            "JOIN mdm.test_item_definition ty ON ty.test_item_id=my.test_item_id "
            "AND ty.program_version_id=tr.program_version_id "
        )

    @staticmethod
    def _scatter_candidate_sql() -> str:
        return (
            " AND mx.test_item_id IN :x_test_item_ids "
            "AND my.test_item_id IN :y_test_item_ids "
            "AND mx.value_numeric IS NOT NULL AND my.value_numeric IS NOT NULL"
        )

    @staticmethod
    def _scatter_oos_sql() -> str:
        return (
            "(mx.measurement_status IN ('OVER_RANGE','UNDER_RANGE') "
            "OR my.measurement_status IN ('OVER_RANGE','UNDER_RANGE') "
            "OR (:x_lsl IS NOT NULL AND ((:x_lower_operator='>' AND mx.value_numeric<=:x_lsl) OR (:x_lower_operator='>=' AND mx.value_numeric<:x_lsl))) "
            "OR (:x_usl IS NOT NULL AND ((:x_upper_operator='<' AND mx.value_numeric>=:x_usl) OR (:x_upper_operator='<=' AND mx.value_numeric>:x_usl))) "
            "OR (:y_lsl IS NOT NULL AND ((:y_lower_operator='>' AND my.value_numeric<=:y_lsl) OR (:y_lower_operator='>=' AND my.value_numeric<:y_lsl))) "
            "OR (:y_usl IS NOT NULL AND ((:y_upper_operator='<' AND my.value_numeric>=:y_usl) OR (:y_upper_operator='<=' AND my.value_numeric>:y_usl))))"
        )

    def _scatter_count(
        self,
        connection: Connection,
        work: _DatasetWork,
        x_parameter: str,
        y_parameter: str,
    ) -> tuple[int, int]:
        oos = self._scatter_oos_sql()
        row = (
            connection.execute(
                _statement(
                    "/* REL_SCATTER_COUNT */ SELECT COUNT_BIG(*) AS candidate_count,"
                    f"SUM(CASE WHEN {oos} THEN CONVERT(bigint,1) ELSE 0 END) "
                    "AS out_of_spec_count"
                    + self._base_join()
                    + self._scatter_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + self._scatter_candidate_sql(),
                    (*work.expanding, "x_test_item_ids", "y_test_item_ids"),
                ),
                self._point_parameters(
                    work,
                    x_parameter=x_parameter,
                    y_parameter=y_parameter,
                ),
            )
            .mappings()
            .one()
        )
        return int(row["candidate_count"] or 0), int(row["out_of_spec_count"] or 0)

    def _fetch_single_scatter_scope(
        self,
        connection: Connection,
        work: _DatasetWork,
        *,
        x_parameter: str,
        y_parameter: str,
        max_points: int,
    ) -> tuple[_PointScope, tuple[Mapping[str, Any], ...]]:
        """Count and deterministically sample one Scatter scope in one scan."""

        oos = self._scatter_oos_sql()
        parameters = self._point_parameters(
            work, x_parameter=x_parameter, y_parameter=y_parameter
        )
        rows = tuple(
            connection.execute(
                _statement(
                    ";WITH candidates AS (SELECT "
                    + self._point_select()
                    + ",my.value_numeric AS y_value,my.measurement_status AS y_status,"
                    ":y_lsl AS y_lsl,:y_usl AS y_usl,"
                    ":y_lower_operator AS y_lower_operator,"
                    ":y_upper_operator AS y_upper_operator,"
                    f"CASE WHEN {oos} THEN CONVERT(int,1) ELSE CONVERT(int,0) END "
                    "AS is_out_of_spec "
                    + self._base_join()
                    + self._scatter_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + self._scatter_candidate_sql()
                    + "), ranked AS (SELECT *,COUNT_BIG(*) OVER() AS candidate_count,"
                    "SUM(CONVERT(bigint,is_out_of_spec)) OVER() AS out_of_spec_count,"
                    "ROW_NUMBER() OVER(PARTITION BY is_out_of_spec ORDER BY "
                    "CASE WHEN started_at_utc IS NULL THEN 1 ELSE 0 END,"
                    "started_at_utc,run_id,sequence_no,unit_id) AS class_rank "
                    "FROM candidates) /* REL_SCATTER_COMBINED */ "
                    "SELECT TOP (:combined_fetch_limit) * FROM ranked WHERE "
                    "is_out_of_spec=1 OR (is_out_of_spec=0 "
                    "AND out_of_spec_count<=:combined_max_points "
                    "AND :combined_max_points-out_of_spec_count>0 "
                    "AND (class_rank-1)%CASE "
                    "WHEN :combined_max_points-out_of_spec_count<=0 THEN 1 "
                    "WHEN candidate_count-out_of_spec_count<="
                    ":combined_max_points-out_of_spec_count THEN 1 ELSE "
                    "(candidate_count-out_of_spec_count+"
                    "(:combined_max_points-out_of_spec_count)-1)/"
                    "NULLIF(:combined_max_points-out_of_spec_count,0) END=0) "
                    "ORDER BY is_out_of_spec DESC,class_rank",
                    (*work.expanding, "x_test_item_ids", "y_test_item_ids"),
                ),
                {
                    **parameters,
                    "combined_max_points": max_points,
                    "combined_fetch_limit": max_points + 1,
                },
            )
            .mappings()
            .all()
        )
        scope = _PointScope("SCATTER", work, x_parameter, y_parameter, 0, 0)
        if not rows:
            return scope, ()
        counts = {
            (int(row["candidate_count"] or 0), int(row["out_of_spec_count"] or 0))
            for row in rows
        }
        if len(counts) != 1:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "combined scatter rows returned conflicting preflight counts",
                409,
            )
        candidate_count, out_of_spec_count = next(iter(counts))
        scope = replace(
            scope,
            candidate_count=candidate_count,
            out_of_spec_count=out_of_spec_count,
        )
        if (
            candidate_count < 0
            or out_of_spec_count < 0
            or out_of_spec_count > candidate_count
        ):
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "combined scatter preflight returned invalid counts",
                409,
            )
        if out_of_spec_count > max_points:
            return scope, rows
        returned_oos = sum(bool(row["is_out_of_spec"]) for row in rows)
        in_spec_count = candidate_count - out_of_spec_count
        in_spec_budget = max_points - out_of_spec_count
        stride = (
            max(1, math.ceil(in_spec_count / in_spec_budget)) if in_spec_budget else 1
        )
        expected_in_spec = (
            math.ceil(in_spec_count / stride) if in_spec_budget > 0 else 0
        )
        if (
            returned_oos != out_of_spec_count
            or len(rows) != out_of_spec_count + expected_in_spec
            or len(rows) > max_points
        ):
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "combined scatter rows do not reconcile to deterministic sampling",
                409,
            )
        return scope, rows

    @staticmethod
    def _multi_scatter_scope_parts(
        works: list[_DatasetWork],
        *,
        x_parameter: str,
        y_parameter: str,
        scopes: list[_PointScope] | None = None,
        allocations: list[int] | None = None,
    ) -> tuple[str, str, str, dict[str, object]]:
        scope_rows: list[str] = []
        x_item_rows: list[str] = []
        y_item_rows: list[str] = []
        parameters: dict[str, object] = {}
        scopes_by_ordinal = (
            {scope.work.dataset_ordinal: scope for scope in scopes}
            if scopes is not None
            else {}
        )
        allocation_by_ordinal = (
            {
                scope.work.dataset_ordinal: allocation
                for scope, allocation in zip(
                    scopes or (), allocations or (), strict=True
                )
            }
            if scopes is not None and allocations is not None
            else {}
        )
        for work in works:
            ordinal = work.dataset_ordinal
            x_spec = work.formal_specs[x_parameter]
            y_spec = work.formal_specs[y_parameter]
            values = {
                f"multi_scatter_ordinal_{ordinal}": ordinal,
                f"multi_scatter_dataset_version_{ordinal}": int(
                    work.context["dataset_version_id"]
                ),
                f"multi_scatter_dataset_{ordinal}": int(work.context["dataset_id"]),
                f"multi_scatter_version_{ordinal}": int(work.context["version_no"]),
                f"multi_scatter_x_lsl_{ordinal}": x_spec.lsl,
                f"multi_scatter_x_usl_{ordinal}": x_spec.usl,
                f"multi_scatter_x_lower_{ordinal}": x_spec.lower_operator,
                f"multi_scatter_x_upper_{ordinal}": x_spec.upper_operator,
                f"multi_scatter_y_lsl_{ordinal}": y_spec.lsl,
                f"multi_scatter_y_usl_{ordinal}": y_spec.usl,
                f"multi_scatter_y_lower_{ordinal}": y_spec.lower_operator,
                f"multi_scatter_y_upper_{ordinal}": y_spec.upper_operator,
            }
            parameters.update(values)
            columns = (
                f"SELECT :multi_scatter_ordinal_{ordinal} AS ordinal_no,"
                f":multi_scatter_dataset_version_{ordinal} AS dataset_version_id,"
                f":multi_scatter_dataset_{ordinal} AS dataset_id,"
                f":multi_scatter_version_{ordinal} AS version_no,"
                f":multi_scatter_x_lsl_{ordinal} AS x_lsl,"
                f":multi_scatter_x_usl_{ordinal} AS x_usl,"
                f":multi_scatter_x_lower_{ordinal} AS x_lower_operator,"
                f":multi_scatter_x_upper_{ordinal} AS x_upper_operator,"
                f":multi_scatter_y_lsl_{ordinal} AS y_lsl,"
                f":multi_scatter_y_usl_{ordinal} AS y_usl,"
                f":multi_scatter_y_lower_{ordinal} AS y_lower_operator,"
                f":multi_scatter_y_upper_{ordinal} AS y_upper_operator"
            )
            if scopes is not None and allocations is not None:
                scope = scopes_by_ordinal[ordinal]
                budget = allocation_by_ordinal[ordinal]
                stride = (
                    max(1, math.ceil(scope.in_spec_count / budget)) if budget else 1
                )
                parameters[f"multi_scatter_budget_{ordinal}"] = budget
                parameters[f"multi_scatter_stride_{ordinal}"] = stride
                columns += (
                    f",:multi_scatter_budget_{ordinal} AS in_spec_budget,"
                    f":multi_scatter_stride_{ordinal} AS sample_stride"
                )
            scope_rows.append(columns)
            for item_index, (test_item_id, program_version_id) in enumerate(
                work.identities[x_parameter].test_item_program_ids, start=1
            ):
                name = f"multi_scatter_x_item_{ordinal}_{item_index}"
                program_name = f"multi_scatter_x_program_{ordinal}_{item_index}"
                parameters[name] = test_item_id
                parameters[program_name] = program_version_id
                x_item_rows.append(
                    f"SELECT :multi_scatter_ordinal_{ordinal} AS ordinal_no,"
                    f":{name} AS test_item_id,:{program_name} AS program_version_id"
                )
            for item_index, (test_item_id, program_version_id) in enumerate(
                work.identities[y_parameter].test_item_program_ids, start=1
            ):
                name = f"multi_scatter_y_item_{ordinal}_{item_index}"
                program_name = f"multi_scatter_y_program_{ordinal}_{item_index}"
                parameters[name] = test_item_id
                parameters[program_name] = program_version_id
                y_item_rows.append(
                    f"SELECT :multi_scatter_ordinal_{ordinal} AS ordinal_no,"
                    f":{name} AS test_item_id,:{program_name} AS program_version_id"
                )
        return (
            " UNION ALL ".join(scope_rows),
            " UNION ALL ".join(x_item_rows),
            " UNION ALL ".join(y_item_rows),
            parameters,
        )

    @staticmethod
    def _multi_scatter_oos_sql() -> str:
        return (
            "(mx.measurement_status IN ('OVER_RANGE','UNDER_RANGE') "
            "OR my.measurement_status IN ('OVER_RANGE','UNDER_RANGE') "
            "OR (rs.x_lsl IS NOT NULL AND ((rs.x_lower_operator='>' AND mx.value_numeric<=rs.x_lsl) OR (rs.x_lower_operator='>=' AND mx.value_numeric<rs.x_lsl))) "
            "OR (rs.x_usl IS NOT NULL AND ((rs.x_upper_operator='<' AND mx.value_numeric>=rs.x_usl) OR (rs.x_upper_operator='<=' AND mx.value_numeric>rs.x_usl))) "
            "OR (rs.y_lsl IS NOT NULL AND ((rs.y_lower_operator='>' AND my.value_numeric<=rs.y_lsl) OR (rs.y_lower_operator='>=' AND my.value_numeric<rs.y_lsl))) "
            "OR (rs.y_usl IS NOT NULL AND ((rs.y_upper_operator='<' AND my.value_numeric>=rs.y_usl) OR (rs.y_upper_operator='<=' AND my.value_numeric>rs.y_usl))))"
        )

    @staticmethod
    def _multi_scatter_join_sql(*, include_program: bool = False) -> str:
        return (
            " FROM requested_scope rs JOIN dataset.dataset_version dv "
            "ON dv.dataset_id=rs.dataset_id AND dv.version_no=rs.version_no "
            "JOIN dataset.dataset_version_run dvr "
            "ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            + (
                "LEFT JOIN mdm.test_program_version pv "
                "ON pv.program_version_id=tr.program_version_id "
                if include_program
                else ""
            )
            + "JOIN x_items xi ON xi.ordinal_no=rs.ordinal_no "
            "AND xi.program_version_id=tr.program_version_id "
            "JOIN test.measurement mx ON mx.unit_id=ur.unit_id "
            "AND mx.test_item_id=xi.test_item_id "
            "JOIN y_items yi ON yi.ordinal_no=rs.ordinal_no "
            "AND yi.program_version_id=tr.program_version_id "
            "JOIN test.measurement my ON my.unit_id=ur.unit_id "
            "AND my.test_item_id=yi.test_item_id "
            "WHERE mx.value_numeric IS NOT NULL AND my.value_numeric IS NOT NULL"
        )

    def _multi_scatter_counts_fast(
        self,
        connection: Connection,
        works: list[_DatasetWork],
        *,
        x_parameter: str,
        y_parameter: str,
    ) -> list[_PointScope]:
        """Preflight exact Scatter pairs without the SQL Server 2014 pivot plan."""

        scope_sql, x_items_sql, y_items_sql, parameters = (
            self._multi_scatter_scope_parts(
                works, x_parameter=x_parameter, y_parameter=y_parameter
            )
        )
        oos = self._multi_scatter_oos_sql()
        rows = tuple(
            connection.execute(
                text(
                    ";WITH /* REL_SCATTER_MULTI_COUNT */ requested_scope AS ("
                    + scope_sql
                    + "), x_items AS ("
                    + x_items_sql
                    + "), y_items AS ("
                    + y_items_sql
                    + ") SELECT rs.ordinal_no,COUNT_BIG(*) AS candidate_count,"
                    f"SUM(CASE WHEN {oos} THEN CONVERT(bigint,1) ELSE 0 END) "
                    "AS out_of_spec_count"
                    + self._multi_scatter_join_sql()
                    + " GROUP BY rs.ordinal_no ORDER BY rs.ordinal_no"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        by_ordinal: dict[int, tuple[int, int]] = {}
        valid_ordinals = {work.dataset_ordinal for work in works}
        for row in rows:
            ordinal = int(row["ordinal_no"])
            if ordinal not in valid_ordinals or ordinal in by_ordinal:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset scatter preflight returned an invalid scope",
                    409,
                )
            by_ordinal[ordinal] = (
                int(row["candidate_count"] or 0),
                int(row["out_of_spec_count"] or 0),
            )
        return [
            _PointScope(
                "SCATTER",
                work,
                x_parameter,
                y_parameter,
                *by_ordinal.get(work.dataset_ordinal, (0, 0)),
            )
            for work in works
        ]

    def _fetch_multi_scatter_rows(
        self,
        connection: Connection,
        scopes: list[_PointScope],
        allocations: list[int],
        *,
        group_by: ParameterRelationshipGroupBy,
    ) -> dict[int, tuple[Mapping[str, Any], ...]]:
        works = [scope.work for scope in scopes]
        x_parameter = scopes[0].parameter
        y_parameter = str(scopes[0].y_parameter)
        scope_sql, x_items_sql, y_items_sql, parameters = (
            self._multi_scatter_scope_parts(
                works,
                x_parameter=x_parameter,
                y_parameter=y_parameter,
                scopes=scopes,
                allocations=allocations,
            )
        )
        oos = self._multi_scatter_oos_sql()
        narrow_projection = group_by in {
            ParameterRelationshipGroupBy.DATASET,
            ParameterRelationshipGroupBy.TEST_BATCH,
            ParameterRelationshipGroupBy.CONDITION,
        }
        include_program = group_by == ParameterRelationshipGroupBy.PROGRAM
        point_context = (
            "tr.run_id,tr.started_at_utc,ur.unit_id,"
            "COALESCE(ur.unit_sequence,ur.unit_id) AS sequence_no,"
            if narrow_projection
            else (
                "tr.run_id,tr.started_at_utc,tr.metadata_json,tr.tester_id,"
                "tr.program_version_id,tr.lot_id,"
                "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                + (
                    "pv.version_code AS program_version,"
                    if include_program
                    else "CONVERT(nvarchar(64),NULL) AS program_version,"
                )
                + "ur.unit_id,ur.unit_sequence AS source_sequence,"
                "COALESCE(ur.unit_sequence,ur.unit_id) AS sequence_no,"
            )
        )
        rows = tuple(
            connection.execute(
                text(
                    ";WITH requested_scope AS ("
                    + scope_sql
                    + "), x_items AS ("
                    + x_items_sql
                    + "), y_items AS ("
                    + y_items_sql
                    + "), candidates AS (SELECT rs.ordinal_no AS dataset_ordinal,"
                    + point_context
                    + "mx.value_numeric AS x_value,mx.measurement_status AS x_status,"
                    "rs.x_lsl,rs.x_usl,rs.x_lower_operator,rs.x_upper_operator,"
                    "my.value_numeric AS y_value,my.measurement_status AS y_status,"
                    "rs.y_lsl,rs.y_usl,rs.y_lower_operator,rs.y_upper_operator,"
                    "rs.in_spec_budget,rs.sample_stride,"
                    f"CASE WHEN {oos} THEN CONVERT(int,1) ELSE CONVERT(int,0) END AS is_out_of_spec"
                    + self._multi_scatter_join_sql(include_program=include_program)
                    + "), selected AS (SELECT * FROM candidates WHERE "
                    "is_out_of_spec=1 OR (is_out_of_spec=0 AND in_spec_budget>0 "
                    "AND ABS(CONVERT(bigint,CHECKSUM(run_id,sequence_no,unit_id)))"
                    "%sample_stride=0)), ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY "
                    "dataset_ordinal,is_out_of_spec ORDER BY "
                    "CASE WHEN started_at_utc IS NULL THEN 1 ELSE 0 END,"
                    "started_at_utc,run_id,sequence_no,unit_id) AS class_rank "
                    "FROM selected) /* REL_SCATTER_MULTI_FETCH */ SELECT * "
                    "FROM ranked WHERE is_out_of_spec=1 OR (is_out_of_spec=0 "
                    "AND class_rank<=in_spec_budget) "
                    "ORDER BY dataset_ordinal,is_out_of_spec DESC,class_rank"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        returned: dict[int, list[Mapping[str, Any]]] = {
            scope.work.dataset_ordinal: [] for scope in scopes
        }
        for row in rows:
            ordinal = int(row["dataset_ordinal"])
            if ordinal not in returned:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset scatter fetch returned an unknown scope",
                    409,
                )
            returned[ordinal].append(row)
        for scope, budget in zip(scopes, allocations, strict=True):
            scope_rows = returned[scope.work.dataset_ordinal]
            returned_oos = sum(bool(row["is_out_of_spec"]) for row in scope_rows)
            returned_in_spec = len(scope_rows) - returned_oos
            if (
                returned_oos != scope.out_of_spec_count
                or returned_in_spec < 0
                or returned_in_spec > budget
                or (scope.in_spec_count > 0 and budget > 0 and returned_in_spec == 0)
            ):
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "multi-Dataset scatter rows do not reconcile to deterministic sampling",
                    409,
                )
        return {ordinal: tuple(scope_rows) for ordinal, scope_rows in returned.items()}

    @staticmethod
    def _trend_join() -> str:
        return (
            "JOIN test.measurement mx ON mx.unit_id=ur.unit_id "
            "JOIN mdm.test_item_definition tx ON tx.test_item_id=mx.test_item_id "
            "AND tx.program_version_id=tr.program_version_id "
        )

    @staticmethod
    def _trend_oos_sql() -> str:
        return (
            "(mx.measurement_status IN ('OVER_RANGE','UNDER_RANGE') "
            "OR (:x_lsl IS NOT NULL AND ((:x_lower_operator='>' AND mx.value_numeric<=:x_lsl) OR (:x_lower_operator='>=' AND mx.value_numeric<:x_lsl))) "
            "OR (:x_usl IS NOT NULL AND ((:x_upper_operator='<' AND mx.value_numeric>=:x_usl) OR (:x_upper_operator='<=' AND mx.value_numeric>:x_usl))))"
        )

    def _trend_count(
        self, connection: Connection, work: _DatasetWork, parameter: str
    ) -> tuple[int, int]:
        oos = self._trend_oos_sql()
        row = (
            connection.execute(
                _statement(
                    "/* REL_TREND_COUNT */ SELECT COUNT_BIG(*) AS candidate_count,"
                    f"SUM(CASE WHEN {oos} THEN CONVERT(bigint,1) ELSE 0 END) "
                    "AS out_of_spec_count"
                    + self._base_join()
                    + self._trend_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + " AND mx.test_item_id IN :x_test_item_ids "
                    "AND mx.value_numeric IS NOT NULL",
                    (*work.expanding, "x_test_item_ids"),
                ),
                self._point_parameters(work, x_parameter=parameter),
            )
            .mappings()
            .one()
        )
        return int(row["candidate_count"] or 0), int(row["out_of_spec_count"] or 0)

    @staticmethod
    def _point_select() -> str:
        return (
            "tr.run_id,tr.started_at_utc,tr.metadata_json,tr.tester_id,"
            "tr.program_version_id,tr.lot_id,"
            "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
            "pv.version_code AS program_version,ur.unit_id,ur.unit_sequence AS source_sequence,"
            "COALESCE(ur.unit_sequence,ur.unit_id) AS sequence_no,"
            "mx.value_numeric AS x_value,mx.measurement_status AS x_status,"
            ":x_lsl AS x_lsl,:x_usl AS x_usl,"
            ":x_lower_operator AS x_lower_operator,:x_upper_operator AS x_upper_operator"
        )

    def _fetch_scatter_rows(
        self,
        connection: Connection,
        scope: _PointScope,
        *,
        in_spec_budget: int,
    ) -> tuple[Mapping[str, Any], ...]:
        work = scope.work
        y_parameter = str(scope.y_parameter)
        base_parameters = self._point_parameters(
            work, x_parameter=scope.parameter, y_parameter=y_parameter
        )
        expanding = (*work.expanding, "x_test_item_ids", "y_test_item_ids")
        oos = self._scatter_oos_sql()
        common = (
            self._base_join()
            + self._scatter_join()
            + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
            + work.filter_sql
            + self._scatter_candidate_sql()
        )
        oos_rows: tuple[Mapping[str, Any], ...] = ()
        if scope.out_of_spec_count:
            oos_rows = tuple(
                connection.execute(
                    _statement(
                        "/* REL_SCATTER_OOS */ SELECT "
                        + self._point_select()
                        + ",my.value_numeric AS y_value,my.measurement_status AS y_status,"
                        ":y_lsl AS y_lsl,:y_usl AS y_usl,"
                        ":y_lower_operator AS y_lower_operator,:y_upper_operator AS y_upper_operator "
                        + common
                        + f" AND {oos} ORDER BY "
                        "CASE WHEN tr.started_at_utc IS NULL THEN 1 ELSE 0 END,"
                        "tr.started_at_utc,tr.run_id,sequence_no,ur.unit_id",
                        expanding,
                    ),
                    base_parameters,
                )
                .mappings()
                .all()
            )
        if len(oos_rows) != scope.out_of_spec_count:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "scatter out-of-spec rows do not reconcile to the preflight count",
                409,
            )
        if in_spec_budget <= 0:
            return oos_rows
        stride = max(1, math.ceil(scope.in_spec_count / in_spec_budget))
        sample_rows = tuple(
            connection.execute(
                _statement(
                    ";WITH candidates AS (SELECT "
                    + self._point_select()
                    + ",my.value_numeric AS y_value,my.measurement_status AS y_status,"
                    ":y_lsl AS y_lsl,:y_usl AS y_usl,"
                    ":y_lower_operator AS y_lower_operator,:y_upper_operator AS y_upper_operator "
                    + common
                    + f" AND NOT {oos}),ranked AS (SELECT *,ROW_NUMBER() OVER("
                    "ORDER BY CASE WHEN started_at_utc IS NULL THEN 1 ELSE 0 END,"
                    "started_at_utc,run_id,sequence_no,unit_id) AS sample_rn FROM candidates) "
                    "/* REL_SCATTER_SAMPLE */ SELECT TOP (:sample_budget) * FROM ranked "
                    "WHERE (sample_rn-1)%:sample_stride=0 ORDER BY sample_rn",
                    expanding,
                ),
                {
                    **base_parameters,
                    "sample_budget": in_spec_budget,
                    "sample_stride": stride,
                },
            )
            .mappings()
            .all()
        )
        if len(sample_rows) > in_spec_budget:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "scatter sample exceeded its deterministic budget",
                409,
            )
        return (*oos_rows, *sample_rows)

    def _fetch_trend_rows(
        self,
        connection: Connection,
        scope: _PointScope,
        *,
        in_spec_budget: int,
    ) -> tuple[Mapping[str, Any], ...]:
        work = scope.work
        base_parameters = self._point_parameters(work, x_parameter=scope.parameter)
        expanding = (*work.expanding, "x_test_item_ids")
        oos = self._trend_oos_sql()
        common = (
            self._base_join()
            + self._trend_join()
            + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
            + work.filter_sql
            + " AND mx.test_item_id IN :x_test_item_ids "
            "AND mx.value_numeric IS NOT NULL"
        )
        oos_rows = tuple(
            connection.execute(
                _statement(
                    "/* REL_TREND_OOS */ SELECT "
                    + self._point_select()
                    + " "
                    + common
                    + f" AND {oos} ORDER BY "
                    "CASE WHEN tr.started_at_utc IS NULL THEN 1 ELSE 0 END,"
                    "tr.started_at_utc,tr.run_id,sequence_no,ur.unit_id",
                    expanding,
                ),
                base_parameters,
            )
            .mappings()
            .all()
        )
        if len(oos_rows) != scope.out_of_spec_count:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "trend out-of-spec rows do not reconcile to the preflight count",
                409,
            )
        if in_spec_budget <= 0:
            return oos_rows
        stride = max(1, math.ceil(scope.in_spec_count / in_spec_budget))
        sample_rows = tuple(
            connection.execute(
                _statement(
                    ";WITH candidates AS (SELECT "
                    + self._point_select()
                    + " "
                    + common
                    + f" AND NOT {oos}),ranked AS (SELECT *,ROW_NUMBER() OVER("
                    "ORDER BY CASE WHEN started_at_utc IS NULL THEN 1 ELSE 0 END,"
                    "started_at_utc,run_id,sequence_no,unit_id) AS sample_rn FROM candidates) "
                    "/* REL_TREND_SAMPLE */ SELECT TOP (:sample_budget) * FROM ranked "
                    "WHERE (sample_rn-1)%:sample_stride=0 ORDER BY sample_rn",
                    expanding,
                ),
                {
                    **base_parameters,
                    "sample_budget": in_spec_budget,
                    "sample_stride": stride,
                },
            )
            .mappings()
            .all()
        )
        if len(sample_rows) > in_spec_budget:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "trend sample exceeded its deterministic budget",
                409,
            )
        return (*oos_rows, *sample_rows)

    @staticmethod
    def _is_oos(
        *,
        value: float,
        status: object,
        lsl: object,
        usl: object,
        lower_operator: object,
        upper_operator: object,
        field: str,
    ) -> bool:
        lower = _finite_float(lsl, field=f"{field} LSL")
        upper = _finite_float(usl, field=f"{field} USL")
        lower_op = str(lower_operator or ">=")
        upper_op = str(upper_operator or "<=")
        return (
            str(status) in {"OVER_RANGE", "UNDER_RANGE"}
            or (
                lower is not None
                and (value <= lower if lower_op == ">" else value < lower)
            )
            or (
                upper is not None
                and (value >= upper if upper_op == "<" else value > upper)
            )
        )

    @staticmethod
    def _group_key(
        request: ParameterRelationshipRequest,
        work: _DatasetWork,
        row: Mapping[str, Any],
        *,
        parameter: str,
        y_parameter: str | None,
    ) -> str:
        dataset_id = int(work.context["dataset_id"])
        version_no = int(work.context["version_no"])
        if request.group_by == ParameterRelationshipGroupBy.DATASET:
            return f"DATASET:{dataset_id}:V{version_no}"
        if request.group_by == ParameterRelationshipGroupBy.TEST_BATCH:
            batch_id = work.context.get("input_batch_id")
            if batch_id is None:
                raise DomainError(
                    "ANALYSIS_GROUP_DIMENSION_UNAVAILABLE",
                    "selected Dataset has no test-batch identity",
                    409,
                )
            return f"TEST_BATCH:{int(batch_id)}"
        if request.group_by == ParameterRelationshipGroupBy.LOT:
            lot_id = str(row.get("lot_id") or "").strip()
            if not lot_id:
                raise DomainError(
                    "ANALYSIS_GROUP_DIMENSION_UNAVAILABLE",
                    "selected relationship row has no Lot identity",
                    409,
                )
            return f"LOT:{lot_id}"
        if request.group_by == ParameterRelationshipGroupBy.WAFER:
            lot_id = str(row.get("lot_id") or "").strip()
            wafer_id = str(row.get("wafer_id") or "").strip()
            if not lot_id or not wafer_id:
                raise DomainError(
                    "ANALYSIS_GROUP_DIMENSION_UNAVAILABLE",
                    "Wafer grouping requires both Lot and Wafer identity",
                    409,
                )
            return f"WAFER:{lot_id}:{wafer_id}"
        if request.group_by == ParameterRelationshipGroupBy.SOURCE:
            return f"SOURCE:{_source_identity(row)}"
        if request.group_by == ParameterRelationshipGroupBy.TESTER:
            return f"TESTER:{row.get('tester_id') or 'UNASSIGNED'!s}"
        if request.group_by == ParameterRelationshipGroupBy.PROGRAM:
            value = row.get("program_version") or f"ID-{int(row['program_version_id'])}"
            return f"PROGRAM:{value}"
        parts = (
            f"{name}={work.identities[name].identity.test_condition or 'UNSPECIFIED'}"
            for name in request.parameters
        )
        return "CONDITION:" + "|".join(parts)

    def _correlation_rows(
        self,
        connection: Connection,
        work: _DatasetWork,
        x_parameter: str,
        y_parameter: str,
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                _statement(
                    "/* REL_CORRELATION */ SELECT tr.run_id,tr.metadata_json,"
                    "tr.tester_id,tr.program_version_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "pv.version_code AS program_version,COUNT_BIG(*) AS pair_count,"
                    "SUM(CONVERT(float,mx.value_numeric)) AS sum_x,"
                    "SUM(CONVERT(float,my.value_numeric)) AS sum_y,"
                    "SUM(CONVERT(float,mx.value_numeric)*CONVERT(float,mx.value_numeric)) AS sum_x2,"
                    "SUM(CONVERT(float,my.value_numeric)*CONVERT(float,my.value_numeric)) AS sum_y2,"
                    "SUM(CONVERT(float,mx.value_numeric)*CONVERT(float,my.value_numeric)) AS sum_xy"
                    + self._base_join()
                    + self._scatter_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql
                    + self._scatter_candidate_sql()
                    + " GROUP BY tr.run_id,tr.metadata_json,tr.tester_id,"
                    "tr.program_version_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id),pv.version_code ORDER BY tr.run_id",
                    (*work.expanding, "x_test_item_ids", "y_test_item_ids"),
                ),
                self._point_parameters(
                    work,
                    x_parameter=x_parameter,
                    y_parameter=y_parameter,
                ),
            )
            .mappings()
            .all()
        )

    def _correlation_group_rows(
        self,
        connection: Connection,
        work: _DatasetWork,
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                _statement(
                    "/* REL_CORRELATION_GROUPS */ SELECT DISTINCT "
                    "tr.run_id,tr.metadata_json,tr.tester_id,tr.program_version_id,"
                    "tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "pv.version_code AS program_version"
                    + self._base_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + work.filter_sql,
                    work.expanding,
                ),
                {
                    "dataset": int(work.context["dataset_id"]),
                    "version": int(work.context["version_no"]),
                    **work.filter_parameters,
                },
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _multi_scatter_batch_eligible(request: ParameterRelationshipRequest) -> bool:
        return (
            set(request.analyses) == {ParameterRelationshipAnalysis.SCATTER}
            and len(request.datasets) == 8
            and len(request.y_parameters) == 1
            and all(
                not value
                for value in (
                    request.filters.lot_ids,
                    request.filters.wafer_ids,
                    request.filters.bin_codes,
                    request.filters.overall_results,
                    request.filters.source_ids,
                    request.filters.tester_ids,
                    request.filters.program_versions,
                    request.filters.test_conditions,
                )
            )
        )

    @staticmethod
    def _scatter_singleflight_eligible(
        request: ParameterRelationshipRequest,
    ) -> bool:
        return set(request.analyses) == {ParameterRelationshipAnalysis.SCATTER}

    def relationship(
        self, request: ParameterRelationshipRequest
    ) -> ParameterRelationshipResult:
        """Coalesce only concurrent identical pure-Scatter read-only requests."""

        if not self._scatter_singleflight_eligible(request):
            return self._relationship_uncached(request)
        request_key = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        key = (type(self), id(self._engine), request_key)
        with _RELATIONSHIP_FLIGHT_LOCK:
            flight = _RELATIONSHIP_FLIGHTS.get(key)
            owner = flight is None
            if flight is None:
                flight = _RelationshipFlight(Event())
                _RELATIONSHIP_FLIGHTS[key] = flight
        if not owner:
            flight.completed.wait()
            if flight.error is not None:
                _raise_relationship_flight_error(flight.error)
            if flight.result is None:
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "coalesced relationship request completed without a result",
                    409,
                )
            return flight.result
        try:
            flight.result = self._relationship_uncached(request)
            return flight.result
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with _RELATIONSHIP_FLIGHT_LOCK:
                if _RELATIONSHIP_FLIGHTS.get(key) is flight:
                    del _RELATIONSHIP_FLIGHTS[key]
                flight.completed.set()

    def _relationship_uncached(
        self, request: ParameterRelationshipRequest
    ) -> ParameterRelationshipResult:
        analysis_types = set(request.analyses)
        use_multi_scatter_batch = self._multi_scatter_batch_eligible(request)
        formal_spec_required = bool(
            analysis_types
            & {
                ParameterRelationshipAnalysis.SCATTER,
                ParameterRelationshipAnalysis.TREND,
            }
        )
        with self._engine.connect() as connection:
            if use_multi_scatter_batch:
                context_rows, multi_identity_rows = self._multi_context_identity_rows(
                    connection, request
                )
            else:
                context_rows = self._context_rows(connection, request)
                multi_identity_rows = None
            correlation_rule = self._resolve_correlation_rule(request, context_rows)
            all_signatures: dict[str, set[tuple[object, ...]]] = {
                name: set() for name in request.parameters
            }
            works: list[_DatasetWork] = []
            total_counts = [0, 0, 0, 0, 0, 0, 0]
            for dataset_ordinal, context in enumerate(context_rows, start=1):
                source_rows = (
                    self._source_rows(connection, context)
                    if request.filters.source_ids
                    else ()
                )
                source_run_ids = self._selected_run_ids(request, source_rows)
                identity_rows = (
                    multi_identity_rows[dataset_ordinal - 1]
                    if multi_identity_rows is not None
                    else self._identity_rows(
                        connection,
                        context,
                        request,
                        source_run_ids=source_run_ids,
                    )
                )
                identities = self._resolve_identities(
                    identity_rows,
                    context,
                    request.parameters,
                )
                for name, identity in identities.items():
                    all_signatures[name].add(identity.signature)
                condition_item_ids = self._condition_item_ids(
                    connection, context, request
                )
                filter_sql, filter_parameters, expanding = self._filter_sql(
                    request,
                    source_run_ids=source_run_ids,
                    condition_item_ids=condition_item_ids,
                )
                work = _DatasetWork(
                    dataset_ordinal=dataset_ordinal,
                    context=context,
                    source_rows=source_rows,
                    identities=identities,
                    formal_specs={},
                    filter_sql=filter_sql,
                    filter_parameters=filter_parameters,
                    expanding=expanding,
                )
                if formal_spec_required:
                    spec_rows_by_name: dict[str, list[Mapping[str, Any]]] = defaultdict(
                        list
                    )
                    unit_scope_required = bool(
                        request.filters.wafer_ids
                        or request.filters.bin_codes
                        or request.filters.overall_results
                        or request.filters.test_conditions
                    )
                    for row in self._formal_spec_rows(
                        connection,
                        work,
                        unit_scope_required=unit_scope_required,
                    ):
                        spec_rows_by_name[str(row["raw_item_name"])].append(row)
                    formal_specs: dict[str, FormalSpecResolution] = {}
                    resolved_identities: dict[str, _ResolvedIdentity] = {}
                    for name, resolved_identity in identities.items():
                        formal_spec = resolve_released_formal_spec(
                            tuple(spec_rows_by_name.get(name, ())),
                            parameter=name,
                            identity_unit=resolved_identity.identity.unit,
                            identity_condition=resolved_identity.identity.test_condition,
                        )
                        formal_specs[name] = formal_spec
                        resolved_identities[name] = replace(
                            resolved_identity,
                            identity=replace(
                                resolved_identity.identity,
                                formal_lsl=formal_spec.lsl,
                                formal_usl=formal_spec.usl,
                                formal_lower_operator=formal_spec.lower_operator,
                                formal_upper_operator=formal_spec.upper_operator,
                                formal_spec_status=formal_spec.status,
                                formal_spec_reason_codes=formal_spec.reason_codes,
                                formal_spec_versions=formal_spec.spec_versions,
                            ),
                        )
                    work = replace(
                        work,
                        identities=resolved_identities,
                        formal_specs=formal_specs,
                    )
                if not use_multi_scatter_batch:
                    self._assert_no_duplicate_measurements(connection, work)
                    counts = self._counts(connection, work)
                    total_counts = [
                        current + value for current, value in zip(total_counts, counts)
                    ]
                works.append(work)
            incompatible = sorted(
                name
                for name, signatures in all_signatures.items()
                if len(signatures) != 1
            )
            if incompatible:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "relationship parameters have incompatible exact identity",
                    409,
                    details=[{"parameters": incompatible}],
                )
            if formal_spec_required and len(works) > 1:
                incompatible_specs: list[str] = []
                for name in request.parameters:
                    resolutions = [work.formal_specs[name] for work in works]
                    signatures = {
                        (
                            item.unit,
                            item.test_condition,
                            item.lsl,
                            item.usl,
                            item.lower_operator,
                            item.upper_operator,
                        )
                        for item in resolutions
                        if item.resolved
                    }
                    if (
                        any(not item.resolved for item in resolutions)
                        or len(signatures) != 1
                    ):
                        incompatible_specs.append(name)
                if incompatible_specs:
                    raise DomainError(
                        "ANALYSIS_SPEC_INCOMPATIBLE",
                        "relationship parameters do not have one compatible Released formal Spec",
                        409,
                        details=[{"parameters": sorted(incompatible_specs)}],
                    )

            scopes: list[_PointScope] = []
            prefetched_scatter_rows: dict[int, tuple[Mapping[str, Any], ...]] = {}
            use_single_scatter = (
                analysis_types == {ParameterRelationshipAnalysis.SCATTER}
                and len(works) == 1
                and len(request.y_parameters) == 1
            )
            if use_single_scatter:
                scope, rows = self._fetch_single_scatter_scope(
                    connection,
                    works[0],
                    x_parameter=request.x_parameter,
                    y_parameter=request.y_parameters[0],
                    max_points=request.max_points,
                )
                scopes.append(scope)
                prefetched_scatter_rows[0] = rows
            elif use_multi_scatter_batch:

                def load_batched_counts():
                    with self._engine.connect() as batch_connection:
                        return self._multi_counts_and_duplicate(batch_connection, works)

                def load_scatter_counts():
                    with self._engine.connect() as batch_connection:
                        return self._multi_scatter_counts_fast(
                            batch_connection,
                            works,
                            x_parameter=request.x_parameter,
                            y_parameter=request.y_parameters[0],
                        )

                count_context = copy_context()
                scatter_context = copy_context()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    count_future = executor.submit(
                        count_context.run, load_batched_counts
                    )
                    scatter_future = executor.submit(
                        scatter_context.run, load_scatter_counts
                    )
                    batched_counts = count_future.result()
                    scopes.extend(scatter_future.result())
                for work in works:
                    counts = batched_counts[work.dataset_ordinal]
                    total_counts = [
                        current + value for current, value in zip(total_counts, counts)
                    ]
            elif ParameterRelationshipAnalysis.SCATTER in analysis_types:
                for work in works:
                    for y_parameter in request.y_parameters:
                        count, oos = self._scatter_count(
                            connection, work, request.x_parameter, y_parameter
                        )
                        scopes.append(
                            _PointScope(
                                "SCATTER",
                                work,
                                request.x_parameter,
                                y_parameter,
                                count,
                                oos,
                            )
                        )
            if ParameterRelationshipAnalysis.TREND in analysis_types:
                for work in works:
                    for parameter in request.parameters:
                        count, oos = self._trend_count(connection, work, parameter)
                        scopes.append(
                            _PointScope("TREND", work, parameter, None, count, oos)
                        )
            if any(
                scope.candidate_count < 0
                or scope.out_of_spec_count < 0
                or scope.out_of_spec_count > scope.candidate_count
                for scope in scopes
            ):
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "relationship point preflight returned invalid counts",
                    409,
                )
            original_points = sum(scope.candidate_count for scope in scopes)
            out_of_spec_points = sum(scope.out_of_spec_count for scope in scopes)
            if out_of_spec_points > request.max_points:
                raise DomainError(
                    "ANALYSIS_RESULT_TOO_LARGE",
                    "out-of-spec relationship points exceed max_points and cannot be dropped",
                    422,
                    details=[
                        {
                            "max_points": request.max_points,
                            "out_of_spec_points": out_of_spec_points,
                        }
                    ],
                )
            allocations = _allocate_budgets(
                scopes, request.max_points - out_of_spec_points
            )
            if use_multi_scatter_batch:
                batched_scatter_rows = self._fetch_multi_scatter_rows(
                    connection,
                    scopes,
                    allocations,
                    group_by=request.group_by,
                )
                prefetched_scatter_rows.update(
                    {
                        scope_ordinal: batched_scatter_rows[scope.work.dataset_ordinal]
                        for scope_ordinal, scope in enumerate(scopes)
                    }
                )
            scatter_points: list[ParameterScatterPoint] = []
            trend_candidates: list[_TrendCandidate] = []
            for scope_ordinal, (scope, in_spec_budget) in enumerate(
                zip(scopes, allocations, strict=True)
            ):
                if scope.kind == "SCATTER":
                    if scope_ordinal in prefetched_scatter_rows:
                        rows = prefetched_scatter_rows[scope_ordinal]
                    else:
                        rows = self._fetch_scatter_rows(
                            connection, scope, in_spec_budget=in_spec_budget
                        )
                    for row in rows:
                        x_value = _finite_float(row["x_value"], field="scatter X")
                        y_value = _finite_float(row["y_value"], field="scatter Y")
                        if x_value is None or y_value is None:
                            raise DomainError(
                                "ANALYSIS_NUMERIC_CONTRACT_INVALID",
                                "scatter SQL returned a NULL numeric point",
                                409,
                            )
                        y_parameter = str(scope.y_parameter)
                        scatter_points.append(
                            ParameterScatterPoint(
                                dataset_id=int(scope.work.context["dataset_id"]),
                                version_no=int(scope.work.context["version_no"]),
                                group_key=self._group_key(
                                    request,
                                    scope.work,
                                    row,
                                    parameter=request.x_parameter,
                                    y_parameter=y_parameter,
                                ),
                                x_parameter=request.x_parameter,
                                y_parameter=y_parameter,
                                x_value=x_value,
                                y_value=y_value,
                                x_out_of_spec=self._is_oos(
                                    value=x_value,
                                    status=row["x_status"],
                                    lsl=row["x_lsl"],
                                    usl=row["x_usl"],
                                    lower_operator=row["x_lower_operator"],
                                    upper_operator=row["x_upper_operator"],
                                    field="scatter X",
                                ),
                                y_out_of_spec=self._is_oos(
                                    value=y_value,
                                    status=row["y_status"],
                                    lsl=row["y_lsl"],
                                    usl=row["y_usl"],
                                    lower_operator=row["y_lower_operator"],
                                    upper_operator=row["y_upper_operator"],
                                    field="scatter Y",
                                ),
                                drilldown_key=f"UNIT:{int(row['unit_id'])}",
                            )
                        )
                else:
                    rows = self._fetch_trend_rows(
                        connection, scope, in_spec_budget=in_spec_budget
                    )
                    for row in rows:
                        value = _finite_float(row["x_value"], field="trend value")
                        if value is None:
                            raise DomainError(
                                "ANALYSIS_NUMERIC_CONTRACT_INVALID",
                                "trend SQL returned a NULL numeric point",
                                409,
                            )
                        trend_candidates.append(
                            _TrendCandidate(
                                work=scope.work,
                                group_key=self._group_key(
                                    request,
                                    scope.work,
                                    row,
                                    parameter=scope.parameter,
                                    y_parameter=None,
                                ),
                                parameter=scope.parameter,
                                row=row,
                                value=value,
                                out_of_spec=self._is_oos(
                                    value=value,
                                    status=row["x_status"],
                                    lsl=row["x_lsl"],
                                    usl=row["x_usl"],
                                    lower_operator=row["x_lower_operator"],
                                    upper_operator=row["x_upper_operator"],
                                    field="trend value",
                                ),
                            )
                        )

            trend_points: list[ParameterTrendPoint] = []
            trend_series: dict[tuple[str, str], list[_TrendCandidate]] = defaultdict(
                list
            )
            for candidate in trend_candidates:
                trend_series[(candidate.group_key, candidate.parameter)].append(
                    candidate
                )
            for candidates in trend_series.values():
                ordered = sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate.work.dataset_ordinal,
                        candidate.row.get("started_at_utc") is None,
                        _iso_datetime(candidate.row.get("started_at_utc")) or "",
                        int(candidate.row["run_id"]),
                        (
                            int(candidate.row["source_sequence"])
                            if candidate.row.get("source_sequence") is not None
                            else int(candidate.row["unit_id"])
                        ),
                        int(candidate.row["unit_id"]),
                    ),
                )
                for ordinal, candidate in enumerate(ordered, start=1):
                    row = candidate.row
                    trend_points.append(
                        ParameterTrendPoint(
                            dataset_id=int(candidate.work.context["dataset_id"]),
                            version_no=int(candidate.work.context["version_no"]),
                            group_key=candidate.group_key,
                            parameter=candidate.parameter,
                            sequence=int(row["sequence_no"]),
                            ordinal=ordinal,
                            source_sequence=(
                                int(row["source_sequence"])
                                if row.get("source_sequence") is not None
                                else None
                            ),
                            run_id=int(row["run_id"]),
                            ordered_at=_iso_datetime(row.get("started_at_utc")),
                            value=candidate.value,
                            out_of_spec=candidate.out_of_spec,
                            drilldown_key=f"UNIT:{int(row['unit_id'])}",
                        )
                    )

            correlation_results: list[ParameterCorrelationResult] = []
            if ParameterRelationshipAnalysis.CORRELATION in analysis_types:
                accumulators: dict[tuple[int, int, str, str, str], list[float]] = (
                    defaultdict(lambda: [0.0] * 6)
                )
                for work in works:
                    group_rows = self._correlation_group_rows(connection, work)
                    if not group_rows and request.group_by in {
                        ParameterRelationshipGroupBy.DATASET,
                        ParameterRelationshipGroupBy.TEST_BATCH,
                        ParameterRelationshipGroupBy.CONDITION,
                    }:
                        group_rows = ({},)
                    for x_parameter, y_parameter in combinations_with_replacement(
                        request.parameters, 2
                    ):
                        for group_row in group_rows:
                            group_key = self._group_key(
                                request,
                                work,
                                group_row,
                                parameter=x_parameter,
                                y_parameter=y_parameter,
                            )
                            accumulators[
                                (
                                    int(work.context["dataset_id"]),
                                    int(work.context["version_no"]),
                                    group_key,
                                    x_parameter,
                                    y_parameter,
                                )
                            ]
                        for row in self._correlation_rows(
                            connection, work, x_parameter, y_parameter
                        ):
                            group_key = self._group_key(
                                request,
                                work,
                                row,
                                parameter=x_parameter,
                                y_parameter=y_parameter,
                            )
                            key = (
                                int(work.context["dataset_id"]),
                                int(work.context["version_no"]),
                                group_key,
                                x_parameter,
                                y_parameter,
                            )
                            values = (
                                float(int(row["pair_count"] or 0)),
                                _finite_float(row["sum_x"], field="correlation sum_x")
                                or 0.0,
                                _finite_float(row["sum_y"], field="correlation sum_y")
                                or 0.0,
                                _finite_float(row["sum_x2"], field="correlation sum_x2")
                                or 0.0,
                                _finite_float(row["sum_y2"], field="correlation sum_y2")
                                or 0.0,
                                _finite_float(row["sum_xy"], field="correlation sum_xy")
                                or 0.0,
                            )
                            accumulator = accumulators[key]
                            for index, value in enumerate(values):
                                accumulator[index] += value
                method = request.correlation.method
                rule_code = request.correlation.rule_code
                assert method is not None and rule_code is not None
                for key, values in sorted(accumulators.items()):
                    count = int(values[0])
                    coefficient, status, reason = _correlation(
                        count=count,
                        sum_x=values[1],
                        sum_y=values[2],
                        sum_x2=values[3],
                        sum_y2=values[4],
                        sum_xy=values[5],
                        minimum_sample_size=int(
                            correlation_rule["minimum_sample_size"]
                        ),
                    )
                    x_parameter = key[3]
                    y_parameter = key[4]
                    directions = (
                        ((x_parameter, y_parameter),)
                        if x_parameter == y_parameter
                        else (
                            (x_parameter, y_parameter),
                            (y_parameter, x_parameter),
                        )
                    )
                    for matrix_x, matrix_y in directions:
                        correlation_results.append(
                            ParameterCorrelationResult(
                                dataset_id=key[0],
                                version_no=key[1],
                                group_key=key[2],
                                x_parameter=matrix_x,
                                y_parameter=matrix_y,
                                sample_count=count,
                                coefficient=coefficient,
                                status=status,
                                reason_code=reason,
                                method=method.value,
                                rule_code=f"{rule_code}:{request.correlation.version_code}",
                            )
                        )

        returned_points = len(scatter_points) + len(trend_points)
        if original_points <= request.max_points and returned_points != original_points:
            raise DomainError(
                "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                "unsampled relationship points do not reconcile to preflight counts",
                409,
            )
        item_values: dict[
            tuple[int, int, str],
            dict[str, list[Any] | tuple[ParameterRelationshipIdentity, ...]],
        ] = {}
        identities_by_dataset = {
            (int(work.context["dataset_id"]), int(work.context["version_no"])): tuple(
                work.identities[name].identity for name in request.parameters
            )
            for work in works
        }

        def ensure_item(dataset_id: int, version_no: int, group_key: str):
            return item_values.setdefault(
                (dataset_id, version_no, group_key),
                {
                    "identities": identities_by_dataset[(dataset_id, version_no)],
                    "scatter": [],
                    "trend": [],
                    "correlation": [],
                },
            )

        if request.group_by == ParameterRelationshipGroupBy.DATASET:
            for work in works:
                dataset_id = int(work.context["dataset_id"])
                version_no = int(work.context["version_no"])
                ensure_item(
                    dataset_id, version_no, f"DATASET:{dataset_id}:V{version_no}"
                )
        for point in scatter_points:
            ensure_item(point.dataset_id, point.version_no, point.group_key)[
                "scatter"
            ].append(point)  # type: ignore[union-attr]
        for point in trend_points:
            ensure_item(point.dataset_id, point.version_no, point.group_key)[
                "trend"
            ].append(  # type: ignore[union-attr]
                point
            )
        for result in correlation_results:
            ensure_item(result.dataset_id, result.version_no, result.group_key)[
                "correlation"
            ].append(result)  # type: ignore[union-attr]

        items = tuple(
            ParameterRelationshipItem(
                dataset_id=key[0],
                version_no=key[1],
                group_key=key[2],
                identities=value["identities"],  # type: ignore[arg-type]
                scatter_points=tuple(value["scatter"]),  # type: ignore[arg-type]
                trend_points=tuple(value["trend"]),  # type: ignore[arg-type]
                correlations=tuple(value["correlation"]),  # type: ignore[arg-type]
            )
            for key, value in sorted(item_values.items())
        )
        warnings: list[str] = []
        if formal_spec_required:
            warnings.extend(
                f"FORMAL_SPEC_NO_SPEC:{int(work.context['dataset_id'])}:"
                f"V{int(work.context['version_no'])}:{name}:"
                f"{','.join(work.formal_specs[name].reason_codes)}"
                for work in works
                for name in request.parameters
                if not work.formal_specs[name].resolved
            )
        if not returned_points and analysis_types & {
            ParameterRelationshipAnalysis.SCATTER,
            ParameterRelationshipAnalysis.TREND,
        }:
            warnings.append("NO_RELATIONSHIP_POINTS")
        capabilities = []
        if ParameterRelationshipAnalysis.SCATTER in analysis_types:
            capabilities.append(
                AnalyticsCapability(
                    "PARAMETER_SCATTER",
                    "AVAILABLE" if scatter_points else "UNAVAILABLE",
                    None if scatter_points else "ANALYSIS_CAPABILITY_UNAVAILABLE",
                    None if scatter_points else "当前范围没有成对数值点",
                )
            )
        if ParameterRelationshipAnalysis.TREND in analysis_types:
            capabilities.append(
                AnalyticsCapability(
                    "PARAMETER_TREND",
                    "AVAILABLE" if trend_points else "UNAVAILABLE",
                    None if trend_points else "ANALYSIS_CAPABILITY_UNAVAILABLE",
                    None if trend_points else "当前范围没有趋势数值点",
                )
            )
        if ParameterRelationshipAnalysis.CORRELATION in analysis_types:
            capabilities.append(
                AnalyticsCapability(
                    "PARAMETER_CORRELATION",
                    "AVAILABLE" if correlation_results else "UNAVAILABLE",
                    None if correlation_results else "ANALYSIS_CAPABILITY_UNAVAILABLE",
                    None if correlation_results else "当前范围没有相关性成对值",
                )
            )
        input_units, included_units, passed, failed, unknown, aborted, missing = (
            total_counts
        )
        method = request.correlation.method
        rule_code = request.correlation.rule_code
        evaluation_versions = (
            (f"RULE:{rule_code}:{request.correlation.version_code}:{method.value}",)
            if ParameterRelationshipAnalysis.CORRELATION in analysis_types
            and method is not None
            and rule_code is not None
            else ()
        )
        return ParameterRelationshipResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=self._dataset_context(context_rows),
            filter_summary=_filter_summary(request),
            rule_context=AnalyticsRuleContext(
                spec_versions=tuple(
                    sorted(
                        {
                            version
                            for work in works
                            for formal_spec in work.formal_specs.values()
                            for version in formal_spec.spec_versions
                        }
                    )
                ),
                bin_mapping_versions=(),
                evaluation_rule_versions=evaluation_versions,
            ),
            capabilities=tuple(capabilities),
            counts=AnalyticsCounts(
                input_units=input_units,
                included_units=included_units,
                excluded_units=input_units - included_units,
                pass_count=passed,
                fail_count=failed,
                unknown_count=unknown,
                abort_count=aborted,
                known_yield_denominator=passed + failed,
                missing_measurements=missing,
            ),
            sampling_summary=AnalyticsSamplingSummary(
                sampled=returned_points < original_points,
                method=(
                    (
                        _MULTI_SAMPLING_METHOD
                        if use_multi_scatter_batch
                        else _SAMPLING_METHOD
                    )
                    if returned_points < original_points
                    else None
                ),
                original_points=original_points,
                returned_points=returned_points,
                preserved_out_of_spec_points=out_of_spec_points,
            ),
            group_by=request.group_by.value,
            trend_order_basis=_TREND_ORDER_BASIS,
            items=items,
            warnings=tuple(warnings),
            computed_at=datetime.now(UTC).isoformat(),
        )
