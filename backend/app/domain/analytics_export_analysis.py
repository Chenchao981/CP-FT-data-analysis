from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.domain.analysis_rule_pinning import (
    AnalysisRuleRequirement,
    deduplicate_analysis_rule_requirements,
    quality_rule_algorithm,
)
from app.domain.analytics import StrictAnalyticsRequest
from app.domain.analytics_risk import (
    AnalyticsRiskAnalysis,
    AnalyticsRiskEvaluationConfig,
)
from app.domain.datasets import (
    DatasetAnalysisGroupBy,
    DatasetAnalysisRuleReference,
    DatasetCapabilityConfig,
    DatasetHistogramConfig,
    DatasetParameterAnalysisType,
)
from app.domain.parameter_relationship import (
    ParameterCorrelationConfig,
    ParameterRelationshipAnalysis,
    ParameterRelationshipGroupBy,
)
from app.domain.quality_evaluation import (
    QualityAnalysisType,
    QualityBinType,
    QualityGroupBy,
    QualityRuleReference,
    SpcOrderField,
    SpcPhase,
)
from app.domain.spatial_analysis import SpatialAnalysisMode
from app.domain.wafer_summary import WaferSummarySort, WaferSummarySortDirection

ANALYTICS_EXPORT_ANALYSIS_CONFIG_VERSION = "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1"


class AnalyticsExportAnalysisSection(StrEnum):
    OVERVIEW = "OVERVIEW"
    PARAMETER_ANALYSIS = "PARAMETER_ANALYSIS"
    PARAMETER_RELATIONSHIP = "PARAMETER_RELATIONSHIP"
    SPATIAL_ANALYSIS = "SPATIAL_ANALYSIS"
    FT_QUALITY = "FT_QUALITY"
    WAFER_SUMMARY = "WAFER_SUMMARY"


class OverviewAnalysisExportConfig(StrictAnalyticsRequest):
    evaluations: list[AnalyticsRiskEvaluationConfig] = Field(
        default_factory=list, max_length=6
    )

    @field_validator("evaluations")
    @classmethod
    def evaluations_are_unique(
        cls, value: list[AnalyticsRiskEvaluationConfig]
    ) -> list[AnalyticsRiskEvaluationConfig]:
        methods = [item.analysis for item in value]
        if len(methods) != len(set(methods)):
            raise ValueError("each Overview instant-risk method may appear only once")
        return value


class ParameterAnalysisExportConfig(StrictAnalyticsRequest):
    parameters: list[str] = Field(min_length=1, max_length=5)
    group_by: DatasetAnalysisGroupBy = DatasetAnalysisGroupBy.DATASET
    analyses: list[DatasetParameterAnalysisType] = Field(min_length=1, max_length=5)
    box_plot: DatasetAnalysisRuleReference = Field(
        default_factory=DatasetAnalysisRuleReference
    )
    histogram: DatasetHistogramConfig = Field(default_factory=DatasetHistogramConfig)
    normal_fit: DatasetAnalysisRuleReference = Field(
        default_factory=DatasetAnalysisRuleReference
    )
    capability: DatasetCapabilityConfig = Field(default_factory=DatasetCapabilityConfig)

    @field_validator("parameters")
    @classmethod
    def parameters_are_unique(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("export analysis parameters must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("export analysis parameters must be unique")
        return normalized

    @field_validator("analyses")
    @classmethod
    def analyses_are_unique(
        cls, value: list[DatasetParameterAnalysisType]
    ) -> list[DatasetParameterAnalysisType]:
        if len(value) != len(set(value)):
            raise ValueError("export parameter analyses must be unique")
        return value

    @model_validator(mode="after")
    def rules_match_analyses(self) -> ParameterAnalysisExportConfig:
        selected = set(self.analyses)
        references = {
            DatasetParameterAnalysisType.BOX_PLOT: self.box_plot.rule_code,
            DatasetParameterAnalysisType.HISTOGRAM: self.histogram.rule_code,
            DatasetParameterAnalysisType.NORMAL_FIT: self.normal_fit.rule_code,
            DatasetParameterAnalysisType.CAPABILITY: self.capability.rule_code,
        }
        for analysis, rule_code in references.items():
            if analysis in selected and rule_code is None:
                raise ValueError(
                    f"{analysis.value} export requires an exact approved rule version"
                )
            if analysis not in selected and rule_code is not None:
                raise ValueError(
                    f"{analysis.value} export rule requires that analysis type"
                )
        return self


class ParameterRelationshipExportConfig(StrictAnalyticsRequest):
    x_parameter: str = Field(min_length=1, max_length=200)
    y_parameters: list[str] = Field(min_length=1, max_length=5)
    analyses: list[ParameterRelationshipAnalysis] = Field(min_length=1, max_length=3)
    group_by: ParameterRelationshipGroupBy = ParameterRelationshipGroupBy.DATASET
    max_points: int = Field(default=10_000, ge=100, le=20_000)
    correlation: ParameterCorrelationConfig = Field(
        default_factory=ParameterCorrelationConfig
    )

    @field_validator("y_parameters")
    @classmethod
    def y_parameters_are_unique(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("export relationship parameters must be bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("export relationship parameters must be unique")
        return normalized

    @field_validator("analyses")
    @classmethod
    def analyses_are_unique(
        cls, value: list[ParameterRelationshipAnalysis]
    ) -> list[ParameterRelationshipAnalysis]:
        if len(value) != len(set(value)):
            raise ValueError("export relationship analyses must be unique")
        return value

    @model_validator(mode="after")
    def relationship_is_complete(self) -> ParameterRelationshipExportConfig:
        if self.x_parameter in self.y_parameters:
            raise ValueError("export relationship X and Y parameters must differ")
        selected = set(self.analyses)
        correlation_selected = ParameterRelationshipAnalysis.CORRELATION in selected
        if correlation_selected != (self.correlation.rule_code is not None):
            raise ValueError(
                "export correlation requires an exact method and approved rule version"
            )
        return self


class SpatialAnalysisExportConfig(StrictAnalyticsRequest):
    mode: SpatialAnalysisMode
    parameter: str | None = Field(default=None, min_length=1, max_length=200)
    focus_dataset_id: int | None = Field(default=None, gt=0)
    max_points: int = Field(default=20_000, ge=100, le=50_000)
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_version: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @model_validator(mode="after")
    def spatial_is_complete(self) -> SpatialAnalysisExportConfig:
        if (
            self.mode
            in {
                SpatialAnalysisMode.PARAMETER_HEATMAP,
                SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
            }
            and self.parameter is None
        ):
            raise ValueError("parameter spatial export requires one exact parameter")
        if (
            self.mode
            in {
                SpatialAnalysisMode.BIN_MAP,
                SpatialAnalysisMode.COMPOSITE_FAILURE,
            }
            and self.parameter is not None
        ):
            raise ValueError("selected spatial export mode does not accept a parameter")
        if self.mode == SpatialAnalysisMode.ZONE_COMPARISON:
            if self.rule_code is None or self.rule_version is None:
                raise ValueError(
                    "zone comparison export requires an exact approved rule version"
                )
        elif self.rule_code is not None or self.rule_version is not None:
            raise ValueError("spatial export rule is only valid for zone comparison")
        return self


class QualityAnalysisExportConfig(StrictAnalyticsRequest):
    analysis: QualityAnalysisType
    parameter: str | None = Field(default=None, min_length=1, max_length=200)
    rule: QualityRuleReference
    group_by: QualityGroupBy
    spc_order: SpcOrderField | None = None
    spc_phase: SpcPhase | None = None
    bin_type: QualityBinType | None = None

    @model_validator(mode="after")
    def quality_is_complete(self) -> QualityAnalysisExportConfig:
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
        if (self.analysis in parameter_methods) != (self.parameter is not None):
            raise ValueError(
                "selected quality export requires exactly its declared parameter"
            )
        if self.analysis in bin_methods:
            if self.bin_type is None:
                raise ValueError("bin quality export requires an exact Bin type")
            if self.group_by == QualityGroupBy.CONDITION:
                raise ValueError("bin quality export cannot infer a test condition")
        elif self.bin_type is not None:
            raise ValueError("Bin type is valid only for bin quality export")
        if self.analysis == QualityAnalysisType.SPC_I_MR:
            if self.spc_order is None or self.spc_phase is None:
                raise ValueError("SPC export requires exact order and phase")
        elif self.spc_order is not None or self.spc_phase is not None:
            raise ValueError("SPC order and phase are valid only for SPC export")
        if (
            self.analysis == QualityAnalysisType.SYL_GROUPED_LIMIT
            and self.group_by == QualityGroupBy.CONDITION
        ):
            raise ValueError("SYL export cannot infer a test condition")
        if (
            self.analysis == QualityAnalysisType.SBL_GROUPED_LIMIT
            and self.bin_type == QualityBinType.ALL_MAPPED_FAILURE
        ):
            raise ValueError("SBL export requires one physical Bin type")
        return self


class WaferSummaryExportConfig(StrictAnalyticsRequest):
    sort_by: WaferSummarySort = WaferSummarySort.DATASET
    sort_direction: WaferSummarySortDirection = WaferSummarySortDirection.ASC


class AnalyticsExportAnalysisConfig(StrictAnalyticsRequest):
    contract_version: Literal[ANALYTICS_EXPORT_ANALYSIS_CONFIG_VERSION] = (
        ANALYTICS_EXPORT_ANALYSIS_CONFIG_VERSION
    )
    section: AnalyticsExportAnalysisSection
    overview: OverviewAnalysisExportConfig | None = None
    parameter_analysis: ParameterAnalysisExportConfig | None = None
    parameter_relationship: ParameterRelationshipExportConfig | None = None
    spatial_analysis: SpatialAnalysisExportConfig | None = None
    ft_quality: QualityAnalysisExportConfig | None = None
    wafer_summary: WaferSummaryExportConfig | None = None

    @model_validator(mode="after")
    def section_has_exact_payload(self) -> AnalyticsExportAnalysisConfig:
        expected_field = {
            AnalyticsExportAnalysisSection.OVERVIEW: "overview",
            AnalyticsExportAnalysisSection.PARAMETER_ANALYSIS: "parameter_analysis",
            AnalyticsExportAnalysisSection.PARAMETER_RELATIONSHIP: "parameter_relationship",
            AnalyticsExportAnalysisSection.SPATIAL_ANALYSIS: "spatial_analysis",
            AnalyticsExportAnalysisSection.FT_QUALITY: "ft_quality",
            AnalyticsExportAnalysisSection.WAFER_SUMMARY: "wafer_summary",
        }[self.section]
        supplied = {
            name
            for name in (
                "overview",
                "parameter_analysis",
                "parameter_relationship",
                "spatial_analysis",
                "ft_quality",
                "wafer_summary",
            )
            if getattr(self, name) is not None
        }
        expected = {expected_field} if expected_field is not None else set()
        if supplied != expected:
            raise ValueError(
                "export analysis section must carry exactly its own payload"
            )
        return self


_TEMPLATE_SECTION = {
    "ANALYTICS_OVERVIEW": AnalyticsExportAnalysisSection.OVERVIEW,
    "PARAMETER_ANALYSIS": AnalyticsExportAnalysisSection.PARAMETER_ANALYSIS,
    "PARAMETER_RELATIONSHIP": AnalyticsExportAnalysisSection.PARAMETER_RELATIONSHIP,
    "SPATIAL_ANALYSIS": AnalyticsExportAnalysisSection.SPATIAL_ANALYSIS,
    "FT_QUALITY": AnalyticsExportAnalysisSection.FT_QUALITY,
    "WAFER_SUMMARY": AnalyticsExportAnalysisSection.WAFER_SUMMARY,
}


def resolve_analytics_export_analysis_config(
    template_code: str, chart_config: dict[str, Any]
) -> AnalyticsExportAnalysisConfig | None:
    """Parse the bounded, versioned report reconstruction contract.

    Data extracts do not need an analysis request. Every report template must
    carry one exact analysis payload; the current UI tab is deliberately not
    treated as the analysis identity.
    """

    expected = _TEMPLATE_SECTION.get(template_code)
    if expected is None:
        return None
    raw = chart_config.get("analysis")
    if raw is None:
        raise ValueError("report export requires chart_config.analysis")
    config = AnalyticsExportAnalysisConfig.model_validate(raw)
    if config.section != expected:
        raise ValueError("report export analysis section does not match template")
    return config


def analytics_export_analysis_parameters(
    config: AnalyticsExportAnalysisConfig | None,
) -> tuple[str, ...]:
    if config is None:
        return ()
    if config.overview is not None:
        return tuple(
            evaluation.parameter
            for evaluation in config.overview.evaluations
            if evaluation.parameter is not None
        )
    if config.parameter_analysis is not None:
        return tuple(config.parameter_analysis.parameters)
    if config.parameter_relationship is not None:
        return (
            config.parameter_relationship.x_parameter,
            *config.parameter_relationship.y_parameters,
        )
    if config.spatial_analysis is not None and config.spatial_analysis.parameter:
        return (config.spatial_analysis.parameter,)
    if config.ft_quality is not None and config.ft_quality.parameter:
        return (config.ft_quality.parameter,)
    return ()


def analytics_export_required_rules(
    config: AnalyticsExportAnalysisConfig | None,
) -> tuple[AnalysisRuleRequirement, ...]:
    """Extract exact rule identities and every parameter scope from typed config."""

    if config is None:
        return ()
    required: list[AnalysisRuleRequirement] = []

    def add(
        rule_code: str | None,
        version_code: str | None,
        algorithm_code: str | None,
        parameters: tuple[str | None, ...],
    ) -> None:
        if rule_code is None and version_code is None and algorithm_code is None:
            return
        if rule_code is None or version_code is None or algorithm_code is None:
            raise ValueError("export analysis has an incomplete exact rule reference")
        normalized_parameters = tuple(dict.fromkeys(parameters or (None,)))
        required.append(
            AnalysisRuleRequirement(
                rule_code, version_code, algorithm_code, normalized_parameters
            )
        )

    if config.overview is not None:
        for evaluation in config.overview.evaluations:
            if evaluation.analysis == AnalyticsRiskAnalysis.CAPABILITY:
                add(
                    evaluation.rule.rule_code,
                    evaluation.rule.version_code,
                    evaluation.capability_method.value
                    if evaluation.capability_method is not None
                    else None,
                    (evaluation.parameter,),
                )
            else:
                quality_type = QualityAnalysisType(evaluation.analysis.value)
                parameter = (
                    evaluation.bin_type.value
                    if evaluation.analysis == AnalyticsRiskAnalysis.SBL_GROUPED_LIMIT
                    and evaluation.bin_type is not None
                    else evaluation.parameter
                )
                add(
                    evaluation.rule.rule_code,
                    evaluation.rule.version_code,
                    quality_rule_algorithm(quality_type),
                    (parameter,),
                )
    elif config.parameter_analysis is not None:
        selected = set(config.parameter_analysis.analyses)
        parameter_scopes = tuple(config.parameter_analysis.parameters)
        if DatasetParameterAnalysisType.BOX_PLOT in selected:
            add(
                config.parameter_analysis.box_plot.rule_code,
                config.parameter_analysis.box_plot.version_code,
                "TUKEY_BOX_V1",
                parameter_scopes,
            )
        if DatasetParameterAnalysisType.HISTOGRAM in selected:
            add(
                config.parameter_analysis.histogram.rule_code,
                config.parameter_analysis.histogram.version_code,
                "EQUAL_WIDTH_HISTOGRAM_V1",
                parameter_scopes,
            )
        if DatasetParameterAnalysisType.NORMAL_FIT in selected:
            add(
                config.parameter_analysis.normal_fit.rule_code,
                config.parameter_analysis.normal_fit.version_code,
                "NORMAL_FIT_MLE_V1",
                parameter_scopes,
            )
        if DatasetParameterAnalysisType.CAPABILITY in selected:
            method = config.parameter_analysis.capability.method
            add(
                config.parameter_analysis.capability.rule_code,
                config.parameter_analysis.capability.version_code,
                method.value if method is not None else None,
                parameter_scopes,
            )
    elif config.parameter_relationship is not None:
        if ParameterRelationshipAnalysis.CORRELATION in set(
            config.parameter_relationship.analyses
        ):
            correlation = config.parameter_relationship.correlation
            add(
                correlation.rule_code,
                correlation.version_code,
                correlation.method.value if correlation.method is not None else None,
                (
                    config.parameter_relationship.x_parameter,
                    *config.parameter_relationship.y_parameters,
                ),
            )
    elif config.spatial_analysis is not None:
        if config.spatial_analysis.mode == SpatialAnalysisMode.ZONE_COMPARISON:
            add(
                config.spatial_analysis.rule_code,
                config.spatial_analysis.rule_version,
                "WAFER_ZONE_GEOMETRY_V2",
                (config.spatial_analysis.parameter,),
            )
    elif config.ft_quality is not None:
        parameter = (
            config.ft_quality.bin_type.value
            if config.ft_quality.analysis
            in {
                QualityAnalysisType.BIN_COOCCURRENCE,
                QualityAnalysisType.SBL_GROUPED_LIMIT,
            }
            and config.ft_quality.bin_type is not None
            else config.ft_quality.parameter
        )
        add(
            config.ft_quality.rule.rule_code,
            config.ft_quality.rule.version_code,
            quality_rule_algorithm(config.ft_quality.analysis),
            (parameter,),
        )

    return deduplicate_analysis_rule_requirements(required)
