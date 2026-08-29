from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_worker_wrapper_passes_database_identity_and_graceful_control_files() -> None:
    script = (ROOT / "scripts" / "windows" / "run_tms_worker.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "[string]$ExpectedDatabase" in script
    assert "[string]$ExpectedSchemaRevision" in script
    assert "[string]$ExpectedDatabaseServer" in script
    assert "[string]$ReadyFile" in script
    assert "[string]$StopFile" in script
    assert "TMS_EXPECTED_DATABASE" in script
    assert "TMS_EXPECTED_SCHEMA_REVISION" in script
    assert "TMS_EXPECTED_DATABASE_SERVER" in script
    assert "TMS_WORKER_READY_FILE" in script
    assert "TMS_WORKER_STOP_FILE" in script
    assert "'--expected-database'" in script
    assert "'--expected-schema-revision'" in script
    assert "'--expected-database-server'" in script
    assert "'--ready-file'" in script
    assert "'--stop-file'" in script
    assert "Stop-Process" not in script
    assert "taskkill" not in script.lower()
