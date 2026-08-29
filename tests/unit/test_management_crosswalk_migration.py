from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sql() -> str:
    return (
        ROOT
        / "db"
        / "alembic"
        / "sql"
        / "0017_management_crosswalk_sql2014.sql"
    ).read_text(encoding="utf-8-sig")


def test_management_crosswalk_revision_extends_worker_operations() -> None:
    wrapper = (
        ROOT
        / "db"
        / "alembic"
        / "versions"
        / "sql2014_0017_management_crosswalk.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0017"' in wrapper
    assert 'down_revision = "sql2014_0016"' in wrapper
    assert 'run_sql_file("0017_management_crosswalk_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_source_products_are_not_implicitly_promoted_to_sap_materials() -> None:
    sql = _sql()

    assert "identity_class IN('SOURCE_OBSERVED','ENTERPRISE_MAPPED')" in sql
    assert "CREATE TABLE mdm.enterprise_product_crosswalk" in sql
    assert "status IN('PENDING','APPROVED','REJECTED','RETIRED')" in sql
    assert "status='APPROVED' AND enterprise_key IS NOT NULL" in sql
    assert "Existing formal facts become reviewable source observations" in sql
    assert "'PENDING'" in sql
    assert "INSERT mdm.product" not in sql
    assert "OPENJSON" not in sql
    assert "JSON_VALUE" not in sql


def test_management_permission_and_global_read_scope_are_explicit() -> None:
    sql = _sql()

    assert "permission_code='MANAGEMENT_READ'" in sql
    assert "permission_name" not in sql
    assert "'SYSTEM_ADMIN','DATA_ADMIN','QUALITY_ENGINEER','MANAGER_VIEWER'" in sql
    assert "scope_key=N'TMS_CURRENT_DATA'" in sql
    assert "'MANAGER_VIEWER','QUALITY_ENGINEER'" in sql
