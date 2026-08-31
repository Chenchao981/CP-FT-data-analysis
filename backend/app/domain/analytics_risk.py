from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from app.domain.analytics import (
    AnalyticsContextRequest,
    AnalyticsFilterSummary,
    StrictAnalyticsRequest,
)
from app.domain.datasets import (
    DatasetCapabilityMethod,
    DatasetMeasurementAggregateContext,
)
from app.domain.quality_evaluation import (
    QualityAnalysisType,
    QualityBinType,
    QualityGroupBy,
    QualityRuleReference,
    SpcOrderField,
    SpcPhase,
)


class AnalyticsRiskAnalysis(StrEnum):
    CAPABILITY = "CAPABILITY"
    PAT_ROBUST_IQR = "PAT_ROBUST_IQR"
    SPC_I_MR = "SPC_I_MR"
    MARGIN_OOS = "MARGIN_OOS"
    SBL_GROUPED_LIMIT = "SBL_GROUPED_LIMIT"
    SYL_GROUPED_LIMIT = "SYL_GROUPED_LIMIT"


class AnalyticsRiskEvaluationConfig(StrictAnalyticsRequest):
    analysis: AnalyticsRiskAnalysis
    rule: QualityRuleReference
    parameter: str | None = Field(default=None, max_length=200)
    group_by: QualityGroupBy | None = None
    capability_method: DatasetCapabilityMethod | None = None
    spc_order: SpcOrderField | None = None
    spc_phase: SpcPhase | None = None
    bin_type: QualityBinType | None = None

    @field_validator("parameter")
    @classmethod
    def parameter_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("risk parameter must be non-empty")
        return value

    @model_validator(mode="after")
    def inputs_are_explicit_and_method_specific(self) -> AnalyticsRiskEvaluationConfig:
        parameter_methods = {
            AnalyticsRiskAnalysis.CAPABILITY,
            AnalyticsRiskAnalysis.PAT_ROBUST_IQR,
            AnalyticsRiskAnalysis.SPC_I_MR,
            AnalyticsRiskAnalysis.MARGIN_OOS,
        }
        if (self.analysis in parameter_methods) != (self.parameter is not None):
            raise ValueError("selected risk method has an invalid parameter contract")
        if self.analysis == AnalyticsRiskAnalysis.CAPABILITY:
            if self.capability_method is None or self.group_by is not None:
                raise ValueError(
                    "Capability risk requires one method and uses its approved subgroup policy"
                )
        elif self.capability_method is not None:
            raise ValueError("capability_method is only accepted for Capability risk")
        if self.analysis != AnalyticsRiskAnalysis.CAPABILITY and self.group_by is None:
            raise ValueError("quality risk methods require an explicit group_by")
        if self.analysis == AnalyticsRiskAnalysis.SPC_I_MR:
            if self.spc_order is None or self.spc_phase is None:
                raise ValueError("SPC risk requires explicit order and Phase-I scope")
        elif self.spc_order is not None or self.spc_phase is not None:
            raise ValueError("SPC order and phase are only accepted for SPC risk")
        if self.analysis == AnalyticsRiskAnalysis.SBL_GROUPED_LIMIT:
            if (
                self.bin_type is None
                or self.bin_type == QualityBinType.ALL_MAPPED_FAILURE
            ):
                raise ValueError("SBL risk requires one explicit physical Bin type")
            if self.group_by == QualityGroupBy.CONDITION:
                raise ValueError("SBL risk cannot infer a Bin test condition")
        elif self.bin_type is not None:
            raise ValueError("bin_type is only accepted for SBL risk")
        if (
            self.analysis == AnalyticsRiskAnalysis.SYL_GROUPED_LIMIT
            and self.group_by == QualityGroupBy.CONDITION
        ):
            raise ValueError("SYL risk cannot infer a measurement test condition")
        return self


class AnalyticsInstantRiskRequest(AnalyticsContextRequest):
    evaluations: list[AnalyticsRiskEvaluationConfig] = Field(min_length=1, max_length=6)

    @field_validator("evaluations")
    @classmethod
    def methods_are_unique(
        cls, value: list[AnalyticsRiskEvaluationConfig]
    ) -> list[AnalyticsRiskEvaluationConfig]:
        methods = [item.analysis for item in value]
        if len(methods) != len(set(methods)):
            raise ValueError("each instant-risk method may appear only once")
        return value


@dataclass(frozen=True, slots=True)
class AnalyticsRiskRuleProvenance:
    rule_code: str
    version_code: str
    algorithm_code: str
    approval_status: str
    activation_status: str
    parameters_sha256: str


@dataclass(frozen=True, slots=True)
class AnalyticsEvaluatedRiskItem:
    code: str
    analysis: str
    category: str
    severity: str
    status: str
    reason_code: str | None
    title: str
    message: str
    dataset_id: int
    version_no: int
    group_key: str
    parameter: str | None
    metric_code: str
    metric_value: float | None
    threshold_operator: str | None
    threshold_value: float | None
    affected_count: int
    denominator_count: int
    rate: float | None
    evidence_drilldown_keys: tuple[str, ...]
    evidence_truncated: bool
    rule: AnalyticsRiskRuleProvenance
    aggregate_drilldown_context: DatasetMeasurementAggregateContext | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsInstantRiskResult:
    contract_version: str
    filter_summary: AnalyticsFilterSummary
    calculation_context_hash: str
    requested_analyses: tuple[str, ...]
    items: tuple[AnalyticsEvaluatedRiskItem, ...]
    warnings: tuple[str, ...]
    computed_at: str


QUALITY_RISK_ANALYSES = {
    AnalyticsRiskAnalysis.PAT_ROBUST_IQR: QualityAnalysisType.PAT_ROBUST_IQR,
    AnalyticsRiskAnalysis.SPC_I_MR: QualityAnalysisType.SPC_I_MR,
    AnalyticsRiskAnalysis.MARGIN_OOS: QualityAnalysisType.MARGIN_OOS,
    AnalyticsRiskAnalysis.SBL_GROUPED_LIMIT: QualityAnalysisType.SBL_GROUPED_LIMIT,
    AnalyticsRiskAnalysis.SYL_GROUPED_LIMIT: QualityAnalysisType.SYL_GROUPED_LIMIT,
}
