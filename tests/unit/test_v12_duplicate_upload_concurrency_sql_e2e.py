from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import text

from scripts.g0 import verify_v12_duplicate_upload_concurrency_sql_e2e as verifier

ROOT = Path(__file__).resolve().parents[2]


class _FakeConnection:
    def execute(self, statement, parameters=None):
        return (str(statement), parameters)


def test_v12_concurrency_database_guard_is_exact() -> None:
    verifier._assert_database_identity(
        {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0019"}
    )
    with pytest.raises(RuntimeError, match="restricted to TMS_G0_DEV/sql2014_0019"):
        verifier._assert_database_identity(
            {"database": "TMS_PROD", "schema_revision": "sql2014_0019"}
        )
    with pytest.raises(RuntimeError, match="restricted to TMS_G0_DEV/sql2014_0019"):
        verifier._assert_database_identity(
            {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0018"}
        )


def test_v12_source_select_barrier_requires_two_threads() -> None:
    barrier = Barrier(2)
    audit = verifier._BarrierAudit()
    sql = text(
        "SELECT source_file_id FROM ingestion.source_file "
        "WITH (UPDLOCK,HOLDLOCK) WHERE sha256=:sha256"
    )

    def execute_once():
        connection = verifier._BarrierConnection(_FakeConnection(), barrier, audit)
        return connection.execute(sql, {"sha256": "a" * 64})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(execute_once), executor.submit(execute_once)]
        assert all(
            future.result(timeout=2)[1] == {"sha256": "a" * 64} for future in results
        )
    assert audit.thread_count == 2


def test_v12_concurrency_e2e_has_exact_cleanup_and_no_filesystem_writes() -> None:
    source = (
        ROOT / "scripts" / "g0" / "verify_v12_duplicate_upload_concurrency_sql_e2e.py"
    ).read_text(encoding="utf-8")

    assert 'EXPECTED_DATABASE = "TMS_G0_DEV"' in source
    assert 'EXPECTED_SCHEMA_REVISION = "sql2014_0019"' in source
    assert "ThreadPoolExecutor(max_workers=2)" in source
    assert "Barrier(2)" in source
    assert "WITH (UPDLOCK,HOLDLOCK)" in source
    assert "service.register_upload" in source
    assert "DELETE FROM ingestion.import_batch_file" in source
    assert "DELETE FROM ingestion.source_file_receipt" in source
    assert "DELETE FROM ingestion.import_batch" in source
    assert "DELETE s FROM ingestion.source_file" in source
    assert '",".join(f":{name}" for name in parameters)' in source
    assert "_table_counts" in source
    assert "_fixture_leak_count" in source
    assert "filesystem_snapshots_created=0" in source
    assert ".mkdir(" not in source
    assert ".write_" not in source
    assert "open(" not in source
