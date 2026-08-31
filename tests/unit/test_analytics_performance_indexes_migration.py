from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def test_analytics_performance_index_migration_is_the_single_head() -> None:
    config = Config(str(ROOT / "db" / "alembic" / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["sql2014_0023"]
    revision = script.get_revision("sql2014_0023")
    assert revision is not None
    assert revision.down_revision == "sql2014_0022"


def test_upgrade_indexes_match_real_sql2014_canonical_columns() -> None:
    sql = _read("db/alembic/sql/0023_analytics_performance_indexes_sql2014.sql")

    assert "SET XACT_ABORT ON" in sql
    assert "ON test.measurement(test_item_id,unit_id)" in sql
    assert "INCLUDE(value_numeric,measurement_status)" in sql
    assert "ON test.unit_result(run_id,wafer_id,unit_id)" in sql
    assert (
        "INCLUDE(unit_sequence,x_coord,y_coord,overall_result,soft_bin,hard_bin)" in sql
    )
    assert (
        "ON test.unit_bin_evaluation(unit_id,mapping_status,raw_bin_code,bin_type)"
        in sql
    )
    assert "bin_mapping_set_id,bin_definition_id,is_pass_snapshot" in sql
    assert "failure_mode_snapshot,processing_run_id,evaluated_at_utc" in sql
    assert "ONLINE" not in sql.upper()
    assert "DROP INDEX" not in sql.upper()


def test_upgrade_index_existence_checks_are_schema_table_and_name_scoped() -> None:
    sql = _read("db/alembic/sql/0023_analytics_performance_indexes_sql2014.sql")

    for table_name, index_name in (
        ("measurement", "IX_measurement_analytics_item_unit"),
        ("unit_result", "IX_unit_result_analytics_run_wafer_unit"),
        (
            "unit_bin_evaluation",
            "IX_unit_bin_evaluation_analytics_snapshot",
        ),
    ):
        assert sql.count("s.name=N'test'") >= 3
        assert f"o.name=N'{table_name}'" in sql
        assert f"i.name=N'{index_name}'" in sql


def test_downgrade_only_drops_indexes_owned_by_0023() -> None:
    sql = _read("db/alembic/sql/0023_analytics_performance_indexes_down_sql2014.sql")

    assert sql.upper().count("DROP INDEX") == 3
    assert "DROP TABLE" not in sql.upper()
    assert "DROP COLUMN" not in sql.upper()
    assert "DELETE " not in sql.upper()
    for table_name, index_name in (
        ("measurement", "IX_measurement_analytics_item_unit"),
        ("unit_result", "IX_unit_result_analytics_run_wafer_unit"),
        (
            "unit_bin_evaluation",
            "IX_unit_bin_evaluation_analytics_snapshot",
        ),
    ):
        assert f"o.name=N'{table_name}'" in sql
        assert f"i.name=N'{index_name}'" in sql


def test_wrapper_uses_reversible_up_and_down_sql_files() -> None:
    wrapper = _read("db/alembic/versions/sql2014_0023_analytics_performance_indexes.py")

    assert 'revision = "sql2014_0023"' in wrapper
    assert 'down_revision = "sql2014_0022"' in wrapper
    assert 'run_sql_file("0023_analytics_performance_indexes_sql2014.sql")' in wrapper
    assert (
        'run_sql_file("0023_analytics_performance_indexes_down_sql2014.sql")' in wrapper
    )
    assert "irreversible_downgrade" not in wrapper
