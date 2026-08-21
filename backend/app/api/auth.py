from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import (
    auth_service,
    current_principal,
    oauth2_scheme,
    require_permission,
)
from app.core.security import (
    decode_access_token,
    hash_password,
    issue_access_token,
    verify_password,
)
from app.domain.auth import (
    AuthService,
    LoginRequest,
    Principal,
    RegisterRequest,
    UserAdminUpdateRequest,
)


router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, service: AuthService = Depends(auth_service)) -> dict:
    return asdict(service.register(payload, hash_password(payload.password)))


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthService = Depends(auth_service),
) -> dict:
    user_id, encoded, normalized_login = service.password_hash_for_login(payload.login_name)
    metadata = _client_metadata(request)
    if not verify_password(payload.password, encoded):
        service.record_login(normalized_login, user_id, "BAD_PASSWORD", **metadata)
        from app.core.errors import DomainError

        raise DomainError("LOGIN_FAILED", "用户名或密码不正确", 401)
    token, jti, expires = issue_access_token(user_id)
    service.create_session(user_id, jti, expires, **metadata)
    service.record_login(normalized_login, user_id, "SUCCESS", **metadata)
    principal = service.principal_for_user(user_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at_utc": expires.isoformat(),
        "user": _principal(principal),
    }


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return _principal(principal)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    token: str | None = Depends(oauth2_scheme),
    service: AuthService = Depends(auth_service),
) -> None:
    if token:
        _user_id, jti = decode_access_token(token)
        service.revoke_session(jti)


@router.get("/users")
def users(
    _principal: Principal = Depends(require_permission("USER_ADMIN")),
    service: AuthService = Depends(auth_service),
) -> list[dict]:
    return [asdict(item) for item in service.list_users()]


@router.get("/roles")
def roles(
    _principal: Principal = Depends(require_permission("USER_ADMIN")),
    service: AuthService = Depends(auth_service),
) -> list[dict[str, str]]:
    return list(service.list_roles())


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserAdminUpdateRequest,
    principal: Principal = Depends(require_permission("USER_ADMIN")),
    service: AuthService = Depends(auth_service),
) -> dict:
    return asdict(service.update_user(user_id, payload, principal.user_id))


def _principal(principal: Principal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "login_name": principal.login_name,
        "display_name": principal.display_name,
        "department_code": principal.department_code,
        "roles": list(principal.roles),
        "permissions": sorted(principal.permissions),
    }


def _client_metadata(request: Request) -> dict[str, str | None]:
    return {
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
