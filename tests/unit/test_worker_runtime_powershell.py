from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name,prefix", [
    ("run_tms_worker.ps1", "TMS_WORKER"),
    ("run_tms_analytics_export_worker.ps1", "TMS_ANALYTICS_EXPORT_WORKER"),
])
def test_worker_wrapper_passes_database_identity_and_graceful_control_files(name, prefix) -> None:
    script = (ROOT / "scripts" / "windows" / name).read_text(
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
    assert f"{prefix}_READY_FILE" in script
    assert f"{prefix}_STOP_FILE" in script
    assert "'--expected-database'" in script
    assert "'--expected-schema-revision'" in script
    assert "'--expected-database-server'" in script
    assert "'--ready-file'" in script
    assert "'--stop-file'" in script
    assert "Stop-Process" not in script
    assert "taskkill" not in script.lower()
