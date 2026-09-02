from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

BUSINESS_PERMISSIONS = frozenset(
    {
        "TASK_CREATE",
        "TASK_RETRY",
        "DATASET_READ",
        "ANALYSIS_RUN",
        "EXPORT_DATA",
        "MANAGEMENT_READ",
    }
)

ALL_PERMISSIONS = frozenset(
    {
        "TASK_CREATE",
        "TASK_RETRY",
        "DATASET_READ",
        "DATASET_PUBLISH",
        "ANALYSIS_RUN",
        "EXPORT_DATA",
        "FORMAT_GOVERN",
        "RULE_GOVERN",
        "DQ_WAIVE_ERROR",
        "AUDIT_READ",
        "MANAGEMENT_READ",
        "USER_ADMIN",
        "DATA_DOMAIN_ADMIN",
        "SOURCE_ADMIN",
        "SYSTEM_OPERATE",
        "DATA_BREAK_GLASS",
    }
)

# Development-first data access: these operational administrator roles may
# inspect and operate every PERSONAL and DOMAIN data object.  Ordinary CP/FT
# users still use owner or data-domain grants.
GLOBAL_DATA_ACCESS_ROLES = frozenset({"SYSTEM_ADMIN", "DATA_DOMAIN_ADMIN"})


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    login_name: str
    display_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    department_code: str | None = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def has_global_data_access(principal: Principal) -> bool:
    return bool(GLOBAL_DATA_ACCESS_ROLES.intersection(principal.roles)) or principal.can(
        "DATA_BREAK_GLASS"
    )


DEVELOPMENT_PRINCIPAL = Principal(
    user_id=1,
    login_name="development-admin",
    display_name="开发管理员",
    roles=("SYSTEM_ADMIN",),
    # DATA_BREAK_GLASS stays reserved for the later security-hardening phase;
    # SYSTEM_ADMIN already has explicit global data access in development.
    permissions=ALL_PERMISSIONS - {"DATA_BREAK_GLASS"},
)


class RegisterRequest(BaseModel):
    login_name: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.@-]+$")
    display_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    department_code: str | None = Field(default=None, max_length=128)

    @field_validator("login_name")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    login_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class UserAdminUpdateRequest(BaseModel):
    status: str = Field(pattern=r"^(PENDING|ACTIVE|LOCKED|DISABLED)$")
    role_codes: list[str] = Field(default_factory=list, max_length=20)
    department_code: str | None = Field(default=None, max_length=128)


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: int
    login_name: str
    display_name: str
    email: str | None
    department_code: str | None
    status: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    created_at_utc: str
    last_login_at_utc: str | None


class AuthService(Protocol):
    def register(self, request: RegisterRequest, password_hash: str) -> UserRecord: ...

    def password_hash_for_login(self, login_name: str) -> tuple[int, str, str]: ...

    def record_login(self, login_name: str, user_id: int | None, outcome: str, **metadata) -> None: ...

    def principal_for_user(self, user_id: int) -> Principal: ...

    def principal_for_development(self) -> Principal: ...

    def create_session(self, user_id: int, token_jti: str, expires_at_utc, **metadata) -> None: ...

    def principal_for_session(self, token_jti: str) -> Principal: ...

    def revoke_session(self, token_jti: str) -> None: ...

    def list_users(self) -> tuple[UserRecord, ...]: ...

    def list_roles(self) -> tuple[dict[str, str], ...]: ...

    def update_user(self, user_id: int, request: UserAdminUpdateRequest, actor_id: int) -> UserRecord: ...
