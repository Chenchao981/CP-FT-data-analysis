from __future__ import annotations

from pathlib import Path

import pytest

from scripts.g0 import verify_v12_visibility_duplicate_sql_e2e as verifier

ROOT = Path(__file__).resolve().parents[2]


def test_v12_sql_e2e_database_guard_is_exact() -> None:
    verifier._assert_database_identity(
        {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0025"}
    )

    with pytest.raises(RuntimeError, match="restricted to TMS_G0_DEV/sql2014_0025"):
        verifier._assert_database_identity(
            {"database": "TMS_PROD", "schema_revision": "sql2014_0025"}
        )
    with pytest.raises(RuntimeError, match="restricted to TMS_G0_DEV/sql2014_0025"):
        verifier._assert_database_identity(
            {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0018"}
        )


def test_v12_sql_e2e_state_assertion_is_fail_closed() -> None:
    good = [
        {
            "dataset_id": 11,
            "dataset_version_id": 101,
            "version_no": 1,
            "version_status": "PUBLISHED",
            "version_current": True,
            "supersedes_dataset_version_id": None,
            "processing_run_id": 201,
            "run_status": "PUBLISHED",
            "run_current": True,
        }
    ]
    expected = {(11, 1): (101, "PUBLISHED", True, 201, "PUBLISHED", True, None)}
    verifier._assert_dataset_run_state(good, expected)

    drifted = [{**good[0], "run_current": False}]
    with pytest.raises(RuntimeError, match="Dataset/Run Current state mismatch"):
        verifier._assert_dataset_run_state(drifted, expected)


def test_v12_sql_e2e_is_rollback_only_and_uses_application_services() -> None:
    source = (
        ROOT / "scripts" / "g0" / "verify_v12_visibility_duplicate_sql_e2e.py"
    ).read_text(encoding="utf-8")

    assert 'EXPECTED_DATABASE = "TMS_G0_DEV"' in source
    assert 'EXPECTED_SCHEMA_REVISION = "sql2014_0025"' in source
    assert "transaction.rollback()" in source
    assert ".commit(" not in source
    assert "_fixture_leak_count" in source
    assert "SqlStageDataService(bound_engine)" in source
    assert "SqlDatasetService(bound_engine)" in source
    assert "stage_service.register_upload(" in source
    assert "dataset_service.create_version(" in source
    assert "dataset_service.publish(" in source
    assert "dataset_service.assert_dataset_access(" in source
    assert "dataset_service.get_summary(" in source
    assert "stage_service.list_uploads(" in source
    assert "UX_processing_run_current" in source
    assert "IX_processing_run_source_state" in source
    assert "database_rows_restored=true durable_fixture_rows=0" in source
