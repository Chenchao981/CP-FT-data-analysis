from __future__ import annotations

from app.core.security import decode_access_token, hash_password, issue_access_token, verify_password


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
