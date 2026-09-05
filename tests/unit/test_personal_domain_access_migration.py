from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_0024_is_single_head_with_reversible_wrapper() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["sql2014_0027"]
    revision = script.get_revision("sql2014_0024")
    assert revision is not None
    assert revision.down_revision == "sql2014_0023"
    wrapper = _read("db/alembic/versions/sql2014_0024_personal_domain_access.py")
    assert 'run_sql_file("0024_personal_domain_access_sql2014.sql")' in wrapper
    assert 'run_sql_file("0024_personal_domain_access_down_sql2014.sql")' in wrapper


def test_0024_creates_explicit_domain_model_and_control_permissions() -> None:
    sql = _read("db/alembic/sql/0024_personal_domain_access_sql2014.sql")

    assert "CREATE TABLE iam.data_domain (" in sql
    assert "CREATE TABLE iam.data_domain_grant (" in sql
    assert "CREATE TABLE ingestion.source_definition (" in sql
    for column in ("access_scope", "data_domain_id", "source_definition_id"):
        assert f"ingestion.import_batch ADD {column}" in sql
        assert f"dataset.dataset ADD {column}" in sql
    for permission in (
        "DATA_DOMAIN_ADMIN",
        "SOURCE_ADMIN",
        "SYSTEM_OPERATE",
        "DATA_BREAK_GLASS",
    ):
        assert permission in sql
    assert "BUSINESS_USER" in sql
    role_block = sql[
        sql.index("MERGE iam.role") : sql.index(";WITH business_permissions")
    ]
    assert "DATA_BREAK_GLASS" not in role_block
    assert "JOIN iam.permission p ON p.permission_code='DATA_BREAK_GLASS'" not in sql
    assert "must not become usable before a reason-bound, durable audit flow" in sql
    assert "DATASET_PUBLISH" not in _business_permission_block(sql)
    assert "USER_ADMIN" not in _business_permission_block(sql)
    platform_start = sql.index(
        "INSERT iam.role_permission",
        sql.index("Platform administrators receive control-plane functions only."),
    )
    platform_end = sql.index("GO", platform_start)
    platform_permissions = sql[platform_start:platform_end]
    assert "'DATA_DOMAIN_ADMIN','SOURCE_ADMIN','SYSTEM_OPERATE'" in platform_permissions
    assert "'DATA_BREAK_GLASS'" not in platform_permissions
    assert "DATA_BREAK_GLASS must not be bound to any role in this release" in sql
    assert "JOIN iam.permission p ON p.permission_id=rp.permission_id" in sql


def test_0024_allows_local_agent_manifest_mode() -> None:
    sql = _read("db/alembic/sql/0024_personal_domain_access_sql2014.sql")

    assert "ALTER TABLE workspace.analysis_session" in sql
    assert "DROP CONSTRAINT CK_analysis_session_manifest_mode" in sql
    assert (
        "source_manifest_mode IN('PATH_SIZE_MTIME_V1','LOCAL_PATH_SIZE_MTIME_V1')"
        in sql
    )


def test_0024_adds_fail_closed_quick_result_acl_binding() -> None:
    sql = _read("db/alembic/sql/0024_personal_domain_access_sql2014.sql")

    assert "workspace.analysis_session ADD access_scope" in sql
    assert "workspace.analysis_session ADD data_domain_id" in sql
    assert "FK_analysis_session_data_domain" in sql
    assert "CK_analysis_session_access_scope" in sql
    assert "CK_analysis_session_access_binding" in sql
    assert "source_root_code=N'LOCAL_AGENT' THEN 'PERSONAL'" in sql
    assert "ELSE @quick_migration_hold_domain_id" in sql
    assert "Quick Analysis access-scope backfill failed closed" in sql
    assert "IX_analysis_session_domain_access" in sql


def test_0024_history_backfill_is_fail_closed_and_not_production_driven() -> None:
    sql = _read("db/alembic/sql/0024_personal_domain_access_sql2014.sql")
    backfill = sql[sql.index("/* Existing owned rows become PERSONAL") :]

    assert "WHERE owner_user_id IS NOT NULL" in backfill
    assert "access_scope='PERSONAL'" in backfill
    assert "MIGRATION_HOLD" in sql
    assert "SYSTEM_INGESTION" in sql
    assert "SYSTEM_INGESTION identity conflicts with an existing user" in sql
    assert "SYSTEM_INGESTION must not have application roles" in sql
    assert "WHERE owner_user_id IS NULL" in backfill
    assert "dataset_acl_classification" in backfill
    assert "b.owner_user_id<>d.owner_user_id" in backfill
    assert "PERSONAL Dataset history has inconsistent Batch ownership" in backfill
    assert "business_domain='PRODUCTION'" not in backfill
    assert "CK_import_batch_access_binding" in sql
    assert "CK_dataset_access_binding" in sql
    assert "UX_data_domain_grant_active" in sql


def test_0024_downgrade_refuses_to_drop_live_domain_data() -> None:
    sql = _read("db/alembic/sql/0024_personal_domain_access_down_sql2014.sql")

    assert "downgrade blocked: data-domain grants exist" in sql
    assert "downgrade blocked: source definitions exist" in sql
    assert "downgrade blocked: business data domains exist" in sql
    assert "downgrade blocked: DOMAIN datasets exist" in sql
    assert "source_manifest_mode='LOCAL_PATH_SIZE_MTIME_V1'" in sql
    assert "downgrade blocked: LOCAL quick-analysis manifests exist" in sql
    assert "source_manifest_mode IN('PATH_SIZE_MTIME_V1')" in sql
    assert "DROP INDEX IX_analysis_session_domain_access" in sql
    assert "DROP CONSTRAINT CK_analysis_session_access_binding" in sql
    assert "workspace.analysis_session DROP COLUMN data_domain_id" in sql
    assert "workspace.analysis_session DROP COLUMN access_scope" in sql


def _business_permission_block(sql: str) -> str:
    start = sql.index(";WITH business_permissions")
    end = sql.index("INSERT iam.role_permission", start)
    return sql[start:end]
