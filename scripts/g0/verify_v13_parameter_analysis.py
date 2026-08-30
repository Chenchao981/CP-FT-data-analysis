from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Protocol

from sqlalchemy import Engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.datasets import (
    DatasetParameterAnalysisRequest,
    DatasetParameterAnalysisType,
)
from app.infrastructure.database import get_engine
from app.infrastructure.sql_dataset_service import SqlDatasetService

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0019"
DEFAULT_WARM_RUNS = 5
DEFAULT_HISTOGRAM_BIN_COUNT = 20
MAX_WARM_RUNS = 100
MAX_CANDIDATE_MEASUREMENTS = 2_000_000
MAX_CANDIDATES = 200
MAX_STAGE_CANDIDATES_EXAMINED = 20
_TECHNICAL_G0_APPROVED_PARAMETER_ANALYSIS_RULE_CODES = frozenset(
    {
        "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
        "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
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
class AnalysisCandidate:
    dataset_id: int
    dataset_version_id: int
    version_no: int
    test_stage: str
    parameter_name: str
    measurement_count: int
    numeric_count: int


@dataclass(frozen=True, slots=True)
class FormalSpecCoverage:
    status: str
    reason_code: str
    signature_count: int
    signature_digest: str | None

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "compatible_signature_count": self.signature_count,
            "signature_sha256": self.signature_digest,
        }


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    counts: Mapping[str, Mapping[str, int]]
    canonical_group_count: int
    canonical_summary_digest: str
    current_group_count: int
    current_summary_digest: str

    def public(self) -> dict[str, Any]:
        return {
            "counts": {scope: dict(values) for scope, values in self.counts.items()},
            "canonical": {
                "summary_group_count": self.canonical_group_count,
                "summary_sha256": self.canonical_summary_digest,
            },
            "current": {
                "summary_group_count": self.current_group_count,
                "summary_sha256": self.current_summary_digest,
            },
        }


@dataclass(frozen=True, slots=True)
class IndependentStatistics:
    row_count: int
    numeric_count: int
    status_counts: Mapping[str, int]
    minimum: float | None
    maximum: float | None
    average: float | None
    sample_stddev: float | None
    box_values: Mapping[str, float] | None
    outlier_count: int
    histogram_total: int
    histogram_non_empty_bin_count: int


def _assert_read_only_sql(sql: str) -> None:
    lexical = _SQL_LITERAL_OR_COMMENT.sub(" ", sql).strip()
    statements = [item.strip() for item in lexical.split(";") if item.strip()]
    if len(statements) != 1:
        raise VerificationError(
            "READ_ONLY_MULTIPLE_STATEMENTS",
            "v1.3 只读验收拒绝空语句或多语句批次",
        )
    statement = statements[0]
    first = re.match(r"[A-Za-z]+", statement)
    if first is None or first.group(0).upper() not in {"SELECT", "WITH"}:
        raise VerificationError(
            "READ_ONLY_STATEMENT_REJECTED",
            "v1.3 只读验收仅允许 SELECT 或只读 CTE",
        )
    for token in _MUTATING_SQL_TOKENS:
        if re.search(rf"\b{token}\b", statement, flags=re.IGNORECASE):
            raise VerificationError(
                "READ_ONLY_MUTATION_REJECTED",
                "v1.3 只读验收检测到被禁止的数据库变更语句",
            )
    if re.search(r"\bSELECT\b[\s\S]*?\bINTO\b", statement, flags=re.IGNORECASE):
        raise VerificationError(
            "READ_ONLY_SELECT_INTO_REJECTED",
            "v1.3 只读验收禁止 SELECT INTO",
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
            f"v1.3 只读验收只允许数据库 {EXPECTED_DATABASE}",
        )
    if revision != EXPECTED_SCHEMA_REVISION:
        raise VerificationError(
            "SCHEMA_REVISION_MISMATCH",
            f"v1.3 只读验收要求 schema {EXPECTED_SCHEMA_REVISION}",
        )
    if (
        "MICROSOFT SQL SERVER" not in version_banner.upper()
        or not product_version
        or engine_edition <= 0
    ):
        raise VerificationError(
            "DATABASE_ENGINE_MISMATCH",
            "v1.3 只读验收要求 Microsoft SQL Server",
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


def _digest_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise VerificationError("SUMMARY_VALUE_INVALID", "快照摘要出现非有限数值")
        return format(value, ".12g")
    if isinstance(value, Decimal):
        return format(value, ".12g")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if is_dataclass(value) and not isinstance(value, type):
        return _digest_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _digest_value(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_digest_value(item) for item in value]
    return str(value)


def _redacted_digest(scope: str, value: Any) -> str:
    payload = json.dumps(
        _digest_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(f"{scope}\n{payload}".encode()).hexdigest()


def _snapshot_counts(connection: SqlConnection) -> dict[str, dict[str, int]]:
    row = (
        connection.execute(
            text(
                "SELECT "
                "(SELECT COUNT_BIG(*) FROM test.test_run) AS canonical_test_run,"
                "(SELECT COUNT_BIG(*) FROM test.unit_result) AS canonical_unit_result,"
                "(SELECT COUNT_BIG(*) FROM test.measurement) AS canonical_measurement,"
                "(SELECT COUNT_BIG(*) FROM analytics.v_current_dataset_version) "
                "AS current_dataset_version,"
                "(SELECT COUNT_BIG(*) FROM analytics.v_current_test_run) "
                "AS current_test_run,"
                "(SELECT COUNT_BIG(*) FROM analytics.v_current_unit_result) "
                "AS current_unit_result,"
                "(SELECT COUNT_BIG(*) FROM analytics.v_current_measurement) "
                "AS current_measurement"
            )
        )
        .mappings()
        .one()
    )
    result = {
        "canonical": {
            "test_run": int(row["canonical_test_run"] or 0),
            "unit_result": int(row["canonical_unit_result"] or 0),
            "measurement": int(row["canonical_measurement"] or 0),
        },
        "current": {
            "dataset_version": int(row["current_dataset_version"] or 0),
            "test_run": int(row["current_test_run"] or 0),
            "unit_result": int(row["current_unit_result"] or 0),
            "measurement": int(row["current_measurement"] or 0),
        },
    }
    if any(value < 0 for scope in result.values() for value in scope.values()):
        raise VerificationError(
            "COUNT_SNAPSHOT_INVALID", "Canonical/Current 计数快照出现无效负数"
        )
    return result


def _canonical_summary_rows(connection: SqlConnection) -> tuple[tuple[Any, ...], ...]:
    rows = (
        connection.execute(
            text(
                "SELECT tr.run_id,tr.processing_run_id,tr.test_stage,tr.lot_id,"
                "COUNT_BIG(DISTINCT ur.unit_id) AS unit_count,"
                "COUNT_BIG(m.measurement_id) AS measurement_count,"
                "SUM(CASE WHEN m.measurement_status='MEASURED' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS measured_count,"
                "SUM(CASE WHEN m.measurement_status='OVER_RANGE' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS over_range_count,"
                "SUM(CASE WHEN m.measurement_status='UNDER_RANGE' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS under_range_count,"
                "MIN(CASE WHEN m.measurement_status='MEASURED' "
                "THEN m.value_numeric END) AS numeric_minimum,"
                "MAX(CASE WHEN m.measurement_status='MEASURED' "
                "THEN m.value_numeric END) AS numeric_maximum,"
                "MIN(m.measurement_id) AS minimum_measurement_id,"
                "MAX(m.measurement_id) AS maximum_measurement_id,"
                "MAX(m.created_at_utc) AS latest_measurement_created_at,"
                "SUM(CONVERT(bigint,BINARY_CHECKSUM("
                "ur.unit_id,ur.logical_unit_key,ur.attempt_no,ur.unit_sequence,"
                "ur.wafer_id,ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                "ur.overall_result,m.measurement_id,m.test_item_id,m.value_numeric,"
                "m.value_text,m.raw_value,m.measurement_status,m.tester_pass_flag,"
                "m.source_column_index))) AS row_checksum_sum "
                "FROM test.test_run tr "
                "LEFT JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "GROUP BY tr.run_id,tr.processing_run_id,tr.test_stage,tr.lot_id "
                "ORDER BY tr.run_id"
            )
        )
        .mappings()
        .all()
    )
    fields = (
        "run_id",
        "processing_run_id",
        "test_stage",
        "lot_id",
        "unit_count",
        "measurement_count",
        "measured_count",
        "over_range_count",
        "under_range_count",
        "numeric_minimum",
        "numeric_maximum",
        "minimum_measurement_id",
        "maximum_measurement_id",
        "latest_measurement_created_at",
        "row_checksum_sum",
    )
    return tuple(tuple(row[field] for field in fields) for row in rows)


def _current_summary_rows(connection: SqlConnection) -> tuple[tuple[Any, ...], ...]:
    rows = (
        connection.execute(
            text(
                "SELECT d.dataset_id,dv.dataset_version_id,dv.version_no,d.test_stage,"
                "dv.status,dv.is_current,dv.unit_count AS stored_unit_count,"
                "dv.measurement_count AS stored_measurement_count,tr.run_id,tr.lot_id,"
                "COUNT_BIG(DISTINCT ur.unit_id) AS unit_count,"
                "COUNT_BIG(m.measurement_id) AS measurement_count,"
                "SUM(CASE WHEN m.measurement_status='MEASURED' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS measured_count,"
                "SUM(CASE WHEN m.measurement_status='OVER_RANGE' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS over_range_count,"
                "SUM(CASE WHEN m.measurement_status='UNDER_RANGE' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS under_range_count,"
                "MIN(CASE WHEN m.measurement_status='MEASURED' "
                "THEN m.value_numeric END) AS numeric_minimum,"
                "MAX(CASE WHEN m.measurement_status='MEASURED' "
                "THEN m.value_numeric END) AS numeric_maximum,"
                "MIN(m.measurement_id) AS minimum_measurement_id,"
                "MAX(m.measurement_id) AS maximum_measurement_id,"
                "MAX(m.created_at_utc) AS latest_measurement_created_at,"
                "SUM(CONVERT(bigint,BINARY_CHECKSUM("
                "ur.unit_id,ur.logical_unit_key,ur.attempt_no,ur.unit_sequence,"
                "ur.wafer_id,ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                "ur.overall_result,m.measurement_id,m.test_item_id,m.value_numeric,"
                "m.value_text,m.raw_value,m.measurement_status,m.tester_pass_flag,"
                "m.source_column_index))) AS row_checksum_sum "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "LEFT JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "LEFT JOIN test.test_run tr "
                "ON tr.processing_run_id=dvr.processing_run_id "
                "LEFT JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                "GROUP BY d.dataset_id,dv.dataset_version_id,dv.version_no,d.test_stage,"
                "dv.status,dv.is_current,dv.unit_count,dv.measurement_count,tr.run_id,tr.lot_id "
                "ORDER BY d.dataset_id,dv.dataset_version_id,tr.run_id"
            )
        )
        .mappings()
        .all()
    )
    fields = (
        "dataset_id",
        "dataset_version_id",
        "version_no",
        "test_stage",
        "status",
        "is_current",
        "stored_unit_count",
        "stored_measurement_count",
        "run_id",
        "lot_id",
        "unit_count",
        "measurement_count",
        "measured_count",
        "over_range_count",
        "under_range_count",
        "numeric_minimum",
        "numeric_maximum",
        "minimum_measurement_id",
        "maximum_measurement_id",
        "latest_measurement_created_at",
        "row_checksum_sum",
    )
    return tuple(tuple(row[field] for field in fields) for row in rows)


def _database_snapshot(connection: SqlConnection) -> DatabaseSnapshot:
    counts = _snapshot_counts(connection)
    canonical_rows = _canonical_summary_rows(connection)
    current_rows = _current_summary_rows(connection)
    return DatabaseSnapshot(
        counts=counts,
        canonical_group_count=len(canonical_rows),
        canonical_summary_digest=_redacted_digest(
            "v13-canonical-summary", canonical_rows
        ),
        current_group_count=len(current_rows),
        current_summary_digest=_redacted_digest("v13-current-summary", current_rows),
    )


def _assert_snapshot_unchanged(
    before: DatabaseSnapshot, after: DatabaseSnapshot
) -> None:
    if before != after:
        raise VerificationError(
            "READ_ONLY_SNAPSHOT_DRIFT",
            "验收期间 Canonical/Current 关键计数或摘要 digest 发生变化，结果作废",
        )


def _candidate_rows(connection: SqlConnection) -> tuple[AnalysisCandidate, ...]:
    rows = (
        connection.execute(
            text(
                f"SELECT TOP ({MAX_CANDIDATES}) d.dataset_id,dv.dataset_version_id,"
                "dv.version_no,d.test_stage,tid.raw_item_name,"
                "COUNT_BIG(*) AS measurement_count,"
                "SUM(CASE WHEN m.measurement_status='MEASURED' "
                "AND m.value_numeric IS NOT NULL THEN CONVERT(bigint,1) ELSE 0 END) "
                "AS numeric_count "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "AND tid.program_version_id=tr.program_version_id "
                "WHERE d.lifecycle_status='ACTIVE' "
                "AND d.test_stage IN('CP','FT') "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.is_analysis_parameter=1 "
                "AND tid.raw_item_name IS NOT NULL "
                "GROUP BY d.dataset_id,dv.dataset_version_id,dv.version_no,"
                "d.test_stage,tid.raw_item_name "
                "HAVING COUNT_BIG(*)>0 "
                "ORDER BY CASE WHEN d.test_stage='CP' THEN 0 ELSE 1 END,"
                "numeric_count DESC,measurement_count DESC,dv.dataset_version_id DESC,"
                "tid.raw_item_name"
            )
        )
        .mappings()
        .all()
    )
    candidates: list[AnalysisCandidate] = []
    for row in rows:
        stage = str(row["test_stage"])
        parameter = str(row["raw_item_name"] or "")
        measurement_count = int(row["measurement_count"] or 0)
        numeric_count = int(row["numeric_count"] or 0)
        if (
            stage not in {"CP", "FT"}
            or not parameter
            or parameter != parameter.strip()
            or len(parameter) > 200
            or measurement_count < 1
            or numeric_count < 0
            or numeric_count > measurement_count
        ):
            raise VerificationError(
                "ANALYSIS_CANDIDATE_INVALID", "参数分析候选数据合同无效"
            )
        candidates.append(
            AnalysisCandidate(
                dataset_id=int(row["dataset_id"]),
                dataset_version_id=int(row["dataset_version_id"]),
                version_no=int(row["version_no"]),
                test_stage=stage,
                parameter_name=parameter,
                measurement_count=measurement_count,
                numeric_count=numeric_count,
            )
        )
    return tuple(candidates)


def _condition_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise VerificationError(
            "ANALYSIS_SPEC_CONTRACT_INVALID", "参数测试条件元数据无效"
        ) from exc
    if not isinstance(decoded, dict):
        raise VerificationError(
            "ANALYSIS_SPEC_CONTRACT_INVALID", "参数测试条件元数据无效"
        )
    raw_text = decoded.get("text")
    if raw_text is None:
        return None
    if not isinstance(raw_text, str):
        raise VerificationError(
            "ANALYSIS_SPEC_CONTRACT_INVALID", "参数测试条件元数据无效"
        )
    normalized = " ".join(raw_text.split())
    return normalized or None


def _optional_float(value: Any, *, code: str) -> float | None:
    if value is None:
        return None
    converted = float(value)
    if not math.isfinite(converted):
        raise VerificationError(code, "参数分析 SQL 摘要出现非有限数值")
    return converted


def _candidate_identity_is_compatible(
    connection: SqlConnection, candidate: AnalysisCandidate
) -> bool:
    rows = (
        connection.execute(
            text(
                "SELECT DISTINCT tid.canonical_parameter_code,tid.unit_code,"
                "tid.program_lsl,tid.program_usl,tid.condition_json "
                "FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN mdm.test_item_definition tid "
                "ON tid.program_version_id=tr.program_version_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.is_analysis_parameter=1 "
                "AND tid.raw_item_name=:parameter_name"
            ),
            {
                "dataset_id": candidate.dataset_id,
                "version_no": candidate.version_no,
                "parameter_name": candidate.parameter_name,
            },
        )
        .mappings()
        .all()
    )
    canonical_codes: set[str | None] = set()
    signatures: set[tuple[Any, ...]] = set()
    for row in rows:
        canonical_codes.add(
            str(row["canonical_parameter_code"]).strip() or None
            if row["canonical_parameter_code"] is not None
            else None
        )
        signatures.add(
            (
                str(row["unit_code"]).strip() or None
                if row["unit_code"] is not None
                else None,
                _optional_float(
                    row["program_lsl"], code="ANALYSIS_PARAMETER_IDENTITY_INVALID"
                ),
                _optional_float(
                    row["program_usl"], code="ANALYSIS_PARAMETER_IDENTITY_INVALID"
                ),
                _condition_text(row["condition_json"]),
            )
        )
    return len(canonical_codes) == 1 and len(signatures) == 1


def _formal_spec_rows(
    connection: SqlConnection, candidate: AnalysisCandidate
) -> tuple[Mapping[str, Any], ...]:
    if candidate.test_stage == "CP":
        joins = (
            "JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
            "AND ss.status='RELEASED' "
            "JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
            "AND si.test_item_id=tid.test_item_id "
        )
    else:
        joins = (
            "JOIN mdm.spec_binding sb ON sb.active=1 "
            "AND sb.program_version_id=tr.program_version_id "
            "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
            "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
            "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
            "JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
            "AND ss.status='RELEASED' "
            "JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
            "AND si.test_item_id=tid.test_item_id "
        )
    return tuple(
        connection.execute(
            text(
                "SELECT DISTINCT ss.spec_set_id,tid.unit_code AS program_unit_code,"
                "tid.condition_json AS program_condition_json,"
                "si.unit_code AS spec_unit_code,si.lsl,si.usl,"
                "si.condition_json AS spec_condition_json "
                "FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr "
                "ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN mdm.test_item_definition tid "
                "ON tid.program_version_id=tr.program_version_id "
                + joins
                + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.is_analysis_parameter=1 "
                "AND tid.raw_item_name=:parameter_name "
                "ORDER BY ss.spec_set_id"
            ),
            {
                "dataset_id": candidate.dataset_id,
                "version_no": candidate.version_no,
                "parameter_name": candidate.parameter_name,
            },
        )
        .mappings()
        .all()
    )


def _formal_spec_coverage(
    connection: SqlConnection, candidate: AnalysisCandidate
) -> FormalSpecCoverage:
    rows = _formal_spec_rows(connection, candidate)
    if not rows:
        return FormalSpecCoverage(
            status="SKIP",
            reason_code="FORMAL_RELEASED_SPEC_NOT_FOUND",
            signature_count=0,
            signature_digest=None,
        )
    spec_ids: set[int] = set()
    signatures: set[tuple[Any, ...]] = set()
    context_mismatch = False
    reversed_limits = False
    for row in rows:
        spec_ids.add(int(row["spec_set_id"]))
        program_unit = (
            str(row["program_unit_code"]).strip() or None
            if row["program_unit_code"] is not None
            else None
        )
        spec_unit = (
            str(row["spec_unit_code"]).strip() or None
            if row["spec_unit_code"] is not None
            else None
        )
        program_condition = _condition_text(row["program_condition_json"])
        spec_condition = _condition_text(row["spec_condition_json"])
        lsl = _optional_float(row["lsl"], code="FORMAL_SPEC_VALUE_INVALID")
        usl = _optional_float(row["usl"], code="FORMAL_SPEC_VALUE_INVALID")
        context_mismatch = context_mismatch or (
            program_unit != spec_unit or program_condition != spec_condition
        )
        reversed_limits = reversed_limits or (
            lsl is not None and usl is not None and lsl > usl
        )
        signatures.add((spec_unit, lsl, usl, spec_condition))
    digest = _redacted_digest("v13-formal-spec-signature", sorted(signatures, key=str))
    if reversed_limits:
        return FormalSpecCoverage(
            status="FAIL",
            reason_code="FORMAL_SPEC_LIMITS_REVERSED",
            signature_count=len(signatures),
            signature_digest=digest,
        )
    if len(spec_ids) != 1 or len(signatures) != 1 or context_mismatch:
        return FormalSpecCoverage(
            status="SKIP",
            reason_code="FORMAL_SPEC_CONTEXT_AMBIGUOUS",
            signature_count=len(signatures),
            signature_digest=digest,
        )
    signature = next(iter(signatures))
    if signature[1] is None and signature[2] is None:
        return FormalSpecCoverage(
            status="SKIP",
            reason_code="FORMAL_SPEC_LIMIT_MISSING",
            signature_count=1,
            signature_digest=digest,
        )
    return FormalSpecCoverage(
        status="PASS",
        reason_code="FORMAL_SPEC_AVAILABLE",
        signature_count=1,
        signature_digest=digest,
    )


def _select_stage_candidates(
    connection: SqlConnection,
    candidates: Sequence[AnalysisCandidate],
) -> dict[str, tuple[AnalysisCandidate, FormalSpecCoverage] | None]:
    selected: dict[str, tuple[AnalysisCandidate, FormalSpecCoverage] | None] = {
        "CP": None,
        "FT": None,
    }
    for stage in ("CP", "FT"):
        fallback: tuple[AnalysisCandidate, FormalSpecCoverage] | None = None
        examined = 0
        for candidate in candidates:
            if candidate.test_stage != stage:
                continue
            if examined >= MAX_STAGE_CANDIDATES_EXAMINED:
                break
            examined += 1
            if (
                candidate.numeric_count < 1
                or candidate.measurement_count > MAX_CANDIDATE_MEASUREMENTS
                or not _candidate_identity_is_compatible(connection, candidate)
            ):
                continue
            coverage = _formal_spec_coverage(connection, candidate)
            if fallback is None:
                fallback = (candidate, coverage)
            if coverage.status == "PASS":
                selected[stage] = (candidate, coverage)
                break
        if selected[stage] is None:
            selected[stage] = fallback
    return selected


def _descriptive_statistics(
    connection: SqlConnection, candidate: AnalysisCandidate
) -> tuple[Mapping[str, Any], dict[str, int]]:
    status_columns = ",".join(
        "SUM(CASE WHEN m.measurement_status='"
        + status
        + "' THEN CONVERT(bigint,1) ELSE 0 END) AS status_"
        + status.lower()
        for status in _MEASUREMENT_STATUSES
    )
    row = (
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) AS row_count,"
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
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.raw_item_name=:parameter_name"
            ),
            {
                "dataset_id": candidate.dataset_id,
                "version_no": candidate.version_no,
                "parameter_name": candidate.parameter_name,
            },
        )
        .mappings()
        .one()
    )
    statuses = {
        status: int(row[f"status_{status.lower()}"] or 0)
        for status in _MEASUREMENT_STATUSES
    }
    return row, statuses


def _box_statistics(
    connection: SqlConnection, candidate: AnalysisCandidate
) -> Mapping[str, Any] | None:
    row = (
        connection.execute(
            text(
                ";WITH numeric_values AS ("
                "SELECT m.value_numeric FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.raw_item_name=:parameter_name "
                "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                "quartiles AS (SELECT value_numeric,"
                "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY value_numeric) OVER() AS q1,"
                "PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY value_numeric) "
                "OVER() AS median,"
                "PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY value_numeric) OVER() AS q3 "
                "FROM numeric_values) "
                "SELECT MIN(value_numeric) AS minimum,MAX(q1) AS q1,"
                "MAX(median) AS median,MAX(q3) AS q3,MAX(value_numeric) AS maximum,"
                "MIN(CASE WHEN value_numeric>=q1-1.5*(q3-q1) THEN value_numeric END) "
                "AS lower_whisker,"
                "MAX(CASE WHEN value_numeric<=q3+1.5*(q3-q1) THEN value_numeric END) "
                "AS upper_whisker,"
                "SUM(CASE WHEN value_numeric<q1-1.5*(q3-q1) "
                "OR value_numeric>q3+1.5*(q3-q1) "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS outlier_count "
                "FROM quartiles"
            ),
            {
                "dataset_id": candidate.dataset_id,
                "version_no": candidate.version_no,
                "parameter_name": candidate.parameter_name,
            },
        )
        .mappings()
        .one()
    )
    return None if row["minimum"] is None else row


def _histogram_count_statistics(
    connection: SqlConnection,
    candidate: AnalysisCandidate,
    *,
    bin_count: int,
) -> tuple[int, int]:
    row = (
        connection.execute(
            text(
                ";WITH numeric_values AS ("
                "SELECT m.value_numeric FROM dataset.dataset_version dv "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND dv.status='PUBLISHED' AND dv.is_current=1 "
                "AND tid.raw_item_name=:parameter_name "
                "AND m.measurement_status='MEASURED' AND m.value_numeric IS NOT NULL),"
                "bounds AS (SELECT MIN(value_numeric) AS range_min,"
                "MAX(value_numeric) AS range_max FROM numeric_values),"
                "bucketed AS (SELECT CASE WHEN b.range_min=b.range_max THEN 0 "
                "WHEN v.value_numeric=b.range_max THEN :histogram_bin_count-1 "
                "ELSE CONVERT(int,FLOOR((v.value_numeric-b.range_min)*"
                ":histogram_bin_count/NULLIF(b.range_max-b.range_min,0))) END "
                "AS bin_index FROM numeric_values v CROSS JOIN bounds b),"
                "bins AS (SELECT bin_index,COUNT_BIG(*) AS bin_value_count "
                "FROM bucketed GROUP BY bin_index) "
                "SELECT COALESCE(SUM(bin_value_count),0) AS histogram_total,"
                "COUNT_BIG(*) AS non_empty_bin_count FROM bins"
            ),
            {
                "dataset_id": candidate.dataset_id,
                "version_no": candidate.version_no,
                "parameter_name": candidate.parameter_name,
                "histogram_bin_count": bin_count,
            },
        )
        .mappings()
        .one()
    )
    return int(row["histogram_total"] or 0), int(row["non_empty_bin_count"] or 0)


def _independent_statistics(
    connection: SqlConnection,
    candidate: AnalysisCandidate,
    *,
    bin_count: int,
) -> IndependentStatistics:
    row, status_counts = _descriptive_statistics(connection, candidate)
    row_count = int(row["row_count"] or 0)
    numeric_count = int(row["numeric_count"] or 0)
    if (
        row_count < 0
        or numeric_count < 0
        or numeric_count > row_count
        or sum(status_counts.values()) != row_count
    ):
        raise VerificationError(
            "INDEPENDENT_AGGREGATE_INVALID", "独立 SQL 描述统计计数无法对账"
        )
    box_row = _box_statistics(connection, candidate) if numeric_count else None
    box_values = None
    outlier_count = 0
    if box_row is not None:
        fields = (
            "minimum",
            "q1",
            "median",
            "q3",
            "maximum",
            "lower_whisker",
            "upper_whisker",
        )
        box_values = {
            field: float(
                _optional_float(box_row[field], code="INDEPENDENT_BOX_INVALID")
            )
            for field in fields
        }
        outlier_count = int(box_row["outlier_count"] or 0)
    histogram_total, non_empty_bins = _histogram_count_statistics(
        connection, candidate, bin_count=bin_count
    )
    if histogram_total != numeric_count:
        raise VerificationError(
            "INDEPENDENT_HISTOGRAM_INVALID", "独立 SQL 直方图分箱合计无法对账"
        )
    return IndependentStatistics(
        row_count=row_count,
        numeric_count=numeric_count,
        status_counts=status_counts,
        minimum=_optional_float(row["minimum"], code="INDEPENDENT_STATISTIC_INVALID"),
        maximum=_optional_float(row["maximum"], code="INDEPENDENT_STATISTIC_INVALID"),
        average=_optional_float(row["average"], code="INDEPENDENT_STATISTIC_INVALID"),
        sample_stddev=_optional_float(
            row["sample_stddev"], code="INDEPENDENT_STATISTIC_INVALID"
        ),
        box_values=box_values,
        outlier_count=outlier_count,
        histogram_total=histogram_total,
        histogram_non_empty_bin_count=non_empty_bins,
    )


def _analysis_request(candidate: AnalysisCandidate) -> DatasetParameterAnalysisRequest:
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": candidate.dataset_id, "version_no": candidate.version_no}
        ],
        parameters=[candidate.parameter_name],
        analyses=[
            DatasetParameterAnalysisType.DESCRIPTIVE,
            DatasetParameterAnalysisType.BOX_PLOT,
            DatasetParameterAnalysisType.HISTOGRAM,
            DatasetParameterAnalysisType.CAPABILITY,
        ],
        histogram={"bin_count": DEFAULT_HISTOGRAM_BIN_COUNT},
    )
    if request.capability.rule_code is not None:
        raise VerificationError(
            "CAPABILITY_RULE_NOT_ALLOWED",
            "首批真实 SQL 验收不得自动选择 Cpk/Ppk 规则",
        )
    return request


def _response_digest(response: Any) -> str:
    payload = asdict(response) if is_dataclass(response) else response
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.pop("computed_at", None)
    return _redacted_digest("v13-parameter-analysis-response", payload)


def _run_invocations(
    service: Any,
    request: DatasetParameterAnalysisRequest,
    audit: ReadOnlyAudit,
    *,
    warm_runs: int,
) -> tuple[Any | None, dict[str, Any]]:
    responses: list[Any] = []
    response_digests: list[str] = []
    invocations: list[dict[str, Any]] = []
    for index in range(warm_runs + 1):
        phase = "cold_candidate" if index == 0 else "warm"
        statements_before = audit.statement_count
        started = perf_counter_ns()
        try:
            response = service.analyze_parameters(request)
        except Exception as exc:  # noqa: BLE001 - evidence boundary redacts message
            elapsed_ms = (perf_counter_ns() - started) / 1_000_000
            invocations.append(
                {
                    "phase": phase,
                    "ordinal": 1 if index == 0 else index,
                    "status": "FAIL",
                    "elapsed_ms": round(elapsed_ms, 3),
                    "sql_statement_count": audit.statement_count - statements_before,
                    "exception_type": type(exc).__name__,
                }
            )
            return None, {
                "status": "FAIL",
                "reason_code": "SERVICE_INVOCATION_FAILED",
                "invocations": invocations,
                "response_summary_sha256": None,
            }
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        digest = _response_digest(response)
        responses.append(response)
        response_digests.append(digest)
        invocations.append(
            {
                "phase": phase,
                "ordinal": 1 if index == 0 else index,
                "status": "PASS",
                "elapsed_ms": round(elapsed_ms, 3),
                "sql_statement_count": audit.statement_count - statements_before,
            }
        )
    if len(set(response_digests)) != 1:
        return responses[-1], {
            "status": "FAIL",
            "reason_code": "SERVICE_RESPONSE_DRIFT",
            "invocations": invocations,
            "response_summary_sha256": None,
        }
    return responses[-1], {
        "status": "PASS",
        "reason_code": "ALL_INVOCATIONS_COMPLETED",
        "invocations": invocations,
        "response_summary_sha256": response_digests[0],
    }


def _assert_optional_float_equal(
    actual: float | None,
    expected: float | None,
    *,
    field: str,
) -> None:
    if actual is None or expected is None:
        if actual is expected:
            return
        raise VerificationError("ANALYSIS_SQL_MISMATCH", f"{field} 与独立 SQL 无法对账")
    if not math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9):
        raise VerificationError("ANALYSIS_SQL_MISMATCH", f"{field} 与独立 SQL 无法对账")


def _assert_capability_rule_gate(capability: Any, *, numeric_count: int) -> None:
    null_fields = (
        "spec_mode",
        "lsl",
        "usl",
        "overall_sigma",
        "within_sigma",
        "ppl",
        "ppu",
        "ppk",
        "cpl",
        "cpu",
        "cpk",
        "rule_code",
    )
    if (
        capability is None
        or capability.status != "NOT_ELIGIBLE"
        or capability.ppk_status != "NOT_REQUESTED"
        or capability.cpk_status != "NOT_REQUESTED"
        or tuple(capability.reason_codes) != ("CAPABILITY_RULE_REQUIRED",)
        or int(capability.sample_count) != numeric_count
        or int(capability.subgroup_count) != 0
        or any(getattr(capability, field) is not None for field in null_fields)
    ):
        raise VerificationError(
            "CAPABILITY_RULE_GATE_MISMATCH",
            "未指定已批准规则时 Capability 未严格失败关闭",
        )


def _reconcile_response(
    response: Any,
    candidate: AnalysisCandidate,
    independent: IndependentStatistics,
) -> dict[str, Any]:
    resolved = response.dataset_context.resolved_datasets
    normalized_filters = response.filter_summary.normalized_filters
    capability_context = {
        item.code: (item.status, item.reason_code) for item in response.capabilities
    }
    try:
        computed_at = datetime.fromisoformat(response.computed_at)
    except (TypeError, ValueError) as exc:
        raise VerificationError(
            "ANALYSIS_RESPONSE_CONTRACT_MISMATCH",
            "参数分析响应缺少有效 UTC computed_at",
        ) from exc
    if (
        response.contract_version != "PARAMETER_ANALYSIS_V1"
        or response.group_by != "DATASET"
        or response.compatibility != "SINGLE_DATASET"
        or len(response.items) != 1
        or not response.dataset_context.current_published_verified
        or response.dataset_context.test_stage != candidate.test_stage
        or len(resolved) != 1
        or int(resolved[0].dataset_id) != candidate.dataset_id
        or int(resolved[0].version_no) != candidate.version_no
        or any(
            (
                normalized_filters.lot_ids,
                normalized_filters.wafer_ids,
                normalized_filters.bin_codes,
                normalized_filters.overall_results,
                normalized_filters.source_ids,
            )
        )
        or len(response.filter_summary.filter_hash) != 64
        or response.rule_context.capability_rule_code is not None
        or response.rule_context.capability_rule_approval_status != "NOT_REQUESTED"
        or capability_context.get("CAPABILITY") != ("GATED", "CAPABILITY_RULE_REQUIRED")
        or tuple(response.warnings) != ("CAPABILITY_RULE_REQUIRED",)
        or response.sampling_summary.sampled
        or response.sampling_summary.method is not None
        or response.sampling_summary.original_points != 0
        or response.sampling_summary.returned_points != 0
        or response.sampling_summary.preserved_out_of_spec_points != 0
        or computed_at.utcoffset() is None
    ):
        raise VerificationError(
            "ANALYSIS_RESPONSE_CONTRACT_MISMATCH", "参数分析响应信封不符合冻结合同"
        )
    item = response.items[0]
    if (
        int(item.dataset_id) != candidate.dataset_id
        or int(item.version_no) != candidate.version_no
        or item.test_stage != candidate.test_stage
        or len(item.parameters) != 1
        or int(item.filter_summary.candidate_measurement_count) != independent.row_count
    ):
        raise VerificationError(
            "ANALYSIS_RESPONSE_SCOPE_MISMATCH",
            "参数分析响应超出所选 Current Dataset 范围",
        )
    parameter = item.parameters[0]
    if parameter.identity.name != candidate.parameter_name:
        raise VerificationError(
            "ANALYSIS_RESPONSE_PARAMETER_MISMATCH", "参数分析响应返回了非目标参数"
        )
    actual_statuses = {row.status: int(row.count) for row in parameter.status_counts}
    if actual_statuses != dict(independent.status_counts):
        raise VerificationError(
            "ANALYSIS_STATUS_SQL_MISMATCH", "测量状态计数与独立 SQL 无法对账"
        )
    descriptive = parameter.descriptive
    if (
        descriptive is None
        or int(descriptive.row_count) != independent.row_count
        or int(descriptive.numeric_count) != independent.numeric_count
        or int(descriptive.excluded_count)
        != independent.row_count - independent.numeric_count
    ):
        raise VerificationError(
            "ANALYSIS_DESCRIPTIVE_SQL_MISMATCH", "描述统计计数与独立 SQL 无法对账"
        )
    for field in ("minimum", "maximum", "average", "sample_stddev"):
        _assert_optional_float_equal(
            getattr(descriptive, field), getattr(independent, field), field=field
        )

    box_plot = parameter.box_plot
    if independent.numeric_count > 0:
        if box_plot is None or independent.box_values is None:
            raise VerificationError(
                "ANALYSIS_BOX_SQL_MISMATCH", "箱线图统计与独立 SQL 无法对账"
            )
        for field, expected in independent.box_values.items():
            _assert_optional_float_equal(
                getattr(box_plot, field), expected, field=field
            )
        if (
            int(box_plot.outlier_count) != independent.outlier_count
            or box_plot.method != "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1"
        ):
            raise VerificationError(
                "ANALYSIS_BOX_SQL_MISMATCH", "Tukey Whisker/异常点与独立 SQL 无法对账"
            )
    elif box_plot is not None:
        raise VerificationError(
            "ANALYSIS_BOX_SQL_MISMATCH", "无数值样本时不得伪造箱线图统计"
        )

    histogram = parameter.histogram
    histogram_total = (
        sum(int(item.count) for item in histogram.bins) if histogram is not None else -1
    )
    if (
        histogram is None
        or int(histogram.requested_bin_count) != DEFAULT_HISTOGRAM_BIN_COUNT
        or histogram.method != "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1"
        or histogram_total != independent.histogram_total
        or histogram_total != independent.numeric_count
    ):
        raise VerificationError(
            "ANALYSIS_HISTOGRAM_SQL_MISMATCH", "直方图 count 合计与独立 SQL 无法对账"
        )
    _assert_capability_rule_gate(
        parameter.capability, numeric_count=independent.numeric_count
    )
    if parameter.identity.limit_source != "NOT_EVALUATED":
        raise VerificationError(
            "CAPABILITY_RULE_GATE_MISMATCH",
            "未指定规则时不得解析或暴露 formal capability limits",
        )
    if (
        response.counts.included_units != item.filter_summary.matched_unit_count
        or response.counts.input_units < response.counts.included_units
        or response.counts.excluded_units
        != response.counts.input_units - response.counts.included_units
    ):
        raise VerificationError(
            "ANALYSIS_RESPONSE_COUNT_MISMATCH",
            "参数分析响应 Unit 计数信封不一致",
        )

    aggregate_digest = _redacted_digest(
        "v13-independent-analysis-summary",
        {
            "row_count": independent.row_count,
            "numeric_count": independent.numeric_count,
            "status_counts": independent.status_counts,
            "minimum": independent.minimum,
            "maximum": independent.maximum,
            "average": independent.average,
            "sample_stddev": independent.sample_stddev,
            "box_values": independent.box_values,
            "outlier_count": independent.outlier_count,
            "histogram_total": independent.histogram_total,
        },
    )
    return {
        "status": "PASS",
        "checked_fields": [
            "row_count",
            "numeric_count",
            "measurement_status_counts",
            "minimum",
            "maximum",
            "average",
            "sample_stddev",
            "percentile_cont_q1_median_q3",
            "tukey_whiskers",
            "outlier_count",
            "histogram_count_sum",
        ],
        "row_count": independent.row_count,
        "numeric_count": independent.numeric_count,
        "status_count_total": sum(independent.status_counts.values()),
        "histogram_count_total": independent.histogram_total,
        "histogram_non_empty_bin_count": independent.histogram_non_empty_bin_count,
        "aggregate_summary_sha256": aggregate_digest,
        "capability_rule_gate": {
            "status": "PASS",
            "reason_code": "CAPABILITY_RULE_REQUIRED",
            "ppk_status": "NOT_REQUESTED",
            "cpk_status": "NOT_REQUESTED",
            "all_sigma_and_indices_null": True,
        },
    }


def _public_candidate(candidate: AnalysisCandidate) -> dict[str, Any]:
    return {
        "test_stage": candidate.test_stage,
        "candidate_reference_sha256": _redacted_digest(
            "v13-dataset-parameter-reference",
            (
                candidate.dataset_id,
                candidate.dataset_version_id,
                candidate.version_no,
                candidate.parameter_name,
            ),
        ),
        "measurement_count": candidate.measurement_count,
        "numeric_count": candidate.numeric_count,
    }


def _verify_stage(
    engine: Connectable,
    service: Any,
    audit: ReadOnlyAudit,
    *,
    stage: str,
    selection: tuple[AnalysisCandidate, FormalSpecCoverage] | None,
    warm_runs: int,
) -> dict[str, Any]:
    if selection is None:
        return {
            "test_stage": stage,
            "status": "SKIP",
            "reason_code": "CURRENT_PUBLISHED_ANALYSIS_PARAMETER_NOT_FOUND",
            "candidate": None,
            "formal_spec_coverage": {
                "status": "SKIP",
                "reason_code": "STAGE_CANDIDATE_NOT_FOUND",
            },
            "service_invocations": None,
            "sql_reconciliation": None,
        }
    candidate, formal_coverage = selection
    try:
        with engine.connect() as connection:
            independent = _independent_statistics(
                connection,
                candidate,
                bin_count=DEFAULT_HISTOGRAM_BIN_COUNT,
            )
        request = _analysis_request(candidate)
        response, invocation_evidence = _run_invocations(
            service, request, audit, warm_runs=warm_runs
        )
        if response is None or invocation_evidence["status"] != "PASS":
            reconciliation = None
            functional_status = "FAIL"
            reason_code = str(invocation_evidence["reason_code"])
        else:
            reconciliation = _reconcile_response(response, candidate, independent)
            functional_status = "PASS"
            reason_code = "ANALYSIS_AND_SQL_RECONCILED"
    except VerificationError as exc:
        return {
            "test_stage": stage,
            "status": "FAIL",
            "reason_code": exc.code,
            "candidate": _public_candidate(candidate),
            "formal_spec_coverage": formal_coverage.public(),
            "service_invocations": None,
            "sql_reconciliation": None,
        }
    except Exception as exc:  # noqa: BLE001 - stage boundary redacts message
        return {
            "test_stage": stage,
            "status": "FAIL",
            "reason_code": "STAGE_VERIFICATION_FAILED",
            "exception_type": type(exc).__name__,
            "candidate": _public_candidate(candidate),
            "formal_spec_coverage": formal_coverage.public(),
            "service_invocations": None,
            "sql_reconciliation": None,
        }
    if functional_status == "FAIL" or formal_coverage.status == "FAIL":
        status = "FAIL"
    elif formal_coverage.status == "SKIP":
        status = "SKIP"
        reason_code = formal_coverage.reason_code
    else:
        status = "PASS"
    return {
        "test_stage": stage,
        "status": status,
        "reason_code": reason_code,
        "candidate": _public_candidate(candidate),
        "formal_spec_coverage": formal_coverage.public(),
        "service_invocations": invocation_evidence,
        "sql_reconciliation": reconciliation,
    }


def _overall_status(stages: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(stage["status"]) for stage in stages}
    if "FAIL" in statuses:
        return "FAIL"
    if "SKIP" in statuses or len(stages) != 2:
        return "SKIP"
    return "PASS"


def _technical_g0_dataset_service(engine: Any) -> SqlDatasetService:
    return SqlDatasetService(
        engine,
        approved_parameter_analysis_rule_codes=(
            _TECHNICAL_G0_APPROVED_PARAMETER_ANALYSIS_RULE_CODES
        ),
    )


def verify(
    raw_engine: Engine,
    *,
    warm_runs: int = DEFAULT_WARM_RUNS,
    dataset_service_factory: Callable[[Any], Any] = _technical_g0_dataset_service,
) -> dict[str, Any]:
    if warm_runs < 1 or warm_runs > MAX_WARM_RUNS:
        raise VerificationError(
            "WARM_RUN_COUNT_INVALID",
            f"warm-runs 必须在 1 到 {MAX_WARM_RUNS} 之间",
        )
    audit = ReadOnlyAudit()
    engine = _ReadOnlyEngine(raw_engine, audit)
    with engine.connect() as connection:
        identity = _identity(connection)
        before = _database_snapshot(connection)

    primary_error: Exception | None = None
    stage_evidence: list[dict[str, Any]] = []
    candidate_count = 0
    selected: dict[str, tuple[AnalysisCandidate, FormalSpecCoverage] | None] = {
        "CP": None,
        "FT": None,
    }
    try:
        with engine.connect() as connection:
            candidates = _candidate_rows(connection)
            candidate_count = len(candidates)
            selected = _select_stage_candidates(connection, candidates)
        service = dataset_service_factory(engine)
        stage_evidence = [
            _verify_stage(
                engine,
                service,
                audit,
                stage=stage,
                selection=selected[stage],
                warm_runs=warm_runs,
            )
            for stage in ("CP", "FT")
        ]
    except Exception as exc:  # noqa: BLE001 - snapshot comparison must still run
        primary_error = exc

    with engine.connect() as connection:
        after = _database_snapshot(connection)
    _assert_snapshot_unchanged(before, after)
    if primary_error is not None:
        raise primary_error
    if audit.blocked_statement_count:
        raise VerificationError(
            "READ_ONLY_GUARD_TRIGGERED", "v1.3 验收期间只读 SQL 门禁被触发"
        )
    overall = _overall_status(stage_evidence)
    return {
        "verification": overall,
        "contract": "v1.3-parameter-analysis-sql-readonly",
        "identity": identity,
        "methodology": {
            "candidate_scope": (
                "active Current+PUBLISHED CP/FT Dataset Versions with measured "
                "analysis parameters"
            ),
            "cold_candidate": "first invocation in this process; caches are not flushed",
            "warm_runs": warm_runs,
            "elapsed_scope": "SqlDatasetService.analyze_parameters call only",
            "reconciliation": (
                "independent read-only SQL for descriptive aggregates, SQL Server "
                "PERCENTILE_CONT Tukey box plot, and histogram count sum"
            ),
            "capability": (
                "CAPABILITY is requested without a rule; the verifier requires the "
                "CAPABILITY_RULE_REQUIRED fail-closed response and never selects a rule"
            ),
            "decision": "FAIL precedes SKIP; both CP and FT require covered candidates",
        },
        "candidate_coverage": {
            "candidate_rows_examined": candidate_count,
            "required_stages": ["CP", "FT"],
            "selected_stage_count": sum(
                value is not None for value in selected.values()
            ),
        },
        "stages": stage_evidence,
        "read_only": {
            "policy": "SELECT_OR_READ_ONLY_CTE_ONLY",
            "executed_statement_count": audit.statement_count,
            "blocked_statement_count": audit.blocked_statement_count,
            "canonical_current_counts_unchanged": True,
            "canonical_current_summary_digests_unchanged": True,
            "snapshot_before": before.public(),
            "snapshot_after": after.public(),
        },
        "evidence_redaction": {
            "raw_measurement_values_emitted": False,
            "connection_details_emitted": False,
            "omitted": [
                "connection_string",
                "server_name",
                "database_login",
                "database_url",
                "dataset_id",
                "dataset_version_id",
                "parameter_name",
                "product",
                "lot",
                "wafer",
                "unit_id",
                "measurement_value",
                "formal_spec_value",
            ],
            "business_identity": "stable_sha256_reference",
            "numeric_evidence": "aggregate counts plus redacted summary SHA-256",
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only v1.3 parameter-analysis acceptance for TMS_G0_DEV"
    )
    parser.add_argument(
        "--warm-runs",
        type=int,
        default=DEFAULT_WARM_RUNS,
        help=f"sequential warm invocations per stage (1-{MAX_WARM_RUNS}; default: 5)",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.3-parameter-analysis-sql-readonly",
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
                    "contract": "v1.3-parameter-analysis-sql-readonly",
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
                    "contract": "v1.3-parameter-analysis-sql-readonly",
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
    if evidence["verification"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
