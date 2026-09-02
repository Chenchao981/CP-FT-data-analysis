from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Protocol

from sqlalchemy import Engine, bindparam, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.auth import DEVELOPMENT_PRINCIPAL
from app.domain.datasets import DatasetComparisonRequest
from app.domain.m2_queries import M2PageFilters
from app.infrastructure.database import get_engine
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_m2_query_service import SqlM2QueryService
from app.infrastructure.sql_management_service import SqlManagementService

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0025"
DEFAULT_WARM_RUNS = 5
PAGE_SIZE = 50
MAX_CANDIDATES = 256

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
_ROW_COUNT_FIELDS = (
    "import_batches",
    "production_import_batches",
    "production_cp_import_batches",
    "production_ft_import_batches",
    "source_files",
    "source_receipts",
    "processing_jobs",
    "datasets",
    "published_current_dataset_versions",
    "production_current_dataset_versions",
    "production_cp_current_dataset_versions",
    "production_ft_current_dataset_versions",
    "test_runs",
    "unit_results",
    "measurements",
)


class VerificationError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(slots=True)
class ReadOnlyAudit:
    statement_count: int = 0
    blocked_statement_count: int = 0


class SqlConnection(Protocol):
    def execute(self, statement: Any, parameters: Any = None) -> Any: ...


class Connectable(Protocol):
    @contextmanager
    def connect(self) -> Iterator[SqlConnection]: ...


class _ReadOnlyConnection:
    def __init__(self, connection: Any, audit: ReadOnlyAudit) -> None:
        self._connection = connection
        self._audit = audit

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        try:
            _assert_read_only_sql(str(statement))
        except VerificationError:
            self._audit.blocked_statement_count += 1
            raise
        self._audit.statement_count += 1
        if parameters is None:
            return self._connection.execute(statement)
        return self._connection.execute(statement, parameters)


class _ReadOnlyEngine:
    """Expose only guarded SELECT/read-only CTE execution to SQL services."""

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


@dataclass(frozen=True, slots=True)
class ProbeDefinition:
    name: str
    warm_p95_limit_ms: float
    operation: Callable[[], Any]
    observed_records: int
    minimum_records: int
    insufficient_reason: str
    cold_candidate_limit_ms: float | None = None


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
            "DATABASE_VERSION_INVALID",
            "SQL Server 产品版本格式无效",
        )
    return {
        "database": EXPECTED_DATABASE,
        "schema_revision": EXPECTED_SCHEMA_REVISION,
        "database_engine": "Microsoft SQL Server",
        "product_major": int(product_major),
        "engine_edition": engine_edition,
    }


def _database_scale(connection: SqlConnection) -> dict[str, int | float]:
    row = (
        connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT_BIG(*) FROM ingestion.import_batch) AS import_batches,"
                "(SELECT COUNT_BIG(*) FROM ingestion.import_batch "
                " WHERE business_domain='PRODUCTION') AS production_import_batches,"
                "(SELECT COUNT_BIG(*) FROM ingestion.import_batch "
                " WHERE business_domain='PRODUCTION' AND test_stage='CP') "
                " AS production_cp_import_batches,"
                "(SELECT COUNT_BIG(*) FROM ingestion.import_batch "
                " WHERE business_domain='PRODUCTION' AND test_stage='FT') "
                " AS production_ft_import_batches,"
                "(SELECT COUNT_BIG(*) FROM ingestion.source_file) AS source_files,"
                "(SELECT COUNT_BIG(*) FROM ingestion.source_file_receipt) "
                " AS source_receipts,"
                "(SELECT COUNT_BIG(*) FROM ingestion.processing_job) "
                " AS processing_jobs,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset) AS datasets,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version "
                " WHERE status='PUBLISHED' AND is_current=1) "
                " AS published_current_dataset_versions,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                " JOIN ingestion.import_batch b "
                " ON b.import_batch_id=dv.input_batch_id "
                " WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                " AND b.business_domain='PRODUCTION') "
                " AS production_current_dataset_versions,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                " JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                " JOIN ingestion.import_batch b "
                " ON b.import_batch_id=dv.input_batch_id "
                " WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                " AND b.business_domain='PRODUCTION' AND d.test_stage='CP') "
                " AS production_cp_current_dataset_versions,"
                "(SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                " JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                " JOIN ingestion.import_batch b "
                " ON b.import_batch_id=dv.input_batch_id "
                " WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                " AND b.business_domain='PRODUCTION' AND d.test_stage='FT') "
                " AS production_ft_current_dataset_versions,"
                "(SELECT COUNT_BIG(*) FROM test.test_run) AS test_runs,"
                "(SELECT COUNT_BIG(*) FROM test.unit_result) AS unit_results,"
                "(SELECT COUNT_BIG(*) FROM test.measurement) AS measurements,"
                "(SELECT CAST(COALESCE(SUM(CASE WHEN type=0 THEN size END),0) "
                " * 8.0 / 1024 AS decimal(18,2)) FROM sys.database_files) "
                " AS data_size_mb,"
                "(SELECT CAST(COALESCE(SUM(CASE WHEN type=1 THEN size END),0) "
                " * 8.0 / 1024 AS decimal(18,2)) FROM sys.database_files) "
                " AS log_size_mb"
            )
        )
        .mappings()
        .one()
    )
    scale: dict[str, int | float] = {}
    for field in _ROW_COUNT_FIELDS:
        value = int(row[field] or 0)
        if value < 0:
            raise VerificationError(
                "DATABASE_SCALE_INVALID",
                "数据库规模快照出现无效负数",
            )
        scale[field] = value
    scale["data_size_mb"] = round(float(row["data_size_mb"] or 0), 2)
    scale["log_size_mb"] = round(float(row["log_size_mb"] or 0), 2)
    return scale


def _assert_scale_unchanged(
    before: Mapping[str, int | float], after: Mapping[str, int | float]
) -> None:
    before_rows = {field: int(before[field]) for field in _ROW_COUNT_FIELDS}
    after_rows = {field: int(after[field]) for field in _ROW_COUNT_FIELDS}
    if before_rows != after_rows:
        raise VerificationError(
            "READ_ONLY_SCALE_DRIFT",
            "验收期间业务表行数发生变化，性能结果作废",
        )


def _dataset_candidates(connection: SqlConnection) -> tuple[DatasetCandidate, ...]:
    rows = (
        connection.execute(
            text(
                f"SELECT TOP ({MAX_CANDIDATES}) d.dataset_id,"
                "dv.dataset_version_id,dv.version_no,d.test_stage,dv.spec_set_id,"
                "unit_scope.unit_count "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "JOIN ingestion.import_batch b "
                "ON b.import_batch_id=dv.input_batch_id "
                "CROSS APPLY(SELECT COUNT_BIG(*) AS unit_count "
                " FROM dataset.dataset_version_run dvr "
                " JOIN test.test_run tr "
                " ON tr.processing_run_id=dvr.processing_run_id "
                " JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                " WHERE dvr.dataset_version_id=dv.dataset_version_id) unit_scope "
                "WHERE d.lifecycle_status='ACTIVE' "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND b.business_domain='PRODUCTION' AND unit_scope.unit_count>0 "
                "ORDER BY unit_scope.unit_count DESC,dv.dataset_version_id DESC"
            )
        )
        .mappings()
        .all()
    )
    candidates: list[DatasetCandidate] = []
    for row in rows:
        stage = str(row["test_stage"])
        unit_count = int(row["unit_count"] or 0)
        if stage not in {"CP", "FT"} or unit_count < 1:
            raise VerificationError(
                "PERFORMANCE_CANDIDATE_INVALID",
                "性能验收候选 Dataset 合同无效",
            )
        candidates.append(
            DatasetCandidate(
                dataset_id=int(row["dataset_id"]),
                dataset_version_id=int(row["dataset_version_id"]),
                version_no=int(row["version_no"]),
                test_stage=stage,
                spec_set_id=(
                    int(row["spec_set_id"]) if row["spec_set_id"] is not None else None
                ),
                unit_count=unit_count,
            )
        )
    return tuple(candidates)


def _comparison_candidates(
    candidates: Sequence[DatasetCandidate],
) -> tuple[DatasetCandidate, ...]:
    groups: dict[tuple[str, int | None], list[DatasetCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.test_stage == "CP" and candidate.spec_set_id is None:
            continue
        key = (
            candidate.test_stage,
            candidate.spec_set_id if candidate.test_stage == "CP" else None,
        )
        groups[key].append(candidate)
    compatible = [group for group in groups.values() if len(group) >= 8]
    if not compatible:
        return ()
    selected = max(
        compatible,
        key=lambda group: (len(group), sum(item.unit_count for item in group)),
    )
    return tuple(selected[:8])


def _signature(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _common_parameters(
    connection: SqlConnection, candidates: Sequence[DatasetCandidate]
) -> tuple[str, ...]:
    if len(candidates) != 8:
        return ()
    version_ids = tuple(item.dataset_version_id for item in candidates)
    statement = text(
        "SELECT DISTINCT dv.dataset_version_id,tid.raw_item_name,tid.unit_code,"
        "tid.program_lsl,tid.program_usl,tid.condition_json "
        "FROM dataset.dataset_version dv "
        "JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id "
        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        "JOIN mdm.test_item_definition tid "
        "ON tid.program_version_id=tr.program_version_id "
        "WHERE dv.dataset_version_id IN :dataset_version_ids "
        "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL "
        "AND EXISTS(SELECT 1 FROM test.unit_result ur "
        "JOIN test.measurement m ON m.unit_id=ur.unit_id "
        "WHERE ur.run_id=tr.run_id AND m.test_item_id=tid.test_item_id)"
    ).bindparams(bindparam("dataset_version_ids", expanding=True))
    rows = (
        connection.execute(statement, {"dataset_version_ids": version_ids})
        .mappings()
        .all()
    )
    by_name: dict[str, dict[int, set[tuple[str | None, ...]]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in rows:
        name = str(row["raw_item_name"] or "").strip()
        if not name or len(name) > 200:
            continue
        signature = (
            _signature(row["unit_code"]),
            _signature(row["program_lsl"]),
            _signature(row["program_usl"]),
            _signature(row["condition_json"]),
        )
        by_name[name][int(row["dataset_version_id"])].add(signature)
    expected = set(version_ids)
    compatible: list[str] = []
    for name, signatures_by_version in by_name.items():
        if set(signatures_by_version) != expected:
            continue
        signatures = [values for values in signatures_by_version.values()]
        if any(len(values) != 1 for values in signatures):
            continue
        if len({next(iter(values)) for values in signatures}) == 1:
            compatible.append(name)
    return tuple(sorted(compatible, key=str.casefold)[:5])


def _quality_window(connection: SqlConnection) -> tuple[datetime, datetime] | None:
    row = (
        connection.execute(
            text(
                "SELECT MIN(dv.published_at_utc) AS first_published_at_utc,"
                "MAX(dv.published_at_utc) AS last_published_at_utc "
                "FROM dataset.dataset_version dv "
                "JOIN ingestion.import_batch b "
                "ON b.import_batch_id=dv.input_batch_id "
                "WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND b.business_domain='PRODUCTION'"
            )
        )
        .mappings()
        .one()
    )
    first = row["first_published_at_utc"]
    last = row["last_published_at_utc"]
    if first is None or last is None:
        return None
    if not isinstance(first, datetime) or not isinstance(last, datetime):
        raise VerificationError(
            "QUALITY_WINDOW_INVALID",
            "Quality 性能验收时间窗口无效",
        )
    lower = first.replace(tzinfo=UTC) if first.tzinfo is None else first.astimezone(UTC)
    upper = last.replace(tzinfo=UTC) if last.tzinfo is None else last.astimezone(UTC)
    return lower - timedelta(seconds=1), upper + timedelta(seconds=1)


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


def _sample(operation: Callable[[], Any], audit: ReadOnlyAudit) -> dict[str, Any]:
    statements_before = audit.statement_count
    started = perf_counter_ns()
    response = operation()
    response_bytes = _serialized_size(response)
    elapsed_ms = (perf_counter_ns() - started) / 1_000_000
    return {
        "elapsed_ms": elapsed_ms,
        "sql_statement_count": audit.statement_count - statements_before,
        "response_bytes": response_bytes,
    }


def _skip_probe(definition: ProbeDefinition) -> dict[str, Any]:
    return {
        "name": definition.name,
        "status": "SKIP",
        "reason_code": definition.insufficient_reason,
        "dataset_requirement": {
            "observed_records": definition.observed_records,
            "minimum_records": definition.minimum_records,
        },
        "thresholds": _thresholds(definition),
        "cold_candidate": None,
        "warm": {
            "sample_count": 0,
            "elapsed_ms": [],
            "p50_ms": None,
            "p95_ms": None,
            "sql_statement_count": [],
            "response_bytes": [],
        },
    }


def _thresholds(definition: ProbeDefinition) -> dict[str, float]:
    thresholds = {"warm_p95_ms_lte": definition.warm_p95_limit_ms}
    if definition.cold_candidate_limit_ms is not None:
        thresholds["cold_candidate_ms_lte"] = definition.cold_candidate_limit_ms
    return thresholds


def _measure_probe(
    definition: ProbeDefinition,
    audit: ReadOnlyAudit,
    *,
    warm_runs: int,
) -> dict[str, Any]:
    if warm_runs < 3:
        raise ValueError("warm_runs must be at least three")
    if definition.observed_records < definition.minimum_records:
        return _skip_probe(definition)
    try:
        cold = _sample(definition.operation, audit)
        warm = [_sample(definition.operation, audit) for _ in range(warm_runs)]
    except Exception as exc:  # noqa: BLE001 - probe boundary must redact failures
        return {
            "name": definition.name,
            "status": "FAIL",
            "reason_code": "PROBE_EXECUTION_FAILED",
            "exception_type": type(exc).__name__,
            "dataset_requirement": {
                "observed_records": definition.observed_records,
                "minimum_records": definition.minimum_records,
            },
            "thresholds": _thresholds(definition),
            "cold_candidate": None,
            "warm": {
                "sample_count": 0,
                "elapsed_ms": [],
                "p50_ms": None,
                "p95_ms": None,
                "sql_statement_count": [],
                "response_bytes": [],
            },
        }
    warm_elapsed = [float(item["elapsed_ms"]) for item in warm]
    warm_p50 = _percentile(warm_elapsed, 0.50)
    warm_p95 = _percentile(warm_elapsed, 0.95)
    threshold_passed = warm_p95 <= definition.warm_p95_limit_ms
    if definition.cold_candidate_limit_ms is not None:
        threshold_passed = threshold_passed and (
            float(cold["elapsed_ms"]) <= definition.cold_candidate_limit_ms
        )
    return {
        "name": definition.name,
        "status": "PASS" if threshold_passed else "FAIL",
        "reason_code": "THRESHOLDS_MET" if threshold_passed else "THRESHOLD_EXCEEDED",
        "dataset_requirement": {
            "observed_records": definition.observed_records,
            "minimum_records": definition.minimum_records,
        },
        "thresholds": _thresholds(definition),
        "cold_candidate": {
            "elapsed_ms": _round_ms(float(cold["elapsed_ms"])),
            "sql_statement_count": int(cold["sql_statement_count"]),
            "response_bytes": int(cold["response_bytes"]),
        },
        "warm": {
            "sample_count": warm_runs,
            "elapsed_ms": [_round_ms(value) for value in warm_elapsed],
            "p50_ms": _round_ms(warm_p50),
            "p95_ms": _round_ms(warm_p95),
            "sql_statement_count": [int(item["sql_statement_count"]) for item in warm],
            "response_bytes": [int(item["response_bytes"]) for item in warm],
        },
    }


def _overall_status(probes: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(probe["status"]) for probe in probes}
    if "FAIL" in statuses:
        return "FAIL"
    if "SKIP" in statuses or not probes:
        return "SKIP"
    return "PASS"


def _dataset_operation(
    service: Any,
    candidate: DatasetCandidate,
    operation: Callable[[], Any],
) -> Any:
    service.assert_dataset_access(
        candidate.dataset_id,
        DEVELOPMENT_PRINCIPAL,
        version_no=candidate.version_no,
    )
    return operation()


def _compare_operation(
    service: Any,
    candidates: Sequence[DatasetCandidate],
    request: DatasetComparisonRequest,
) -> Any:
    for candidate in candidates:
        service.assert_dataset_access(
            candidate.dataset_id,
            DEVELOPMENT_PRINCIPAL,
            version_no=candidate.version_no,
        )
    return service.compare(request)


def verify(
    raw_engine: Engine,
    *,
    warm_runs: int = DEFAULT_WARM_RUNS,
    dataset_service_factory: Callable[[Any], Any] = SqlDatasetService,
    m2_service_factory: Callable[[Any], Any] = SqlM2QueryService,
    management_service_factory: Callable[[Any], Any] = SqlManagementService,
) -> dict[str, Any]:
    if warm_runs < 3 or warm_runs > 20:
        raise VerificationError(
            "WARM_RUN_COUNT_INVALID",
            "warm-runs 必须在 3 到 20 之间",
        )
    audit = ReadOnlyAudit()
    engine = _ReadOnlyEngine(raw_engine, audit)
    with engine.connect() as connection:
        identity = _identity(connection)
        scale_before = _database_scale(connection)
        candidates = _dataset_candidates(connection)
        comparison_candidates = _comparison_candidates(candidates)
        common_parameters = _common_parameters(connection, comparison_candidates)
        quality_window = _quality_window(connection)

    dataset_service = dataset_service_factory(engine)
    m2_service = m2_service_factory(engine)
    management_service = management_service_factory(engine)
    largest = candidates[0] if candidates else None
    compare_request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": item.dataset_id, "version_no": item.version_no}
            for item in comparison_candidates
        ]
        or [{"dataset_id": 1, "version_no": 1}]
    )
    parameter_compare_request = compare_request.model_copy(
        update={"parameters": list(common_parameters)}
    )

    definitions: list[ProbeDefinition] = []
    for stage in ("CP", "FT"):
        stage_batches = int(scale_before[f"production_{stage.lower()}_import_batches"])
        definitions.append(
            ProbeDefinition(
                name=f"stage_{stage.lower()}_uploads",
                warm_p95_limit_ms=3_000.0,
                operation=lambda stage=stage: m2_service.list_uploads_page(
                    DEVELOPMENT_PRINCIPAL,
                    "PRODUCTION",
                    stage,
                    M2PageFilters(page=1, page_size=PAGE_SIZE),
                ),
                observed_records=stage_batches,
                minimum_records=1,
                insufficient_reason=f"PRODUCTION_{stage}_UPLOAD_MISSING",
            )
        )
    current_count = int(scale_before["production_current_dataset_versions"])
    definitions.append(
        ProbeDefinition(
            name="current_catalog",
            warm_p95_limit_ms=3_000.0,
            operation=lambda: m2_service.list_current_datasets(
                DEVELOPMENT_PRINCIPAL,
                M2PageFilters(
                    page=1,
                    page_size=PAGE_SIZE,
                    business_domain="PRODUCTION",
                ),
            ),
            observed_records=current_count,
            minimum_records=1,
            insufficient_reason="PRODUCTION_CURRENT_DATASET_MISSING",
        )
    )
    definitions.append(
        ProbeDefinition(
            name="dataset_chart",
            warm_p95_limit_ms=3_000.0,
            operation=(
                lambda: (
                    _dataset_operation(
                        dataset_service,
                        largest,
                        lambda: dataset_service.get_chart_data(
                            largest.dataset_id, largest.version_no
                        ),
                    )
                    if largest is not None
                    else None
                )
            ),
            observed_records=largest.unit_count if largest is not None else 0,
            minimum_records=1,
            insufficient_reason="PRODUCTION_CHART_DATASET_MISSING",
        )
    )
    definitions.append(
        ProbeDefinition(
            name="dataset_detail_page",
            warm_p95_limit_ms=3_000.0,
            operation=(
                lambda: (
                    _dataset_operation(
                        dataset_service,
                        largest,
                        lambda: dataset_service.get_detail_page(
                            largest.dataset_id,
                            largest.version_no,
                            page=1,
                            page_size=PAGE_SIZE,
                        ),
                    )
                    if largest is not None
                    else None
                )
            ),
            observed_records=largest.unit_count if largest is not None else 0,
            minimum_records=1,
            insufficient_reason="PRODUCTION_DETAIL_DATASET_MISSING",
        )
    )
    definitions.append(
        ProbeDefinition(
            name="quality_summary",
            warm_p95_limit_ms=3_000.0,
            cold_candidate_limit_ms=5_000.0,
            operation=(
                lambda: (
                    management_service.quality_summary(
                        principal=DEVELOPMENT_PRINCIPAL,
                        from_utc=quality_window[0],
                        to_utc=quality_window[1],
                        access_scope="PERSONAL",
                        business_domain="PRODUCTION",
                        recent_limit=20,
                    )
                    if quality_window is not None
                    else None
                )
            ),
            observed_records=current_count,
            minimum_records=1,
            insufficient_reason="PRODUCTION_QUALITY_DATASET_MISSING",
        )
    )
    compare_count = len(comparison_candidates)
    definitions.append(
        ProbeDefinition(
            name="compare_8_datasets_no_parameters",
            warm_p95_limit_ms=3_000.0,
            operation=lambda: _compare_operation(
                dataset_service, comparison_candidates, compare_request
            ),
            observed_records=compare_count,
            minimum_records=8,
            insufficient_reason="EIGHT_COMPATIBLE_DATASETS_MISSING",
        )
    )
    definitions.append(
        ProbeDefinition(
            name="compare_8_datasets_5_parameters",
            warm_p95_limit_ms=5_000.0,
            operation=lambda: _compare_operation(
                dataset_service, comparison_candidates, parameter_compare_request
            ),
            observed_records=len(common_parameters) if compare_count == 8 else 0,
            minimum_records=5,
            insufficient_reason=(
                "EIGHT_COMPATIBLE_DATASETS_MISSING"
                if compare_count < 8
                else "FIVE_COMMON_COMPATIBLE_PARAMETERS_MISSING"
            ),
        )
    )

    probes = [
        _measure_probe(definition, audit, warm_runs=warm_runs)
        for definition in definitions
    ]
    with engine.connect() as connection:
        scale_after = _database_scale(connection)
    _assert_scale_unchanged(scale_before, scale_after)
    if audit.blocked_statement_count:
        raise VerificationError(
            "READ_ONLY_GUARD_TRIGGERED",
            "性能验收期间只读 SQL 门禁被触发",
        )
    overall = _overall_status(probes)
    return {
        "verification": overall,
        "contract": "v1.2-performance-sql-readonly",
        "identity": identity,
        "methodology": {
            "elapsed_scope": "service_call_plus_json_serialization",
            "cold_candidate": (
                "first measured invocation in this process; database and OS caches "
                "are not flushed because this verifier is strictly read-only"
            ),
            "warm": f"the next {warm_runs} sequential invocations",
            "percentile": "linear interpolation over measured samples",
            "decision": "FAIL precedes SKIP; every required probe must PASS",
        },
        "database_scale": scale_before,
        "candidate_coverage": {
            "production_nonempty_current_dataset_count_examined": len(candidates),
            "compatible_compare_dataset_count": compare_count,
            "common_compatible_parameter_count_capped_at_five": len(common_parameters),
        },
        "probes": probes,
        "read_only": {
            "policy": "SELECT_OR_READ_ONLY_CTE_ONLY",
            "executed_statement_count": audit.statement_count,
            "blocked_statement_count": audit.blocked_statement_count,
            "business_row_counts_unchanged": True,
        },
        "evidence_redaction": {
            "omitted": [
                "connection_string",
                "server_name",
                "database_login",
                "application_login",
                "dataset_id",
                "dataset_version_id",
                "product",
                "lot",
                "wafer",
                "parameter_name",
                "source_file_name",
                "response_value",
            ],
            "response_evidence": "serialized_byte_count_only",
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only v1.2 performance acceptance for TMS_G0_DEV"
    )
    parser.add_argument(
        "--warm-runs",
        type=int,
        default=DEFAULT_WARM_RUNS,
        help="sequential warm samples per probe (3-20; default: 5)",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.2-performance-sql-readonly",
                    "error_code": "DATABASE_URL_REQUIRED",
                    "message": "TMS_DATABASE_URL is required",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
    engine = get_engine()
    try:
        evidence = verify(engine, warm_runs=args.warm_runs)
    except VerificationError as exc:
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.2-performance-sql-readonly",
                    "error_code": exc.code,
                    "message": exc.safe_message,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except Exception as exc:  # noqa: BLE001 - CLI boundary must redact failures
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.2-performance-sql-readonly",
                    "error_code": "UNEXPECTED_FAILURE",
                    "exception_type": type(exc).__name__,
                    "message": "性能验收发生未预期错误，详细内容已从输出中移除",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    finally:
        engine.dispose()
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    if evidence["verification"] == "FAIL":
        raise SystemExit(2)
    if evidence["verification"] == "SKIP":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
