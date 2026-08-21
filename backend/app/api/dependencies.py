from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.security import decode_access_token
from app.domain.auth import AuthService, DEVELOPMENT_PRINCIPAL, Principal


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def auth_service(request: Request) -> AuthService:
    instance = getattr(request.app.state, "auth_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "用户服务尚未连接数据库", 503)
    return instance


def current_principal(
    request: Request, token: str | None = Depends(oauth2_scheme)
) -> Principal:
    if not get_settings().auth_required:
        return DEVELOPMENT_PRINCIPAL
    if not token:
        raise DomainError("AUTH_REQUIRED", "请先登录", 401)
    user_id, jti = decode_access_token(token)
    principal = auth_service(request).principal_for_session(jti)
    if principal.user_id != user_id:
        raise DomainError("AUTH_TOKEN_INVALID", "登录状态与用户不匹配", 401)
    return principal


def require_permission(permission: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise DomainError("PERMISSION_DENIED", f"缺少权限：{permission}", 403)
        return principal

    return dependency
