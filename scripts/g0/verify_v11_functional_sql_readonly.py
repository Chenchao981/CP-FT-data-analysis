from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine, text

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
EXPECTED_SCHEMA_REVISION = "sql2014_0026"
DETAIL_PAGE_SIZE = 50
CATALOG_PAGE_SIZE = 2
_ALLOWED_RESULTS = frozenset({"PASS", "FAIL", "UNKNOWN", "ABORT"})
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
    """Expose only guarded SELECT/CTE execution to application SQL services."""

    def __init__(self, engine: Any, audit: ReadOnlyAudit) -> None:
        self._engine = engine
        self._audit = audit

    @contextmanager
    def connect(self) -> Iterator[_ReadOnlyConnection]:
        with self._engine.connect() as connection:
            yield _ReadOnlyConnection(connection, self._audit)


@dataclass(frozen=True, slots=True)
class DatasetTarget:
    dataset_id: int
    dataset_version_id: int
    version_no: int
    test_stage: str
    spec_set_id: int | None
    published_at_utc: datetime


@dataclass(frozen=True, slots=True)
class ResultCounts:
    total_units: int
    pass_units: int
    fail_units: int
    unknown_units: int
    abort_units: int
    other_units: int

    @property
    def known_yield_denominator(self) -> int:
        return self.pass_units + self.fail_units

    @property
    def yield_rate(self) -> float | None:
        denominator = self.known_yield_denominator
        return self.pass_units / denominator if denominator else None

    def public(self) -> dict[str, int | float | None]:
        return {
            "total_units": self.total_units,
            "pass_units": self.pass_units,
            "fail_units": self.fail_units,
            "unknown_units": self.unknown_units,
            "abort_units": self.abort_units,
            "known_yield_denominator": self.known_yield_denominator,
            "yield_rate": self.yield_rate,
        }


CatalogKey = tuple[int, int, int, str]
CatalogSnapshotRow = tuple[int, int, int, str, str | None]


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Private full-fidelity Current key and Canonical Lot membership snapshot."""

    rows: tuple[CatalogSnapshotRow, ...]

    @property
    def members_by_key(self) -> dict[CatalogKey, frozenset[str]]:
        members: dict[CatalogKey, set[str]] = {}
        for dataset_id, version_id, version_no, stage, lot_id in self.rows:
            key = (dataset_id, version_id, version_no, stage)
            members.setdefault(key, set())
            if lot_id is not None:
                members[key].add(lot_id)
        return {key: frozenset(values) for key, values in members.items()}

    @property
    def distinct_lots(self) -> tuple[str, ...]:
        return tuple(sorted({row[4] for row in self.rows if row[4] is not None}))

    @property
    def digest(self) -> str:
        return _redacted_digest("current-canonical-lot-snapshot", self.rows)

    def public(self) -> dict[str, int | str]:
        members = self.members_by_key
        return {
            "current_key_count": len(members),
            "canonical_lot_member_count": sum(len(value) for value in members.values()),
            "distinct_lot_count": len(self.distinct_lots),
            "snapshot_sha256": self.digest,
        }


def _assert_read_only_sql(sql: str) -> None:
    lexical = _SQL_LITERAL_OR_COMMENT.sub(" ", sql).strip()
    statements = [item.strip() for item in lexical.split(";") if item.strip()]
    if len(statements) != 1:
        raise VerificationError(
            "READ_ONLY_MULTIPLE_STATEMENTS",
            "只读验收拒绝空语句或多语句批次",
        )
    statement = statements[0]
    first = re.match(r"[A-Za-z]+", statement)
    if first is None or first.group(0).upper() not in {"SELECT", "WITH"}:
        raise VerificationError(
            "READ_ONLY_STATEMENT_REJECTED",
            "只读验收仅允许 SELECT 或只读 CTE",
        )
    for token in _MUTATING_SQL_TOKENS:
        if re.search(rf"\b{token}\b", statement, flags=re.IGNORECASE):
            raise VerificationError(
                "READ_ONLY_MUTATION_REJECTED",
                "只读验收检测到被禁止的数据库变更语句",
            )
    if re.search(r"\bSELECT\b[\s\S]*?\bINTO\b", statement, flags=re.IGNORECASE):
        raise VerificationError(
            "READ_ONLY_SELECT_INTO_REJECTED",
            "只读验收禁止 SELECT INTO",
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
            f"只读验收只允许数据库 {EXPECTED_DATABASE}",
        )
    if revision != EXPECTED_SCHEMA_REVISION:
        raise VerificationError(
            "SCHEMA_REVISION_MISMATCH",
            f"只读验收要求 schema {EXPECTED_SCHEMA_REVISION}",
        )
    if (
        "MICROSOFT SQL SERVER" not in version_banner.upper()
        or not product_version
        or engine_edition <= 0
    ):
        raise VerificationError(
            "DATABASE_ENGINE_MISMATCH",
            "只读验收要求 Microsoft SQL Server",
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


def _count_snapshot(connection: SqlConnection) -> dict[str, dict[str, int]]:
    statements = {
        "canonical": {
            "test_run": "SELECT COUNT_BIG(*) FROM test.test_run",
            "unit_result": "SELECT COUNT_BIG(*) FROM test.unit_result",
            "measurement": "SELECT COUNT_BIG(*) FROM test.measurement",
        },
        "current": {
            "dataset_version": (
                "SELECT COUNT_BIG(*) FROM analytics.v_current_dataset_version"
            ),
            "test_run": "SELECT COUNT_BIG(*) FROM analytics.v_current_test_run",
            "unit_result": ("SELECT COUNT_BIG(*) FROM analytics.v_current_unit_result"),
            "measurement": ("SELECT COUNT_BIG(*) FROM analytics.v_current_measurement"),
        },
    }
    snapshot: dict[str, dict[str, int]] = {}
    for scope, queries in statements.items():
        snapshot[scope] = {}
        for name, query in queries.items():
            value = int(connection.execute(text(query)).scalar_one())
            if value < 0:
                raise VerificationError(
                    "COUNT_SNAPSHOT_INVALID",
                    "数据库计数快照出现无效负数",
                )
            snapshot[scope][name] = value
    return snapshot


def _assert_snapshot_unchanged(
    before: Mapping[str, Mapping[str, int]],
    after: Mapping[str, Mapping[str, int]],
) -> None:
    if before != after:
        raise VerificationError(
            "READ_ONLY_SNAPSHOT_DRIFT",
            "验收期间 Canonical 或 Current 计数发生变化，结果作废",
        )


def _redacted_digest(scope: str, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(f"{scope}\n{payload}".encode("utf-8")).hexdigest()


def _catalog_snapshot(connection: SqlConnection) -> CatalogSnapshot:
    """Read every Current key and its distinct Canonical Lot membership."""

    rows = (
        connection.execute(
            text(
                "SELECT DISTINCT d.dataset_id,dv.dataset_version_id,dv.version_no,"
                "d.test_stage,tr.lot_id "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "LEFT JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "LEFT JOIN test.test_run tr "
                "ON tr.processing_run_id=dvr.processing_run_id "
                "AND tr.lot_id IS NOT NULL "
                "WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                "ORDER BY d.dataset_id,dv.dataset_version_id,dv.version_no,"
                "d.test_stage,tr.lot_id"
            )
        )
        .mappings()
        .all()
    )
    normalized: list[CatalogSnapshotRow] = []
    for row in rows:
        key = (
            int(row["dataset_id"]),
            int(row["dataset_version_id"]),
            int(row["version_no"]),
            str(row["test_stage"]),
        )
        if min(key[:3]) < 1 or key[3] not in {"CP", "FT"}:
            raise VerificationError(
                "CURRENT_CATALOG_KEY_INVALID",
                "Current Catalog 存在无效键或测试阶段",
            )
        raw_lot = row["lot_id"]
        lot_id = None if raw_lot is None else str(raw_lot)
        normalized.append((*key, lot_id))
    snapshot_rows = tuple(normalized)
    if len(snapshot_rows) != len(set(snapshot_rows)):
        raise VerificationError(
            "CURRENT_CATALOG_SNAPSHOT_DUPLICATE",
            "Current Catalog 完整快照存在重复成员",
        )
    return CatalogSnapshot(rows=snapshot_rows)


def _assert_catalog_snapshot_unchanged(
    before: CatalogSnapshot, after: CatalogSnapshot
) -> None:
    if before.rows != after.rows:
        raise VerificationError(
            "READ_ONLY_CATALOG_SNAPSHOT_DRIFT",
            "验收期间 Current 键或 Canonical Lot 成员发生变化，结果作废",
        )


def _catalog_item_key(item: Any) -> CatalogKey:
    return (
        int(item.dataset_id),
        int(item.dataset_version_id),
        int(item.version_no),
        str(item.test_stage),
    )


def _collect_catalog_pages(
    service: Any,
    *,
    lot_id: str | None = None,
) -> tuple[tuple[Any, ...], int]:
    collected: list[Any] = []
    page_no = 1
    expected_total: int | None = None
    while True:
        page = service.list_current_datasets(
            DEVELOPMENT_PRINCIPAL,
            M2PageFilters(
                page=page_no,
                page_size=CATALOG_PAGE_SIZE,
                lot_id=lot_id,
            ),
        )
        total = int(page.total)
        items = tuple(page.items)
        if (
            total < 0
            or int(page.page) != page_no
            or int(page.page_size) != CATALOG_PAGE_SIZE
            or len(items) > CATALOG_PAGE_SIZE
        ):
            raise VerificationError(
                "CURRENT_CATALOG_PAGE_INVALID",
                "Current Catalog 服务分页边界无效",
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise VerificationError(
                "CURRENT_CATALOG_TOTAL_DRIFT",
                "Current Catalog 分页期间 total 发生变化",
            )
        collected.extend(items)
        if len(collected) >= total:
            break
        if not items:
            raise VerificationError(
                "CURRENT_CATALOG_PAGE_GAP",
                "Current Catalog 分页未覆盖全部数据",
            )
        page_no += 1
    if len(collected) != int(expected_total or 0):
        raise VerificationError(
            "CURRENT_CATALOG_TOTAL_MISMATCH",
            "Current Catalog 分页结果数与 total 不一致",
        )
    keys = tuple(_catalog_item_key(item) for item in collected)
    if len(keys) != len(set(keys)):
        raise VerificationError(
            "CURRENT_CATALOG_PAGE_DUPLICATE",
            "Current Catalog 跨页返回重复键",
        )
    return tuple(collected), page_no


def _assert_catalog_items(
    items: Sequence[Any], expected: Mapping[CatalogKey, frozenset[str]]
) -> None:
    for item in items:
        key = _catalog_item_key(item)
        members = expected.get(key)
        if members is None:
            raise VerificationError(
                "CURRENT_CATALOG_SERVICE_EXTRA_KEY",
                "Current Catalog 服务返回了完整快照之外的键",
            )
        expected_lot_id = next(iter(members)) if len(members) == 1 else None
        actual_lot_id = str(item.lot_id) if item.lot_id is not None else None
        if actual_lot_id != expected_lot_id or int(item.lot_count) != len(members):
            raise VerificationError(
                "CURRENT_CATALOG_LOT_MISMATCH",
                "Current Catalog 的 lot_id 或 lot_count 未与 Canonical 对账",
            )


def _verify_current_catalog(service: Any, snapshot: CatalogSnapshot) -> dict[str, Any]:
    expected = snapshot.members_by_key
    items, page_count = _collect_catalog_pages(service)
    _assert_catalog_items(items, expected)
    actual_keys = {_catalog_item_key(item) for item in items}
    if actual_keys != set(expected):
        raise VerificationError(
            "CURRENT_CATALOG_KEY_MISMATCH",
            "Current Catalog 服务分页未覆盖完整 Current 键集合",
        )

    service_rows = sorted(
        (
            *_catalog_item_key(item),
            str(item.lot_id) if item.lot_id is not None else None,
            int(item.lot_count),
        )
        for item in items
    )
    filter_rows: list[tuple[str, tuple[CatalogKey, ...]]] = []
    filter_page_count = 0
    for lot_id in snapshot.distinct_lots:
        filtered, pages = _collect_catalog_pages(service, lot_id=lot_id)
        filter_page_count += pages
        _assert_catalog_items(filtered, expected)
        actual = {_catalog_item_key(item) for item in filtered}
        exact_owners = {key for key, members in expected.items() if lot_id in members}
        compatible_owners = {
            key
            for key, members in expected.items()
            if any(lot_id.casefold() in member.casefold() for member in members)
        }
        if not exact_owners.issubset(actual) or not actual.issubset(compatible_owners):
            raise VerificationError(
                "CURRENT_CATALOG_LOT_FILTER_MISMATCH",
                "Current Catalog 逐 Lot 筛选未命中对应 Canonical 成员",
            )
        filter_rows.append((lot_id, tuple(sorted(actual))))

    return {
        "verification": "PASS",
        **snapshot.public(),
        "service_page_count": page_count,
        "lot_filter_count": len(snapshot.distinct_lots),
        "lot_filter_page_count": filter_page_count,
        "service_reconciliation_sha256": _redacted_digest(
            "current-catalog-service-reconciliation", service_rows
        ),
        "lot_filter_reconciliation_sha256": _redacted_digest(
            "current-catalog-lot-filter-reconciliation", filter_rows
        ),
    }


def _candidate_targets(
    connection: SqlConnection, stage: str
) -> tuple[DatasetTarget, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT TOP (32) cv.dataset_id,cv.dataset_version_id,cv.version_no,"
                "cv.test_stage,dv.spec_set_id,cv.published_at_utc "
                "FROM analytics.v_current_dataset_version cv "
                "JOIN dataset.dataset d ON d.dataset_id=cv.dataset_id "
                "JOIN dataset.dataset_version dv "
                "ON dv.dataset_version_id=cv.dataset_version_id "
                "WHERE d.lifecycle_status='ACTIVE' AND cv.test_stage=:stage "
                "AND EXISTS(SELECT 1 FROM analytics.v_current_unit_result cur "
                "WHERE cur.dataset_version_id=cv.dataset_version_id) "
                "ORDER BY cv.published_at_utc DESC,cv.dataset_version_id DESC"
            ),
            {"stage": stage},
        )
        .mappings()
        .all()
    )
    targets: list[DatasetTarget] = []
    for row in rows:
        published = row["published_at_utc"]
        if not isinstance(published, datetime):
            raise VerificationError(
                "CURRENT_DATASET_TIME_INVALID",
                "Current Dataset 发布时间无效",
            )
        targets.append(
            DatasetTarget(
                dataset_id=int(row["dataset_id"]),
                dataset_version_id=int(row["dataset_version_id"]),
                version_no=int(row["version_no"]),
                test_stage=str(row["test_stage"]),
                spec_set_id=(
                    int(row["spec_set_id"]) if row["spec_set_id"] is not None else None
                ),
                published_at_utc=published,
            )
        )
    return tuple(targets)


def _select_targets(connection: SqlConnection, stage: str) -> tuple[DatasetTarget, ...]:
    candidates = _candidate_targets(connection, stage)
    if not candidates:
        raise VerificationError(
            f"CURRENT_{stage}_DATASET_MISSING",
            f"缺少可验收的 Current {stage} Dataset",
        )
    if any(target.test_stage != stage for target in candidates):
        raise VerificationError(
            "CURRENT_DATASET_STAGE_MISMATCH",
            "Current Dataset 阶段身份不一致",
        )
    if stage == "FT":
        return candidates[:2]
    if stage != "CP":
        raise VerificationError(
            "CURRENT_DATASET_STAGE_INVALID",
            "只允许验收 CP 或 FT Dataset",
        )
    by_spec: defaultdict[int, list[DatasetTarget]] = defaultdict(list)
    for target in candidates:
        if target.spec_set_id is not None:
            by_spec[target.spec_set_id].append(target)
    for target in candidates:
        if target.spec_set_id is None:
            continue
        compatible = by_spec[target.spec_set_id]
        if len(compatible) >= 2:
            return tuple(compatible[:2])
    return (candidates[0],)


def _counts_from_row(row: Mapping[str, Any]) -> ResultCounts:
    counts = ResultCounts(
        total_units=int(row["total_units"] or 0),
        pass_units=int(row["pass_units"] or 0),
        fail_units=int(row["fail_units"] or 0),
        unknown_units=int(row["unknown_units"] or 0),
        abort_units=int(row["abort_units"] or 0),
        other_units=int(row["other_units"] or 0),
    )
    values = (
        counts.total_units,
        counts.pass_units,
        counts.fail_units,
        counts.unknown_units,
        counts.abort_units,
        counts.other_units,
    )
    if any(value < 0 for value in values):
        raise VerificationError(
            "RESULT_COUNT_INVALID",
            "PASS/FAIL/UNKNOWN/ABORT 独立计数出现负数",
        )
    if (
        counts.pass_units
        + counts.fail_units
        + counts.unknown_units
        + counts.abort_units
        + counts.other_units
        != counts.total_units
        or counts.other_units != 0
    ):
        raise VerificationError(
            "RESULT_COUNT_NOT_RECONCILED",
            "PASS/FAIL/UNKNOWN/ABORT 未与总 Unit 数对账",
        )
    return counts


def _independent_counts(
    connection: SqlConnection, target: DatasetTarget
) -> ResultCounts:
    row = (
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) AS total_units,"
                "SUM(CASE WHEN overall_result='PASS' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,"
                "SUM(CASE WHEN overall_result='FAIL' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,"
                "SUM(CASE WHEN overall_result='UNKNOWN' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units,"
                "SUM(CASE WHEN overall_result='ABORT' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS abort_units,"
                "SUM(CASE WHEN overall_result NOT IN "
                "('PASS','FAIL','UNKNOWN','ABORT') OR overall_result IS NULL "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS other_units "
                "FROM analytics.v_current_unit_result "
                "WHERE dataset_version_id=:dataset_version_id"
            ),
            {"dataset_version_id": target.dataset_version_id},
        )
        .mappings()
        .one()
    )
    counts = _counts_from_row(row)
    if counts.total_units < 1:
        raise VerificationError(
            "CURRENT_DATASET_EMPTY",
            "选中的 Current Dataset 没有 Unit 明细",
        )
    return counts


def _same_rate(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return abs(float(actual) - float(expected)) <= 1e-12


def _assert_compare_item(item: Any, expected: ResultCounts) -> None:
    actual = (
        int(item.unit_count),
        int(item.pass_count),
        int(item.fail_count),
        int(item.unknown_count),
        int(item.abort_count),
        int(item.known_yield_denominator),
    )
    wanted = (
        expected.total_units,
        expected.pass_units,
        expected.fail_units,
        expected.unknown_units,
        expected.abort_units,
        expected.known_yield_denominator,
    )
    if actual != wanted or not _same_rate(item.yield_rate, expected.yield_rate):
        raise VerificationError(
            "COMPARE_INDEPENDENT_SQL_MISMATCH",
            "Dataset compare 与独立 Current SQL 计数不一致",
        )


def _aggregate_counts(values: Sequence[ResultCounts]) -> ResultCounts:
    return ResultCounts(
        total_units=sum(value.total_units for value in values),
        pass_units=sum(value.pass_units for value in values),
        fail_units=sum(value.fail_units for value in values),
        unknown_units=sum(value.unknown_units for value in values),
        abort_units=sum(value.abort_units for value in values),
        other_units=sum(value.other_units for value in values),
    )


def _verify_compare(
    service: Any,
    connection: SqlConnection,
    stage: str,
    targets: tuple[DatasetTarget, ...],
) -> tuple[dict[str, Any], dict[int, ResultCounts]]:
    request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": target.dataset_id, "version_no": target.version_no}
            for target in targets
        ]
    )
    result = service.compare(request)
    if result.test_stage != stage or len(result.items) != len(targets):
        raise VerificationError(
            "COMPARE_SCOPE_MISMATCH",
            "Dataset compare 返回了错误的阶段或 Dataset 数量",
        )
    expected_compatibility = (
        "SINGLE_DATASET"
        if len(targets) == 1
        else "COMPATIBLE"
        if stage == "CP"
        else "NOT_EVALUATED"
    )
    if result.spec_compatibility != expected_compatibility:
        raise VerificationError(
            "COMPARE_SPEC_COMPATIBILITY_MISMATCH",
            "Dataset compare 的 Spec 兼容性结论不符合选取合同",
        )
    items = {
        (int(item.dataset_id), int(item.version_no)): item for item in result.items
    }
    if len(items) != len(result.items):
        raise VerificationError(
            "COMPARE_DUPLICATE_RESULT",
            "Dataset compare 返回重复 Dataset",
        )
    independent: dict[int, ResultCounts] = {}
    for target in targets:
        expected = _independent_counts(connection, target)
        item = items.get((target.dataset_id, target.version_no))
        if item is None:
            raise VerificationError(
                "COMPARE_DATASET_MISSING",
                "Dataset compare 缺少选中的 Dataset",
            )
        _assert_compare_item(item, expected)
        independent[target.dataset_version_id] = expected
    combined = _aggregate_counts(tuple(independent.values()))
    return (
        {
            "selected_dataset_count": len(targets),
            "spec_compatibility": result.spec_compatibility,
            "status_reconciled": True,
            "independent_sql_match": True,
            "combined_counts": combined.public(),
        },
        independent,
    )


def _independent_page_ids(
    connection: SqlConnection,
    target: DatasetTarget,
    *,
    page: int,
    page_size: int,
) -> tuple[int, ...]:
    rows = connection.execute(
        text(
            "SELECT unit_id FROM analytics.v_current_unit_result "
            "WHERE dataset_version_id=:dataset_version_id "
            "ORDER BY run_id,COALESCE(unit_sequence,unit_id),unit_id "
            "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
        ),
        {
            "dataset_version_id": target.dataset_version_id,
            "offset": (page - 1) * page_size,
            "page_size": page_size,
        },
    ).all()
    return tuple(int(row[0]) for row in rows)


def _assert_detail_page(
    page: Any,
    *,
    target: DatasetTarget,
    expected_total: int,
    expected_ids: tuple[int, ...],
    page_no: int,
    page_size: int,
) -> None:
    actual_ids = tuple(int(item.unit_id) for item in page.items)
    if (
        int(page.dataset_id) != target.dataset_id
        or int(page.version_no) != target.version_no
        or page.test_stage != target.test_stage
        or int(page.page) != page_no
        or int(page.page_size) != page_size
        or int(page.total) != expected_total
        or actual_ids != expected_ids
        or len(actual_ids) > page_size
        or len(actual_ids) != len(set(actual_ids))
        or any(item.overall_result not in _ALLOWED_RESULTS for item in page.items)
    ):
        raise VerificationError(
            "DETAIL_PAGE_BOUNDARY_MISMATCH",
            "Dataset details 分页与独立 Current SQL 边界不一致",
        )


def _verify_details(
    service: Any,
    connection: SqlConnection,
    target: DatasetTarget,
    expected: ResultCounts,
) -> dict[str, Any]:
    last_page_no = max(
        1, (expected.total_units + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE
    )
    observed_pages = tuple(dict.fromkeys((1, last_page_no, last_page_no + 1)))
    pages: dict[int, Any] = {}
    for page_no in observed_pages:
        page = service.get_detail_page(
            target.dataset_id,
            target.version_no,
            page=page_no,
            page_size=DETAIL_PAGE_SIZE,
        )
        expected_ids = _independent_page_ids(
            connection,
            target,
            page=page_no,
            page_size=DETAIL_PAGE_SIZE,
        )
        _assert_detail_page(
            page,
            target=target,
            expected_total=expected.total_units,
            expected_ids=expected_ids,
            page_no=page_no,
            page_size=DETAIL_PAGE_SIZE,
        )
        pages[page_no] = page
    if pages[last_page_no + 1].items:
        raise VerificationError(
            "DETAIL_PAGE_OVERFLOW",
            "Dataset details 超出末页后仍返回数据",
        )
    first_ids = {int(item.unit_id) for item in pages[1].items}
    last_ids = {int(item.unit_id) for item in pages[last_page_no].items}
    if last_page_no > 1 and first_ids.intersection(last_ids):
        raise VerificationError(
            "DETAIL_PAGE_OVERLAP",
            "Dataset details 首末页出现重复 Unit",
        )
    parameter_probe = False
    parameter_options = tuple(pages[1].parameter_options)
    if parameter_options:
        parameter = parameter_options[0]
        parameter_page = service.get_detail_page(
            target.dataset_id,
            target.version_no,
            page=1,
            page_size=DETAIL_PAGE_SIZE,
            parameters=(parameter,),
        )
        expected_ids = _independent_page_ids(
            connection,
            target,
            page=1,
            page_size=DETAIL_PAGE_SIZE,
        )
        _assert_detail_page(
            parameter_page,
            target=target,
            expected_total=expected.total_units,
            expected_ids=expected_ids,
            page_no=1,
            page_size=DETAIL_PAGE_SIZE,
        )
        if any(
            measurement.parameter != parameter
            for item in parameter_page.items
            for measurement in item.measurements
        ):
            raise VerificationError(
                "DETAIL_PARAMETER_SCOPE_MISMATCH",
                "Dataset details 返回了未选中的参数",
            )
        parameter_probe = True
    expected_last_size = expected.total_units - ((last_page_no - 1) * DETAIL_PAGE_SIZE)
    if len(pages[last_page_no].items) != expected_last_size:
        raise VerificationError(
            "DETAIL_LAST_PAGE_SIZE_MISMATCH",
            "Dataset details 末页行数不符合分页合同",
        )
    return {
        "total_units": expected.total_units,
        "page_size": DETAIL_PAGE_SIZE,
        "last_page": last_page_no,
        "last_page_rows": expected_last_size,
        "beyond_last_page_empty": True,
        "independent_page_sql_match": True,
        "parameter_measurement_probe": parameter_probe,
    }


def _quality_counts(
    connection: SqlConnection,
    *,
    stage: str,
    from_utc: datetime,
    to_utc: datetime,
) -> tuple[int, ResultCounts]:
    row = (
        connection.execute(
            text(
                "SELECT COUNT(DISTINCT cdv.dataset_version_id) AS dataset_count,"
                "COUNT_BIG(*) AS total_units,"
                "SUM(CASE WHEN cur.overall_result='PASS' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,"
                "SUM(CASE WHEN cur.overall_result='FAIL' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,"
                "SUM(CASE WHEN cur.overall_result='UNKNOWN' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units,"
                "SUM(CASE WHEN cur.overall_result='ABORT' "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS abort_units,"
                "SUM(CASE WHEN cur.overall_result NOT IN "
                "('PASS','FAIL','UNKNOWN','ABORT') OR cur.overall_result IS NULL "
                "THEN CONVERT(bigint,1) ELSE 0 END) AS other_units "
                "FROM analytics.v_current_dataset_version cdv "
                "JOIN dataset.dataset cd ON cd.dataset_id=cdv.dataset_id "
                "JOIN analytics.v_current_unit_result cur "
                "ON cur.dataset_version_id=cdv.dataset_version_id "
                "WHERE cdv.test_stage=:stage "
                "AND cd.access_scope='PERSONAL' "
                "AND cd.owner_user_id=:user_id "
                "AND cdv.published_at_utc>=:from_utc "
                "AND cdv.published_at_utc<:to_utc"
            ),
            {
                "stage": stage,
                "user_id": DEVELOPMENT_PRINCIPAL.user_id,
                "from_utc": from_utc,
                "to_utc": to_utc,
            },
        )
        .mappings()
        .one()
    )
    return int(row["dataset_count"] or 0), _counts_from_row(row)


def _verify_quality_summary(
    service: Any,
    connection: SqlConnection,
    *,
    stage: str,
    from_utc: datetime,
    to_utc: datetime,
) -> dict[str, Any]:
    summary = service.quality_summary(
        principal=DEVELOPMENT_PRINCIPAL,
        from_utc=from_utc,
        to_utc=to_utc,
        access_scope="PERSONAL",
        test_stage=stage,
        recent_limit=20,
    )
    dataset_count, expected = _quality_counts(
        connection,
        stage=stage,
        from_utc=from_utc.replace(tzinfo=None),
        to_utc=to_utc.replace(tzinfo=None),
    )
    kpis = summary.kpis
    actual = (
        int(kpis.dataset_count),
        int(kpis.total_units),
        int(kpis.pass_units),
        int(kpis.fail_units),
        int(kpis.unknown_units),
        int(kpis.abort_units),
        int(kpis.known_yield_denominator),
    )
    wanted = (
        dataset_count,
        expected.total_units,
        expected.pass_units,
        expected.fail_units,
        expected.unknown_units,
        expected.abort_units,
        expected.known_yield_denominator,
    )
    expected_unknown_rate = (
        expected.unknown_units / expected.total_units if expected.total_units else None
    )
    if (
        actual != wanted
        or not _same_rate(kpis.yield_rate, expected.yield_rate)
        or not _same_rate(kpis.unknown_rate, expected_unknown_rate)
    ):
        raise VerificationError(
            "QUALITY_SUMMARY_SQL_MISMATCH",
            "Quality summary 与独立 Current SQL 核心计数不一致",
        )
    return {
        "dataset_count": dataset_count,
        **expected.public(),
        "independent_sql_match": True,
    }


def _as_aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _public_dataset_ref(target: DatasetTarget) -> str:
    material = (
        f"v1.1:{target.test_stage}:{target.dataset_id}:"
        f"{target.dataset_version_id}:{target.version_no}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _public_targets(targets: Sequence[DatasetTarget]) -> list[dict[str, Any]]:
    return [
        {
            "dataset_ref": _public_dataset_ref(target),
            "version_no": target.version_no,
        }
        for target in targets
    ]


DatasetServiceFactory = Callable[[Any], Any]
ManagementServiceFactory = Callable[[Any], Any]
CurrentCatalogServiceFactory = Callable[[Any], Any]


def verify(
    raw_engine: Engine,
    *,
    dataset_service_factory: DatasetServiceFactory = SqlDatasetService,
    management_service_factory: ManagementServiceFactory = SqlManagementService,
    current_catalog_service_factory: CurrentCatalogServiceFactory = SqlM2QueryService,
) -> dict[str, Any]:
    audit = ReadOnlyAudit()
    engine = _ReadOnlyEngine(raw_engine, audit)
    with engine.connect() as connection:
        identity = _identity(connection)
        before = _count_snapshot(connection)
        catalog_before = _catalog_snapshot(connection)
        targets_by_stage = {
            stage: _select_targets(connection, stage) for stage in ("CP", "FT")
        }
    all_targets = tuple(
        target for targets in targets_by_stage.values() for target in targets
    )
    from_utc = min(_as_aware_utc(target.published_at_utc) for target in all_targets)
    to_utc = max(_as_aware_utc(target.published_at_utc) for target in all_targets)
    to_utc += timedelta(seconds=1)
    dataset_service = dataset_service_factory(engine)
    management_service = management_service_factory(engine)
    current_catalog_service = current_catalog_service_factory(engine)
    stage_evidence: dict[str, Any] = {}
    catalog_evidence: dict[str, Any] = {}
    primary_error: BaseException | None = None
    try:
        with engine.connect() as connection:
            catalog_evidence = _verify_current_catalog(
                current_catalog_service, catalog_before
            )
            for stage, targets in targets_by_stage.items():
                compare, independent = _verify_compare(
                    dataset_service, connection, stage, targets
                )
                details = [
                    {
                        "dataset_ref": _public_dataset_ref(target),
                        **_verify_details(
                            dataset_service,
                            connection,
                            target,
                            independent[target.dataset_version_id],
                        ),
                    }
                    for target in targets
                ]
                quality = _verify_quality_summary(
                    management_service,
                    connection,
                    stage=stage,
                    from_utc=from_utc,
                    to_utc=to_utc,
                )
                stage_evidence[stage] = {
                    "datasets": _public_targets(targets),
                    "compare": compare,
                    "details": details,
                    "quality_summary": quality,
                }
    except BaseException as exc:
        primary_error = exc
    with engine.connect() as connection:
        after = _count_snapshot(connection)
        catalog_after = _catalog_snapshot(connection)
    _assert_snapshot_unchanged(before, after)
    _assert_catalog_snapshot_unchanged(catalog_before, catalog_after)
    if primary_error is not None:
        raise primary_error
    if audit.blocked_statement_count != 0:
        raise VerificationError(
            "READ_ONLY_GUARD_TRIGGERED",
            "验收过程中只读 SQL 门禁被触发",
        )
    return {
        "verification": "PASS",
        "contract": "v1.1-functional-sql-readonly",
        "identity": identity,
        "read_only": {
            "policy": "SELECT_OR_READ_ONLY_CTE_ONLY",
            "executed_statement_count": audit.statement_count,
            "blocked_statement_count": audit.blocked_statement_count,
            "count_snapshot_unchanged": True,
            "full_catalog_snapshot_unchanged": True,
        },
        "count_snapshot_before": before,
        "count_snapshot_after": after,
        "current_catalog": {
            **catalog_evidence,
            "full_snapshot_unchanged": True,
        },
        "stages": stage_evidence,
        "evidence_redaction": {
            "omitted": [
                "connection_string",
                "server_name",
                "login_name",
                "dataset_id",
                "dataset_version_id",
                "product",
                "lot",
                "wafer",
                "parameter",
                "unit_id",
            ],
            "dataset_identity": "stable_sha256_reference",
        },
    }


def main() -> None:
    if not os.getenv("TMS_DATABASE_URL"):
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.1-functional-sql-readonly",
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
        evidence = verify(engine)
    except VerificationError as exc:
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.1-functional-sql-readonly",
                    "error_code": exc.code,
                    "message": exc.safe_message,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verification": "FAIL",
                    "contract": "v1.1-functional-sql-readonly",
                    "error_code": "UNEXPECTED_FAILURE",
                    "exception_type": type(exc).__name__,
                    "message": "验收发生未预期错误，详细内容已从输出中移除",
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


if __name__ == "__main__":
    main()
