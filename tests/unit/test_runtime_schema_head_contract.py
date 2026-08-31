from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
CURRENT_SCHEMA_HEAD = "sql2014_0023"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_repository_has_the_expected_single_schema_head() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [CURRENT_SCHEMA_HEAD]


@pytest.mark.parametrize(
    ("relative_path", "required_fragment"),
    [
        (
            "scripts/windows/start_tms_local_test.ps1",
            "$expectedSchemaRevision = 'sql2014_0023'",
        ),
        (
            "scripts/run_analytics_export_worker.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/run_analytics_export_cleanup.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/smoke_analytics_export_content.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_analytics_export_lifecycle_sql_e2e.py",
            'database.get("schema_revision") != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_route_a_worker_foundation.py",
            'assert revision == "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_sql2014_schema.py",
            'assert revision == "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v11_functional_sql_readonly.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v12_performance.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v12_duplicate_upload_concurrency_sql_e2e.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v12_duplicate_upload_to_current_sql_e2e.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v12_visibility_duplicate_sql_e2e.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v13_parameter_analysis.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_v13_analytics_closure_performance.py",
            'EXPECTED_SCHEMA_REVISION = "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_quick_cleanup_sql_e2e.py",
            'revision != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_quick_pat_sql_e2e.py",
            'revision != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_a5_archive_sql_e2e.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_a5_lifecycle_concurrency_sql_e2e.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_a5_reprocess_sql_e2e.py",
            'schema_revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_atomic_finalize_sql_e2e.py",
            'identity["revision"] != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_initial_import_state_consistency.py",
            'revision != "sql2014_0023"',
        ),
        (
            "scripts/g0/verify_lot_input_resume_sql_e2e.py",
            '"revision": "sql2014_0023"',
        ),
        (
            "scripts/g0/bootstrap_existing_cleaner_releases.py",
            'revision != "sql2014_0023"',
        ),
        (
            "scripts/g0/repair_processing_run_current.py",
            'default="sql2014_0023"',
        ),
    ],
)
def test_operational_entrypoints_require_current_schema_head(
    relative_path: str,
    required_fragment: str,
) -> None:
    assert required_fragment in _read(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        "backend/README.md",
        "docs/development/TMS_Local_Test_User_Guide_2026-08-28.md",
        "docs/development/TMS_Windows_Runtime_Deployment_Guide.md",
        "docs/operations/TMS_Production_Deployment_Backup_Restore_Runbook.md",
    ],
)
def test_current_runtime_documentation_names_current_schema_head(
    relative_path: str,
) -> None:
    assert CURRENT_SCHEMA_HEAD in _read(relative_path)
