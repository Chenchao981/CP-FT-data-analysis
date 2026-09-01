from __future__ import annotations

from contextlib import contextmanager

import pytest
from app.core.errors import DomainError
from app.domain.auth import BUSINESS_PERMISSIONS, Principal
from app.infrastructure.sql_auth_service import SqlAuthService


def _principal(user_id: int) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name=f"User {user_id}",
        roles=("SYSTEM_ADMIN",),
        permissions=frozenset({"USER_ADMIN"}),
    )


class _ScalarResult:
    def __init__(self, value: int | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> int | None:
        return self._value


class _DevelopmentPrincipalEngine:
    def __init__(self, selected_user_id: int | None) -> None:
        self._selected_user_id = selected_user_id
        self.statements: list[str] = []

    @contextmanager
    def connect(self):
        engine = self

        class Connection:
            def execute(self, statement):
                engine.statements.append(str(statement))
                return _ScalarResult(engine._selected_user_id)

        yield Connection()


class _PrincipalRowsResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _ActiveBusinessUserEngine:
    @contextmanager
    def connect(self):
        class Connection:
            def execute(self, statement, _parameters=None):
                sql = str(statement)
                if "FROM iam.app_user WHERE user_id" in sql:
                    return _PrincipalRowsResult(
                        [
                            {
                                "user_id": 31,
                                "login_name": "business.user",
                                "display_name": "Business User",
                                "department_code": None,
                                "status": "ACTIVE",
                            }
                        ]
                    )
                return _PrincipalRowsResult([])

        yield Connection()


@pytest.mark.parametrize("configured_value", ["not-an-integer", "0", "-3"])
def test_development_principal_rejects_invalid_configured_user_id(
    monkeypatch, configured_value: str
) -> None:
    monkeypatch.setenv("TMS_DEVELOPMENT_USER_ID", configured_value)
    service = SqlAuthService(_DevelopmentPrincipalEngine(None))

    with pytest.raises(DomainError) as exc_info:
        service.principal_for_development()

    assert exc_info.value.code == "DEVELOPMENT_PRINCIPAL_INVALID"
    assert exc_info.value.status_code == 503


def test_development_principal_uses_explicit_active_user(monkeypatch) -> None:
    monkeypatch.setenv("TMS_DEVELOPMENT_USER_ID", " 27 ")
    engine = _DevelopmentPrincipalEngine(None)
    service = SqlAuthService(engine)
    expected = _principal(27)
    service.principal_for_user = lambda user_id: expected if user_id == 27 else None

    assert service.principal_for_development() == expected
    assert engine.statements == []


def test_development_principal_maps_inactive_override_to_configuration_error(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TMS_DEVELOPMENT_USER_ID", "27")
    service = SqlAuthService(_DevelopmentPrincipalEngine(None))

    def inactive_user(_user_id: int) -> Principal:
        raise DomainError("USER_NOT_ACTIVE", "inactive", 401)

    service.principal_for_user = inactive_user

    with pytest.raises(DomainError) as exc_info:
        service.principal_for_development()

    assert exc_info.value.code == "DEVELOPMENT_PRINCIPAL_INVALID"
    assert exc_info.value.status_code == 503


def test_development_principal_selects_first_active_system_admin(monkeypatch) -> None:
    monkeypatch.delenv("TMS_DEVELOPMENT_USER_ID", raising=False)
    engine = _DevelopmentPrincipalEngine(12)
    service = SqlAuthService(engine)
    expected = _principal(12)
    service.principal_for_user = lambda user_id: expected if user_id == 12 else None

    assert service.principal_for_development() == expected
    assert len(engine.statements) == 1
    statement = engine.statements[0]
    assert "TOP (1)" in statement
    assert "u.status='ACTIVE'" in statement
    assert "r.active=1" in statement
    assert "r.role_code='SYSTEM_ADMIN'" in statement
    assert "ORDER BY u.user_id" in statement


def test_development_principal_requires_an_active_system_admin(monkeypatch) -> None:
    monkeypatch.delenv("TMS_DEVELOPMENT_USER_ID", raising=False)
    service = SqlAuthService(_DevelopmentPrincipalEngine(None))

    with pytest.raises(DomainError) as exc_info:
        service.principal_for_development()

    assert exc_info.value.code == "DEVELOPMENT_PRINCIPAL_NOT_CONFIGURED"
    assert exc_info.value.status_code == 503


def test_active_user_receives_business_permissions_without_control_plane() -> None:
    principal = SqlAuthService(_ActiveBusinessUserEngine()).principal_for_user(31)

    assert principal.roles == ()
    assert principal.permissions == BUSINESS_PERMISSIONS
    assert principal.permissions.isdisjoint(
        {
            "USER_ADMIN",
            "DATA_DOMAIN_ADMIN",
            "SOURCE_ADMIN",
            "SYSTEM_OPERATE",
            "DATA_BREAK_GLASS",
        }
    )
