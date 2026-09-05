from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from threading import Lock
from time import perf_counter_ns
from typing import Any, Protocol

from sqlalchemy import Engine, bindparam, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsDatasetReference,
    AnalyticsDetailRequest,
    AnalyticsFilters,
    AnalyticsOverviewRequest,
)
from app.domain.datasets import (
    DatasetParameterAnalysisRequest,
    DatasetParameterAnalysisType,
    DatasetReference,
)
from app.domain.parameter_relationship import (
    ParameterCorrelationConfig,
    ParameterCorrelationMethod,
    ParameterRelationshipAnalysis,
    ParameterRelationshipRequest,
)
from app.domain.spatial_analysis import SpatialAnalysisMode, SpatialAnalysisRequest
from app.domain.wafer_summary import WaferSummaryRequest
from app.infrastructure.database import get_engine
from app.infrastructure.formal_spec_resolver import resolve_released_formal_spec
from app.infrastructure.sql_analytics_service import SqlAnalyticsService
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_parameter_relationship_service import (
    SqlParameterRelationshipService,
)
from app.infrastructure.sql_spatial_analysis_service import SqlSpatialAnalysisService
from app.infrastructure.sql_wafer_summary_service import SqlWaferSummaryService

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0028"
DEFAULT_WARMUP = 2
DEFAULT_ITERATIONS = 30
DEFAULT_CONCURRENCY = (1, 5)
MAX_ITERATIONS = 100
MAX_CANDIDATES = 256
LARGE_SCATTER_MAX_POINTS = 10_000
_UNAPPROVED_CORRELATION_RULE_CODE = "PERFORMANCE_UNAPPROVED_CORRELATION"
_UNAPPROVED_CORRELATION_RULE_VERSION = "UNAPPROVED"
_INVALID_CONDITION = object()

_MUTATING_SQL_TOKENS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "UPSERT",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "DENY",
    "EXEC",
    "EXECUTE",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "BULK",
)
_SQL_LITERAL_OR_COMMENT = re.compile(
    r"'(?:''|[^'])*'|--[^\r\n]*(?:\r?\n|$)|/\*.*?\*/",
    flags=re.DOTALL,
)
_CANONICAL_COUNT_FIELDS = (
    "datasets",
    "dataset_versions",
    "current_published_versions",
    "test_runs",
    "unit_results",
    "measurements",
)


class VerificationError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class SqlConnection(Protocol):
    def execute(self, statement: Any, parameters: Any = None) -> Any: ...


class Connectable(Protocol):
    @contextmanager
    def connect(self) -> Iterator[SqlConnection]: ...


@dataclass(slots=True)
class _InvocationSqlCounter:
    statement_count: int = 0


_INVOCATION_SQL_COUNTER: ContextVar[_InvocationSqlCounter | None] = ContextVar(
    "analytics_performance_invocation_sql_counter", default=None
)


@dataclass(slots=True)
class ReadOnlyAudit:
    statement_count: int = 0
    blocked_statement_count: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_statement(self) -> None:
        with self._lock:
            self.statement_count += 1
        counter = _INVOCATION_SQL_COUNTER.get()
        if counter is not None:
            counter.statement_count += 1

    def record_blocked(self) -> None:
        with self._lock:
            self.blocked_statement_count += 1


class _ReadOnlyConnection:
    def __init__(self, connection: Any, audit: ReadOnlyAudit) -> None:
        self._connection = connection
        self._audit = audit

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        try:
            _assert_read_only_sql(str(statement))
        except VerificationError:
            self._audit.record_blocked()
            raise
        self._audit.record_statement()
        if parameters is None:
            return self._connection.execute(statement)
        return self._connection.execute(statement, parameters)


class _ReadOnlyEngine:
    """Narrow SQLAlchemy adapter that permits only SELECT/read-only CTE calls."""

    def __init__(self, engine: Any, audit: ReadOnlyAudit) -> None:
        self._engine = engine
        self._audit = audit

    @contextmanager
    def connect(self) -> Iterator[_ReadOnlyConnection]:
        with self._engine.connect() as connection:
            yield _ReadOnlyConnection(connection, self._audit)


@dataclass(frozen=True, slots=True)
class DatasetCandidate:
    dataset_id: int
    dataset_version_id: int
    version_no: int
    test_stage: str
    spec_set_id: int | None
    unit_count: int
    measurement_count: int
    wafer_count: int
    coordinate_count: int


@dataclass(frozen=True, slots=True)
class ParameterCoverage:
    name: str
    signature: tuple[object, ...]
    minimum_measurement_count: int
    total_measurement_count: int
    spec_versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WaferScope:
    dataset_id: int
    lot_id: str
    wafer_id: str
    unit_count: int


@dataclass(frozen=True, slots=True)
class MultiWaferScope:
    dataset_id: int
    lot_id: str
    wafer_count: int
    unit_count: int


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    name: str
    operation: Callable[[], Any]
    p95_limit_ms: float
    coverage_observed: int
    coverage_required: int
    coverage_reason: str
    cold_limit_ms: float | None = None
    stable_sampling_required: bool = False
    minimum_original_points: int | None = None


@dataclass(frozen=True, slots=True)
class ResponseObservation:
    response_bytes: int
    observed_row_count: int
    returned_record_count: int
    sampling_original_points: int | None
    sampling_returned_points: int | None
    sampling_preserved_out_of_spec_points: int | None
    sampling_digest_sha256: str | None
    expected_gate_code: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationResult:
    elapsed_ms: float
    sql_statement_count: int
    observation: ResponseObservation | None
    error_code: str | None
    exception_type: str | None


def _assert_read_only_sql(sql: str) -> None:
    lexical = _SQL_LITERAL_OR_COMMENT.sub(" ", sql).strip()
    statements = [item.strip() for item in lexical.split(";") if item.strip()]
    if len(statements) != 1:
        raise VerificationError(
            "READ_ONLY_MULTIPLE_STATEMENTS",
            "只读性能验收拒绝空语句或多语句批次",
        )
    statement = statements[0]
    first = re.match(r"[A-Za-z]+", statement)
    if first is None or first.group(0).upper() not in {"SELECT", "WITH"}:
        raise VerificationError(
            "READ_ONLY_STATEMENT_REJECTED",
            "只读性能验收仅允许 SELECT 或只读 CTE",
        )
    for token in _MUTATING_SQL_TOKENS:
        if re.search(rf"\b{token}\b", statement, flags=re.IGNORECASE):
            raise VerificationError(
                "READ_ONLY_MUTATION_REJECTED",
                "只读性能验收检测到被禁止的数据库变更语句",
            )
    if re.search(r"\bSELECT\b[\s\S]*?\bINTO\b", statement, flags=re.IGNORECASE):
        raise VerificationError(
            "READ_ONLY_SELECT_INTO_REJECTED",
            "只读性能验收禁止 SELECT INTO",
        )


def _identity(connection: SqlConnection) -> dict[str, str | int]:
    row = (
        connection.execute(
            text(
                "SELECT DB_NAME() AS database_name,"
                "(SELECT version_num FROM alembic_version) AS schema_revision,"
                "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) "
                "AS product_version,"
                "CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition,"
                "CAST(@@VERSION AS nvarchar(4000)) AS version_banner"
            )
        )
        .mappings()
        .one()
    )
    database = str(row["database_name"] or "")
    revision = str(row["schema_revision"] or "")
    product_version = str(row["product_version"] or "")
    version_banner = str(row["version_banner"] or "")
    engine_edition = int(row["engine_edition"] or 0)
    if database != EXPECTED_DATABASE:
        raise VerificationError(
            "DATABASE_IDENTITY_MISMATCH",
            f"性能验收只允许数据库 {EXPECTED_DATABASE}",
        )
    if revision != EXPECTED_SCHEMA_REVISION:
        raise VerificationError(
            "SCHEMA_REVISION_MISMATCH",
            f"性能验收要求 schema {EXPECTED_SCHEMA_REVISION}",
        )
    if (
        "MICROSOFT SQL SERVER" not in version_banner.upper()
        or not product_version
        or engine_edition <= 0
    ):
        raise VerificationError(
            "DATABASE_ENGINE_MISMATCH",
            "性能验收要求 Microsoft SQL Server",
        )
    product_major = product_version.split(".", maxsplit=1)[0]
    if not product_major.isdigit():
        raise VerificationError(
            "DATABASE_VERSION_INVALID", "SQL Server 产品版本格式无效"
        )
    return {
        "database": database,
        "schema_revision": revision,
        "database_engine": "Microsoft SQL Server",
        "product_major": int(product_major),
        "engine_edition": engine_edition,
    }


def _canonical_counts(connection: SqlConnection) -> dict[str, int]:
    row = (
        connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT_BIG(*) FROM dataset.dataset) AS datasets,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version) "
                "AS dataset_versions,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version "
                " WHERE status='PUBLISHED' AND is_current=1) "
                "AS current_published_versions,"
                "(SELECT COUNT_BIG(*) FROM test.test_run) AS test_runs,"
                "(SELECT COUNT_BIG(*) FROM test.unit_result) AS unit_results,"
                "(SELECT COUNT_BIG(*) FROM test.measurement) AS measurements"
            )
        )
        .mappings()
        .one()
    )
    return {field: int(row[field] or 0) for field in _CANONICAL_COUNT_FIELDS}


def _assert_canonical_counts_unchanged(
    before: Mapping[str, int], after: Mapping[str, int]
) -> None:
    if {key: int(before[key]) for key in _CANONICAL_COUNT_FIELDS} != {
        key: int(after[key]) for key in _CANONICAL_COUNT_FIELDS
    }:
        raise VerificationError(
            "READ_ONLY_CANONICAL_DRIFT",
            "性能验收期间 Canonical 或 Dataset 行数发生变化，结果作废",
        )


def _dataset_candidates(connection: SqlConnection) -> tuple[DatasetCandidate, ...]:
    rows = (
        connection.execute(
            text(
                f"SELECT TOP ({MAX_CANDIDATES}) d.dataset_id,"
                "dv.dataset_version_id,dv.version_no,d.test_stage,dv.spec_set_id,"
                "scope.unit_count,scope.measurement_count,scope.wafer_count,"
                "scope.coordinate_count "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "JOIN ingestion.import_batch b "
                "ON b.import_batch_id=dv.input_batch_id "
                "CROSS APPLY(SELECT COUNT_BIG(DISTINCT ur.unit_id) AS unit_count,"
                "COUNT_BIG(DISTINCT m.measurement_id) AS measurement_count,"
                "COUNT(DISTINCT CASE WHEN COALESCE(ur.wafer_id,tr.wafer_id) "
                "IS NOT NULL THEN COALESCE(ur.wafer_id,tr.wafer_id) END) "
                "AS wafer_count,"
                "COUNT_BIG(DISTINCT CASE WHEN ur.x_coord IS NOT NULL "
                "AND ur.y_coord IS NOT NULL THEN ur.unit_id END) "
                "AS coordinate_count "
                "FROM dataset.dataset_version_run dvr "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "WHERE dvr.dataset_version_id=dv.dataset_version_id) scope "
                "WHERE d.lifecycle_status='ACTIVE' AND dv.status='PUBLISHED' "
                "AND dv.is_current=1 AND b.business_domain='PRODUCTION' "
                "AND scope.unit_count>0 "
                "ORDER BY scope.measurement_count DESC,scope.unit_count DESC,"
                "dv.dataset_version_id ASC"
            )
        )
        .mappings()
        .all()
    )
    candidates: list[DatasetCandidate] = []
    for row in rows:
        stage = str(row["test_stage"])
        if stage not in {"CP", "FT"}:
            continue
        candidates.append(
            DatasetCandidate(
                dataset_id=int(row["dataset_id"]),
                dataset_version_id=int(row["dataset_version_id"]),
                version_no=int(row["version_no"]),
                test_stage=stage,
                spec_set_id=(
                    int(row["spec_set_id"]) if row["spec_set_id"] is not None else None
                ),
                unit_count=int(row["unit_count"] or 0),
                measurement_count=int(row["measurement_count"] or 0),
                wafer_count=int(row["wafer_count"] or 0),
                coordinate_count=int(row["coordinate_count"] or 0),
            )
        )
    return tuple(candidates)


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _normalized_condition(value: object) -> str | None | object:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return _INVALID_CONDITION
    if not isinstance(decoded, dict) or not set(decoded).issubset(
        {"text", "bias1", "bias2"}
    ):
        return _INVALID_CONDITION
    normalized: dict[str, str] = {}
    for key in ("text", "bias1", "bias2"):
        raw = decoded.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            return _INVALID_CONDITION
        compact = " ".join(raw.split())
        if compact:
            normalized[key] = compact
    if not normalized:
        return None
    if set(normalized) == {"text"}:
        return normalized["text"]
    return json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _parameter_coverage(
    connection: SqlConnection, candidates: Sequence[DatasetCandidate]
) -> dict[int, tuple[ParameterCoverage, ...]]:
    if not candidates:
        return {}
    version_ids = tuple(item.dataset_version_id for item in candidates)
    identity_statement = text(
        "SELECT DISTINCT dv.dataset_version_id,"
        "tr.program_version_id AS run_program_version_id,"
        "tid.program_version_id AS item_program_version_id,tid.raw_item_name,"
        "tid.canonical_parameter_code,tid.step_code,tid.sequence_no,"
        "tid.unit_code,tid.condition_json "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "LEFT JOIN mdm.test_item_definition tid "
        "ON tid.program_version_id=tr.program_version_id "
        "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL "
        "WHERE dv.dataset_version_id IN :dataset_version_ids"
    ).bindparams(bindparam("dataset_version_ids", expanding=True))
    identity_rows = (
        connection.execute(identity_statement, {"dataset_version_ids": version_ids})
        .mappings()
        .all()
    )
    expected_programs: dict[int, set[int | None]] = defaultdict(set)
    by_version: dict[int, dict[str, set[tuple[object, ...]]]] = defaultdict(
        lambda: defaultdict(set)
    )
    programs_by_parameter: dict[int, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    invalid_parameters: dict[int, set[str]] = defaultdict(set)
    for row in identity_rows:
        version_id = int(row["dataset_version_id"])
        run_program = row["run_program_version_id"]
        expected_programs[version_id].add(
            int(run_program) if run_program is not None else None
        )
        if row["raw_item_name"] is None or run_program is None:
            continue
        raw_name = str(row["raw_item_name"])
        name = raw_name.strip()
        if not name or raw_name != name or len(name) > 200:
            continue
        step = str(row["step_code"] or "").strip().upper()
        sequence = row["sequence_no"]
        condition = _normalized_condition(row["condition_json"])
        if (
            row["item_program_version_id"] is None
            or int(row["item_program_version_id"]) != int(run_program)
            or not step
            or sequence is None
            or condition is _INVALID_CONDITION
        ):
            invalid_parameters[version_id].add(name)
            continue
        signature = (
            step,
            int(sequence),
            str(row["canonical_parameter_code"] or "").strip() or None,
            str(row["unit_code"] or "").strip() or None,
            condition,
        )
        by_version[version_id][name].add(signature)
        programs_by_parameter[version_id][name].add(int(run_program))

    count_statement = text(
        "SELECT dv.dataset_version_id,tid.raw_item_name,"
        "COUNT_BIG(m.measurement_id) AS measurement_count "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
        "JOIN test.measurement m ON m.unit_id=ur.unit_id "
        "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
        "AND tid.program_version_id=tr.program_version_id "
        "WHERE dv.dataset_version_id IN :dataset_version_ids "
        "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL "
        "GROUP BY dv.dataset_version_id,tid.raw_item_name"
    ).bindparams(bindparam("dataset_version_ids", expanding=True))
    count_rows = (
        connection.execute(count_statement, {"dataset_version_ids": version_ids})
        .mappings()
        .all()
    )
    counts_by_version: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in count_rows:
        if row["raw_item_name"] is None:
            continue
        raw_name = str(row["raw_item_name"])
        name = raw_name.strip()
        if not name or raw_name != name or len(name) > 200:
            continue
        version_id = int(row["dataset_version_id"])
        counts_by_version[version_id][name] += int(row["measurement_count"] or 0)

    result: dict[int, tuple[ParameterCoverage, ...]] = {}
    for candidate in candidates:
        parameters: list[ParameterCoverage] = []
        required_programs = expected_programs[candidate.dataset_version_id]
        if not required_programs or None in required_programs:
            result[candidate.dataset_version_id] = ()
            continue
        for name, signatures in by_version[candidate.dataset_version_id].items():
            if (
                name in invalid_parameters[candidate.dataset_version_id]
                or len(signatures) != 1
                or programs_by_parameter[candidate.dataset_version_id][name]
                != required_programs
            ):
                continue
            count = counts_by_version[candidate.dataset_version_id].get(name, 0)
            if count <= 0:
                continue
            signature = next(iter(signatures))
            parameters.append(
                ParameterCoverage(
                    name=name,
                    signature=signature,
                    minimum_measurement_count=count,
                    total_measurement_count=count,
                )
            )
        result[candidate.dataset_version_id] = tuple(
            sorted(
                parameters,
                key=lambda item: (-item.total_measurement_count, item.name.casefold()),
            )
        )
    return result


def _released_formal_spec_coverage(
    connection: SqlConnection,
    candidates: Sequence[DatasetCandidate],
    parameter_coverage: Mapping[int, Sequence[ParameterCoverage]],
) -> dict[int, tuple[ParameterCoverage, ...]]:
    """Resolve live Released Specs with the relationship-service reducer.

    Spec set/version identities remain evidence, while cross-Dataset compatibility
    is based on unit, condition, limits, and explicit comparison operators.
    """
    if not candidates:
        return {}
    by_stage: dict[str, tuple[int, ...]] = {
        stage: tuple(
            item.dataset_version_id for item in candidates if item.test_stage == stage
        )
        for stage in ("CP", "FT")
    }
    rows: list[Mapping[str, Any]] = []
    common_select = (
        "SELECT DISTINCT dv.dataset_version_id,tr.run_id,tr.test_stage,"
        "COALESCE(tr.started_at_utc,pr.started_at_utc) AS event_at_utc,"
        "tr.program_version_id AS run_program_version_id,"
        "tid.program_version_id AS item_program_version_id,tid.test_item_id,"
        "tr.lot_id,tid.raw_item_name,sb.spec_binding_id,"
        "sp.priority AS scope_priority,ss.spec_set_id,ss.version_code,"
        "si.spec_item_id,si.unit_code,si.lsl,si.usl,si.lower_operator,"
        "si.upper_operator,si.condition_json "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN ingestion.processing_run pr "
        "ON pr.processing_run_id=dvr.processing_run_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "JOIN mdm.test_item_definition tid "
        "ON tid.program_version_id=tr.program_version_id "
        "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL "
    )
    stage_joins = {
        "CP": (
            "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
            "AND ss.status='RELEASED' "
            "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<="
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>"
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
            "AND si.test_item_id=tid.test_item_id "
            "LEFT JOIN mdm.spec_binding sb ON 1=0 "
            "LEFT JOIN mdm.scope_priority sp ON 1=0 "
        ),
        "FT": (
            "LEFT JOIN mdm.spec_binding sb ON "
            "(sb.program_version_id IS NULL "
            "OR sb.program_version_id=tr.program_version_id) "
            "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
            "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
            "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
            "AND (sb.effective_from_utc IS NULL OR sb.effective_from_utc<="
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "AND (sb.effective_to_utc IS NULL OR sb.effective_to_utc>"
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "LEFT JOIN mdm.scope_priority sp ON sp.scope_code=sb.scope_code "
            "AND sp.active=1 "
            "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
            "AND ss.status='RELEASED' "
            "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<="
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>"
            "COALESCE(tr.started_at_utc,pr.started_at_utc)) "
            "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
            "AND si.test_item_id=tid.test_item_id "
        ),
    }
    for stage, version_ids in by_stage.items():
        if not version_ids:
            continue
        statement = text(
            common_select
            + stage_joins[stage]
            + "WHERE dv.dataset_version_id IN :dataset_version_ids "
            + "AND EXISTS(SELECT 1 FROM test.unit_result spec_ur "
            + "JOIN test.measurement spec_m ON spec_m.unit_id=spec_ur.unit_id "
            + "AND spec_m.test_item_id=tid.test_item_id "
            + "WHERE spec_ur.run_id=tr.run_id)"
        ).bindparams(bindparam("dataset_version_ids", expanding=True))
        rows.extend(
            connection.execute(statement, {"dataset_version_ids": version_ids})
            .mappings()
            .all()
        )
    expected_by_version = {
        candidate.dataset_version_id: {
            parameter.name: parameter
            for parameter in parameter_coverage.get(candidate.dataset_version_id, ())
        }
        for candidate in candidates
    }
    grouped: dict[int, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        version_id = int(row["dataset_version_id"])
        raw_name = str(row["raw_item_name"] or "")
        name = raw_name.strip()
        if raw_name != name:
            continue
        if expected_by_version.get(version_id, {}).get(name) is not None:
            grouped[version_id][name].append(row)

    result: dict[int, tuple[ParameterCoverage, ...]] = {}
    for candidate in candidates:
        compatible: list[ParameterCoverage] = []
        expected = expected_by_version[candidate.dataset_version_id]
        for name, identity in expected.items():
            resolution = resolve_released_formal_spec(
                tuple(grouped[candidate.dataset_version_id].get(name, ())),
                parameter=name,
                identity_unit=identity.signature[3],
                identity_condition=identity.signature[4],
            )
            if not resolution.resolved:
                continue
            compatible.append(
                ParameterCoverage(
                    name=name,
                    signature=(
                        resolution.unit,
                        resolution.test_condition,
                        resolution.lsl,
                        resolution.usl,
                        resolution.lower_operator,
                        resolution.upper_operator,
                    ),
                    minimum_measurement_count=identity.minimum_measurement_count,
                    total_measurement_count=identity.total_measurement_count,
                    spec_versions=resolution.spec_versions,
                )
            )
        result[candidate.dataset_version_id] = tuple(
            sorted(
                compatible,
                key=lambda item: (-item.total_measurement_count, item.name.casefold()),
            )
        )
    return result


def _common_parameters(
    candidates: Sequence[DatasetCandidate],
    coverage: Mapping[int, Sequence[ParameterCoverage]],
) -> tuple[ParameterCoverage, ...]:
    if not candidates:
        return ()
    expected = {item.dataset_version_id for item in candidates}
    by_name: dict[str, dict[int, ParameterCoverage]] = defaultdict(dict)
    for candidate in candidates:
        for parameter in coverage.get(candidate.dataset_version_id, ()):
            by_name[parameter.name][candidate.dataset_version_id] = parameter
    common: list[ParameterCoverage] = []
    for name, by_version in by_name.items():
        if set(by_version) != expected:
            continue
        signatures = {item.signature for item in by_version.values()}
        if len(signatures) != 1:
            continue
        counts = [item.minimum_measurement_count for item in by_version.values()]
        common.append(
            ParameterCoverage(
                name=name,
                signature=next(iter(signatures)),
                minimum_measurement_count=min(counts),
                total_measurement_count=sum(counts),
                spec_versions=tuple(
                    sorted(
                        {
                            version
                            for item in by_version.values()
                            for version in item.spec_versions
                        }
                    )
                ),
            )
        )
    return tuple(
        sorted(
            common,
            key=lambda item: (-item.total_measurement_count, item.name.casefold()),
        )
    )


def _compatible_group_key(candidate: DatasetCandidate) -> tuple[str, int | None]:
    return (candidate.test_stage, None)


def _common_relationship_parameters(
    candidates: Sequence[DatasetCandidate],
    coverage: Mapping[int, Sequence[ParameterCoverage]],
    formal_spec_coverage: Mapping[int, Sequence[ParameterCoverage]],
) -> tuple[ParameterCoverage, ...]:
    if not candidates:
        return ()
    expected = {item.dataset_version_id for item in candidates}
    exact_by_name: dict[str, dict[int, ParameterCoverage]] = defaultdict(dict)
    formal_by_name: dict[str, dict[int, ParameterCoverage]] = defaultdict(dict)
    for candidate in candidates:
        version_id = candidate.dataset_version_id
        for parameter in coverage.get(version_id, ()):
            exact_by_name[parameter.name][version_id] = parameter
        for parameter in formal_spec_coverage.get(version_id, ()):
            formal_by_name[parameter.name][version_id] = parameter
    common: list[ParameterCoverage] = []
    for name in exact_by_name.keys() & formal_by_name.keys():
        exact = exact_by_name[name]
        formal = formal_by_name[name]
        if set(exact) != expected or set(formal) != expected:
            continue
        exact_signatures = {item.signature for item in exact.values()}
        formal_signatures = {item.signature for item in formal.values()}
        if len(exact_signatures) != 1 or len(formal_signatures) != 1:
            continue
        counts = [
            min(
                exact[version_id].minimum_measurement_count,
                formal[version_id].minimum_measurement_count,
            )
            for version_id in expected
        ]
        common.append(
            ParameterCoverage(
                name=name,
                signature=(
                    next(iter(exact_signatures)),
                    next(iter(formal_signatures)),
                ),
                minimum_measurement_count=min(counts),
                total_measurement_count=sum(counts),
                spec_versions=tuple(
                    sorted(
                        {
                            version
                            for item in formal.values()
                            for version in item.spec_versions
                        }
                    )
                ),
            )
        )
    return tuple(
        sorted(
            common,
            key=lambda item: (-item.total_measurement_count, item.name.casefold()),
        )
    )


def _select_eight_candidates(
    candidates: Sequence[DatasetCandidate],
    coverage: Mapping[int, Sequence[ParameterCoverage]],
    formal_spec_coverage: Mapping[int, Sequence[ParameterCoverage]] | None = None,
) -> tuple[DatasetCandidate, ...]:
    groups: dict[tuple[str, int | None], list[DatasetCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.test_stage == "CP" and candidate.spec_set_id is None:
            continue
        groups[_compatible_group_key(candidate)].append(candidate)
    selections: list[
        tuple[tuple[int, int, int, int, int], tuple[DatasetCandidate, ...]]
    ] = []
    for group in groups.values():
        relationship_selection_found = False
        if formal_spec_coverage is not None:
            by_version = {item.dataset_version_id: item for item in group}
            support: dict[
                tuple[str, tuple[object, ...], tuple[object, ...]], set[int]
            ] = defaultdict(set)
            for candidate in group:
                version_id = candidate.dataset_version_id
                exact = {item.name: item for item in coverage.get(version_id, ())}
                formal = {
                    item.name: item for item in formal_spec_coverage.get(version_id, ())
                }
                for name in exact.keys() & formal.keys():
                    support[(name, exact[name].signature, formal[name].signature)].add(
                        version_id
                    )
            eligible = [key for key, versions in support.items() if len(versions) >= 8]
            seen: set[tuple[int, ...]] = set()
            for index, left in enumerate(eligible):
                for right in eligible[index + 1 :]:
                    if left[0] == right[0]:
                        continue
                    common_versions = support[left] & support[right]
                    if len(common_versions) < 8:
                        continue
                    ordered = tuple(
                        sorted(
                            (by_version[item] for item in common_versions),
                            key=lambda item: (
                                -item.measurement_count,
                                -item.unit_count,
                                item.dataset_version_id,
                            ),
                        )[:8]
                    )
                    identity = tuple(item.dataset_version_id for item in ordered)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    common_count = len(
                        _common_relationship_parameters(
                            ordered, coverage, formal_spec_coverage
                        )
                    )
                    if common_count < 2:
                        continue
                    relationship_selection_found = True
                    score = (
                        1,
                        min(common_count, 5),
                        common_count,
                        sum(item.measurement_count for item in ordered),
                        sum(item.unit_count for item in ordered),
                    )
                    selections.append((score, ordered))
        if relationship_selection_found:
            continue
        ordered = tuple(
            sorted(
                group,
                key=lambda item: (
                    -item.measurement_count,
                    -item.unit_count,
                    item.dataset_version_id,
                ),
            )[:8]
        )
        if len(ordered) != 8:
            continue
        common_count = len(_common_parameters(ordered, coverage))
        score = (
            0,
            min(common_count, 5),
            common_count,
            sum(item.measurement_count for item in ordered),
            sum(item.unit_count for item in ordered),
        )
        selections.append((score, ordered))
    if not selections:
        return ()
    return max(selections, key=lambda item: item[0])[1]


def _single_wafer_scopes(
    connection: SqlConnection, candidates: Sequence[DatasetCandidate]
) -> tuple[WaferScope, ...]:
    cp_versions = tuple(
        candidate.dataset_version_id
        for candidate in candidates
        if candidate.test_stage == "CP"
    )
    if not cp_versions:
        return ()
    statement = text(
        "SELECT dv.dataset_id,tr.lot_id,"
        "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
        "COUNT_BIG(*) AS unit_count,"
        "SUM(CASE WHEN ur.x_coord IS NULL OR ur.y_coord IS NULL THEN 1 ELSE 0 END) "
        "AS missing_coordinate_count,"
        "COUNT(DISTINCT CONCAT(CONVERT(nvarchar(30),ur.x_coord),N':',"
        "CONVERT(nvarchar(30),ur.y_coord))) AS unique_coordinate_count "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
        "WHERE dv.dataset_version_id IN :dataset_version_ids "
        "AND tr.lot_id IS NOT NULL "
        "AND COALESCE(ur.wafer_id,tr.wafer_id) IS NOT NULL "
        "GROUP BY dv.dataset_id,tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id)"
    ).bindparams(bindparam("dataset_version_ids", expanding=True))
    rows = (
        connection.execute(statement, {"dataset_version_ids": cp_versions})
        .mappings()
        .all()
    )
    scopes = [
        WaferScope(
            dataset_id=int(row["dataset_id"]),
            lot_id=str(row["lot_id"]),
            wafer_id=str(row["wafer_id"]),
            unit_count=int(row["unit_count"]),
        )
        for row in rows
        if int(row["missing_coordinate_count"] or 0) == 0
        and int(row["unique_coordinate_count"] or 0) == int(row["unit_count"] or 0)
    ]
    return tuple(
        sorted(
            scopes, key=lambda item: (-item.unit_count, item.dataset_id, item.lot_id)
        )
    )


def _multi_wafer_scopes(
    connection: SqlConnection, candidates: Sequence[DatasetCandidate]
) -> tuple[MultiWaferScope, ...]:
    cp_versions = tuple(
        candidate.dataset_version_id
        for candidate in candidates
        if candidate.test_stage == "CP"
    )
    if not cp_versions:
        return ()
    statement = text(
        "SELECT dv.dataset_id,tr.lot_id,COUNT_BIG(*) AS unit_count,"
        "COUNT(DISTINCT COALESCE(ur.wafer_id,tr.wafer_id)) AS wafer_count,"
        "SUM(CASE WHEN ur.x_coord IS NULL OR ur.y_coord IS NULL THEN 1 ELSE 0 END) "
        "AS missing_coordinate_count,"
        "COUNT(DISTINCT CONCAT(COALESCE(ur.wafer_id,tr.wafer_id),N'|',"
        "CONVERT(nvarchar(30),ur.x_coord),N':',CONVERT(nvarchar(30),ur.y_coord))) "
        "AS unique_coordinate_count "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
        "WHERE dv.dataset_version_id IN :dataset_version_ids "
        "AND tr.lot_id IS NOT NULL "
        "AND COALESCE(ur.wafer_id,tr.wafer_id) IS NOT NULL "
        "GROUP BY dv.dataset_id,tr.lot_id"
    ).bindparams(bindparam("dataset_version_ids", expanding=True))
    rows = (
        connection.execute(statement, {"dataset_version_ids": cp_versions})
        .mappings()
        .all()
    )
    scopes = [
        MultiWaferScope(
            dataset_id=int(row["dataset_id"]),
            lot_id=str(row["lot_id"]),
            wafer_count=int(row["wafer_count"]),
            unit_count=int(row["unit_count"]),
        )
        for row in rows
        if int(row["wafer_count"] or 0) >= 2
        and int(row["missing_coordinate_count"] or 0) == 0
        and int(row["unique_coordinate_count"] or 0) == int(row["unit_count"] or 0)
    ]
    return tuple(
        sorted(
            scopes, key=lambda item: (-item.unit_count, item.dataset_id, item.lot_id)
        )
    )


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(f"unsupported response type: {type(value).__name__}")


def _serialized_size(value: Any) -> int:
    payload = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(payload.encode("utf-8"))


def _sampling_digest(response: Any) -> str | None:
    records: list[tuple[object, ...]] = []
    for item in getattr(response, "items", ()):
        for point in getattr(item, "scatter_points", ()):
            records.append(
                ("S", point.drilldown_key, point.x_parameter, point.y_parameter)
            )
        for point in getattr(item, "trend_points", ()):
            records.append(("T", point.drilldown_key, point.parameter, point.sequence))
    for point in getattr(response, "points", ()):
        records.append(
            (
                "P",
                point.drilldown_key,
                point.x,
                point.y,
                point.observed_count,
                point.fail_count,
            )
        )
    if not records:
        return None
    encoded = json.dumps(records, separators=(",", ":"), sort_keys=False).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _response_observation(response: Any) -> ResponseObservation:
    if isinstance(response, dict) and response.get("expected_gate_code"):
        return ResponseObservation(
            response_bytes=_serialized_size(response),
            observed_row_count=0,
            returned_record_count=0,
            sampling_original_points=None,
            sampling_returned_points=None,
            sampling_preserved_out_of_spec_points=None,
            sampling_digest_sha256=None,
            expected_gate_code=str(response["expected_gate_code"]),
        )
    class_name = type(response).__name__
    sampling = getattr(response, "sampling_summary", None)
    original = int(sampling.original_points) if sampling is not None else None
    returned = int(sampling.returned_points) if sampling is not None else None
    preserved = (
        int(sampling.preserved_out_of_spec_points) if sampling is not None else None
    )
    if class_name == "AnalyticsOverviewResult":
        observed_rows = int(response.counts.included_units)
        returned_records = (
            len(response.datasets)
            + len(response.yield_trend)
            + len(response.bin_pareto)
            + len(response.wafer_map)
        )
    elif class_name == "AnalyticsDetailResult":
        observed_rows = int(response.total)
        returned_records = len(response.items)
    elif class_name == "DatasetParameterAnalysisResult":
        observed_rows = sum(
            int(item.filter_summary.candidate_measurement_count)
            for item in response.items
        )
        returned_records = sum(len(item.parameters) for item in response.items)
    elif class_name == "ParameterRelationshipResult":
        observed_rows = int(original or 0)
        returned_records = sum(
            len(item.scatter_points) + len(item.trend_points) + len(item.correlations)
            for item in response.items
        )
    elif class_name == "SpatialAnalysisResult":
        observed_rows = int(response.data_quality.input_units)
        returned_records = len(response.points) + len(response.zones)
    elif class_name == "WaferSummaryResult":
        observed_rows = int(response.total)
        returned_records = len(response.items)
    else:
        observed_rows = len(getattr(response, "items", ()))
        returned_records = observed_rows
    return ResponseObservation(
        response_bytes=_serialized_size(response),
        observed_row_count=observed_rows,
        returned_record_count=returned_records,
        sampling_original_points=original,
        sampling_returned_points=returned,
        sampling_preserved_out_of_spec_points=preserved,
        sampling_digest_sha256=_sampling_digest(response),
    )


def _invoke(operation: Callable[[], Any]) -> InvocationResult:
    counter = _InvocationSqlCounter()
    token = _INVOCATION_SQL_COUNTER.set(counter)
    started = perf_counter_ns()
    observation: ResponseObservation | None = None
    error_code: str | None = None
    exception_type: str | None = None
    try:
        observation = _response_observation(operation())
    except Exception as exc:  # noqa: BLE001 - benchmark boundary redacts messages
        error_code = str(getattr(exc, "code", "PROBE_EXECUTION_FAILED"))
        exception_type = type(exc).__name__
    finally:
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        _INVOCATION_SQL_COUNTER.reset(token)
    return InvocationResult(
        elapsed_ms=elapsed_ms,
        sql_statement_count=counter.statement_count,
        observation=observation,
        error_code=error_code,
        exception_type=exception_type,
    )


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if quantile < 0 or quantile > 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _round_ms(value: float) -> float:
    return round(value, 3)


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    return {
        "p50": _round_ms(_percentile(values, 0.50)),
        "p95": _round_ms(_percentile(values, 0.95)),
        "max": _round_ms(max(values)),
    }


def _integer_distribution(values: Sequence[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "p50": None, "max": None}
    return {
        "min": min(values),
        "p50": round(_percentile(values, 0.50), 3),
        "max": max(values),
    }


def _load_result(
    operation: Callable[[], Any], *, iterations: int, concurrency: int
) -> tuple[list[InvocationResult], float]:
    started = perf_counter_ns()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda _: _invoke(operation), range(iterations)))
    wall_ms = (perf_counter_ns() - started) / 1_000_000
    return results, wall_ms


def _sampling_evidence(
    observations: Sequence[ResponseObservation],
) -> dict[str, Any]:
    originals = [
        item.sampling_original_points
        for item in observations
        if item.sampling_original_points is not None
    ]
    returned = [
        item.sampling_returned_points
        for item in observations
        if item.sampling_returned_points is not None
    ]
    preserved = [
        item.sampling_preserved_out_of_spec_points
        for item in observations
        if item.sampling_preserved_out_of_spec_points is not None
    ]
    digests = {
        item.sampling_digest_sha256
        for item in observations
        if item.sampling_digest_sha256 is not None
    }
    return {
        "original_points": _integer_distribution(originals),
        "returned_points": _integer_distribution(returned),
        "preserved_out_of_spec_points": _integer_distribution(preserved),
        "distinct_sampling_digest_count": len(digests),
        "stable": len(digests) <= 1,
    }


def _aggregate_load(
    results: Sequence[InvocationResult], wall_ms: float, *, concurrency: int
) -> dict[str, Any]:
    successes = [item for item in results if item.observation is not None]
    observations = [item.observation for item in successes if item.observation]
    errors: dict[tuple[str, str], int] = defaultdict(int)
    for item in results:
        if item.observation is None:
            errors[
                (item.error_code or "UNKNOWN", item.exception_type or "Unknown")
            ] += 1
    elapsed = [item.elapsed_ms for item in results]
    response_bytes = [item.response_bytes for item in observations]
    observed_rows = [item.observed_row_count for item in observations]
    returned_records = [item.returned_record_count for item in observations]
    sql_counts = [item.sql_statement_count for item in results]
    return {
        "concurrency": concurrency,
        "request_count": len(results),
        "success_count": len(successes),
        "error_count": len(results) - len(successes),
        "error_rate": round((len(results) - len(successes)) / len(results), 6),
        "wall_ms": _round_ms(wall_ms),
        "throughput_requests_per_second": round(len(results) / (wall_ms / 1_000), 3)
        if wall_ms > 0
        else None,
        "request_latency_ms": _distribution(elapsed),
        "response_bytes": _integer_distribution(response_bytes),
        "observed_row_count": _integer_distribution(observed_rows),
        "returned_record_count": _integer_distribution(returned_records),
        "sql_statement_count": {
            **_integer_distribution(sql_counts),
            "total": sum(sql_counts),
        },
        "sampling": _sampling_evidence(observations),
        "expected_gate_codes": sorted(
            {
                item.expected_gate_code
                for item in observations
                if item.expected_gate_code is not None
            }
        ),
        "errors": [
            {"error_code": key[0], "exception_type": key[1], "count": count}
            for key, count in sorted(errors.items())
        ],
    }


def _skip_scenario(definition: ScenarioDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "status": "SKIP",
        "reason_code": definition.coverage_reason,
        "coverage": {
            "observed": definition.coverage_observed,
            "required": definition.coverage_required,
        },
        "thresholds": {
            "warm_p95_ms_lte": definition.p95_limit_ms,
            "cold_candidate_ms_lte": definition.cold_limit_ms,
        },
        "cold_candidate": None,
        "warmup": None,
        "loads": [],
    }


def _measure_scenario(
    definition: ScenarioDefinition,
    *,
    warmup: int,
    iterations: int,
    concurrencies: Sequence[int],
) -> dict[str, Any]:
    if definition.coverage_observed < definition.coverage_required:
        return _skip_scenario(definition)
    cold = _invoke(definition.operation)
    warmup_results = [_invoke(definition.operation) for _ in range(warmup)]
    load_results: list[dict[str, Any]] = []
    raw_loads: list[list[InvocationResult]] = []
    for concurrency in concurrencies:
        raw, wall_ms = _load_result(
            definition.operation, iterations=iterations, concurrency=concurrency
        )
        raw_loads.append(raw)
        load_results.append(_aggregate_load(raw, wall_ms, concurrency=concurrency))

    all_success_observations = [
        result.observation
        for result in (
            cold,
            *warmup_results,
            *(item for load in raw_loads for item in load),
        )
        if result.observation is not None
    ]
    max_original = max(
        (item.sampling_original_points or 0 for item in all_success_observations),
        default=0,
    )
    if (
        definition.minimum_original_points is not None
        and max_original < definition.minimum_original_points
    ):
        status = "SKIP"
        reason = "LARGE_SCATTER_PAIR_COVERAGE_MISSING"
    else:
        errors_present = cold.observation is None or any(
            item.observation is None
            for item in (*warmup_results, *(row for load in raw_loads for row in load))
        )
        threshold_exceeded = any(
            float(load["request_latency_ms"]["p95"] or float("inf"))
            > definition.p95_limit_ms
            for load in load_results
        )
        if definition.cold_limit_ms is not None:
            threshold_exceeded = threshold_exceeded or (
                cold.elapsed_ms > definition.cold_limit_ms
            )
        sampling_unstable = definition.stable_sampling_required and any(
            not bool(load["sampling"]["stable"]) for load in load_results
        )
        if errors_present:
            status, reason = "FAIL", "PROBE_EXECUTION_FAILED"
        elif sampling_unstable:
            status, reason = "FAIL", "SAMPLING_UNSTABLE"
        elif threshold_exceeded:
            status, reason = "FAIL", "THRESHOLD_EXCEEDED"
        else:
            status, reason = "PASS", "THRESHOLDS_MET"
    return {
        "name": definition.name,
        "status": status,
        "reason_code": reason,
        "coverage": {
            "observed": definition.coverage_observed,
            "required": definition.coverage_required,
            "maximum_observed_sampling_input": max_original,
        },
        "thresholds": {
            "warm_p95_ms_lte": definition.p95_limit_ms,
            "cold_candidate_ms_lte": definition.cold_limit_ms,
        },
        "cold_candidate": {
            "elapsed_ms": _round_ms(cold.elapsed_ms),
            "sql_statement_count": cold.sql_statement_count,
            "response_bytes": (
                cold.observation.response_bytes
                if cold.observation is not None
                else None
            ),
            "error_code": cold.error_code,
            "exception_type": cold.exception_type,
        },
        "warmup": {
            "request_count": len(warmup_results),
            "error_count": sum(item.observation is None for item in warmup_results),
        },
        "loads": load_results,
    }


def _candidate_digest(candidates: Sequence[DatasetCandidate]) -> str | None:
    if not candidates:
        return None
    payload = [
        (item.dataset_id, item.dataset_version_id, item.version_no)
        for item in candidates
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _public_candidate(candidate: DatasetCandidate | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "candidate_reference_sha256": _candidate_digest((candidate,)),
        "test_stage": candidate.test_stage,
        "unit_count": candidate.unit_count,
        "measurement_count": candidate.measurement_count,
        "wafer_count": candidate.wafer_count,
        "coordinate_count": candidate.coordinate_count,
    }


def _formal_spec_identity_evidence(
    candidates: Sequence[DatasetCandidate],
    formal_spec_coverage: Mapping[int, Sequence[ParameterCoverage]],
    parameter_names: set[str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for candidate in candidates:
        parameters = [
            {
                "parameter": parameter.name,
                "spec_versions": list(parameter.spec_versions),
            }
            for parameter in formal_spec_coverage.get(candidate.dataset_version_id, ())
            if parameter.name in parameter_names
        ]
        versions = sorted(
            {
                version
                for parameter in parameters
                for version in parameter["spec_versions"]
            }
        )
        evidence.append(
            {
                "candidate_reference_sha256": _candidate_digest((candidate,)),
                "spec_versions": versions,
                "parameters": parameters,
            }
        )
    return evidence


def _analytics_refs(
    candidates: Sequence[DatasetCandidate],
) -> list[AnalyticsDatasetReference]:
    return [
        AnalyticsDatasetReference(
            dataset_id=item.dataset_id, version_no=item.version_no
        )
        for item in candidates
    ]


def _dataset_refs(candidates: Sequence[DatasetCandidate]) -> list[DatasetReference]:
    return [
        DatasetReference(dataset_id=item.dataset_id, version_no=item.version_no)
        for item in candidates
    ]


def _expected_correlation_gate(
    engine: Any, request: ParameterRelationshipRequest
) -> dict[str, str]:
    try:
        SqlParameterRelationshipService(engine).relationship(request)
    except DomainError as exc:
        if exc.code != "ANALYSIS_RULE_NOT_APPROVED":
            raise
        return {
            "status": "GATED",
            "expected_gate_code": "ANALYSIS_RULE_NOT_APPROVED",
        }
    raise VerificationError(
        "CORRELATION_GATE_UNEXPECTEDLY_OPEN",
        "未批准 Correlation 规则时能力不应开放",
    )


def _scenario_definitions(
    engine: Any,
    candidates: Sequence[DatasetCandidate],
    coverage: Mapping[int, Sequence[ParameterCoverage]],
    formal_spec_coverage: Mapping[int, Sequence[ParameterCoverage]],
    eight: Sequence[DatasetCandidate],
    single_wafer_scopes: Sequence[WaferScope],
    multi_wafer_scopes: Sequence[MultiWaferScope],
) -> tuple[ScenarioDefinition, ...]:
    parameter_candidates = [
        item
        for item in candidates
        if len(coverage.get(item.dataset_version_id, ())) >= 2
    ]
    spatial_candidates = {
        item.dataset_id: item
        for item in candidates
        if item.test_stage == "CP"
        and len(coverage.get(item.dataset_version_id, ())) >= 1
    }
    single = parameter_candidates[0] if parameter_candidates else None
    large = max(
        parameter_candidates,
        key=lambda item: min(
            parameter.minimum_measurement_count
            for parameter in coverage[item.dataset_version_id][:2]
        ),
        default=None,
    )
    wafer_scope = next(
        (item for item in single_wafer_scopes if item.dataset_id in spatial_candidates),
        None,
    )
    spatial = spatial_candidates.get(wafer_scope.dataset_id) if wafer_scope else None
    multi_scope = next(
        (item for item in multi_wafer_scopes if item.dataset_id in spatial_candidates),
        None,
    )
    multi = spatial_candidates.get(multi_scope.dataset_id) if multi_scope else None

    placeholder = DatasetCandidate(1, 1, 1, "CP", 1, 0, 0, 0, 0)
    single_ref = single or placeholder
    single_parameters = list(coverage.get(single_ref.dataset_version_id, ()))[:5]
    single_parameter_names = [item.name for item in single_parameters]
    single_analytics_refs = _analytics_refs((single_ref,))
    single_dataset_refs = _dataset_refs((single_ref,))

    overview_request = AnalyticsOverviewRequest(datasets=single_analytics_refs)
    detail_request = AnalyticsDetailRequest(
        datasets=single_analytics_refs,
        focus_dataset_id=single_ref.dataset_id,
        parameters=[],
        page=1,
        page_size=200,
    )
    parameter_request = DatasetParameterAnalysisRequest(
        datasets=single_dataset_refs,
        parameters=single_parameter_names or ["MISSING_PARAMETER"],
        analyses=[DatasetParameterAnalysisType.DESCRIPTIVE],
    )
    relationship_request = ParameterRelationshipRequest(
        datasets=single_analytics_refs,
        x_parameter=single_parameter_names[0]
        if single_parameter_names
        else "MISSING_X",
        y_parameters=(single_parameter_names[1:2] or ["MISSING_Y"]),
        analyses=[ParameterRelationshipAnalysis.SCATTER],
        max_points=10_000,
    )
    correlation_request = ParameterRelationshipRequest(
        datasets=single_analytics_refs,
        x_parameter=single_parameter_names[0]
        if single_parameter_names
        else "MISSING_X",
        y_parameters=(single_parameter_names[1:2] or ["MISSING_Y"]),
        analyses=[ParameterRelationshipAnalysis.CORRELATION],
        correlation=ParameterCorrelationConfig(
            method=ParameterCorrelationMethod.PEARSON_PAIRWISE_V1,
            rule_code=_UNAPPROVED_CORRELATION_RULE_CODE,
            version_code=_UNAPPROVED_CORRELATION_RULE_VERSION,
        ),
    )

    large_ref = large or placeholder
    large_parameters = list(coverage.get(large_ref.dataset_version_id, ()))[:2]
    large_request = ParameterRelationshipRequest(
        datasets=_analytics_refs((large_ref,)),
        x_parameter=large_parameters[0].name if large_parameters else "MISSING_X",
        y_parameters=(
            [large_parameters[1].name] if len(large_parameters) >= 2 else ["MISSING_Y"]
        ),
        analyses=[ParameterRelationshipAnalysis.SCATTER],
        max_points=LARGE_SCATTER_MAX_POINTS,
    )
    large_coverage = (
        min(item.minimum_measurement_count for item in large_parameters)
        if len(large_parameters) >= 2
        else 0
    )

    spatial_ref = spatial or placeholder
    spatial_parameter = list(coverage.get(spatial_ref.dataset_version_id, ()))[:1]
    spatial_request = SpatialAnalysisRequest(
        datasets=_analytics_refs((spatial_ref,)),
        filters=AnalyticsFilters(
            lot_ids=[wafer_scope.lot_id] if wafer_scope else [],
            wafer_ids=[wafer_scope.wafer_id] if wafer_scope else [],
        ),
        parameters=(
            [spatial_parameter[0].name] if spatial_parameter else ["MISSING_PARAMETER"]
        ),
        mode=SpatialAnalysisMode.PARAMETER_HEATMAP,
        focus_dataset_id=spatial_ref.dataset_id,
        max_points=50_000,
    )
    multi_ref = multi or placeholder
    multi_request = SpatialAnalysisRequest(
        datasets=_analytics_refs((multi_ref,)),
        filters=AnalyticsFilters(lot_ids=[multi_scope.lot_id] if multi_scope else []),
        mode=SpatialAnalysisMode.COMPOSITE_FAILURE,
        focus_dataset_id=multi_ref.dataset_id,
        max_points=50_000,
    )
    wafer_request = WaferSummaryRequest(
        datasets=_analytics_refs((spatial_ref,)),
        parameters=(
            [item.name for item in coverage.get(spatial_ref.dataset_version_id, ())[:5]]
        ),
        page=1,
        page_size=200,
    )

    eight_candidates = tuple(eight)
    eight_refs = _analytics_refs(eight_candidates or (placeholder,))
    eight_common = _common_parameters(eight_candidates, coverage)[:5]
    eight_names = [item.name for item in eight_common]
    eight_relationship_common = _common_relationship_parameters(
        eight_candidates, coverage, formal_spec_coverage
    )[:2]
    eight_relationship_names = [item.name for item in eight_relationship_common]
    eight_focus = eight_candidates[0] if eight_candidates else placeholder
    eight_overview_request = AnalyticsOverviewRequest(datasets=eight_refs)
    eight_detail_request = AnalyticsDetailRequest(
        datasets=eight_refs,
        focus_dataset_id=eight_focus.dataset_id,
        parameters=[],
        page=1,
        page_size=200,
    )
    eight_parameter_request = DatasetParameterAnalysisRequest(
        datasets=_dataset_refs(eight_candidates or (placeholder,)),
        parameters=eight_names or ["MISSING_PARAMETER"],
        analyses=[DatasetParameterAnalysisType.DESCRIPTIVE],
    )
    eight_relationship_request = ParameterRelationshipRequest(
        datasets=eight_refs,
        x_parameter=(
            eight_relationship_names[0] if eight_relationship_names else "MISSING_X"
        ),
        y_parameters=(
            [eight_relationship_names[1]]
            if len(eight_relationship_names) >= 2
            else ["MISSING_Y"]
        ),
        analyses=[ParameterRelationshipAnalysis.SCATTER],
        max_points=10_000,
    )

    single_ready = 1 if single is not None else 0
    eight_count = len(eight_candidates)
    return (
        ScenarioDefinition(
            "single_dataset_overview",
            lambda: SqlAnalyticsService(engine).overview(overview_request),
            3_000,
            single_ready,
            1,
            "SINGLE_DATASET_WITH_PARAMETERS_MISSING",
        ),
        ScenarioDefinition(
            "single_dataset_detail_200",
            lambda: SqlAnalyticsService(engine).detail(detail_request),
            3_000,
            single_ready,
            1,
            "SINGLE_DATASET_WITH_PARAMETERS_MISSING",
        ),
        ScenarioDefinition(
            "single_dataset_parameter_analysis_up_to_5",
            lambda: SqlDatasetService(engine).analyze_parameters(parameter_request),
            5_000,
            len(single_parameter_names),
            1,
            "SINGLE_DATASET_PARAMETER_MISSING",
        ),
        ScenarioDefinition(
            "single_dataset_parameter_relationship",
            lambda: SqlParameterRelationshipService(engine).relationship(
                relationship_request
            ),
            5_000,
            len(single_parameter_names),
            2,
            "TWO_EXACT_PARAMETERS_MISSING",
            stable_sampling_required=True,
        ),
        ScenarioDefinition(
            "single_parameter_large_scatter",
            lambda: SqlParameterRelationshipService(engine).relationship(large_request),
            5_000,
            large_coverage,
            LARGE_SCATTER_MAX_POINTS + 1,
            "LARGE_SCATTER_PARAMETER_COVERAGE_MISSING",
            cold_limit_ms=5_000,
            stable_sampling_required=True,
            minimum_original_points=LARGE_SCATTER_MAX_POINTS + 1,
        ),
        ScenarioDefinition(
            "single_wafer_parameter_heatmap",
            lambda: SqlSpatialAnalysisService(engine).analyze(spatial_request),
            3_000,
            wafer_scope.unit_count if wafer_scope else 0,
            1,
            "VALID_SINGLE_WAFER_COORDINATE_SCOPE_MISSING",
            stable_sampling_required=True,
        ),
        ScenarioDefinition(
            "multi_wafer_composite_failure",
            lambda: SqlSpatialAnalysisService(engine).analyze(multi_request),
            5_000,
            multi_scope.wafer_count if multi_scope else 0,
            2,
            "VALID_MULTI_WAFER_COORDINATE_SCOPE_MISSING",
            stable_sampling_required=True,
        ),
        ScenarioDefinition(
            "wafer_summary_page_200_up_to_5_parameters",
            lambda: SqlWaferSummaryService(engine).summarize(wafer_request),
            3_000,
            spatial_ref.wafer_count if spatial is not None else 0,
            1,
            "VALID_CP_WAFER_SCOPE_MISSING",
        ),
        ScenarioDefinition(
            "correlation_rule_gate",
            lambda: _expected_correlation_gate(engine, correlation_request),
            5_000,
            len(single_parameter_names),
            2,
            "TWO_EXACT_PARAMETERS_MISSING",
        ),
        ScenarioDefinition(
            "eight_dataset_overview",
            lambda: SqlAnalyticsService(engine).overview(eight_overview_request),
            3_000,
            eight_count,
            8,
            "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING",
        ),
        ScenarioDefinition(
            "eight_dataset_detail_200",
            lambda: SqlAnalyticsService(engine).detail(eight_detail_request),
            3_000,
            eight_count,
            8,
            "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING",
        ),
        ScenarioDefinition(
            "eight_dataset_five_parameter_analysis",
            lambda: SqlDatasetService(engine).analyze_parameters(
                eight_parameter_request
            ),
            5_000,
            len(eight_names) if eight_count == 8 else 0,
            5,
            (
                "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING"
                if eight_count < 8
                else "FIVE_COMMON_EXACT_PARAMETERS_MISSING"
            ),
        ),
        ScenarioDefinition(
            "eight_dataset_parameter_relationship",
            lambda: SqlParameterRelationshipService(engine).relationship(
                eight_relationship_request
            ),
            5_000,
            len(eight_relationship_names) if eight_count == 8 else 0,
            2,
            (
                "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING"
                if eight_count < 8
                else "TWO_COMMON_FORMAL_SPEC_COMPATIBLE_PARAMETERS_MISSING"
            ),
            stable_sampling_required=True,
        ),
    )


def _workload_coverage(
    candidates: Sequence[DatasetCandidate],
    coverage: Mapping[int, Sequence[ParameterCoverage]],
    eight: Sequence[DatasetCandidate],
    single_wafer_scopes: Sequence[WaferScope],
    multi_wafer_scopes: Sequence[MultiWaferScope],
) -> dict[str, Any]:
    parameter_candidates = [
        item
        for item in candidates
        if len(coverage.get(item.dataset_version_id, ())) >= 2
    ]
    single = parameter_candidates[0] if parameter_candidates else None
    large = max(
        parameter_candidates,
        key=lambda item: min(
            parameter.minimum_measurement_count
            for parameter in coverage[item.dataset_version_id][:2]
        ),
        default=None,
    )
    spatial_candidates = {
        item.dataset_id: item
        for item in candidates
        if item.test_stage == "CP"
        and len(coverage.get(item.dataset_version_id, ())) >= 1
    }
    wafer_scope = next(
        (item for item in single_wafer_scopes if item.dataset_id in spatial_candidates),
        None,
    )
    multi_scope = next(
        (item for item in multi_wafer_scopes if item.dataset_id in spatial_candidates),
        None,
    )
    spatial = spatial_candidates.get(wafer_scope.dataset_id) if wafer_scope else None
    multi = spatial_candidates.get(multi_scope.dataset_id) if multi_scope else None
    large_parameters = (
        tuple(coverage.get(large.dataset_version_id, ()))[:2] if large else ()
    )
    return {
        "single_dataset": {
            "status": "PASS" if single else "SKIP",
            "candidate": _public_candidate(single),
            "exact_parameter_count": (
                len(coverage.get(single.dataset_version_id, ())) if single else 0
            ),
        },
        "large_scatter": {
            "status": (
                "PASS"
                if len(large_parameters) == 2
                and min(item.minimum_measurement_count for item in large_parameters)
                > LARGE_SCATTER_MAX_POINTS
                else "SKIP"
            ),
            "candidate": _public_candidate(large),
            "pair_coverage_upper_bound": (
                min(item.minimum_measurement_count for item in large_parameters)
                if len(large_parameters) == 2
                else 0
            ),
            "required_original_pair_points": LARGE_SCATTER_MAX_POINTS + 1,
        },
        "single_wafer_spatial": {
            "status": "PASS" if spatial and wafer_scope else "SKIP",
            "candidate": _public_candidate(spatial),
            "scope_unit_count": wafer_scope.unit_count if wafer_scope else 0,
        },
        "multi_wafer_spatial": {
            "status": "PASS" if multi and multi_scope else "SKIP",
            "candidate": _public_candidate(multi),
            "scope_unit_count": multi_scope.unit_count if multi_scope else 0,
            "scope_wafer_count": multi_scope.wafer_count if multi_scope else 0,
        },
        "eight_dataset_totals": {
            "unit_count": sum(item.unit_count for item in eight),
            "measurement_count": sum(item.measurement_count for item in eight),
            "wafer_count": sum(item.wafer_count for item in eight),
        },
    }


def _assert_selected_still_current(
    connection: SqlConnection, candidates: Sequence[DatasetCandidate]
) -> None:
    for candidate in candidates:
        row = (
            connection.execute(
                text(
                    "SELECT status,is_current FROM dataset.dataset_version "
                    "WHERE dataset_version_id=:dataset_version_id"
                ),
                {"dataset_version_id": candidate.dataset_version_id},
            )
            .mappings()
            .one()
        )
        if str(row["status"]) != "PUBLISHED" or not bool(row["is_current"]):
            raise VerificationError(
                "SELECTED_DATASET_CURRENT_DRIFT",
                "性能验收期间所选 Dataset 不再是 Current/PUBLISHED",
            )


def _overall_status(scenarios: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(item["status"]) for item in scenarios}
    if "FAIL" in statuses:
        return "FAIL"
    if "SKIP" in statuses or not scenarios:
        return "SKIP"
    return "PASS"


def _verification_exit_code(verification: str, *, smoke: bool) -> int:
    """Fail closed for formal acceptance while allowing coverage-only smoke runs."""

    if verification == "PASS":
        return 0
    if verification == "SKIP" and smoke:
        return 0
    return 1


def _validate_run_controls(
    warmup: int,
    iterations: int,
    concurrencies: Sequence[int],
    *,
    smoke: bool = False,
) -> tuple[int, ...]:
    if warmup < 0 or warmup > 10:
        raise VerificationError("WARMUP_INVALID", "warmup 必须在 0 到 10 之间")
    minimum_iterations = 1 if smoke else 30
    if iterations < minimum_iterations or iterations > MAX_ITERATIONS:
        raise VerificationError(
            "ITERATIONS_INVALID",
            (
                f"smoke iterations 必须在 1 到 {MAX_ITERATIONS} 之间"
                if smoke
                else f"正式性能验收 iterations 必须在 30 到 {MAX_ITERATIONS} 之间"
            ),
        )
    normalized = tuple(dict.fromkeys(int(value) for value in concurrencies))
    if not normalized or any(value not in DEFAULT_CONCURRENCY for value in normalized):
        raise VerificationError("CONCURRENCY_INVALID", "concurrency 只允许 1 和/或 5")
    return normalized


def _latency_statistic_label(*, smoke: bool, iterations: int) -> str:
    if not smoke:
        return "FORMAL_P95"
    if iterations == 1:
        return "SINGLE_OBSERVATION_NOT_FORMAL_P95"
    return "SMOKE_SAMPLE_PERCENTILE_NOT_FORMAL_P95"


def verify(
    raw_engine: Engine,
    *,
    warmup: int = DEFAULT_WARMUP,
    iterations: int = DEFAULT_ITERATIONS,
    concurrencies: Sequence[int] = DEFAULT_CONCURRENCY,
    scenario_names: Sequence[str] = (),
    smoke: bool = False,
) -> dict[str, Any]:
    normalized_concurrency = _validate_run_controls(
        warmup, iterations, concurrencies, smoke=smoke
    )
    audit = ReadOnlyAudit()
    engine = _ReadOnlyEngine(raw_engine, audit)
    with engine.connect() as connection:
        identity = _identity(connection)
        before = _canonical_counts(connection)
        candidates = _dataset_candidates(connection)
        coverage = _parameter_coverage(connection, candidates)
        formal_spec_coverage = _released_formal_spec_coverage(
            connection, candidates, coverage
        )
        eight = _select_eight_candidates(candidates, coverage, formal_spec_coverage)
        single_wafer_scopes = _single_wafer_scopes(connection, candidates)
        multi_wafer_scopes = _multi_wafer_scopes(connection, candidates)
    definitions = _scenario_definitions(
        engine,
        candidates,
        coverage,
        formal_spec_coverage,
        eight,
        single_wafer_scopes,
        multi_wafer_scopes,
    )
    available_names = {item.name for item in definitions}
    requested_names = tuple(dict.fromkeys(scenario_names))
    unknown = sorted(set(requested_names) - available_names)
    if unknown:
        raise VerificationError(
            "SCENARIO_UNKNOWN", f"未知性能场景: {', '.join(unknown)}"
        )
    selected = tuple(
        item
        for item in definitions
        if not requested_names or item.name in set(requested_names)
    )
    scenarios = [
        _measure_scenario(
            item,
            warmup=warmup,
            iterations=iterations,
            concurrencies=normalized_concurrency,
        )
        for item in selected
    ]
    with engine.connect() as connection:
        after = _canonical_counts(connection)
        _assert_selected_still_current(connection, candidates)
    _assert_canonical_counts_unchanged(before, after)
    if audit.blocked_statement_count:
        raise VerificationError(
            "READ_ONLY_GUARD_TRIGGERED", "性能验收期间只读 SQL 门禁被触发"
        )
    eight_common = _common_parameters(eight, coverage)
    relationship_common = _common_relationship_parameters(
        eight, coverage, formal_spec_coverage
    )
    return {
        "verification": _overall_status(scenarios),
        "contract": "v1.3-analytics-closure-performance-sql-readonly",
        "identity": identity,
        "methodology": {
            "run_mode": "SMOKE" if smoke else "FORMAL",
            "formal_sample_requirement_met": not smoke and iterations >= 30,
            "elapsed_scope": "service_call_plus_response_serialization",
            "transport_excluded": "HTTP/browser/network are outside this SQL service benchmark",
            "cold_candidate": (
                "first invocation per scenario in this process; SQL Server and OS caches "
                "are not flushed because the verifier is strictly read-only"
            ),
            "warmup_per_scenario": warmup,
            "iterations_per_concurrency": iterations,
            "concurrency_levels": list(normalized_concurrency),
            "percentile": "linear interpolation over all request latencies",
            "latency_statistic_label": _latency_statistic_label(
                smoke=smoke, iterations=iterations
            ),
            "smoke_threshold_policy": (
                "threshold exceedance remains FAIL even though smoke latency is not a formal p95"
            ),
            "decision": "FAIL precedes SKIP; missing real coverage can never become PASS",
        },
        "coverage": {
            "current_published_nonempty_candidates_examined": len(candidates),
            "cp_candidate_count": sum(item.test_stage == "CP" for item in candidates),
            "ft_candidate_count": sum(item.test_stage == "FT" for item in candidates),
            "eight_dataset": {
                "status": "PASS" if len(eight) == 8 else "SKIP",
                "reason_code": (
                    "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_FOUND"
                    if len(eight) == 8
                    else "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING"
                ),
                "count": len(eight),
                "candidate_reference_sha256": _candidate_digest(eight),
                "test_stage": eight[0].test_stage if eight else None,
                "common_exact_parameter_count": len(eight_common),
                "common_relationship_parameter_count": len(relationship_common),
                "formal_spec_identity_evidence": _formal_spec_identity_evidence(
                    eight,
                    formal_spec_coverage,
                    {item.name for item in relationship_common[:2]},
                ),
            },
            "largest_candidate": _public_candidate(
                candidates[0] if candidates else None
            ),
            "workloads": _workload_coverage(
                candidates,
                coverage,
                eight,
                single_wafer_scopes,
                multi_wafer_scopes,
            ),
            "canonical_counts": before,
        },
        "scenarios": scenarios,
        "read_only": {
            "policy": "SELECT_OR_READ_ONLY_CTE_ONLY",
            "executed_statement_count": audit.statement_count,
            "blocked_statement_count": audit.blocked_statement_count,
            "canonical_counts_unchanged": True,
            "selected_dataset_versions_remained_current_published": True,
        },
        "evidence_redaction": {
            "connection_details_emitted": False,
            "raw_measurement_values_emitted": False,
            "dataset_identity": "stable_sha256_reference",
            "omitted": [
                "connection_string",
                "server_name",
                "database_login",
                "dataset_id",
                "dataset_version_id",
                "product",
                "lot",
                "wafer",
                "parameter_name",
                "measurement_value",
                "response_body",
            ],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only v1.3 analytics closure performance acceptance"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="unmeasured warmup calls per scenario (0-10; default: 2)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help="measured requests per concurrency (30-100; default: 30)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        choices=DEFAULT_CONCURRENCY,
        nargs="+",
        default=list(DEFAULT_CONCURRENCY),
        help="one or both supported levels: 1 5 (default: both)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="run one named scenario; repeat to select several (default: all)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="allow 1-29 iterations for contract smoke; never formal acceptance",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.3-analytics-closure-performance-sql-readonly",
                    "error_code": "DATABASE_URL_REQUIRED",
                    "message": "TMS_DATABASE_URL is required",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        evidence = verify(
            get_engine(),
            warmup=args.warmup,
            iterations=args.iterations,
            concurrencies=args.concurrency,
            scenario_names=args.scenario,
            smoke=args.smoke,
        )
    except VerificationError as exc:
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.3-analytics-closure-performance-sql-readonly",
                    "error_code": exc.code,
                    "message": exc.safe_message,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.3-analytics-closure-performance-sql-readonly",
                    "error_code": "UNEXPECTED_VERIFICATION_FAILURE",
                    "exception_type": type(exc).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    exit_code = _verification_exit_code(
        str(evidence.get("verification", "FAIL")),
        smoke=args.smoke,
    )
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
