from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_route_a_worker import (
    is_stop_requested,
    remove_ready_file,
    validate_database_identity,
    write_ready_file,
)


def test_stop_file_requests_drain_only_after_it_exists(tmp_path: Path) -> None:
    stop_file = tmp_path / "worker.stop"

    assert is_stop_requested(None) is False
    assert is_stop_requested(stop_file) is False

    stop_file.write_text("stop", encoding="ascii")

    assert is_stop_requested(stop_file) is True


def test_ready_file_is_atomic_and_removed_on_shutdown(tmp_path: Path, monkeypatch) -> None:
    ready_file = tmp_path / "worker.ready.json"
    monkeypatch.setattr("scripts.run_route_a_worker.os.getpid", lambda: 4321)

    write_ready_file(
        ready_file,
        "local-test-worker",
        {
            "database": "TMS_G0_DEV",
            "schema_revision": "sql2014_0014",
            "database_server": "LOCALHOST\\SQLEXPRESS",
        },
    )

    payload = ready_file.read_text(encoding="utf-8")
    assert '"status": "READY"' in payload
    assert '"pid": 4321' in payload
    assert '"worker_id": "local-test-worker"' in payload
    assert '"database": "TMS_G0_DEV"' in payload
    assert '"schema_revision": "sql2014_0014"' in payload
    assert '"database_server": "LOCALHOST\\\\SQLEXPRESS"' in payload
    assert list(tmp_path.glob("*.tmp")) == []

    remove_ready_file(ready_file)

    assert ready_file.exists() is False


def test_worker_database_identity_fails_closed_before_queue_processing() -> None:
    metadata = {
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0014",
        "database_server": "SQL-B",
    }

    with pytest.raises(RuntimeError, match="Worker database identity rejected"):
        validate_database_identity(
            metadata,
            expected_database="TMS_G0_DEV",
            expected_schema_revision="sql2014_0014",
            expected_database_server="SQL-A",
        )
