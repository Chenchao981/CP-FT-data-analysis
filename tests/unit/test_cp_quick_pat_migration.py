from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_0025_is_single_head_and_allows_cp_ft_quick_sessions() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["sql2014_0028"]
    revision = script.get_revision("sql2014_0025")
    assert revision is not None
    assert revision.down_revision == "sql2014_0024"
    sql = _read("db/alembic/sql/0025_cp_quick_pat_sql2014.sql")
    assert "DROP CONSTRAINT CK_analysis_session_stage" in sql
    assert "test_stage IN('CP','FT')" in sql
    assert "SET NOCOUNT OFF" in sql


def test_0025_downgrade_fails_closed_when_cp_sessions_exist() -> None:
    sql = _read("db/alembic/sql/0025_cp_quick_pat_down_sql2014.sql")
    assert "WHERE test_stage='CP'" in sql
    assert "downgrade blocked: CP Quick Analysis sessions exist" in sql
    assert "test_stage IN('FT')" in sql
