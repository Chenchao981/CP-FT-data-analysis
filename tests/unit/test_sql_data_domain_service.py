from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from app.domain.auth import Principal
from app.infrastructure.sql_data_domain_service import SqlDataDomainService


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _Connection:
    def __init__(self, result_sets: list[list[dict[str, Any]]]) -> None:
        self._result_sets = result_sets
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement), dict(parameters or {})))
        return _Result(self._result_sets.pop(0))


class _Context(AbstractContextManager[_Connection]):
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def __enter__(self) -> _Connection:
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _Engine:
    def __init__(self, *result_sets: list[dict[str, Any]]) -> None:
        self.connection = _Connection(list(result_sets))

    def connect(self) -> _Context:
        return _Context(self.connection)


def _principal() -> Principal:
    return Principal(
        user_id=7,
        login_name="engineer",
        display_name="Engineer",
        roles=("BUSINESS_USER",),
        permissions=frozenset({"DATASET_READ"}),
    )


def _domain_row() -> dict[str, Any]:
    return {
        "data_domain_id": 11,
        "domain_code": "HUAHONG_CP",
        "domain_name": "华虹 CP",
        "test_stage": "CP",
        "factory_code": "HUAHONG",
        "active": True,
        "expires_at_utc": datetime(2026, 12, 31, tzinfo=UTC),
    }


def test_current_user_query_returns_only_active_unexpired_grants() -> None:
    engine = _Engine([_domain_row()])

    records = SqlDataDomainService(engine).list_for_principal(_principal())

    assert records[0].domain_code == "HUAHONG_CP"
    assert records[0].grant_expires_at_utc == "2026-12-31T00:00:00Z"
    assert records[0].grants == ()
    sql, params = engine.connection.statements[0]
    assert "g.status='ACTIVE'" in sql
    assert "d.active=1" in sql
    assert "g.expires_at_utc>SYSUTCDATETIME()" in sql
    assert params == {"user_id": 7}


def test_admin_query_nests_current_grants_and_hides_migration_hold() -> None:
    domain = _domain_row()
    domain.pop("expires_at_utc")
    grant = {
        "data_domain_id": 11,
        "user_id": 23,
        "login_name": "cp.user",
        "display_name": "CP User",
        "expires_at_utc": None,
        "granted_at_utc": datetime(2026, 9, 1, tzinfo=UTC),
        "reason": "approved source access",
    }
    engine = _Engine([domain], [grant])

    records = SqlDataDomainService(engine).list_admin()

    assert records[0].grants[0].login_name == "cp.user"
    domain_sql, _ = engine.connection.statements[0]
    grant_sql, _ = engine.connection.statements[1]
    assert "domain_code<>N'MIGRATION_HOLD'" in domain_sql
    assert "g.status='ACTIVE'" in grant_sql
    assert "g.expires_at_utc>SYSUTCDATETIME()" in grant_sql


def test_grantable_user_query_is_active_only_and_minimal() -> None:
    engine = _Engine(
        [
            {
                "user_id": 23,
                "login_name": "cp.user",
                "display_name": "CP User",
            }
        ]
    )

    users = SqlDataDomainService(engine).list_grantable_users()

    assert users[0].user_id == 23
    assert users[0].login_name == "cp.user"
    sql, params = engine.connection.statements[0]
    assert "WHERE status='ACTIVE'" in sql
    assert "password_hash" not in sql
    assert "email" not in sql
    assert params == {}
