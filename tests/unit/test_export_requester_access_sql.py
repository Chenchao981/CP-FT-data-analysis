from __future__ import annotations

import sqlite3

import pytest

from app.infrastructure.sql_analytics_export_worker import _requester_dataset_access_sql


@pytest.mark.parametrize("lock_grants", [False, True])
@pytest.mark.parametrize("scope,owner,user_status,role,grant_status,expiry,current,version_status,expected", [
    ("PERSONAL", 1, "ACTIVE", None, None, None, 1, "PUBLISHED", 1),
    ("PERSONAL", 2, "ACTIVE", None, None, None, 1, "PUBLISHED", 0),
    ("PERSONAL", 1, "DISABLED", None, None, None, 1, "PUBLISHED", 0),
    ("PERSONAL", 2, "ACTIVE", "SYSTEM_ADMIN", None, None, 1, "PUBLISHED", 1),
    ("PERSONAL", 2, "DISABLED", "SYSTEM_ADMIN", None, None, 1, "PUBLISHED", 0),
    ("PERSONAL", 2, "ACTIVE", "DATA_DOMAIN_ADMIN", None, None, 1, "PUBLISHED", 1),
    ("DOMAIN", 2, "ACTIVE", None, "ACTIVE", None, 1, "PUBLISHED", 1),
    ("DOMAIN", 2, "ACTIVE", None, "ACTIVE", "2020-01-01", 1, "PUBLISHED", 0),
    ("DOMAIN", 2, "ACTIVE", None, "REVOKED", None, 1, "PUBLISHED", 0),
    ("DOMAIN", 2, "ACTIVE", None, "ACTIVE", None, 0, "PUBLISHED", 0),
    ("DOMAIN", 2, "ACTIVE", None, "ACTIVE", None, 1, "DRAFT", 0),
    ("DOMAIN", 2, "ACTIVE", None, None, None, 1, "PUBLISHED", 0),
])
def test_requester_scope_executes_and_preserves_authorization(
    lock_grants, scope, owner, user_status, role, grant_status, expiry, current, version_status, expected,
):
    # Execute the actual predicate rather than merely checking SQL substrings.
    # SQL Server's lock hints are the only syntax removed for this portable test;
    # the managed-Worker acceptance exercises the unmodified SQL on SQL Server.
    predicate = _requester_dataset_access_sql(
        requester_expression=":user_id", dataset_alias="d", version_alias="dv", lock_grants=lock_grants,
    ).replace(" WITH (UPDLOCK,HOLDLOCK)", "")
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS iam")
        db.create_function("SYSUTCDATETIME", 0, lambda: "2026-09-05")
        db.executescript("""
            CREATE TABLE iam.app_user(user_id,status);
            CREATE TABLE iam.user_role(user_id,role_id);
            CREATE TABLE iam.role(role_id,role_code);
            CREATE TABLE iam.data_domain(data_domain_id,active);
            CREATE TABLE iam.data_domain_grant(data_domain_id,user_id,status,expires_at_utc);
            INSERT INTO iam.data_domain VALUES(9,1);
        """)
        db.execute("INSERT INTO iam.app_user VALUES(1,?)", (user_status,))
        if role:
            db.execute("INSERT INTO iam.role VALUES(1,?)", (role,))
            db.execute("INSERT INTO iam.user_role VALUES(1,1)")
        if grant_status:
            db.execute("INSERT INTO iam.data_domain_grant VALUES(9,1,?,?)", (grant_status, expiry))
        result = db.execute(
            f"SELECT CASE WHEN {predicate} THEN 1 ELSE 0 END "
            "FROM (SELECT :scope AS access_scope,:owner AS owner_user_id,9 AS data_domain_id) d "
            "CROSS JOIN (SELECT :status AS status,:current AS is_current) dv",
            dict(user_id=1, scope=scope, owner=owner, status=version_status, current=current),
        ).fetchone()[0]
        assert result == expected
