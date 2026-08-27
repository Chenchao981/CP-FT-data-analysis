from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_lot_id(value: str) -> str:
    normalized = normalize("NFKC", value).strip().upper()
    if not normalized:
        return normalized
    if not normalized[0].isalnum() or not normalized[-1].isalnum():
        raise ValueError("lot_id must start and end with a letter or number")
    allowed_separators = frozenset("-._/+")
    if any(
        not character.isalnum() and character not in allowed_separators
        for character in normalized
    ):
        raise ValueError("lot_id contains unsupported characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ProcessingInputRequestFile:
    input_request_id: int
    source_file_id: int
    original_file_name: str
    current_value: None = None


@dataclass(frozen=True, slots=True)
class ProcessingInputRequestSummary:
    import_batch_id: int
    status: str
    field_code: str
    prompt: str
    latest_job_id: int | None
    requests: tuple[ProcessingInputRequestFile, ...]


class LotResolutionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_request_id: int = Field(gt=0)
    lot_id: str = Field(min_length=1, max_length=128)

    @field_validator("lot_id", mode="before")
    @classmethod
    def normalize_and_validate_lot_id(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_lot_id(value)


class ResolveLotInputRequests(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolutions: list[LotResolutionItem] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_unique_requests(self) -> ResolveLotInputRequests:
        request_ids = [item.input_request_id for item in self.resolutions]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("input_request_id values must be unique")
        return self


@dataclass(frozen=True, slots=True)
class LotResolutionResult:
    import_batch_id: int
    job_id: int
    status: str


class ProcessingInputRequestService(Protocol):
    def list_open(
        self,
        principal,
        business_domain: str,
        test_stage: str,
        import_batch_id: int,
    ) -> ProcessingInputRequestSummary: ...

    def resolve(
        self,
        principal,
        business_domain: str,
        test_stage: str,
        import_batch_id: int,
        request: ResolveLotInputRequests,
    ) -> LotResolutionResult: ...
