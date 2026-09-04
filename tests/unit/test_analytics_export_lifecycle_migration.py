from __future__ import annotations

from pathlib import Path

import pytest

from scripts.g0.verify_analytics_export_lifecycle_sql_e2e import (
    assert_lifecycle_write_target,
)

ROOT = Path(__file__).resolve().parents[2]


def test_analytics_export_lifecycle_migration_is_forward_safe_and_fenced() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0021_analytics_export_lifecycle_sql2014.sql"
    ).read_text(encoding="utf-8-sig")

    assert "SET XACT_ABORT ON" in sql
    assert "sql2014_0020 analytics governance is missing" in sql
    assert "attempt_count int NOT NULL" in sql
    assert "max_attempts tinyint NOT NULL" in sql
    assert "lease_token uniqueidentifier NULL" in sql
    assert "lease_owner nvarchar(100) NULL" in sql
    assert "lease_expires_at_utc datetime2(3) NULL" in sql
    assert "heartbeat_at_utc datetime2(3) NULL" in sql
    assert "CK_export_job_attempts" in sql
    assert "CK_export_job_lease" in sql
    assert "status='RUNNING' AND lease_token IS NOT NULL" in sql
    assert "physical_status varchar(16) NOT NULL" in sql
    assert "deletion_attempt_count int NOT NULL" in sql
    assert "deleted_at_utc datetime2(3) NULL" in sql
    assert "deletion_reason nvarchar(1000) NULL" in sql
    assert "'PRESENT','DELETING','DELETED','MISSING','BLOCKED','ERROR'" in sql
    assert "IX_export_artifact_ttl_cleanup" in sql
    assert sql.rstrip().endswith("SET NOCOUNT OFF;\nGO")
    assert "DELETE FROM test." not in sql
    assert "UPDATE test." not in sql
    assert "DROP TABLE test." not in sql


def test_analytics_export_lifecycle_wrapper_follows_0020_and_is_irreversible() -> None:
    wrapper = (
        ROOT
        / "db"
        / "alembic"
        / "versions"
        / "sql2014_0021_analytics_export_lifecycle.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0021"' in wrapper
    assert 'down_revision = "sql2014_0020"' in wrapper
    assert 'run_sql_file("0021_analytics_export_lifecycle_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_analytics_export_runners_and_global_verifier_require_current_head() -> None:
    worker = (ROOT / "scripts" / "run_analytics_export_worker.py").read_text(
        encoding="utf-8-sig"
    )
    cleanup = (ROOT / "scripts" / "run_analytics_export_cleanup.py").read_text(
        encoding="utf-8-sig"
    )
    verifier = (ROOT / "scripts" / "g0" / "verify_sql2014_schema.py").read_text(
        encoding="utf-8-sig"
    )

    assert 'schema_revision"] != "sql2014_0026"' in worker
    assert 'schema_revision"] != "sql2014_0026"' in cleanup
    assert 'assert revision == "sql2014_0026"' in verifier


def test_analytics_export_cleanup_is_packaged_and_scheduled_dry_run_by_default() -> (
    None
):
    release = (ROOT / "scripts" / "release" / "build_tms_release.py").read_text(
        encoding="utf-8-sig"
    )
    wrapper = (
        ROOT / "scripts" / "windows" / "run_tms_analytics_export_cleanup.ps1"
    ).read_text(encoding="utf-8-sig")
    installer = (
        ROOT / "scripts" / "windows" / "install_tms_scheduled_tasks.ps1"
    ).read_text(encoding="utf-8-sig")
    status = (
        ROOT / "scripts" / "windows" / "get_tms_scheduled_task_status.ps1"
    ).read_text(encoding="utf-8-sig")
    uninstaller = (
        ROOT / "scripts" / "windows" / "uninstall_tms_scheduled_tasks.ps1"
    ).read_text(encoding="utf-8-sig")

    assert '"scripts/run_analytics_export_cleanup.py"' in release
    assert "scripts\\run_analytics_export_cleanup.py" in wrapper
    assert "[switch]$Delete" in wrapper
    assert "if ($Delete)" in wrapper
    assert "$arguments += '--delete'" in wrapper
    assert "TMS-AnalyticsExportCleanup" in installer
    assert "AnalyticsExportCleanupMode = 'DryRun'" in installer
    assert "TMS-AnalyticsExportCleanup" in status
    assert "ExpectedAnalyticsExportCleanupMode = 'DryRun'" in status
    assert "TMS-AnalyticsExportCleanup" in uninstaller


def test_real_lifecycle_smoke_is_synthetic_and_never_mutates_canonical() -> None:
    smoke = (
        ROOT / "scripts" / "g0" / "verify_analytics_export_lifecycle_sql_e2e.py"
    ).read_text(encoding="utf-8-sig")

    assert "ANALYTICS_EXPORT_WORKER_CLAIM_LOST" in smoke
    assert "dry_run=True" in smoke
    assert "dry_run=False" in smoke
    assert "DELETE FROM delivery.export_artifact" in smoke
    assert "DELETE FROM delivery.export_job_dataset" in smoke
    assert "DELETE FROM delivery.export_job" in smoke
    assert "DELETE FROM test." not in smoke
    assert "UPDATE test." not in smoke
    assert "INSERT test." not in smoke
    assert "WHERE status='ACTIVE'" in smoke
    assert "is_active" not in smoke


def test_real_lifecycle_smoke_write_guard_requires_exact_dev_target() -> None:
    assert_lifecycle_write_target(
        {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0026"}
    )
    with pytest.raises(RuntimeError, match="TMS_G0_DEV"):
        assert_lifecycle_write_target(
            {"database": "NCE_TMS", "schema_revision": "sql2014_0026"}
        )
    with pytest.raises(RuntimeError, match="sql2014_0026"):
        assert_lifecycle_write_target(
            {"database": "TMS_G0_DEV", "schema_revision": "sql2014_0021"}
        )


def test_real_lifecycle_smoke_checks_target_before_obtaining_write_engine() -> None:
    source = (
        ROOT / "scripts" / "g0" / "verify_analytics_export_lifecycle_sql_e2e.py"
    ).read_text(encoding="utf-8-sig")
    main_source = source[source.index("def main()") :]

    assert main_source.index("assert_lifecycle_write_target(database)") < (
        main_source.index("engine = get_engine()")
    )
