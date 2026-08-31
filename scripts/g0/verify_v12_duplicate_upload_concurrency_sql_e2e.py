from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Lock, get_ident
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.auth import Principal
from app.domain.stage_data import StoredUpload
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_stage_data_service import SqlStageDataService

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0023"
_SOURCE_SELECT_MARKERS = (
    "SELECT source_file_id FROM ingestion.source_file",
    "WITH (UPDLOCK,HOLDLOCK)",
    "WHERE sha256",
)
_COUNTED_TABLES = (
    "ingestion.import_batch",
    "ingestion.source_file",
    "ingestion.source_file_receipt",
    "ingestion.import_batch_file",
)


@dataclass(frozen=True, slots=True)
class _ExpectedUpload:
    owner_user_id: int
    batch_name: str
    storage_uri: str


class _BarrierAudit:
    def __init__(self) -> None:
        self._lock = Lock()
        self._thread_ids: set[int] = set()

    def mark_source_select(self) -> None:
        with self._lock:
            self._thread_ids.add(get_ident())

    @property
    def thread_count(self) -> int:
        with self._lock:
            return len(self._thread_ids)


class _BarrierConnection:
    def __init__(
        self,
        connection: Connection,
        barrier: Barrier,
        audit: _BarrierAudit,
    ) -> None:
        self._connection = connection
        self._barrier = barrier
        self._audit = audit
        self._source_select_waited = False

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        sql = str(statement)
        if not self._source_select_waited and all(
            marker in sql for marker in _SOURCE_SELECT_MARKERS
        ):
            self._source_select_waited = True
            self._audit.mark_source_select()
            self._barrier.wait(timeout=15)
        if parameters is None:
            return self._connection.execute(statement)
        return self._connection.execute(statement, parameters)


class _ConcurrentEngine:
    """Give each service call a real connection and synchronize its Source SELECT."""

    def __init__(
        self,
        engine: Engine,
        barrier: Barrier,
        audit: _BarrierAudit,
    ) -> None:
        self._engine = engine
        self._barrier = barrier
        self._audit = audit

    @contextmanager
    def begin(self) -> Iterator[_BarrierConnection]:
        with self._engine.begin() as connection:
            connection.execute(text("SET LOCK_TIMEOUT 15000"))
            yield _BarrierConnection(connection, self._barrier, self._audit)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify concurrent same-SHA upload registration in "
            "TMS_G0_DEV/sql2014_0023, then precisely delete the random fixture"
        )
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="include the random fixture token in PASS output",
    )
    return parser.parse_args()


def _assert_database_identity(identity: Mapping[str, str]) -> None:
    database = identity.get("database")
    revision = identity.get("schema_revision")
    if database != EXPECTED_DATABASE or revision != EXPECTED_SCHEMA_REVISION:
        raise RuntimeError(
            "concurrency E2E is restricted to "
            f"{EXPECTED_DATABASE}/{EXPECTED_SCHEMA_REVISION}; "
            f"got {database}/{revision}"
        )


def _table_counts(connection: Connection) -> dict[str, int]:
    return {
        table: int(
            connection.execute(text(f"SELECT COUNT_BIG(*) FROM {table}")).scalar_one()
        )
        for table in _COUNTED_TABLES
    }


def _fixture_leak_count(
    connection: Connection,
    *,
    sha256: str,
    batch_names: Sequence[str],
    remark: str,
) -> int:
    return sum(
        (
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.import_batch "
                        "WHERE batch_name IN(:first,:second) AND remark=:remark"
                    ),
                    {
                        "first": batch_names[0],
                        "second": batch_names[1],
                        "remark": remark,
                    },
                ).scalar_one()
            ),
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.source_file "
                        "WHERE sha256=:sha"
                    ),
                    {"sha": sha256},
                ).scalar_one()
            ),
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.source_file_receipt r "
                        "JOIN ingestion.source_file s "
                        "ON s.source_file_id=r.source_file_id WHERE s.sha256=:sha"
                    ),
                    {"sha": sha256},
                ).scalar_one()
            ),
        )
    )


def _active_principals(connection: Connection) -> tuple[Principal, Principal]:
    rows = (
        connection.execute(
            text(
                "SELECT TOP (2) user_id,login_name,display_name "
                "FROM iam.app_user WHERE status='ACTIVE' ORDER BY user_id"
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 2:
        raise RuntimeError(
            "two active application users are required before any fixture write"
        )
    principals = tuple(
        Principal(
            user_id=int(row["user_id"]),
            login_name=str(row["login_name"]),
            display_name=str(row["display_name"]),
            roles=(),
            permissions=frozenset({"TASK_CREATE"}),
        )
        for row in rows
    )
    return principals  # type: ignore[return-value]


def _load_evidence(
    connection: Connection,
    *,
    batch_ids: Sequence[int],
) -> list[Mapping[str, Any]]:
    return list(
        connection.execute(
            text(
                "SELECT b.import_batch_id,b.owner_user_id,b.batch_name,b.remark,"
                "b.business_domain,b.test_stage,b.factory_code,r.receipt_id,"
                "r.is_duplicate_receipt,r.metadata_json,s.source_file_id,s.sha256 "
                "FROM ingestion.import_batch b "
                "JOIN ingestion.import_batch_file ibf "
                "ON ibf.import_batch_id=b.import_batch_id "
                "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                "WHERE b.import_batch_id IN(:first,:second) "
                "ORDER BY b.import_batch_id"
            ),
            {"first": batch_ids[0], "second": batch_ids[1]},
        )
        .mappings()
        .all()
    )


def _assert_concurrent_outcome(
    rows: Sequence[Mapping[str, Any]],
    *,
    batch_ids: Sequence[int],
    sha256: str,
    expected: Mapping[str, _ExpectedUpload],
) -> int:
    if len(rows) != 2:
        raise RuntimeError(f"expected two committed upload rows, got {len(rows)}")
    if len(set(batch_ids)) != 2:
        raise RuntimeError("concurrent register_upload calls reused an Import Batch")
    if {int(row["import_batch_id"]) for row in rows} != set(batch_ids):
        raise RuntimeError("returned Batch identities do not match committed rows")
    source_ids = {int(row["source_file_id"]) for row in rows}
    receipt_ids = {int(row["receipt_id"]) for row in rows}
    if len(source_ids) != 1:
        raise RuntimeError("same SHA created more than one immutable Source")
    if len(receipt_ids) != 2:
        raise RuntimeError("concurrent uploads did not create two Receipts")
    if {str(row["sha256"]) for row in rows} != {sha256}:
        raise RuntimeError("committed Source SHA does not match the fixture")
    if sorted(bool(row["is_duplicate_receipt"]) for row in rows) != [False, True]:
        raise RuntimeError(
            "duplicate Receipt flags must contain exactly one false and one true"
        )
    for row in rows:
        batch_name = str(row["batch_name"])
        contract = expected.get(batch_name)
        if contract is None:
            raise RuntimeError("unexpected Batch entered the random fixture scope")
        metadata = json.loads(row["metadata_json"] or "{}")
        if int(row["owner_user_id"]) != contract.owner_user_id:
            raise RuntimeError("Batch owner does not match its register_upload caller")
        if metadata.get("receipt_storage_uri") != contract.storage_uri:
            raise RuntimeError("Receipt did not retain its uploader-specific path")
        if (
            row["business_domain"] != "PRODUCTION"
            or row["test_stage"] != "CP"
            or row["factory_code"] != "V12-CONCURRENT"
        ):
            raise RuntimeError("Batch business identity drifted during registration")
    return next(iter(source_ids))


def _cleanup_fixture(
    engine: Engine,
    *,
    sha256: str,
    batch_names: Sequence[str],
    remark: str,
    expected_owner_ids: set[int],
) -> dict[str, int]:
    deleted = {"batch_files": 0, "receipts": 0, "batches": 0, "sources": 0}
    with engine.begin() as connection:
        batch_rows = (
            connection.execute(
                text(
                    "SELECT import_batch_id,owner_user_id,business_domain,test_stage,"
                    "factory_code,batch_name,remark FROM ingestion.import_batch "
                    "WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE batch_name IN(:first,:second) AND remark=:remark"
                ),
                {
                    "first": batch_names[0],
                    "second": batch_names[1],
                    "remark": remark,
                },
            )
            .mappings()
            .all()
        )
        batch_ids: list[int] = []
        for row in batch_rows:
            if (
                int(row["owner_user_id"]) not in expected_owner_ids
                or row["business_domain"] != "PRODUCTION"
                or row["test_stage"] != "CP"
                or row["factory_code"] != "V12-CONCURRENT"
                or str(row["batch_name"]) not in batch_names
                or row["remark"] != remark
            ):
                raise RuntimeError("cleanup guard rejected a non-fixture Batch")
            batch_ids.append(int(row["import_batch_id"]))
        if len(batch_ids) > 2:
            raise RuntimeError("cleanup guard found too many fixture Batches")

        source_rows = (
            connection.execute(
                text(
                    "SELECT source_file_id FROM ingestion.source_file "
                    "WITH (UPDLOCK,HOLDLOCK) WHERE sha256=:sha"
                ),
                {"sha": sha256},
            )
            .scalars()
            .all()
        )
        if len(source_rows) > 1:
            raise RuntimeError("cleanup guard found duplicate Sources for one SHA")
        if batch_ids:
            parameters = {
                f"batch_{index}": batch_id for index, batch_id in enumerate(batch_ids)
            }
            placeholders = ",".join(f":{name}" for name in parameters)
            foreign_receipts = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.source_file_receipt r "
                        "JOIN ingestion.source_file s "
                        "ON s.source_file_id=r.source_file_id "
                        f"WHERE r.import_batch_id IN({placeholders}) AND s.sha256<>:sha"
                    ),
                    parameters | {"sha": sha256},
                ).scalar_one()
            )
            if foreign_receipts:
                raise RuntimeError(
                    "cleanup guard found a non-fixture Source in fixture Batches"
                )
            deleted["batch_files"] = int(
                connection.execute(
                    text(
                        "DELETE FROM ingestion.import_batch_file "
                        f"WHERE import_batch_id IN({placeholders})"
                    ),
                    parameters,
                ).rowcount
            )
            deleted["receipts"] = int(
                connection.execute(
                    text(
                        "DELETE FROM ingestion.source_file_receipt "
                        f"WHERE import_batch_id IN({placeholders})"
                    ),
                    parameters,
                ).rowcount
            )
            deleted["batches"] = int(
                connection.execute(
                    text(
                        "DELETE FROM ingestion.import_batch "
                        f"WHERE import_batch_id IN({placeholders})"
                    ),
                    parameters,
                ).rowcount
            )
        deleted["sources"] = int(
            connection.execute(
                text(
                    "DELETE s FROM ingestion.source_file s WHERE s.sha256=:sha "
                    "AND NOT EXISTS(SELECT 1 FROM ingestion.source_file_receipt r "
                    "WHERE r.source_file_id=s.source_file_id)"
                ),
                {"sha": sha256},
            ).rowcount
        )
    return deleted


def main() -> None:
    args = _parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    _assert_database_identity(identity)
    engine = get_engine()
    token = uuid4().hex
    sha256 = hashlib.sha256(f"v12-concurrent:{token}".encode()).hexdigest()
    remark = f"v1.2 same-SHA concurrency cleanup token {token}"
    paths = (
        ROOT / "data" / "work" / "v12-concurrency" / token / "uploader-a.csv",
        ROOT / "data" / "work" / "v12-concurrency" / token / "uploader-b.csv",
    )
    batch_names = (f"uploader-a-{token}.csv", f"uploader-b-{token}.csv")

    with engine.connect() as connection:
        principals = _active_principals(connection)
        baseline = _table_counts(connection)
        if _fixture_leak_count(
            connection,
            sha256=sha256,
            batch_names=batch_names,
            remark=remark,
        ):
            raise RuntimeError(
                "random fixture token already exists before concurrency test"
            )

    barrier = Barrier(2)
    audit = _BarrierAudit()
    concurrent_engine = _ConcurrentEngine(engine, barrier, audit)
    service = SqlStageDataService(concurrent_engine)  # type: ignore[arg-type]
    expected = {
        batch_names[index]: _ExpectedUpload(
            principals[index].user_id,
            batch_names[index],
            str(paths[index]),
        )
        for index in range(2)
    }
    failure: BaseException | None = None
    batch_ids: list[int] = []
    source_file_id: int | None = None
    deleted: dict[str, int] | None = None
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    service.register_upload,
                    principals[index],
                    "PRODUCTION",
                    "CP",
                    "V12-CONCURRENT",
                    (
                        StoredUpload(
                            original_name=batch_names[index],
                            path=paths[index],
                            size_bytes=256,
                            sha256=sha256,
                        ),
                    ),
                    remark,
                )
                for index in range(2)
            ]
            batch_ids = [int(future.result(timeout=30)) for future in futures]
        if audit.thread_count != 2:
            raise RuntimeError(
                "thread barrier did not intercept two independent Source SELECT calls"
            )
        with engine.connect() as connection:
            evidence = _load_evidence(connection, batch_ids=batch_ids)
            source_file_id = _assert_concurrent_outcome(
                evidence,
                batch_ids=batch_ids,
                sha256=sha256,
                expected=expected,
            )
            source_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.source_file WHERE sha256=:sha"
                    ),
                    {"sha": sha256},
                ).scalar_one()
            )
            if source_count != 1:
                raise RuntimeError("same SHA did not persist exactly one Source")
    except BaseException as exc:
        failure = exc
    finally:
        try:
            deleted = _cleanup_fixture(
                engine,
                sha256=sha256,
                batch_names=batch_names,
                remark=remark,
                expected_owner_ids={principal.user_id for principal in principals},
            )
        except BaseException as cleanup_exc:
            if failure is not None:
                raise RuntimeError(
                    "fixture verification and exact cleanup both failed"
                ) from failure
            raise cleanup_exc

    with engine.connect() as connection:
        after = _table_counts(connection)
        leaked = _fixture_leak_count(
            connection,
            sha256=sha256,
            batch_names=batch_names,
            remark=remark,
        )
    if baseline != after or leaked:
        cleanup_error = RuntimeError(
            "concurrency cleanup did not restore database rows: "
            f"count_drift={baseline != after}, fixture_rows={leaked}"
        )
        if failure is not None:
            raise cleanup_error from failure
        raise cleanup_error
    if failure is not None:
        raise failure
    if source_file_id is None or deleted is None:
        raise RuntimeError("concurrency E2E produced no verification evidence")
    if deleted != {"batch_files": 2, "receipts": 2, "batches": 2, "sources": 1}:
        raise RuntimeError(f"unexpected exact cleanup counts: {deleted}")

    token_output = f" token={token}" if args.show_token else ""
    print(
        "v12_same_sha_concurrency=PASS register_success=2 source_count=1 "
        "batch_count=2 receipt_count=2 duplicate_flags=false,true "
        f"source_file_id={source_file_id}{token_output}"
    )
    print("v12_source_select_barrier=PASS connections=2 threads=2 source_select_hits=2")
    print(
        "v12_concurrency_cleanup=PASS database=TMS_G0_DEV schema=sql2014_0023 "
        "counts_restored=true fixture_rows=0 filesystem_snapshots_created=0"
    )


if __name__ == "__main__":
    main()
