from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_quick_analysis_revision_is_the_single_alembic_head() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["sql2014_0018"]


def test_quick_analysis_sql_keeps_workspace_out_of_canonical_facts() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0012_quick_analysis_workspace_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    assert "CREATE TABLE workspace.analysis_session" in sql
    assert "analysis_session_id bigint NULL" in sql
    assert "'QUICK_PAT'" in sql
    assert "CREATE TABLE test." not in sql
    assert "INSERT test." not in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql


def test_quick_workspace_lifecycle_sql_is_auditable_and_sql2014_safe() -> None:
    sql = (
        ROOT
        / "db"
        / "alembic"
        / "sql"
        / "0013_quick_workspace_lifecycle_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    assert "reserved_bytes bigint NOT NULL" in sql
    assert "cleanup_status varchar(16) NOT NULL" in sql
    assert "physical_status varchar(16) NOT NULL" in sql
    assert "deleted_at_utc datetime2(3) NULL" in sql
    assert "CREATE TABLE test." not in sql
    assert "INSERT test." not in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql
