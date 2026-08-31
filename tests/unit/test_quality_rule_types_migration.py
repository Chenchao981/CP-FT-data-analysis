from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quality_rule_type_migration_is_forward_only_and_trusted() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0022_quality_rule_types_sql2014.sql"
    ).read_text(encoding="utf-8-sig")

    assert "SET XACT_ABORT ON" in sql
    assert "sql2014_0021 analytics export lifecycle is missing" in sql
    assert "DROP CONSTRAINT CK_evaluation_rule_type" in sql
    assert "WITH CHECK ADD CONSTRAINT CK_evaluation_rule_type" in sql
    assert "'SYL'" in sql
    assert "'PASS_FAIL_DISTRIBUTION'" in sql
    assert "INSERT INTO evaluation." not in sql
    assert "UPDATE test." not in sql
    assert "DELETE FROM test." not in sql
    assert "DROP TABLE test." not in sql


def test_quality_rule_type_wrapper_follows_0021_and_is_irreversible() -> None:
    wrapper = (
        ROOT / "db" / "alembic" / "versions" / "sql2014_0022_quality_rule_types.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0022"' in wrapper
    assert 'down_revision = "sql2014_0021"' in wrapper
    assert 'run_sql_file("0022_quality_rule_types_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper
