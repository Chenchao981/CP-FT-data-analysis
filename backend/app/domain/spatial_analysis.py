from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsContextRequest,
    AnalyticsDatasetContext,
    AnalyticsFilterSummary,
    AnalyticsRuleContext,
)


class SpatialAnalysisMode(StrEnum):
    BIN_MAP = "BIN_MAP"
    PARAMETER_HEATMAP = "PARAMETER_HEATMAP"
    PARAMETER_FAIL_OVERLAY = "PARAMETER_FAIL_OVERLAY"
    COMPOSITE_FAILURE = "COMPOSITE_FAILURE"
    ZONE_COMPARISON = "ZONE_COMPARISON"


class SpatialAnalysisRequest(AnalyticsContextRequest):
    mode: SpatialAnalysisMode
    focus_dataset_id: int | None = Field(default=None, gt=0)
    max_points: int = Field(default=20_000, ge=100, le=50_000)
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_version: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @model_validator(mode="after")
    def mode_contract_is_complete(self) -> SpatialAnalysisRequest:
        if self.focus_dataset_id is not None and self.focus_dataset_id not in {
            item.dataset_id for item in self.datasets
        }:
            raise ValueError("focus_dataset_id must belong to the selected context")
        parameter_modes = {
            SpatialAnalysisMode.PARAMETER_HEATMAP,
            SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
        }
        if self.mode in parameter_modes and len(self.parameters) != 1:
            raise ValueError("parameter spatial modes require exactly one parameter")
        if (
            self.mode
            in {
                SpatialAnalysisMode.BIN_MAP,
                SpatialAnalysisMode.COMPOSITE_FAILURE,
            }
            and self.parameters
        ):
            raise ValueError("this spatial mode does not accept parameters")
        if self.mode == SpatialAnalysisMode.ZONE_COMPARISON:
            if len(self.parameters) > 1:
                raise ValueError("zone comparison accepts at most one parameter")
            if self.rule_code is None or self.rule_version is None:
                raise ValueError("zone comparison requires an approved rule reference")
        elif self.rule_code is not None or self.rule_version is not None:
            raise ValueError("rule reference is only accepted by a gated spatial mode")
        return self


@dataclass(frozen=True, slots=True)
class SpatialColorDomain:
    minimum: float
    maximum: float
    p02: float
    p98: float


@dataclass(frozen=True, slots=True)
class SpatialPoint:
    dataset_id: int | None
    version_no: int | None
    lot_id: str | None
    wafer_id: str | None
    x: int
    y: int
    bin_code: str | None
    result: str | None
    value: float | None
    unit: str | None
    lsl: float | None
    usl: float | None
    spec_status: str | None
    drilldown_key: str | None
    observed_count: int
    fail_count: int
    fail_ratio: float | None
    wafer_count: int
    zone: str | None = None
    raw_bin_code: str | None = None
    bin_mapping_set_id: int | None = None
    bin_mapping_version: str | None = None
    bin_name: str | None = None
    failure_mode: str | None = None
    bin_is_pass: bool | None = None
    spec_set_id: int | None = None
    spec_version: str | None = None
    quadrant: str | None = None
    member_drilldown_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpatialWaferIdentity:
    key: str
    dataset_id: int
    version_no: int
    lot_id: str
    wafer_id: str


@dataclass(frozen=True, slots=True)
class SpatialZoneSummary:
    zone: str
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    yield_rate: float | None
    measured_count: int
    missing_measurement_count: int
    mean: float | None
    minimum: float | None
    maximum: float | None
    drilldown_key: str | None = None
    member_drilldown_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SpatialQuadrantSummary:
    quadrant: str
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    yield_rate: float | None
    measured_count: int
    missing_measurement_count: int
    mean: float | None
    minimum: float | None
    maximum: float | None
    member_drilldown_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpatialZoneGeometry:
    center_x: float
    center_y: float
    radius: float
    center_ratio: float
    mid_ratio: float
    quadrant_axis_rotation_degrees: float
    quadrant_y_direction: str
    quadrant_labels_ccw: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class SpatialDataQuality:
    input_units: int
    returned_points: int
    wafer_count: int
    missing_coordinate_count: int
    duplicate_coordinate_count: int
    measured_count: int
    missing_measurement_count: int
    layer_point_count: int


@dataclass(frozen=True, slots=True)
class SpatialAnalysisResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    mode: str
    parameter: str | None
    color_domain: SpatialColorDomain | None
    data_quality: SpatialDataQuality
    points: tuple[SpatialPoint, ...]
    wafer_manifest: tuple[SpatialWaferIdentity, ...]
    wafer_layers: tuple[SpatialPoint, ...]
    zones: tuple[SpatialZoneSummary, ...]
    warnings: tuple[str, ...]
    computed_at: str
    zone_geometry: SpatialZoneGeometry | None = None
    quadrants: tuple[SpatialQuadrantSummary, ...] = ()
