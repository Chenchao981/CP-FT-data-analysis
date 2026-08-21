from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings
from app.core.errors import DomainError


password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def issue_access_token(user_id: int) -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=get_settings().access_token_minutes)
    jti = str(uuid4())
    token = jwt.encode(
        {"sub": str(user_id), "jti": jti, "iat": now, "exp": expires, "aud": "tms"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    return token, jti, expires


def decode_access_token(token: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            audience="tms",
        )
        return int(payload["sub"]), str(payload["jti"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise DomainError("AUTH_TOKEN_INVALID", "登录状态已失效，请重新登录", 401) from exc
