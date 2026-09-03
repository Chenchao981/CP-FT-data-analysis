from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.auth import Principal


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatasetType(StrEnum):
    CP_DETAIL = "CP_DETAIL"
    FT_DETAIL = "FT_DETAIL"
    WAFER_SUMMARY = "WAFER_SUMMARY"
    YIELD_REPORT = "YIELD_REPORT"
    OTHER = "OTHER"


class DatasetStage(StrEnum):
    CP = "CP"
    FT = "FT"
    OTHER = "OTHER"


class DatasetAnalysisGroupBy(StrEnum):
    DATASET = "DATASET"


class DatasetParameterAnalysisType(StrEnum):
    DESCRIPTIVE = "DESCRIPTIVE"
    BOX_PLOT = "BOX_PLOT"
    HISTOGRAM = "HISTOGRAM"
    NORMAL_FIT = "NORMAL_FIT"
    CAPABILITY = "CAPABILITY"


class DatasetCapabilityMethod(StrEnum):
    CPK_POOLED_WITHIN_RUN_V1 = "CPK_POOLED_WITHIN_RUN_V1"
    CPK_POOLED_WITHIN_LOT_WAFER_V1 = "CPK_POOLED_WITHIN_LOT_WAFER_V1"


class DatasetAnalysisOverallResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ABORT = "ABORT"


class CreateDatasetRequest(StrictRequest):
    dataset_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,127}$")
    dataset_name: str = Field(min_length=1, max_length=300)
    dataset_type: DatasetType
    test_stage: DatasetStage
    supplier_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    project_code: str | None = Field(default=None, max_length=128)
    owner_user_id: int = Field(gt=0)

    @model_validator(mode="after")
    def stage_identity_is_complete(self) -> CreateDatasetRequest:
        if self.test_stage == DatasetStage.CP and self.supplier_id is None:
            raise ValueError(
                "CP dataset requires a wafer-fab/source identity from the file or manual input"
            )
        if self.test_stage == DatasetStage.FT and self.product_id is None:
            raise ValueError(
                "FT dataset requires a product identity from the file or manual input"
            )
        return self


class CreateDatasetVersionRequest(StrictRequest):
    input_batch_id: int = Field(gt=0)
    processing_run_ids: list[int] = Field(min_length=1, max_length=10_000)
    canonical_model_version: str = Field(
        default="1.0", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
    )

    @field_validator("processing_run_ids")
    @classmethod
    def run_ids_are_unique(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("processing run identities must be positive")
        if len(value) != len(set(value)):
            raise ValueError("processing run identities must be unique")
        return value


class PublishDatasetVersionRequest(StrictRequest):
    published_by: int = Field(gt=0)


class DatasetReference(StrictRequest):
    dataset_id: int = Field(gt=0)
    version_no: int = Field(gt=0)


class DatasetComparisonRequest(StrictRequest):
    datasets: list[DatasetReference] = Field(min_length=1, max_length=8)
    lot_ids: list[str] = Field(default_factory=list, max_length=50)
    wafer_ids: list[str] = Field(default_factory=list, max_length=100)
    bin_codes: list[str] = Field(default_factory=list, max_length=50)
    parameters: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("datasets")
    @classmethod
    def dataset_refs_are_unique(
        cls, value: list[DatasetReference]
    ) -> list[DatasetReference]:
        dataset_ids = [item.dataset_id for item in value]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("each dataset may appear only once in a comparison")
        return value

    @field_validator("lot_ids", "wafer_ids", "bin_codes", "parameters")
    @classmethod
    def filter_values_are_unique_and_non_empty(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("analysis filter values must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("analysis filter values must be unique")
        return normalized


class DatasetParameterAnalysisFilters(StrictRequest):
    lot_ids: list[str] = Field(default_factory=list, max_length=50)
    wafer_ids: list[str] = Field(default_factory=list, max_length=100)
    bin_codes: list[str] = Field(default_factory=list, max_length=50)
    overall_results: list[DatasetAnalysisOverallResult] = Field(
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
    def values_are_unique_and_non_empty(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("analysis filter values must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("analysis filter values must be unique")
        return normalized

    @field_validator("overall_results")
    @classmethod
    def result_values_are_unique(
        cls, value: list[DatasetAnalysisOverallResult]
    ) -> list[DatasetAnalysisOverallResult]:
        if len(value) != len(set(value)):
            raise ValueError("analysis overall-result values must be unique")
        return value


class DatasetAnalysisRuleReference(StrictRequest):
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    version_code: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @model_validator(mode="after")
    def exact_rule_reference_is_paired(self) -> DatasetAnalysisRuleReference:
        if (self.rule_code is None) != (self.version_code is None):
            raise ValueError("rule_code and version_code must be supplied together")
        return self


class DatasetHistogramConfig(DatasetAnalysisRuleReference):
    pass


class DatasetCapabilityConfig(DatasetAnalysisRuleReference):
    method: DatasetCapabilityMethod | None = None

    @model_validator(mode="after")
    def method_and_rule_reference_are_complete(self) -> DatasetCapabilityConfig:
        has_rule = self.rule_code is not None
        if has_rule != (self.method is not None):
            raise ValueError(
                "capability method, rule_code and version_code must be supplied together"
            )
        return self


class DatasetParameterAnalysisRequest(StrictRequest):
    datasets: list[DatasetReference] = Field(min_length=1, max_length=8)
    group_by: DatasetAnalysisGroupBy = DatasetAnalysisGroupBy.DATASET
    filters: DatasetParameterAnalysisFilters = Field(
        default_factory=DatasetParameterAnalysisFilters
    )
    parameters: list[str] = Field(min_length=1, max_length=20)
    analyses: list[DatasetParameterAnalysisType] = Field(
        default_factory=lambda: [DatasetParameterAnalysisType.DESCRIPTIVE],
        min_length=1,
        max_length=5,
    )
    box_plot: DatasetAnalysisRuleReference = Field(
        default_factory=DatasetAnalysisRuleReference
    )
    histogram: DatasetHistogramConfig = Field(default_factory=DatasetHistogramConfig)
    normal_fit: DatasetAnalysisRuleReference = Field(
        default_factory=DatasetAnalysisRuleReference
    )
    capability: DatasetCapabilityConfig = Field(default_factory=DatasetCapabilityConfig)

    @field_validator("datasets")
    @classmethod
    def dataset_refs_are_unique(
        cls, value: list[DatasetReference]
    ) -> list[DatasetReference]:
        dataset_ids = [item.dataset_id for item in value]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ValueError("each dataset may appear only once in an analysis")
        return value

    @field_validator("parameters")
    @classmethod
    def parameters_are_unique_and_non_empty(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("analysis parameters must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("analysis parameters must be unique")
        return normalized

    @field_validator("analyses")
    @classmethod
    def analyses_are_unique(
        cls, value: list[DatasetParameterAnalysisType]
    ) -> list[DatasetParameterAnalysisType]:
        if len(value) != len(set(value)):
            raise ValueError("analysis types must be unique")
        return value

    @model_validator(mode="after")
    def rule_references_match_requested_analyses(
        self,
    ) -> DatasetParameterAnalysisRequest:
        references = {
            DatasetParameterAnalysisType.BOX_PLOT: self.box_plot.rule_code,
            DatasetParameterAnalysisType.HISTOGRAM: self.histogram.rule_code,
            DatasetParameterAnalysisType.NORMAL_FIT: self.normal_fit.rule_code,
            DatasetParameterAnalysisType.CAPABILITY: self.capability.rule_code,
        }
        selected = set(self.analyses)
        for analysis, rule_code in references.items():
            if analysis in selected and rule_code is None:
                raise ValueError(
                    f"{analysis.value} requires an exact rule_code and version_code"
                )
            if analysis not in selected and rule_code is not None:
                raise ValueError(
                    f"{analysis.value} rule reference requires that analysis type"
                )
        return self


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    dataset_id: int
    dataset_code: str
    dataset_name: str
    dataset_type: str
    test_stage: str
    supplier_id: int | None
    product_id: int | None
    owner_user_id: int


@dataclass(frozen=True, slots=True)
class DatasetVersionRecord:
    dataset_version_id: int
    dataset_id: int
    version_no: int
    input_batch_id: int
    canonical_model_version: str
    status: str
    is_current: bool
    run_count: int


@dataclass(frozen=True, slots=True)
class GateReason:
    code: str
    count: int
    message: str


@dataclass(frozen=True, slots=True)
class DqGateResult:
    dataset_id: int
    version_no: int
    status: str
    run_count: int
    unit_count: int
    measurement_count: int
    reasons: tuple[GateReason, ...]


@dataclass(frozen=True, slots=True)
class DatasetResultSummary:
    dataset_id: int
    dataset_code: str
    dataset_name: str
    version_no: int
    version_status: str
    is_current: bool
    run_count: int
    lot_count: int
    wafer_count: int
    unit_count: int
    pass_count: int | None
    fail_count: int | None
    yield_rate: float | None
    measurement_count: int
    bin_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class WaferOption:
    lot_id: str
    wafer_id: str


@dataclass(frozen=True, slots=True)
class WaferYieldPoint:
    lot_id: str
    wafer_id: str
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    known_yield_denominator: int
    yield_rate: float | None


@dataclass(frozen=True, slots=True)
class BinCountPoint:
    soft_bin: str
    unit_count: int
    percent: float


@dataclass(frozen=True, slots=True)
class WaferMapPoint:
    x: int
    y: int
    soft_bin: str | None
    result: str


@dataclass(frozen=True, slots=True)
class FtParameterOption:
    name: str
    unit: str | None
    lsl: float | None
    usl: float | None
    test_condition: str | None


@dataclass(frozen=True, slots=True)
class FtParameterPoint:
    sequence: int
    lot_id: str
    source_id: str
    value: float | None
    status: str


@dataclass(frozen=True, slots=True)
class DatasetChartData:
    dataset_id: int
    version_no: int
    test_stage: str
    product_name: str | None
    selected_lot_id: str | None
    selected_wafer_id: str | None
    selected_source_id: str | None
    selected_parameter: str | None
    lot_options: tuple[str, ...]
    wafer_options: tuple[WaferOption, ...]
    source_options: tuple[str, ...]
    parameter_options: tuple[FtParameterOption, ...]
    wafer_yield: tuple[WaferYieldPoint, ...]
    bin_counts: tuple[BinCountPoint, ...]
    wafer_map: tuple[WaferMapPoint, ...]
    ft_parameter_points: tuple[FtParameterPoint, ...]
    ft_total_point_count: int
    ft_sampled: bool


@dataclass(frozen=True, slots=True)
class DatasetParameterStatistic:
    name: str
    unit: str | None
    lsl: float | None
    usl: float | None
    test_condition: str | None
    measured_count: int
    missing_count: int
    minimum: float | None
    maximum: float | None
    average: float | None


@dataclass(frozen=True, slots=True)
class DatasetComparisonItem:
    dataset_id: int
    version_no: int
    test_stage: str
    product_name: str | None
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    abort_count: int
    known_yield_denominator: int
    yield_rate: float | None
    parameter_statistics: tuple[DatasetParameterStatistic, ...]


@dataclass(frozen=True, slots=True)
class DatasetComparisonResult:
    test_stage: str
    spec_compatibility: str
    lot_ids: tuple[str, ...]
    wafer_ids: tuple[str, ...]
    bin_codes: tuple[str, ...]
    parameters: tuple[str, ...]
    items: tuple[DatasetComparisonItem, ...]


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisFilterSummary:
    lot_ids: tuple[str, ...]
    wafer_ids: tuple[str, ...]
    bin_codes: tuple[str, ...]
    overall_results: tuple[str, ...]
    source_ids: tuple[str, ...]
    tester_ids: tuple[str, ...]
    program_versions: tuple[str, ...]
    test_conditions: tuple[str, ...]
    matched_unit_count: int
    candidate_measurement_count: int


@dataclass(frozen=True, slots=True)
class DatasetAnalysisParameterIdentity:
    name: str
    canonical_parameter_code: str | None
    unit: str | None
    program_lsl: float | None
    program_usl: float | None
    test_condition: str | None
    spec_set_ids: tuple[int, ...]
    limit_source: str
    formal_lsl: float | None = None
    formal_usl: float | None = None
    formal_lower_operator: str | None = None
    formal_upper_operator: str | None = None
    formal_spec_status: str = "NO_SPEC"
    formal_spec_reason_codes: tuple[str, ...] = ()
    formal_spec_versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DatasetMeasurementStatusCount:
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class DatasetDescriptiveStatistics:
    row_count: int
    numeric_count: int
    excluded_count: int
    minimum: float | None
    maximum: float | None
    average: float | None
    sample_stddev: float | None


@dataclass(frozen=True, slots=True)
class DatasetBoxPlotStatistics:
    minimum: float
    q1: float
    median: float
    q3: float
    maximum: float
    lower_whisker: float
    upper_whisker: float
    outlier_count: int
    method: str
    outlier_evidence: tuple[DatasetMeasurementEvidence, ...] = ()
    outlier_sampling: DatasetEvidenceSampling | None = None


@dataclass(frozen=True, slots=True)
class DatasetMeasurementEvidence:
    measurement_id: int
    value: float
    drilldown_key: str
    spec_status: str


@dataclass(frozen=True, slots=True)
class DatasetEvidenceSampling:
    sampled: bool
    method: str
    original_points: int
    returned_points: int


@dataclass(frozen=True, slots=True)
class DatasetHistogramBin:
    index: int
    lower_bound: float
    upper_bound: float
    count: int
    lower_inclusive: bool
    upper_inclusive: bool
    spec_region: str = "NO_SPEC"
    aggregate_drilldown_context: DatasetMeasurementAggregateContext | None = None


@dataclass(frozen=True, slots=True)
class DatasetHistogramStatistics:
    bin_count: int
    requested_bin_count: int
    range_min: float | None
    range_max: float | None
    bins: tuple[DatasetHistogramBin, ...]
    method: str = "EQUAL_WIDTH_HISTOGRAM_V1"


@dataclass(frozen=True, slots=True)
class DatasetNormalFitPoint:
    x: float
    probability_density: float


@dataclass(frozen=True, slots=True)
class DatasetNormalFitStatistics:
    status: str
    reason_code: str | None
    sample_count: int
    mean: float | None
    standard_deviation: float | None
    points: tuple[DatasetNormalFitPoint, ...]
    method: str = "NORMAL_FIT_MLE_V1"
    observed_evidence: tuple[DatasetMeasurementEvidence, ...] = ()
    evidence_sampling: DatasetEvidenceSampling | None = None


@dataclass(frozen=True, slots=True)
class DatasetMeasurementAggregateContext:
    dataset_id: int
    version_no: int
    parameter: str
    lower_bound: float | None = None
    upper_bound: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True


DatasetCapabilityDrilldownContext = DatasetMeasurementAggregateContext


@dataclass(frozen=True, slots=True)
class DatasetCapabilityStatistics:
    status: str
    ppk_status: str
    cpk_status: str
    reason_codes: tuple[str, ...]
    spec_mode: str | None
    lsl: float | None
    usl: float | None
    sample_count: int
    subgroup_count: int
    overall_sigma: float | None
    within_sigma: float | None
    ppl: float | None
    ppu: float | None
    ppk: float | None
    cpl: float | None
    cpu: float | None
    cpk: float | None
    rule_code: str | None
    risk_metric: str | None = None
    risk_threshold: float | None = None
    parameters_sha256: str | None = None
    drilldown_context: DatasetCapabilityDrilldownContext | None = None


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysis:
    identity: DatasetAnalysisParameterIdentity
    status_counts: tuple[DatasetMeasurementStatusCount, ...]
    descriptive: DatasetDescriptiveStatistics | None
    box_plot: DatasetBoxPlotStatistics | None
    histogram: DatasetHistogramStatistics | None
    capability: DatasetCapabilityStatistics | None
    normal_fit: DatasetNormalFitStatistics | None = None


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisItem:
    dataset_id: int
    version_no: int
    test_stage: str
    group_key: str
    filter_summary: DatasetParameterAnalysisFilterSummary
    parameters: tuple[DatasetParameterAnalysis, ...]


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisResolvedDataset:
    dataset_id: int
    version_no: int


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisDatasetContext:
    resolved_datasets: tuple[DatasetParameterAnalysisResolvedDataset, ...]
    test_stage: str
    current_published_verified: bool


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisNormalizedFilters:
    lot_ids: tuple[str, ...]
    wafer_ids: tuple[str, ...]
    bin_codes: tuple[str, ...]
    overall_results: tuple[str, ...]
    source_ids: tuple[str, ...]
    tester_ids: tuple[str, ...]
    program_versions: tuple[str, ...]
    test_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisContextFilterSummary:
    normalized_filters: DatasetParameterAnalysisNormalizedFilters
    filter_hash: str


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisRuleContext:
    spec_versions: tuple[str, ...]
    bin_mapping_versions: tuple[str, ...]
    evaluation_rule_versions: tuple[str, ...]
    capability_rule_code: str | None
    capability_rule_approval_status: str


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisCapability:
    code: str
    status: str
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisCounts:
    input_units: int
    included_units: int
    excluded_units: int
    missing_measurements: int


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisSamplingSummary:
    sampled: bool
    method: str | None
    original_points: int
    returned_points: int
    preserved_out_of_spec_points: int


@dataclass(frozen=True, slots=True)
class DatasetParameterAnalysisResult:
    contract_version: str
    group_by: str
    compatibility: str
    dataset_context: DatasetParameterAnalysisDatasetContext
    filter_summary: DatasetParameterAnalysisContextFilterSummary
    rule_context: DatasetParameterAnalysisRuleContext
    capabilities: tuple[DatasetParameterAnalysisCapability, ...]
    counts: DatasetParameterAnalysisCounts
    sampling_summary: DatasetParameterAnalysisSamplingSummary
    warnings: tuple[str, ...]
    computed_at: str
    items: tuple[DatasetParameterAnalysisItem, ...]


@dataclass(frozen=True, slots=True)
class DatasetDetailMeasurement:
    parameter: str
    value_numeric: float | None
    value_text: str | None
    status: str
    unit: str | None
    lsl: float | None
    usl: float | None


@dataclass(frozen=True, slots=True)
class DatasetDetailRow:
    unit_id: int
    logical_unit_key: str
    lot_id: str | None
    wafer_id: str | None
    x: int | None
    y: int | None
    soft_bin: str | None
    hard_bin: str | None
    overall_result: str
    source_row_no: int | None
    measurements: tuple[DatasetDetailMeasurement, ...]


@dataclass(frozen=True, slots=True)
class DatasetDetailPage:
    dataset_id: int
    version_no: int
    test_stage: str
    page: int
    page_size: int
    total: int
    lot_options: tuple[str, ...]
    wafer_options: tuple[str, ...]
    bin_options: tuple[str, ...]
    parameter_options: tuple[str, ...]
    items: tuple[DatasetDetailRow, ...]


class DatasetService(Protocol):
    def list_datasets(self, principal) -> tuple[DatasetRecord, ...]: ...

    def assert_dataset_access(
        self,
        dataset_id: int,
        principal,
        mode: str = "READ",
        *,
        version_no: int | None = None,
    ) -> None: ...

    def create_dataset(self, request: CreateDatasetRequest) -> DatasetRecord: ...

    def create_version(
        self,
        dataset_id: int,
        request: CreateDatasetVersionRequest,
        principal: Principal,
    ) -> DatasetVersionRecord: ...

    def evaluate_gate(
        self, dataset_id: int, version_no: int, principal
    ) -> DqGateResult: ...

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord: ...

    def get_summary(
        self, dataset_id: int, version_no: int, principal
    ) -> DatasetResultSummary: ...

    def get_chart_data(
        self,
        dataset_id: int,
        version_no: int,
        lot_id: str | None = None,
        wafer_id: str | None = None,
        source_id: str | None = None,
        parameter: str | None = None,
    ) -> DatasetChartData: ...

    def compare(self, request: DatasetComparisonRequest) -> DatasetComparisonResult: ...

    def assert_parameter_analysis_rules_approved(
        self, request: DatasetParameterAnalysisRequest
    ) -> None: ...

    def analyze_parameters(
        self, request: DatasetParameterAnalysisRequest
    ) -> DatasetParameterAnalysisResult: ...

    def get_detail_page(
        self,
        dataset_id: int,
        version_no: int,
        *,
        page: int,
        page_size: int,
        lot_ids: tuple[str, ...] = (),
        wafer_ids: tuple[str, ...] = (),
        bin_codes: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> DatasetDetailPage: ...
