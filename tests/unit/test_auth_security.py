from __future__ import annotations

import pytest
from app.core.security import (
    decode_access_token,
    hash_password,
    issue_access_token,
    verify_password,
)
from fastapi.testclient import TestClient


def test_authentication_is_required_by_default(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("TMS_AUTH_REQUIRED", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().auth_required is True
    finally:
        get_settings.cache_clear()


def test_invalid_authentication_switch_is_rejected(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("TMS_AUTH_REQUIRED", "flase")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TMS_AUTH_REQUIRED must be one of"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_password_hash_and_jwt_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("TMS_JWT_SECRET", "unit-test-secret-with-enough-entropy")
    from app.core.config import get_settings

    get_settings.cache_clear()
    encoded = hash_password("Example123!")
    assert encoded != "Example123!"
    assert verify_password("Example123!", encoded)
    assert not verify_password("wrong", encoded)
    token, jti, _expires = issue_access_token(42)
    assert decode_access_token(token) == (42, jti)
    get_settings.cache_clear()


def test_principal_permission_contract() -> None:
    from app.domain.auth import Principal

    principal = Principal(1, "cp.user", "CP工程师", ("CP_ENGINEER",), frozenset({"DATASET_READ"}))
    assert principal.can("DATASET_READ")
    assert not principal.can("USER_ADMIN")


def test_development_principal_does_not_receive_break_glass() -> None:
    from app.domain.auth import DEVELOPMENT_PRINCIPAL

    assert "DATA_BREAK_GLASS" not in DEVELOPMENT_PRINCIPAL.permissions


def test_auth_disabled_uses_real_database_principal_when_service_exists(
    monkeypatch,
) -> None:
    from app.core.config import get_settings
    from app.domain.auth import ALL_PERMISSIONS, Principal
    from app.main import create_app

    expected = Principal(
        27,
        "acceptance.admin",
        "验收管理员",
        ("SYSTEM_ADMIN",),
        ALL_PERMISSIONS,
    )

    class StubAuthService:
        def principal_for_development(self):
            return expected

    monkeypatch.setenv("TMS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("TMS_ENV", "development")
    get_settings.cache_clear()
    try:
        app = create_app()
        app.state.auth_service = StubAuthService()

        response = TestClient(app).get("/api/v1/auth/me")

        assert response.status_code == 200
        assert response.json()["user_id"] == 27
        assert response.json()["login_name"] == "acceptance.admin"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "environment",
    ["local", "dev", "testing", "ci", ""],
)
def test_auth_cannot_be_disabled_outside_development_or_test(
    monkeypatch, environment: str
) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    class StubAuthService:
        def principal_for_development(self):
            raise AssertionError(
                "non-development environments must not resolve a development principal"
            )

    monkeypatch.setenv("TMS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("TMS_ENV", environment)
    get_settings.cache_clear()
    try:
        app = create_app()
        app.state.auth_service = StubAuthService()

        response = TestClient(app).get("/api/v1/auth/me")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTH_CONFIGURATION_INVALID"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_protected_environments_fail_at_startup_with_unsafe_authentication(
    monkeypatch, environment: str
) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("TMS_ENV", environment)
    monkeypatch.setenv("TMS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("TMS_JOB_REPOSITORY", "sql")
    monkeypatch.setenv("TMS_JWT_SECRET", "x" * 40)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="TMS_AUTH_REQUIRED must be true"):
            create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("secret", "repository", "expected"),
    [
        ("tms-local-development-only", "sql", "TMS_JWT_SECRET"),
        ("short", "sql", "TMS_JWT_SECRET"),
        ("x" * 40, "memory", "TMS_JOB_REPOSITORY"),
    ],
)
def test_production_rejects_weak_secret_or_memory_queue(
    monkeypatch, secret: str, repository: str, expected: str
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("TMS_ENV", "production")
    monkeypatch.setenv("TMS_AUTH_REQUIRED", "true")
    monkeypatch.setenv("TMS_JOB_REPOSITORY", repository)
    monkeypatch.setenv("TMS_JWT_SECRET", secret)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match=expected):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_explicit_development_mode_without_database_uses_static_principal(
    monkeypatch,
) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("TMS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("TMS_ENV", "development")
    monkeypatch.delenv("TMS_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        app = create_app()

        response = TestClient(app).get("/api/v1/auth/me")

        assert response.status_code == 200
        assert response.json()["user_id"] == 1
        assert response.json()["login_name"] == "development-admin"
    finally:
        get_settings.cache_clear()
