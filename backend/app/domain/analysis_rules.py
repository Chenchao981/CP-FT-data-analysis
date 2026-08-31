from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.formal_pat_contract import (
    FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
    FORMAL_PAT_ALGORITHM_CODE,
)


class StrictRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisRuleType(StrEnum):
    PAT = "PAT"
    SBL = "SBL"
    SYL = "SYL"
    CPK = "CPK"
    SPC = "SPC"
    HISTOGRAM = "HISTOGRAM"
    BOX_PLOT = "BOX_PLOT"
    NORMAL_FIT = "NORMAL_FIT"
    CORRELATION = "CORRELATION"
    MARGIN = "MARGIN"
    ZONE = "ZONE"
    BIN_COOCCURRENCE = "BIN_COOCCURRENCE"
    PASS_FAIL_DISTRIBUTION = "PASS_FAIL_DISTRIBUTION"


class AnalysisAlgorithmCode(StrEnum):
    TUKEY_BOX_V1 = "TUKEY_BOX_V1"
    EQUAL_WIDTH_HISTOGRAM_V1 = "EQUAL_WIDTH_HISTOGRAM_V1"
    NORMAL_FIT_MLE_V1 = "NORMAL_FIT_MLE_V1"
    PEARSON_PAIRWISE_V1 = "PEARSON_PAIRWISE_V1"
    SPEARMAN_PAIRWISE_V1 = "SPEARMAN_PAIRWISE_V1"
    CPK_POOLED_WITHIN_RUN_V1 = "CPK_POOLED_WITHIN_RUN_V1"
    CPK_POOLED_WITHIN_LOT_WAFER_V1 = "CPK_POOLED_WITHIN_LOT_WAFER_V1"
    PAT_SHARED_IQR_1_35_V1 = FORMAL_PAT_ALGORITHM_CODE
    SBL_GROUPED_LIMIT_V1 = "SBL_GROUPED_LIMIT_V1"
    SYL_GROUPED_LIMIT_V1 = "SYL_GROUPED_LIMIT_V1"
    SPC_I_MR_V1 = "SPC_I_MR_V1"
    SPEC_MARGIN_V1 = "SPEC_MARGIN_V1"
    WAFER_ZONE_GEOMETRY_V1 = "WAFER_ZONE_GEOMETRY_V1"
    WAFER_ZONE_GEOMETRY_V2 = "WAFER_ZONE_GEOMETRY_V2"
    BIN_COOCCURRENCE_UNIT_V1 = "BIN_COOCCURRENCE_UNIT_V1"
    PASS_FAIL_DISTRIBUTION_V1 = "PASS_FAIL_DISTRIBUTION_V1"


class MissingValuePolicy(StrEnum):
    EXCLUDE_AND_COUNT = "EXCLUDE_AND_COUNT"
    PAIRWISE_EXCLUDE_AND_COUNT = "PAIRWISE_EXCLUDE_AND_COUNT"
    FAIL_IF_ANY = "FAIL_IF_ANY"


class RetestPolicy(StrEnum):
    EACH_ATTEMPT = "EACH_ATTEMPT"
    LATEST_ATTEMPT = "LATEST_ATTEMPT"
    FIRST_ATTEMPT = "FIRST_ATTEMPT"


class OutlierPolicy(StrEnum):
    MARK_ONLY = "MARK_ONLY"
    EXCLUDE_WITH_AUDIT = "EXCLUDE_WITH_AUDIT"


class SigmaDefinition(StrEnum):
    SAMPLE = "SAMPLE"
    POPULATION = "POPULATION"
    POOLED_WITHIN = "POOLED_WITHIN"


class LimitRoundingPolicy(StrEnum):
    NONE = "NONE"
    FLOOR_TO_STEP = "FLOOR_TO_STEP"
    CEILING_TO_STEP = "CEILING_TO_STEP"


class SpcRunRuleMode(StrEnum):
    NONE = "NONE"
    BASIC = "BASIC"


class CapabilityRiskMetric(StrEnum):
    """Approved metric used to classify a capability result as a risk."""

    CPK = "CPK"
    PPK = "PPK"
    MIN_CPK_PPK = "MIN_CPK_PPK"


class QuadrantYDirection(StrEnum):
    """Approved interpretation of increasing wafer-map Y coordinates."""

    UP = "UP"
    DOWN = "DOWN"


class AnalysisRuleParameters(StrictRuleRequest):
    missing_value_policy: MissingValuePolicy
    retest_policy: RetestPolicy
    outlier_policy: OutlierPolicy
    minimum_sample_size: int = Field(ge=2, le=1_000_000)
    histogram_bin_count: int | None = Field(default=None, ge=5, le=100)
    whisker_multiplier: float | None = Field(default=None, gt=0, le=10)
    sigma_definition: SigmaDefinition | None = None
    subgroup_dimension: str | None = Field(default=None, max_length=64)
    lower_multiplier: float | None = Field(default=None, gt=0, le=100)
    upper_multiplier: float | None = Field(default=None, gt=0, le=100)
    zone_layout_center_x: float | None = Field(default=None, allow_inf_nan=False)
    zone_layout_center_y: float | None = Field(default=None, allow_inf_nan=False)
    zone_layout_radius_die: float | None = Field(
        default=None, gt=0, le=100_000, allow_inf_nan=False
    )
    zone_center_ratio: float | None = Field(
        default=None, gt=0, lt=1, allow_inf_nan=False
    )
    zone_mid_ratio: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    quadrant_axis_rotation_degrees: float | None = Field(
        default=None, ge=0, lt=360, allow_inf_nan=False
    )
    quadrant_y_direction: QuadrantYDirection | None = None
    quadrant_labels_ccw: list[str] | None = Field(
        default=None, min_length=4, max_length=4
    )
    equality_is_in_spec: bool | None = None
    sparse_matrix_minimum_count: int | None = Field(default=None, ge=1, le=1_000_000)
    limit_rounding_policy: LimitRoundingPolicy | None = None
    limit_rounding_step: float | None = Field(
        default=None, gt=0, le=1, allow_inf_nan=False
    )
    spc_run_rule_mode: SpcRunRuleMode | None = None
    spc_consecutive_beyond_count: int | None = Field(default=None, ge=2, le=20)
    spc_consecutive_beyond_sigma: float | None = Field(
        default=None, gt=0, le=10, allow_inf_nan=False
    )
    spc_same_side_run_length: int | None = Field(default=None, ge=2, le=50)
    spc_monotonic_run_length: int | None = Field(default=None, ge=3, le=50)
    capability_risk_metric: CapabilityRiskMetric | None = None
    capability_risk_threshold: float | None = Field(
        default=None, gt=0, le=100, allow_inf_nan=False
    )

    @field_validator("subgroup_dimension")
    @classmethod
    def subgroup_is_a_supported_business_dimension(
        cls, value: str | None
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {
            "DATASET",
            "LOT",
            "WAFER",
            "LOT_WAFER",
            "RUN",
            "TESTER",
            "PROGRAM",
            "CONDITION",
        }:
            raise ValueError("subgroup_dimension is not a supported business dimension")
        return normalized

    @field_validator("quadrant_labels_ccw")
    @classmethod
    def quadrant_labels_are_four_unique_business_labels(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if (
            any(
                not item
                or len(item) > 64
                or any(ord(character) < 32 for character in item)
                for item in normalized
            )
            or len(set(normalized)) != 4
        ):
            raise ValueError(
                "quadrant_labels_ccw must contain four unique bounded labels"
            )
        return normalized


class AnalysisRuleApplicability(StrictRuleRequest):
    test_stages: list[str] = Field(min_length=1, max_length=2)
    supplier_ids: list[int] = Field(default_factory=list, max_length=100)
    product_ids: list[int] = Field(default_factory=list, max_length=100)
    parameter_patterns: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("test_stages")
    @classmethod
    def stages_are_cp_or_ft(cls, value: list[str]) -> list[str]:
        normalized = [item.upper() for item in value]
        if len(normalized) != len(set(normalized)) or not set(normalized).issubset(
            {"CP", "FT"}
        ):
            raise ValueError("rule test stages must be unique CP/FT values")
        return normalized

    @field_validator("supplier_ids", "product_ids")
    @classmethod
    def identities_are_positive_and_unique(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value) or len(value) != len(set(value)):
            raise ValueError("rule scope identities must be positive and unique")
        return value

    @field_validator("parameter_patterns")
    @classmethod
    def patterns_are_bounded(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(
            not item
            or len(item) > 300
            or item == "*"
            or "*" in item[:-1]
            or any(ord(character) < 32 for character in item)
            for item in normalized
        ) or len(normalized) != len(set(normalized)):
            raise ValueError("parameter patterns must be non-empty, bounded and unique")
        return normalized


class CreateAnalysisRuleSetRequest(StrictRuleRequest):
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_name: str = Field(min_length=1, max_length=300)
    evaluation_type: AnalysisRuleType
    business_owner_user_id: int = Field(gt=0)
    technical_owner_user_id: int = Field(gt=0)
    quality_validator_user_id: int = Field(gt=0)
    description: str = Field(min_length=8, max_length=1000)

    @model_validator(mode="after")
    def owners_must_be_separated(self) -> CreateAnalysisRuleSetRequest:
        owners = {
            self.business_owner_user_id,
            self.technical_owner_user_id,
            self.quality_validator_user_id,
        }
        if len(owners) != 3:
            raise ValueError("business, technical and quality owners must be distinct")
        return self


_ALGORITHM_TYPES = {
    AnalysisAlgorithmCode.TUKEY_BOX_V1: AnalysisRuleType.BOX_PLOT,
    AnalysisAlgorithmCode.EQUAL_WIDTH_HISTOGRAM_V1: AnalysisRuleType.HISTOGRAM,
    AnalysisAlgorithmCode.NORMAL_FIT_MLE_V1: AnalysisRuleType.NORMAL_FIT,
    AnalysisAlgorithmCode.PEARSON_PAIRWISE_V1: AnalysisRuleType.CORRELATION,
    AnalysisAlgorithmCode.SPEARMAN_PAIRWISE_V1: AnalysisRuleType.CORRELATION,
    AnalysisAlgorithmCode.CPK_POOLED_WITHIN_RUN_V1: AnalysisRuleType.CPK,
    AnalysisAlgorithmCode.CPK_POOLED_WITHIN_LOT_WAFER_V1: AnalysisRuleType.CPK,
    AnalysisAlgorithmCode.PAT_SHARED_IQR_1_35_V1: AnalysisRuleType.PAT,
    AnalysisAlgorithmCode.SBL_GROUPED_LIMIT_V1: AnalysisRuleType.SBL,
    AnalysisAlgorithmCode.SYL_GROUPED_LIMIT_V1: AnalysisRuleType.SYL,
    AnalysisAlgorithmCode.SPC_I_MR_V1: AnalysisRuleType.SPC,
    AnalysisAlgorithmCode.SPEC_MARGIN_V1: AnalysisRuleType.MARGIN,
    AnalysisAlgorithmCode.WAFER_ZONE_GEOMETRY_V1: AnalysisRuleType.ZONE,
    AnalysisAlgorithmCode.WAFER_ZONE_GEOMETRY_V2: AnalysisRuleType.ZONE,
    AnalysisAlgorithmCode.BIN_COOCCURRENCE_UNIT_V1: AnalysisRuleType.BIN_COOCCURRENCE,
    AnalysisAlgorithmCode.PASS_FAIL_DISTRIBUTION_V1: AnalysisRuleType.PASS_FAIL_DISTRIBUTION,
}


class CreateAnalysisRuleVersionRequest(StrictRuleRequest):
    version_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    implementation_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    algorithm_code: AnalysisAlgorithmCode
    parameters: AnalysisRuleParameters
    applicability: AnalysisRuleApplicability
    algorithm_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    golden_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_from_utc: datetime | None = None
    effective_to_utc: datetime | None = None
    supersedes_rule_version_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def dates_and_algorithm_parameters_are_complete(
        self,
    ) -> CreateAnalysisRuleVersionRequest:
        if (
            self.effective_from_utc is not None
            and self.effective_to_utc is not None
            and self.effective_to_utc <= self.effective_from_utc
        ):
            raise ValueError("rule effective_to must be after effective_from")
        algorithm = self.algorithm_code
        parameters = self.parameters
        if algorithm == AnalysisAlgorithmCode.TUKEY_BOX_V1 and (
            parameters.whisker_multiplier is None
        ):
            raise ValueError("Tukey Box requires whisker_multiplier")
        if algorithm == AnalysisAlgorithmCode.EQUAL_WIDTH_HISTOGRAM_V1 and (
            parameters.histogram_bin_count is None
        ):
            raise ValueError("Histogram requires histogram_bin_count")
        if (
            algorithm
            in {
                AnalysisAlgorithmCode.CPK_POOLED_WITHIN_RUN_V1,
                AnalysisAlgorithmCode.CPK_POOLED_WITHIN_LOT_WAFER_V1,
                AnalysisAlgorithmCode.SPC_I_MR_V1,
            }
            and parameters.sigma_definition is None
        ):
            raise ValueError("Capability/SPC rules require sigma_definition")
        if algorithm in {
            AnalysisAlgorithmCode.CPK_POOLED_WITHIN_RUN_V1,
            AnalysisAlgorithmCode.CPK_POOLED_WITHIN_LOT_WAFER_V1,
        } and (
            parameters.capability_risk_metric is None
            or parameters.capability_risk_threshold is None
        ):
            raise ValueError(
                "Capability rules require an explicit risk metric and threshold"
            )
        if (
            algorithm
            in {
                AnalysisAlgorithmCode.CPK_POOLED_WITHIN_RUN_V1,
                AnalysisAlgorithmCode.CPK_POOLED_WITHIN_LOT_WAFER_V1,
                AnalysisAlgorithmCode.PAT_SHARED_IQR_1_35_V1,
                AnalysisAlgorithmCode.SBL_GROUPED_LIMIT_V1,
                AnalysisAlgorithmCode.SYL_GROUPED_LIMIT_V1,
                AnalysisAlgorithmCode.SPC_I_MR_V1,
                AnalysisAlgorithmCode.SPEC_MARGIN_V1,
                AnalysisAlgorithmCode.BIN_COOCCURRENCE_UNIT_V1,
                AnalysisAlgorithmCode.PASS_FAIL_DISTRIBUTION_V1,
            }
            and parameters.subgroup_dimension is None
        ):
            raise ValueError("quality rules require an explicit subgroup_dimension")
        if algorithm == AnalysisAlgorithmCode.PAT_SHARED_IQR_1_35_V1 and (
            parameters.lower_multiplier is None or parameters.upper_multiplier is None
        ):
            raise ValueError("PAT requires lower_multiplier and upper_multiplier")
        if algorithm == AnalysisAlgorithmCode.PAT_SHARED_IQR_1_35_V1 and (
            parameters.lower_multiplier != 6.0 or parameters.upper_multiplier != 6.0
        ):
            raise ValueError(
                "shared-engine PAT requires lower_multiplier=6 and upper_multiplier=6"
            )
        if (
            algorithm == AnalysisAlgorithmCode.PAT_SHARED_IQR_1_35_V1
            and self.algorithm_sha256 != FORMAL_PAT_ADAPTER_MANIFEST_SHA256
        ):
            raise ValueError(
                "shared-engine PAT algorithm_sha256 must match the frozen Adapter manifest"
            )
        if algorithm == AnalysisAlgorithmCode.SPC_I_MR_V1 and (
            parameters.sigma_definition != SigmaDefinition.POOLED_WITHIN
        ):
            raise ValueError("SPC I-MR requires POOLED_WITHIN sigma_definition")
        if algorithm == AnalysisAlgorithmCode.SPC_I_MR_V1:
            if parameters.spc_run_rule_mode is None:
                raise ValueError("SPC I-MR requires an explicit spc_run_rule_mode")
            basic_values = (
                parameters.spc_consecutive_beyond_count,
                parameters.spc_consecutive_beyond_sigma,
                parameters.spc_same_side_run_length,
                parameters.spc_monotonic_run_length,
            )
            if parameters.spc_run_rule_mode == SpcRunRuleMode.BASIC and any(
                value is None for value in basic_values
            ):
                raise ValueError("SPC BASIC run rules require all versioned thresholds")
            if parameters.spc_run_rule_mode == SpcRunRuleMode.NONE and any(
                value is not None for value in basic_values
            ):
                raise ValueError("SPC NONE run rules cannot carry hidden thresholds")
        if algorithm == AnalysisAlgorithmCode.SBL_GROUPED_LIMIT_V1 and (
            parameters.upper_multiplier is None
            or parameters.sigma_definition != SigmaDefinition.SAMPLE
        ):
            raise ValueError(
                "SBL requires upper_multiplier and SAMPLE sigma_definition"
            )
        if algorithm == AnalysisAlgorithmCode.SYL_GROUPED_LIMIT_V1 and (
            parameters.lower_multiplier is None
            or parameters.sigma_definition != SigmaDefinition.SAMPLE
            or parameters.limit_rounding_policy is None
        ):
            raise ValueError(
                "SYL requires lower_multiplier, SAMPLE sigma and an explicit rounding policy"
            )
        if algorithm == AnalysisAlgorithmCode.SYL_GROUPED_LIMIT_V1:
            if (
                parameters.limit_rounding_policy == LimitRoundingPolicy.NONE
                and parameters.limit_rounding_step is not None
            ):
                raise ValueError("SYL NONE rounding cannot carry a rounding step")
            if (
                parameters.limit_rounding_policy != LimitRoundingPolicy.NONE
                and parameters.limit_rounding_step is None
            ):
                raise ValueError("SYL step rounding requires limit_rounding_step")
        if algorithm == AnalysisAlgorithmCode.PASS_FAIL_DISTRIBUTION_V1 and (
            parameters.histogram_bin_count is None
        ):
            raise ValueError("Pass/Fail distribution requires histogram_bin_count")
        if algorithm in {
            AnalysisAlgorithmCode.WAFER_ZONE_GEOMETRY_V1,
            AnalysisAlgorithmCode.WAFER_ZONE_GEOMETRY_V2,
        }:
            required_zone_values = (
                parameters.zone_layout_center_x,
                parameters.zone_layout_center_y,
                parameters.zone_layout_radius_die,
                parameters.zone_center_ratio,
                parameters.zone_mid_ratio,
            )
            if any(value is None for value in required_zone_values):
                raise ValueError(
                    "Zone rule requires versioned layout center, radius and ratios"
                )
            center_ratio = parameters.zone_center_ratio
            mid_ratio = parameters.zone_mid_ratio
            if (
                center_ratio is not None
                and mid_ratio is not None
                and center_ratio >= mid_ratio
            ):
                raise ValueError("Zone center ratio must be below mid ratio")
        if algorithm == AnalysisAlgorithmCode.WAFER_ZONE_GEOMETRY_V2 and (
            parameters.quadrant_axis_rotation_degrees is None
            or parameters.quadrant_y_direction is None
            or parameters.quadrant_labels_ccw is None
        ):
            raise ValueError(
                "Zone geometry V2 requires explicit quadrant rotation, Y direction and four CCW labels"
            )
        if algorithm == AnalysisAlgorithmCode.SPEC_MARGIN_V1 and (
            parameters.equality_is_in_spec is None
        ):
            raise ValueError("Margin rule requires equality_is_in_spec")
        if algorithm == AnalysisAlgorithmCode.BIN_COOCCURRENCE_UNIT_V1 and (
            parameters.sparse_matrix_minimum_count is None
        ):
            raise ValueError("Bin cooccurrence requires sparse threshold")
        return self

    @property
    def expected_rule_type(self) -> AnalysisRuleType:
        return _ALGORITHM_TYPES[self.algorithm_code]


class RuleApprovalRole(StrEnum):
    BUSINESS = "BUSINESS"
    TECHNICAL = "TECHNICAL"
    QUALITY = "QUALITY"


class RuleApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class DecideAnalysisRuleRequest(StrictRuleRequest):
    approval_role: RuleApprovalRole
    decision: RuleApprovalDecision
    decision_note: str = Field(min_length=8, max_length=1000)
    golden_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def quality_approval_requires_golden(self) -> DecideAnalysisRuleRequest:
        if (
            self.approval_role == RuleApprovalRole.QUALITY
            and self.decision == RuleApprovalDecision.APPROVED
            and self.golden_manifest_sha256 is None
        ):
            raise ValueError("quality approval requires a Golden manifest SHA-256")
        return self


class ActivateAnalysisRuleRequest(StrictRuleRequest):
    confirmation: str = Field(pattern=r"^ACTIVATE$")
    test_stage: str = Field(pattern=r"^(CP|FT)$")
    supplier_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    parameter_pattern: str | None = Field(default=None, max_length=300)
    effective_from_utc: datetime | None = None
    effective_to_utc: datetime | None = None

    @field_validator("parameter_pattern")
    @classmethod
    def parameter_pattern_is_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or normalized == "*"
            or "*" in normalized[:-1]
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError("parameter pattern supports only an optional trailing *")
        return normalized

    @model_validator(mode="after")
    def dates_are_ordered(self) -> ActivateAnalysisRuleRequest:
        if (
            self.effective_from_utc is not None
            and self.effective_to_utc is not None
            and self.effective_to_utc <= self.effective_from_utc
        ):
            raise ValueError("activation effective_to must be after effective_from")
        return self


@dataclass(frozen=True, slots=True)
class AnalysisRuleSetRecord:
    evaluation_rule_set_id: int
    rule_code: str
    rule_name: str
    evaluation_type: str
    business_owner_user_id: int
    technical_owner_user_id: int
    quality_validator_user_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class AnalysisRuleVersionRecord:
    evaluation_rule_version_id: int
    evaluation_rule_set_id: int
    rule_code: str
    version_code: str
    implementation_version: str
    status: str
    activation_status: str
    algorithm_code: str
    approvals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisRuleActivationRecord:
    rule_activation_id: int
    evaluation_rule_version_id: int
    test_stage: str
    supplier_id: int | None
    product_id: int | None
    parameter_pattern: str | None
    active: bool
