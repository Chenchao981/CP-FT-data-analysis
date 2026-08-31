from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsContextRequest,
    AnalyticsDatasetContext,
    AnalyticsFilterSummary,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
    StrictAnalyticsRequest,
)


class QualityAnalysisType(StrEnum):
    PAT_ROBUST_IQR = "PAT_ROBUST_IQR"
    SPC_I_MR = "SPC_I_MR"
    MARGIN_OOS = "MARGIN_OOS"
    BIN_COOCCURRENCE = "BIN_COOCCURRENCE"
    SBL_GROUPED_LIMIT = "SBL_GROUPED_LIMIT"
    SYL_GROUPED_LIMIT = "SYL_GROUPED_LIMIT"
    PASS_FAIL_DISTRIBUTION = "PASS_FAIL_DISTRIBUTION"


class QualityGroupBy(StrEnum):
    DATASET = "DATASET"
    LOT = "LOT"
    WAFER = "WAFER"
    RUN = "RUN"
    TESTER = "TESTER"
    PROGRAM = "PROGRAM"
    CONDITION = "CONDITION"


class SpcOrderField(StrEnum):
    UNIT_SEQUENCE = "UNIT_SEQUENCE"


class SpcPhase(StrEnum):
    PHASE_I_BASELINE = "PHASE_I_BASELINE"


class QualityBinType(StrEnum):
    CP_BIN = "CP_BIN"
    SOFT_BIN = "SOFT_BIN"
    HARD_BIN = "HARD_BIN"
    ALL_MAPPED_FAILURE = "ALL_MAPPED_FAILURE"


class QualityRuleReference(StrictAnalyticsRequest):
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    version_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class QualityEvaluationRequest(AnalyticsContextRequest):
    analysis: QualityAnalysisType
    rule: QualityRuleReference
    group_by: QualityGroupBy
    spc_order: SpcOrderField | None = None
    spc_phase: SpcPhase | None = None
    bin_type: QualityBinType | None = None

    @model_validator(mode="after")
    def analysis_inputs_are_explicit(self) -> QualityEvaluationRequest:
        parameter_methods = {
            QualityAnalysisType.PAT_ROBUST_IQR,
            QualityAnalysisType.SPC_I_MR,
            QualityAnalysisType.MARGIN_OOS,
            QualityAnalysisType.PASS_FAIL_DISTRIBUTION,
        }
        bin_methods = {
            QualityAnalysisType.BIN_COOCCURRENCE,
            QualityAnalysisType.SBL_GROUPED_LIMIT,
        }
        if self.analysis in parameter_methods and len(self.parameters) != 1:
            raise ValueError(
                "the selected quality method requires exactly one parameter"
            )
        if self.analysis in bin_methods and self.parameters:
            raise ValueError("bin quality methods do not accept measurement parameters")
        if self.analysis == QualityAnalysisType.SPC_I_MR:
            if self.spc_order is None:
                raise ValueError("SPC I-MR requires an explicit order field")
            if self.spc_phase is None:
                raise ValueError("SPC I-MR requires an explicit Phase-I baseline scope")
        elif self.spc_order is not None or self.spc_phase is not None:
            raise ValueError("SPC order and phase are only accepted for SPC I-MR")
        if self.analysis in bin_methods:
            if self.bin_type is None:
                raise ValueError("bin quality methods require an explicit bin_type")
            if self.group_by == QualityGroupBy.CONDITION:
                raise ValueError(
                    "bin quality methods cannot infer a measurement test condition"
                )
            if (
                self.analysis == QualityAnalysisType.SBL_GROUPED_LIMIT
                and self.bin_type == QualityBinType.ALL_MAPPED_FAILURE
            ):
                raise ValueError("SBL requires one explicit physical Bin type")
        elif self.bin_type is not None:
            raise ValueError("bin_type is only accepted for bin quality methods")
        if (
            self.analysis == QualityAnalysisType.SYL_GROUPED_LIMIT
            and self.group_by == QualityGroupBy.CONDITION
        ):
            raise ValueError("SYL cannot infer a measurement test condition")
        return self


@dataclass(frozen=True, slots=True)
class QualityRuleProvenance:
    rule_code: str
    version_code: str
    algorithm_code: str
    approval_status: str
    activation_status: str
    parameters_sha256: str


@dataclass(frozen=True, slots=True)
class QualityParameterIdentity:
    name: str
    canonical_parameter_code: str | None
    step_code: str
    sequence_no: int
    unit: str | None
    test_condition: str | None
    program_lsl: float | None
    program_usl: float | None


@dataclass(frozen=True, slots=True)
class QualityCalculationCounts:
    input_units: int
    included_units: int
    excluded_units: int
    input_measurements: int
    included_measurements: int
    missing_measurements: int
    excluded_measurements: int


@dataclass(frozen=True, slots=True)
class QualityEvidencePoint:
    dataset_id: int
    version_no: int
    unit_id: int
    measurement_id: int | None
    value: float | None
    drilldown_key: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class PatGroupResult:
    dataset_id: int
    version_no: int
    group_key: str
    valid_n: int
    missing_n: int
    q1: float | None
    median: float | None
    q3: float | None
    iqr: float | None
    robust_sigma: float | None
    lower_limit: float | None
    upper_limit: float | None
    outlier_count: int
    outlier_rate: float | None
    status: str
    evidence: tuple[QualityEvidencePoint, ...]


@dataclass(frozen=True, slots=True)
class SpcPoint:
    sequence: int
    value: float
    moving_range: float | None
    drilldown_key: str
    rule_hits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpcGroupResult:
    dataset_id: int
    version_no: int
    group_key: str
    valid_n: int
    missing_n: int
    center_line: float | None
    lower_control_limit: float | None
    upper_control_limit: float | None
    mr_bar: float | None
    mr_upper_control_limit: float | None
    boundary_reset: bool
    baseline_context_hash: str
    status: str
    points: tuple[SpcPoint, ...]
    sampling_summary: AnalyticsSamplingSummary = field(
        default_factory=lambda: AnalyticsSamplingSummary(False, None, 0, 0, 0)
    )


@dataclass(frozen=True, slots=True)
class MarginPoint:
    dataset_id: int
    version_no: int
    unit_id: int
    measurement_id: int
    value: float
    lower_margin: float | None
    upper_margin: float | None
    nearest_margin: float
    out_of_spec: bool
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class MarginGroupResult:
    dataset_id: int
    version_no: int
    group_key: str
    spec_set_id: int
    spec_version: str
    spec_mode: str
    lsl: float | None
    usl: float | None
    valid_n: int
    missing_n: int
    out_of_spec_count: int
    out_of_spec_rate: float | None
    minimum_margin: float | None
    points: tuple[MarginPoint, ...]
    sampling_summary: AnalyticsSamplingSummary = field(
        default_factory=lambda: AnalyticsSamplingSummary(False, None, 0, 0, 0)
    )


@dataclass(frozen=True, slots=True)
class BinCooccurrenceCell:
    dataset_id: int
    version_no: int
    group_key: str
    left_bin: str
    right_bin: str
    physical_unit_count: int
    denominator_units: int
    rate: float
    drilldown_keys: tuple[str, ...]
    pareto_rank: int = 0
    pair_count_share: float | None = None
    cumulative_pair_count_share: float | None = None


@dataclass(frozen=True, slots=True)
class SblGroupRate:
    group_key: str
    physical_unit_count: int
    fail_unit_count: int
    rate: float
    drilldown_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SblBinLimit:
    dataset_id: int
    version_no: int
    bin_code: str
    subgroup_count: int
    mean_rate: float | None
    sample_stddev: float | None
    upper_limit: float | None
    status: str
    exceeding_groups: tuple[str, ...]
    groups: tuple[SblGroupRate, ...]
    pareto_rank: int = 0
    fail_unit_count: int = 0
    fail_unit_share: float | None = None
    cumulative_fail_unit_share: float | None = None


@dataclass(frozen=True, slots=True)
class SylGroupYield:
    group_key: str
    pass_unit_count: int
    fail_unit_count: int
    unknown_excluded_count: int
    abort_excluded_count: int
    other_result_excluded_count: int
    yield_rate: float | None
    drilldown_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SylDatasetLimit:
    dataset_id: int
    version_no: int
    subgroup_count: int
    mean_yield: float | None
    sample_stddev: float | None
    raw_lower_limit: float | None
    lower_limit: float | None
    rounding_policy: str
    rounding_step: float | None
    status: str
    below_limit_groups: tuple[str, ...]
    groups: tuple[SylGroupYield, ...]


@dataclass(frozen=True, slots=True)
class PassFailHistogramBin:
    bin_index: int
    lower: float
    upper: float
    pass_count: int
    fail_count: int
    pass_drilldown_keys: tuple[str, ...]
    fail_drilldown_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PassFailDistributionGroup:
    dataset_id: int
    version_no: int
    group_key: str
    pass_count: int
    fail_count: int
    unknown_excluded_count: int
    abort_excluded_count: int
    other_result_excluded_count: int
    missing_measurements: int
    pass_mean: float | None
    fail_mean: float | None
    minimum: float | None
    maximum: float | None
    status: str
    bins: tuple[PassFailHistogramBin, ...]


@dataclass(frozen=True, slots=True)
class QualityEvaluationResult:
    contract_version: str
    analysis: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    calculation_context_hash: str
    rule_context: AnalyticsRuleContext
    rule: QualityRuleProvenance
    parameter_identity: QualityParameterIdentity | None
    capabilities: tuple[AnalyticsCapability, ...]
    counts: QualityCalculationCounts
    sampling_summary: AnalyticsSamplingSummary
    pat: tuple[PatGroupResult, ...]
    spc: tuple[SpcGroupResult, ...]
    margin: tuple[MarginGroupResult, ...]
    bin_cooccurrence: tuple[BinCooccurrenceCell, ...]
    sbl: tuple[SblBinLimit, ...]
    warnings: tuple[str, ...]
    computed_at: str
    syl: tuple[SylDatasetLimit, ...] = ()
    pass_fail_distribution: tuple[PassFailDistributionGroup, ...] = ()
