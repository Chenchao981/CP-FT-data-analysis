from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_analytics_governance_migration_is_forward_only_and_keeps_one_fact_chain() -> (
    None
):
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0020_analytics_governance_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    wrapper = (
        ROOT / "db" / "alembic" / "versions" / "sql2014_0020_analytics_governance.py"
    ).read_text(encoding="utf-8-sig")

    assert "SET XACT_ABORT ON" in sql
    assert 'revision = "sql2014_0020"' in wrapper
    assert 'down_revision = "sql2014_0019"' in wrapper
    assert 'run_sql_file("0020_analytics_governance_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper
    assert "CREATE TABLE test." not in sql
    assert "ALTER TABLE test.measurement" not in sql
    assert "UPDATE test." not in sql
    assert "DELETE FROM" not in sql
    assert "CREATE OR ALTER" not in sql


def test_rule_governance_requires_three_approval_roles_and_disabled_default() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0020_analytics_governance_sql2014.sql"
    ).read_text(encoding="utf-8-sig")

    assert "evaluation.rule_approval_record" in sql
    assert "'BUSINESS','TECHNICAL','QUALITY'" in sql
    assert "DEFAULT('DISABLED') WITH VALUES" in sql
    assert "evaluation.rule_activation" in sql
    for rule_type in (
        "HISTOGRAM",
        "BOX_PLOT",
        "NORMAL_FIT",
        "CORRELATION",
        "MARGIN",
        "ZONE",
        "BIN_COOCCURRENCE",
    ):
        assert f"'{rule_type}'" in sql


def test_saved_export_and_evaluation_context_support_one_to_eight_versions() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0020_analytics_governance_sql2014.sql"
    ).read_text(encoding="utf-8-sig")

    assert "analysis.saved_analysis_revision" in sql
    assert "analysis.saved_analysis_revision_dataset" in sql
    assert "evaluation.evaluation_run_dataset" in sql
    assert "delivery.export_job_dataset" in sql
    assert "ordinal_no BETWEEN 1 AND 8" in sql
    assert "filter_hash char(64)" in sql
    assert "context_hash char(64)" in sql
    assert "rule_context_json" in sql
    assert "UX_export_job_idempotency" in sql
