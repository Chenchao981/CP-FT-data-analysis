from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_0026_is_single_head_and_enables_personal_tool_types() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["sql2014_0026"]
    revision = script.get_revision("sql2014_0026")
    assert revision is not None
    assert revision.down_revision == "sql2014_0025"
    sql = _read("db/alembic/sql/0026_personal_tools_sql2014.sql")
    for analysis_type in (
        "QUICK_PAT",
        "QUICK_CLEAN",
        "QUICK_CHART",
        "QUICK_SYL_SBL",
    ):
        assert analysis_type in sql
    assert "DROP CONSTRAINT CK_analysis_session_type" in sql


def test_0026_downgrade_fails_closed_when_personal_tool_rows_exist() -> None:
    sql = _read("db/alembic/sql/0026_personal_tools_down_sql2014.sql")
    assert "WHERE analysis_type <> 'QUICK_PAT'" in sql
    assert "downgrade blocked" in sql
