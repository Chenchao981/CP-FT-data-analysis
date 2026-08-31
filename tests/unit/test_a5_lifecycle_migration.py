from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a5_lifecycle_migration_is_sql2014_safe_and_fail_closed() -> None:
    sql = (ROOT / "db" / "alembic" / "sql" / "0018_a5_lifecycle_sql2014.sql").read_text(
        encoding="utf-8-sig"
    )

    assert "SET XACT_ABORT ON" in sql
    assert "COL_LENGTH(N'dataset.dataset', N'lifecycle_status') IS NULL" in sql
    assert "lifecycle_status varchar(16) NOT NULL" in sql
    assert "archived_at_utc datetime2(3) NULL" in sql
    assert "archived_by_user_id bigint NULL" in sql
    assert "archive_reason nvarchar(1000) NULL" in sql
    assert "lifecycle_row_version rowversion NOT NULL" in sql
    assert "CREATE TABLE ingestion.lifecycle_job_target" in sql
    assert "action_type IN('EXPORT_LATEST','REPROCESS_UPDATE','DELETE_TASK')" in sql
    assert "DELETING" in sql
    assert "sql2014_0013 artifact cleanup columns are missing" in sql
    assert "IF NOT EXISTS" in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql
    assert "CREATE OR ALTER" not in sql
    assert "DROP TABLE test." not in sql
    assert "DELETE FROM test." not in sql
    assert "DELETE FROM ingestion.import_batch" not in sql
    assert "DELETE FROM ingestion.source_file" not in sql


def test_a5_lifecycle_alembic_wrapper_follows_0017_and_is_irreversible() -> None:
    wrapper = (
        ROOT / "db" / "alembic" / "versions" / "sql2014_0018_a5_lifecycle.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0018"' in wrapper
    assert 'down_revision = "sql2014_0017"' in wrapper
    assert 'run_sql_file("0018_a5_lifecycle_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_sql2014_verifier_tracks_current_head_and_a5_lifecycle_contract() -> None:
    verifier = (ROOT / "scripts" / "g0" / "verify_sql2014_schema.py").read_text(
        encoding="utf-8-sig"
    )

    assert 'assert revision == "sql2014_0023"' in verifier
    assert '"ingestion.lifecycle_job_target"' in verifier
    assert '"lifecycle_status"' in verifier
    assert 'assert "DELETING" in artifact_status_check[0]' in verifier
