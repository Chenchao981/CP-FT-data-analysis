from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field

from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsContextRequest,
    AnalyticsDatasetContext,
    AnalyticsFilterSummary,
    AnalyticsRuleContext,
)


class WaferSummarySort(StrEnum):
    DATASET = "DATASET"
    LOT = "LOT"
    WAFER = "WAFER"
    UNIT_COUNT = "UNIT_COUNT"
    YIELD = "YIELD"


class WaferSummarySortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class WaferSummaryRequest(AnalyticsContextRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort_by: WaferSummarySort = WaferSummarySort.DATASET
    sort_direction: WaferSummarySortDirection = WaferSummarySortDirection.ASC


@dataclass(frozen=True, slots=True)
class WaferParameterSummary:
    parameter: str
    unit: str | None
    measured_count: int
    missing_count: int
    out_of_spec_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None


@dataclass(frozen=True, slots=True)
class WaferSummaryDrilldownContext:
    dataset_id: int
    version_no: int
    lot_id: str
    wafer_id: str


@dataclass(frozen=True, slots=True)
class WaferSummaryRow:
    dataset_id: int
    version_no: int
    lot_id: str
    wafer_id: str
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    known_yield_denominator: int
    yield_rate: float | None
    parameters: tuple[WaferParameterSummary, ...]
    drilldown_context: WaferSummaryDrilldownContext | None = None


@dataclass(frozen=True, slots=True)
class WaferSummaryResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    page: int
    page_size: int
    total: int
    sort_by: str
    sort_direction: str
    items: tuple[WaferSummaryRow, ...]
    warnings: tuple[str, ...]
    computed_at: str
