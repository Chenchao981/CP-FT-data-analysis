from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_quick_analysis_revision_is_the_single_alembic_head() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["sql2014_0012"]


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
