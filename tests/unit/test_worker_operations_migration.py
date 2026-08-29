from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sql() -> str:
    return (
        ROOT / "db" / "alembic" / "sql" / "0016_worker_operations_sql2014.sql"
    ).read_text(encoding="utf-8-sig")


def test_worker_operations_revision_extends_atomic_finalize() -> None:
    wrapper = (
        ROOT
        / "db"
        / "alembic"
        / "versions"
        / "sql2014_0016_worker_operations.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0016"' in wrapper
    assert 'down_revision = "sql2014_0015"' in wrapper
    assert 'run_sql_file("0016_worker_operations_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_worker_instance_has_bounded_health_and_drain_contract() -> None:
    sql = _sql()

    assert "CREATE TABLE ingestion.worker_instance" in sql
    assert "worker_id nvarchar(128) NOT NULL" in sql
    assert "worker_kind varchar(32) NOT NULL" in sql
    assert "state IN('READY','DRAINING','STOPPED','FAILED')" in sql
    assert "desired_state IN('RUN','DRAIN')" in sql
    assert "started_at_utc datetime2(3) NOT NULL" in sql
    assert "last_seen_at_utc datetime2(3) NOT NULL" in sql
    assert "stopped_at_utc datetime2(3) NULL" in sql
    assert "database_name nvarchar(128) NOT NULL" in sql
    assert "schema_revision varchar(128) NOT NULL" in sql
    assert "host_fingerprint char(64) NOT NULL" in sql
    assert "row_version rowversion NOT NULL" in sql
    assert "CK_worker_instance_lifecycle" in sql
    assert "IX_worker_instance_health" in sql
    assert "IX_worker_instance_control" in sql


def test_worker_instance_migration_is_reentrant_and_sql2014_safe() -> None:
    sql = _sql()

    assert "IF OBJECT_ID(N'ingestion.worker_instance', N'U') IS NULL" in sql
    assert "worker_instance has an incompatible column contract" in sql
    assert "worker_instance has an incompatible column definition" in sql
    assert "worker_instance constraints are incomplete" in sql
    assert "worker_instance defaults are incomplete" in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql
    assert "CREATE OR ALTER" not in sql
    assert "DROP TABLE IF EXISTS" not in sql


def test_worker_registry_does_not_define_sensitive_identity_columns() -> None:
    sql = _sql().lower()

    assert "account_name" not in sql
    assert "user_name" not in sql
    assert "password" not in sql
    assert "connection_string" not in sql
    assert "working_directory" not in sql
    assert "host_name" not in sql
