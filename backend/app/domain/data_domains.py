from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class DataDomainGrantRecord:
    user_id: int
    login_name: str
    display_name: str
    expires_at_utc: str | None
    granted_at_utc: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class GrantableUserRecord:
    user_id: int
    login_name: str
    display_name: str


@dataclass(frozen=True, slots=True)
class DataDomainRecord:
    data_domain_id: int
    domain_code: str
    domain_name: str
    test_stage: str
    factory_code: str | None
    active: bool
    grant_expires_at_utc: str | None = None
    grants: tuple[DataDomainGrantRecord, ...] = ()


class CreateDataDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain_code: str = Field(min_length=2, max_length=128)
    domain_name: str = Field(min_length=2, max_length=200)
    test_stage: str = Field(pattern=r"^(CP|FT)$")
    factory_code: str | None = Field(default=None, max_length=64)
    active: bool = True

    @field_validator("domain_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.upper()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9_-]+", normalized) is None:
            raise ValueError("domain_code must use letters, numbers, underscore or dash")
        return normalized

    @field_validator("factory_code")
    @classmethod
    def normalize_factory(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized.upper() if normalized else None


class UpdateDataDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain_name: str = Field(min_length=2, max_length=200)
    factory_code: str | None = Field(default=None, max_length=64)
    active: bool

    @field_validator("factory_code")
    @classmethod
    def normalize_factory(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized.upper() if normalized else None


class CreateDataDomainGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: int = Field(gt=0)
    expires_at_utc: datetime | None = None
    reason: str = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def expiry_is_future(self) -> CreateDataDomainGrantRequest:
        if self.expires_at_utc is None:
            return self
        value = self.expires_at_utc
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if aware.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("expires_at_utc must be in the future")
        return self


class DataDomainService(Protocol):
    def list_for_principal(
        self, principal: Principal
    ) -> tuple[DataDomainRecord, ...]: ...

    def list_admin(self) -> tuple[DataDomainRecord, ...]: ...

    def list_grantable_users(self) -> tuple[GrantableUserRecord, ...]: ...

    def create(
        self, request: CreateDataDomainRequest, principal: Principal
    ) -> DataDomainRecord: ...

    def update(
        self,
        data_domain_id: int,
        request: UpdateDataDomainRequest,
        principal: Principal,
    ) -> DataDomainRecord: ...

    def grant(
        self,
        data_domain_id: int,
        request: CreateDataDomainGrantRequest,
        principal: Principal,
    ) -> DataDomainGrantRecord: ...

    def revoke(
        self, data_domain_id: int, user_id: int, principal: Principal
    ) -> None: ...
