from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class ProductCrosswalk:
    crosswalk_id: int
    supplier_id: int
    supplier_code: str
    supplier_name: str
    test_stage: str
    raw_product_code: str
    product_id: int
    tms_product_code: str
    identity_class: str
    enterprise_system: str
    enterprise_key: str | None
    status: str
    first_observed_at_utc: str
    last_observed_at_utc: str
    approved_by_login: str | None
    approved_at_utc: str | None
    decision_reason: str | None


@dataclass(frozen=True, slots=True)
class ProductCrosswalkPage:
    items: tuple[ProductCrosswalk, ...]
    total: int
    page: int
    page_size: int


class ApproveProductCrosswalkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enterprise_system: str = Field(default="SAP_B1", pattern=r"^SAP_B1$")
    enterprise_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=4, max_length=1000)

    @field_validator("enterprise_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("enterprise_key contains control characters")
        return value


class RejectProductCrosswalkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=4, max_length=1000)


class MasterDataService(Protocol):
    def list_product_crosswalks(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        supplier_code: str | None = None,
        test_stage: str | None = None,
        raw_product_code: str | None = None,
    ) -> ProductCrosswalkPage: ...

    def approve_product_crosswalk(
        self,
        crosswalk_id: int,
        request: ApproveProductCrosswalkRequest,
        principal: Principal,
    ) -> ProductCrosswalk: ...

    def reject_product_crosswalk(
        self,
        crosswalk_id: int,
        request: RejectProductCrosswalkRequest,
        principal: Principal,
    ) -> ProductCrosswalk: ...
