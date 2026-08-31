from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.analysis_rule_pinning import (
    ANALYSIS_VIEW_STATE_CONTRACT_VERSION,
    required_rules_from_analysis_view_state,
)
from app.domain.analytics import AnalyticsContextRequest

SAVED_ANALYSIS_CONTRACT_VERSION = "SAVED_ANALYSIS_V1"
MAX_RULE_CONTEXT_ITEMS = 100
MAX_CONFIG_TOP_LEVEL_FIELDS = 100
MAX_CONFIG_DEPTH = 8
MAX_CONFIG_ARRAY_ITEMS = 1_000
MAX_CONFIG_TOTAL_NODES = 4_000
MAX_CONFIG_KEY_CHARS = 128
MAX_CONFIG_STRING_CHARS = 4_000
MAX_CONFIG_JSON_BYTES = 256 * 1024
MAX_REVISION_JSON_BYTES = 512 * 1024


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("saved analysis state must contain JSON values only") from exc


def _validate_json_value(
    value: Any,
    *,
    field_name: str,
    depth: int = 0,
    node_count: list[int] | None = None,
) -> None:
    if node_count is None:
        node_count = [0]
    node_count[0] += 1
    if node_count[0] > MAX_CONFIG_TOTAL_NODES:
        raise ValueError(f"{field_name} exceeds the JSON node limit")
    if depth > MAX_CONFIG_DEPTH:
        raise ValueError(f"{field_name} exceeds the JSON depth limit")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_CONFIG_STRING_CHARS:
            raise ValueError(f"{field_name} contains an oversized string")
        return
    if isinstance(value, list):
        if len(value) > MAX_CONFIG_ARRAY_ITEMS:
            raise ValueError(f"{field_name} contains an oversized array")
        for item in value:
            _validate_json_value(
                item,
                field_name=field_name,
                depth=depth + 1,
                node_count=node_count,
            )
        return
    if isinstance(value, dict):
        if len(value) > MAX_CONFIG_TOP_LEVEL_FIELDS:
            raise ValueError(f"{field_name} contains too many object fields")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_CONFIG_KEY_CHARS:
                raise ValueError(f"{field_name} contains an invalid object key")
            _validate_json_value(
                item,
                field_name=field_name,
                depth=depth + 1,
                node_count=node_count,
            )
        return
    raise ValueError(f"{field_name} contains a non-JSON value")


def validate_analysis_presentation_config(
    chart_config: dict[str, Any], display_config: dict[str, Any]
) -> str:
    """Validate and canonically hash a bounded chart/display snapshot."""

    _validate_json_value(chart_config, field_name="chart_config")
    _validate_json_value(display_config, field_name="display_config")
    config_json = canonical_json(
        {
            "chart_config": chart_config,
            "display_config": display_config,
        }
    )
    if len(config_json.encode("utf-8")) > MAX_CONFIG_JSON_BYTES:
        raise ValueError("chart/display configuration exceeds the JSON size limit")
    return hashlib.sha256(config_json.encode("utf-8")).hexdigest()


class StrictSavedAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SavedAnalysisRuleContext(StrictSavedAnalysisRequest):
    spec_versions: list[str] = Field(
        default_factory=list, max_length=MAX_RULE_CONTEXT_ITEMS
    )
    bin_mapping_versions: list[str] = Field(
        default_factory=list, max_length=MAX_RULE_CONTEXT_ITEMS
    )
    evaluation_rule_versions: list[str] = Field(
        default_factory=list, max_length=MAX_RULE_CONTEXT_ITEMS
    )

    @field_validator(
        "spec_versions", "bin_mapping_versions", "evaluation_rule_versions"
    )
    @classmethod
    def versions_are_unique_and_bounded(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("rule-context versions must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("rule-context versions must be unique")
        return sorted(normalized)


class SavedAnalysisState(AnalyticsContextRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: str = Field(
        default=SAVED_ANALYSIS_CONTRACT_VERSION,
        pattern=r"^[A-Z][A-Z0-9_.-]{2,63}$",
    )
    rule_context: SavedAnalysisRuleContext
    chart_config: dict[str, Any] = Field(
        default_factory=dict, max_length=MAX_CONFIG_TOP_LEVEL_FIELDS
    )
    display_config: dict[str, Any] = Field(
        default_factory=dict, max_length=MAX_CONFIG_TOP_LEVEL_FIELDS
    )

    @model_validator(mode="after")
    def json_state_is_bounded(self) -> SavedAnalysisState:
        validate_analysis_presentation_config(self.chart_config, self.display_config)
        required_rules_from_analysis_view_state(
            self.chart_config, tuple(self.parameters)
        )
        revision_json = canonical_json(
            {
                "contract_version": self.contract_version,
                "datasets": [item.model_dump(mode="json") for item in self.datasets],
                "filters": self.filters.model_dump(mode="json"),
                "parameters": self.parameters,
                "rule_context": self.rule_context.model_dump(mode="json"),
                "chart_config": self.chart_config,
                "display_config": self.display_config,
            }
        )
        if len(revision_json.encode("utf-8")) > MAX_REVISION_JSON_BYTES:
            raise ValueError("saved analysis revision exceeds the JSON size limit")
        return self


def _validate_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError("analysis name must be non-empty printable text")
    return normalized


def _normalize_row_version(value: str) -> str:
    return value.upper()


class _CurrentSavedAnalysisWriteState(SavedAnalysisState):
    @model_validator(mode="after")
    def write_contract_is_complete_and_current(
        self,
    ) -> _CurrentSavedAnalysisWriteState:
        if self.contract_version != SAVED_ANALYSIS_CONTRACT_VERSION:
            raise ValueError(
                f"contract_version must be {SAVED_ANALYSIS_CONTRACT_VERSION}"
            )
        analysis_view_state = self.chart_config.get("analysis_view_state")
        if not isinstance(analysis_view_state, dict):
            raise ValueError(  # noqa: TRY004 - Pydantic validator contract
                "chart_config.analysis_view_state must be an object"
            )
        if (
            analysis_view_state.get("contract_version")
            != ANALYSIS_VIEW_STATE_CONTRACT_VERSION
        ):
            raise ValueError(
                "chart_config.analysis_view_state.contract_version must be "
                f"{ANALYSIS_VIEW_STATE_CONTRACT_VERSION}"
            )
        return self


class CreateSavedAnalysisRequest(_CurrentSavedAnalysisWriteState):
    analysis_name: str = Field(min_length=1, max_length=300)
    change_reason: str = Field(min_length=8, max_length=1000)

    @field_validator("analysis_name")
    @classmethod
    def name_is_printable(cls, value: str) -> str:
        return _validate_name(value)


class CreateSavedAnalysisRevisionRequest(_CurrentSavedAnalysisWriteState):
    expected_row_version: str = Field(pattern=r"^[0-9A-Fa-f]{16}$")
    analysis_name: str | None = Field(default=None, min_length=1, max_length=300)
    change_reason: str = Field(min_length=8, max_length=1000)

    @field_validator("expected_row_version")
    @classmethod
    def row_version_is_canonical(cls, value: str) -> str:
        return _normalize_row_version(value)

    @field_validator("analysis_name")
    @classmethod
    def optional_name_is_printable(cls, value: str | None) -> str | None:
        return _validate_name(value) if value is not None else None


class DeleteSavedAnalysisRequest(StrictSavedAnalysisRequest):
    expected_row_version: str = Field(pattern=r"^[0-9A-Fa-f]{16}$")
    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("expected_row_version")
    @classmethod
    def row_version_is_canonical(cls, value: str) -> str:
        return _normalize_row_version(value)


@dataclass(frozen=True, slots=True)
class SavedAnalysisHashes:
    filter_hash: str
    context_hash: str
    normalized_filters: dict[str, list[str]]
    normalized_parameters: tuple[str, ...]


def saved_analysis_hashes(state: AnalyticsContextRequest) -> SavedAnalysisHashes:
    raw_filters = state.filters.model_dump(mode="json")
    normalized_filters = {
        key: sorted(str(item) for item in value) for key, value in raw_filters.items()
    }
    filter_hash = hashlib.sha256(
        canonical_json(normalized_filters).encode("utf-8")
    ).hexdigest()
    normalized_parameters = tuple(sorted(state.parameters))
    context_hash = hashlib.sha256(
        json.dumps(
            {
                "datasets": sorted(
                    (item.dataset_id, item.version_no) for item in state.datasets
                ),
                "filter_hash": filter_hash,
                "parameters": normalized_parameters,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return SavedAnalysisHashes(
        filter_hash=filter_hash,
        context_hash=context_hash,
        normalized_filters=normalized_filters,
        normalized_parameters=normalized_parameters,
    )


class SavedAnalysisRestoreStatus(StrEnum):
    CURRENT = "CURRENT"
    NON_CURRENT = "NON_CURRENT"
    RULE_CHANGED = "RULE_CHANGED"
    ACCESS_REVOKED = "ACCESS_REVOKED"


class SavedAnalysisDatasetStatus(StrEnum):
    CURRENT = "CURRENT"
    NON_CURRENT = "NON_CURRENT"
    ACCESS_REVOKED = "ACCESS_REVOKED"


@dataclass(frozen=True, slots=True)
class SavedAnalysisDatasetRecord:
    dataset_version_id: int
    dataset_id: int
    version_no: int
    ordinal_no: int
    test_stage: str
    status: SavedAnalysisDatasetStatus


@dataclass(frozen=True, slots=True)
class SavedAnalysisRevisionRecord:
    saved_analysis_revision_id: int
    revision_no: int
    contract_version: str
    filters: dict[str, list[str]]
    parameters: tuple[str, ...]
    filter_hash: str
    context_hash: str
    rule_context: SavedAnalysisRuleContext
    chart_config: dict[str, Any]
    display_config: dict[str, Any]
    datasets: tuple[SavedAnalysisDatasetRecord, ...]
    created_by_user_id: int
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class SavedAnalysisRecord:
    saved_analysis_id: int
    analysis_name: str
    owner_user_id: int
    lifecycle_status: str
    current_revision_no: int
    row_version: str
    restore_status: SavedAnalysisRestoreStatus
    revision: SavedAnalysisRevisionRecord
    created_at_utc: str
    updated_at_utc: str


@dataclass(frozen=True, slots=True)
class SavedAnalysisPage:
    items: tuple[SavedAnalysisRecord, ...]
    total: int
    page: int
    page_size: int


class SavedAnalysisService(Protocol):
    def create(
        self, request: CreateSavedAnalysisRequest, principal
    ) -> SavedAnalysisRecord: ...

    def list_page(
        self, principal, *, page: int, page_size: int, include_deleted: bool = False
    ) -> SavedAnalysisPage: ...

    def get(
        self, saved_analysis_id: int, principal, *, revision_no: int | None = None
    ) -> SavedAnalysisRecord: ...

    def create_revision(
        self,
        saved_analysis_id: int,
        request: CreateSavedAnalysisRevisionRequest,
        principal,
    ) -> SavedAnalysisRecord: ...

    def delete(
        self,
        saved_analysis_id: int,
        request: DeleteSavedAnalysisRequest,
        principal,
    ) -> SavedAnalysisRecord: ...
