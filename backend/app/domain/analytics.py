from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictAnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalyticsStage(StrEnum):
    CP = "CP"
    FT = "FT"


class AnalyticsOverallResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ABORT = "ABORT"


class AnalyticsDetailView(StrEnum):
    WIDE = "WIDE"
    LONG = "LONG"


class AnalyticsDetailSort(StrEnum):
    UNIT_SEQUENCE = "UNIT_SEQUENCE"
    LOT = "LOT"
    WAFER = "WAFER"
    SOURCE_ROW = "SOURCE_ROW"
    RESULT = "RESULT"
    SOFT_BIN = "SOFT_BIN"
    HARD_BIN = "HARD_BIN"


class AnalyticsSortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class AnalyticsDatasetReference(StrictAnalyticsRequest):
    dataset_id: int = Field(gt=0)
    version_no: int = Field(gt=0)


class AnalyticsFilters(StrictAnalyticsRequest):
    lot_ids: list[str] = Field(default_factory=list, max_length=50)
    wafer_ids: list[str] = Field(default_factory=list, max_length=100)
    bin_codes: list[str] = Field(default_factory=list, max_length=50)
    overall_results: list[AnalyticsOverallResult] = Field(
        default_factory=list, max_length=4
    )
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    tester_ids: list[str] = Field(default_factory=list, max_length=50)
    program_versions: list[str] = Field(default_factory=list, max_length=50)
    test_conditions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator(
        "lot_ids",
        "wafer_ids",
        "bin_codes",
        "source_ids",
        "tester_ids",
        "program_versions",
        "test_conditions",
    )
    @classmethod
    def values_are_unique_and_bounded(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("analytics filter values must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("analytics filter values must be unique")
        return normalized

    @field_validator("overall_results")
    @classmethod
    def results_are_unique(
        cls, value: list[AnalyticsOverallResult]
    ) -> list[AnalyticsOverallResult]:
        if len(value) != len(set(value)):
            raise ValueError("analytics result filters must be unique")
        return value


class AnalyticsContextRequest(StrictAnalyticsRequest):
    datasets: list[AnalyticsDatasetReference] = Field(min_length=1, max_length=8)
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    parameters: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("datasets")
    @classmethod
    def datasets_are_unique(
        cls, value: list[AnalyticsDatasetReference]
    ) -> list[AnalyticsDatasetReference]:
        identities = [(item.dataset_id, item.version_no) for item in value]
        dataset_ids = [item.dataset_id for item in value]
        if len(identities) != len(set(identities)) or len(dataset_ids) != len(
            set(dataset_ids)
        ):
            raise ValueError("each dataset may appear only once in analytics context")
        return value

    @field_validator("parameters")
    @classmethod
    def parameters_are_unique_and_bounded(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("analytics parameters must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("analytics parameters must be unique")
        return normalized


class AnalyticsOverviewRequest(AnalyticsContextRequest):
    focus_dataset_id: int | None = Field(default=None, gt=0)
    max_points: int = Field(default=10_000, ge=100, le=20_000)

    @model_validator(mode="after")
    def focus_must_be_selected(self) -> AnalyticsOverviewRequest:
        if self.focus_dataset_id is not None and self.focus_dataset_id not in {
            item.dataset_id for item in self.datasets
        }:
            raise ValueError("focus_dataset_id must belong to the selected context")
        return self


class AnalyticsEvaluationFilter(StrictAnalyticsRequest):
    evaluation_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    evaluation_results: list[str] = Field(min_length=1, max_length=20)
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_version: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @field_validator("evaluation_results")
    @classmethod
    def evaluation_results_are_bounded_and_unique(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value]
        if any(
            not item
            or len(item) > 64
            or not all(character.isalnum() or character == "_" for character in item)
            for item in normalized
        ) or len(normalized) != len(set(normalized)):
            raise ValueError("evaluation results must be bounded uppercase identifiers")
        return normalized

    @model_validator(mode="after")
    def rule_identity_is_complete_or_explicitly_unversioned(
        self,
    ) -> AnalyticsEvaluationFilter:
        if (self.rule_code is None) != (self.rule_version is None):
            raise ValueError(
                "evaluation rule_code and rule_version must be supplied together"
            )
        return self


class AnalyticsMeasurementFilter(StrictAnalyticsRequest):
    parameter: str = Field(min_length=1, max_length=200)
    lower_bound: float | None = None
    upper_bound: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @model_validator(mode="after")
    def numeric_bounds_are_finite_and_ordered(self) -> AnalyticsMeasurementFilter:
        bounds = (self.lower_bound, self.upper_bound)
        if any(value is not None and not math.isfinite(value) for value in bounds):
            raise ValueError("measurement drilldown bounds must be finite")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("measurement drilldown lower bound exceeds upper bound")
        return self


class AnalyticsDetailRequest(AnalyticsContextRequest):
    focus_dataset_id: int = Field(gt=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    view: AnalyticsDetailView = AnalyticsDetailView.WIDE
    sort_by: AnalyticsDetailSort = AnalyticsDetailSort.UNIT_SEQUENCE
    sort_direction: AnalyticsSortDirection = AnalyticsSortDirection.ASC
    evaluation_filter: AnalyticsEvaluationFilter | None = None
    measurement_filter: AnalyticsMeasurementFilter | None = None

    @model_validator(mode="after")
    def focus_must_be_selected(self) -> AnalyticsDetailRequest:
        if self.focus_dataset_id not in {item.dataset_id for item in self.datasets}:
            raise ValueError("focus_dataset_id must belong to the selected context")
        return self


class AnalyticsDrilldownRequest(AnalyticsContextRequest):
    drilldown_key: str = Field(pattern=r"^UNIT:[1-9][0-9]{0,18}$")


@dataclass(frozen=True, slots=True)
class AnalyticsResolvedDataset:
    dataset_id: int
    version_no: int
    dataset_name: str
    test_stage: str
    product_name: str | None


@dataclass(frozen=True, slots=True)
class AnalyticsDatasetContext:
    resolved_datasets: tuple[AnalyticsResolvedDataset, ...]
    test_stage: str
    current_published_verified: bool


@dataclass(frozen=True, slots=True)
class AnalyticsNormalizedFilters:
    lot_ids: tuple[str, ...]
    wafer_ids: tuple[str, ...]
    bin_codes: tuple[str, ...]
    overall_results: tuple[str, ...]
    source_ids: tuple[str, ...]
    tester_ids: tuple[str, ...]
    program_versions: tuple[str, ...]
    test_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsFilterSummary:
    normalized_filters: AnalyticsNormalizedFilters
    parameters: tuple[str, ...]
    filter_hash: str
    context_hash: str


@dataclass(frozen=True, slots=True)
class AnalyticsRuleContext:
    spec_versions: tuple[str, ...]
    bin_mapping_versions: tuple[str, ...]
    evaluation_rule_versions: tuple[str, ...]
    # Rules that are fully approved, enabled, effective and applicable to every
    # Dataset/parameter in this exact analysis Context.  This is deliberately
    # separate from evaluation_rule_versions, which describes historical
    # evaluations already persisted on measurements.
    applicable_rule_versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalyticsCapability:
    code: str
    status: str
    reason_code: str | None
    message: str | None


@dataclass(frozen=True, slots=True)
class AnalyticsEvaluationDrilldownContext:
    evaluation_type: str
    evaluation_results: tuple[str, ...]
    rule_code: str | None
    rule_version: str | None


@dataclass(frozen=True, slots=True)
class AnalyticsMeasurementDrilldownContext:
    parameter: str
    lower_bound: float | None
    upper_bound: float | None
    lower_inclusive: bool
    upper_inclusive: bool


@dataclass(frozen=True, slots=True)
class AnalyticsRiskItem:
    code: str
    category: str
    severity: str
    status: str
    reason_code: str | None
    title: str
    message: str
    affected_count: int
    denominator_count: int
    rate: float | None
    drilldown_target: str | None
    rule_versions: tuple[str, ...]
    aggregate_drilldown_context: AnalyticsEvaluationDrilldownContext | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsCounts:
    input_units: int
    included_units: int
    excluded_units: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    known_yield_denominator: int
    missing_measurements: int
    yield_rate: float | None = None
    unknown_abort_denominator: int = 0
    unknown_abort_rate: float | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsSamplingSummary:
    sampled: bool
    method: str | None
    original_points: int
    returned_points: int
    preserved_out_of_spec_points: int


@dataclass(frozen=True, slots=True)
class AnalyticsOptionSet:
    lot_ids: tuple[str, ...]
    wafer_ids: tuple[str, ...]
    bin_codes: tuple[str, ...]
    source_ids: tuple[str, ...]
    tester_ids: tuple[str, ...]
    program_versions: tuple[str, ...]
    test_conditions: tuple[str, ...]
    parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsDatasetOverview:
    dataset_id: int
    version_no: int
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    known_yield_denominator: int
    yield_rate: float | None


@dataclass(frozen=True, slots=True)
class AnalyticsYieldPoint:
    dataset_id: int
    version_no: int
    test_batch_id: int
    run_id: int
    sequence: int
    ordered_at: str | None
    order_basis: str
    source_id: str
    lot_id: str
    wafer_id: str | None
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    yield_rate: float | None
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class AnalyticsBinPoint:
    dataset_id: int
    version_no: int
    mapping_set_id: int
    mapping_version: str
    bin_type: str
    bin_code: str
    bin_name: str | None
    failure_mode: str | None
    is_pass: bool
    unit_count: int
    percent: float
    cumulative_percent: float
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class AnalyticsWaferMapPoint:
    x: int
    y: int
    bin_code: str | None
    result: str
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class AnalyticsOverviewResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    counts: AnalyticsCounts
    sampling_summary: AnalyticsSamplingSummary
    options: AnalyticsOptionSet
    datasets: tuple[AnalyticsDatasetOverview, ...]
    yield_trend: tuple[AnalyticsYieldPoint, ...]
    bin_pareto: tuple[AnalyticsBinPoint, ...]
    wafer_map: tuple[AnalyticsWaferMapPoint, ...]
    risk_summary: tuple[AnalyticsRiskItem, ...]
    warnings: tuple[str, ...]
    computed_at: str


@dataclass(frozen=True, slots=True)
class AnalyticsShellContextResult:
    """Shared filter/options context; it intentionally exposes no Overview charts."""

    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    counts: AnalyticsCounts
    sampling_summary: AnalyticsSamplingSummary
    options: AnalyticsOptionSet
    warnings: tuple[str, ...]
    computed_at: str


@dataclass(frozen=True, slots=True)
class AnalyticsDetailSourceFile:
    source_file_id: int
    receipt_id: int | None
    original_file_name: str | None
    sha256: str | None
    ordinal_no: int | None
    file_role: str | None
    lineage_basis: str


@dataclass(frozen=True, slots=True)
class AnalyticsDetailBinEvaluation:
    unit_bin_evaluation_id: int
    bin_type: str
    raw_bin_code: str
    mapping_status: str
    bin_mapping_set_id: int | None
    mapping_version: str | None
    bin_definition_id: int | None
    mapped_bin_name: str | None
    failure_mode_snapshot: str | None
    is_pass_snapshot: bool | None
    processing_run_id: int | None
    evaluated_at_utc: str


@dataclass(frozen=True, slots=True)
class AnalyticsDetailMeasurementEvaluation:
    evaluation_id: int
    evaluation_type: str
    evaluation_scope_key: str
    evaluation_result: str
    evaluation_reason: str | None
    evaluation_run_id: int | None
    rule_code: str | None
    rule_version_id: int | None
    rule_version: str | None
    spec_binding_id: int | None
    spec_set_id: int | None
    spec_version: str | None
    spec_item_id: int | None
    lsl_applied: float | None
    usl_applied: float | None
    lower_operator_applied: str | None
    upper_operator_applied: str | None
    processing_run_id: int | None
    evaluated_at_utc: str


@dataclass(frozen=True, slots=True)
class AnalyticsDetailFormalSpec:
    status: str
    reason_code: str | None
    evaluation_id: int | None
    evaluation_result: str | None
    evaluation_scope_key: str | None
    spec_binding_id: int | None
    spec_set_id: int | None
    spec_version: str | None
    spec_item_id: int | None
    lsl_applied: float | None
    usl_applied: float | None
    lower_operator_applied: str | None
    upper_operator_applied: str | None


@dataclass(frozen=True, slots=True)
class AnalyticsDetailMeasurement:
    measurement_id: int
    parameter: str
    canonical_parameter_code: str | None
    step_code: str
    sequence_no: int
    value_numeric: float | None
    value_text: str | None
    status: str
    unit: str | None
    program_lsl: float | None
    program_usl: float | None
    program_limit_source: str
    formal_spec: AnalyticsDetailFormalSpec
    evaluations: tuple[AnalyticsDetailMeasurementEvaluation, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsDetailRow:
    drilldown_key: str
    unit_id: int
    logical_unit_key: str
    lot_id: str
    wafer_id: str | None
    x: int | None
    y: int | None
    soft_bin: str | None
    hard_bin: str | None
    overall_result: str
    source_row_no: int | None
    processing_run_id: int
    source_file_id: int
    receipt_id: int | None
    original_file_name: str | None
    sha256: str | None
    source_id: str
    tester_id: str | None
    program_version: str | None
    cleaner_release: str | None
    source_files: tuple[AnalyticsDetailSourceFile, ...]
    bin_evaluations: tuple[AnalyticsDetailBinEvaluation, ...]
    measurements: tuple[AnalyticsDetailMeasurement, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsDetailResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    counts: AnalyticsCounts
    sampling_summary: AnalyticsSamplingSummary
    evaluation_filter: AnalyticsEvaluationDrilldownContext | None
    measurement_filter: AnalyticsMeasurementDrilldownContext | None
    page: int
    page_size: int
    total: int
    view: str
    sort_by: str
    sort_direction: str
    items: tuple[AnalyticsDetailRow, ...]
    warnings: tuple[str, ...]
    computed_at: str


@dataclass(frozen=True, slots=True)
class AnalyticsDrilldownResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    unit: AnalyticsDetailRow
    warnings: tuple[str, ...]
    computed_at: str
