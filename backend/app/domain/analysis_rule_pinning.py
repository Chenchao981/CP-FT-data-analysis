from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.datasets import (
    DatasetCapabilityMethod,
    DatasetParameterAnalysisType,
)
from app.domain.parameter_relationship import (
    ParameterCorrelationMethod,
    ParameterRelationshipAnalysis,
    ParameterRelationshipGroupBy,
)
from app.domain.quality_evaluation import (
    QualityAnalysisType,
    QualityBinType,
    QualityGroupBy,
    SpcOrderField,
    SpcPhase,
)
from app.domain.spatial_analysis import SpatialAnalysisMode

ANALYSIS_VIEW_STATE_CONTRACT_VERSION = "ANALYSIS_VIEW_STATE_V1"

_RULE_CODE_PATTERN = r"^(?:|[A-Z][A-Z0-9_]{2,127})$"
_RULE_VERSION_PATTERN = r"^(?:|[A-Za-z0-9][A-Za-z0-9._-]{0,63})$"

_QUALITY_ALGORITHMS = {
    QualityAnalysisType.PAT_ROBUST_IQR: "PAT_SHARED_IQR_1_35_V1",
    QualityAnalysisType.SPC_I_MR: "SPC_I_MR_V1",
    QualityAnalysisType.MARGIN_OOS: "SPEC_MARGIN_V1",
    QualityAnalysisType.BIN_COOCCURRENCE: "BIN_COOCCURRENCE_UNIT_V1",
    QualityAnalysisType.SBL_GROUPED_LIMIT: "SBL_GROUPED_LIMIT_V1",
    QualityAnalysisType.SYL_GROUPED_LIMIT: "SYL_GROUPED_LIMIT_V1",
    QualityAnalysisType.PASS_FAIL_DISTRIBUTION: "PASS_FAIL_DISTRIBUTION_V1",
}


def quality_rule_algorithm(analysis: QualityAnalysisType) -> str:
    return _QUALITY_ALGORITHMS[analysis]


@dataclass(frozen=True, slots=True)
class AnalysisRuleRequirement:
    rule_code: str
    version_code: str
    algorithm_code: str
    parameters: tuple[str | None, ...]

    @property
    def identity(self) -> str:
        return f"RULE:{self.rule_code}:{self.version_code}"


class _StrictViewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=False
    )


class _ExactRule(_StrictViewModel):
    rule_code: str = Field(alias="ruleCode", pattern=_RULE_CODE_PATTERN)
    version_code: str = Field(alias="versionCode", pattern=_RULE_VERSION_PATTERN)

    @model_validator(mode="after")
    def exact_reference_is_paired(self) -> _ExactRule:
        if bool(self.rule_code) != bool(self.version_code):
            raise ValueError("ruleCode and versionCode must be supplied together")
        return self


class _CapabilityRule(_ExactRule):
    method: DatasetCapabilityMethod


class _OverviewSblRule(_ExactRule):
    bin_type: Literal[
        QualityBinType.CP_BIN,
        QualityBinType.SOFT_BIN,
        QualityBinType.HARD_BIN,
    ] = Field(alias="binType")


class _OverviewRiskView(_StrictViewModel):
    analyses: list[
        Literal[
            "CAPABILITY",
            "PAT_ROBUST_IQR",
            "SPC_I_MR",
            "MARGIN_OOS",
            "SBL_GROUPED_LIMIT",
            "SYL_GROUPED_LIMIT",
        ]
    ] = Field(max_length=6)
    parameter: str = Field(max_length=200)
    group_by: QualityGroupBy = Field(alias="groupBy")
    capability: _CapabilityRule
    pat: _ExactRule
    spc: _ExactRule
    margin: _ExactRule
    sbl: _OverviewSblRule
    syl: _ExactRule

    @model_validator(mode="after")
    def selected_risk_contract_is_complete(self) -> _OverviewRiskView:
        if len(self.analyses) != len(set(self.analyses)):
            raise ValueError("Overview risk analyses must be unique")
        parameter_methods = {
            "CAPABILITY",
            "PAT_ROBUST_IQR",
            "SPC_I_MR",
            "MARGIN_OOS",
        }
        if parameter_methods.intersection(self.analyses) and not self.parameter:
            raise ValueError("selected Overview risk method requires a parameter")
        if self.group_by == QualityGroupBy.CONDITION and {
            "SBL_GROUPED_LIMIT",
            "SYL_GROUPED_LIMIT",
        }.intersection(self.analyses):
            raise ValueError("SBL/SYL risk cannot infer a test condition")
        selected_refs = {
            "CAPABILITY": self.capability,
            "PAT_ROBUST_IQR": self.pat,
            "SPC_I_MR": self.spc,
            "MARGIN_OOS": self.margin,
            "SBL_GROUPED_LIMIT": self.sbl,
            "SYL_GROUPED_LIMIT": self.syl,
        }
        if any(not selected_refs[item].rule_code for item in self.analyses):
            raise ValueError("selected Overview risk method requires an exact rule")
        return self


class _DetailEvaluationFilter(_StrictViewModel):
    evaluation_type: Literal["SPEC", "PAT", "SBL", "SAFE_LAUNCH", "OTHER"]
    evaluation_results: list[
        Literal[
            "PASS",
            "FAIL",
            "NOT_EVALUATED",
            "NO_MATCH",
            "CONFIG_AMBIGUOUS",
            "INVALID_VALUE",
        ]
    ] = Field(min_length=1, max_length=6)
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_version: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @model_validator(mode="after")
    def identity_and_results_are_exact(self) -> _DetailEvaluationFilter:
        if len(self.evaluation_results) != len(set(self.evaluation_results)):
            raise ValueError("Detail evaluation results must be unique")
        if (self.rule_code is None) != (self.rule_version is None):
            raise ValueError(
                "Detail evaluation Rule identity must be supplied together"
            )
        return self


class _DetailMeasurementFilter(_StrictViewModel):
    parameter: str = Field(min_length=1, max_length=200)
    lower_bound: float | None = None
    upper_bound: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @model_validator(mode="after")
    def bounds_are_finite_and_ordered(self) -> _DetailMeasurementFilter:
        if any(
            value is not None and not math.isfinite(value)
            for value in (self.lower_bound, self.upper_bound)
        ):
            raise ValueError("Detail measurement bounds must be finite")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("Detail measurement lower bound exceeds upper bound")
        return self


class _DetailView(_StrictViewModel):
    view: Literal["WIDE", "LONG"]
    sort_by: Literal[
        "UNIT_SEQUENCE",
        "LOT",
        "WAFER",
        "SOURCE_ROW",
        "RESULT",
        "SOFT_BIN",
        "HARD_BIN",
    ] = Field(alias="sortBy")
    sort_direction: Literal["ASC", "DESC"] = Field(alias="sortDirection")
    evaluation_filter: _DetailEvaluationFilter | None = None
    measurement_filter: _DetailMeasurementFilter | None = None


class _ParameterAnalysisView(_StrictViewModel):
    group_by: Literal["DATASET"] = Field(alias="groupBy")
    analyses: list[DatasetParameterAnalysisType] = Field(min_length=1, max_length=5)
    box_plot: _ExactRule = Field(alias="boxPlot")
    histogram: _ExactRule
    normal_fit: _ExactRule = Field(alias="normalFit")
    capability: _CapabilityRule
    box_parameter: str = Field(alias="boxParameter", max_length=200)
    histogram_dataset: str = Field(alias="histogramDataset", max_length=512)
    histogram_parameter: str = Field(alias="histogramParameter", max_length=200)
    normal_fit_dataset: str = Field(alias="normalFitDataset", max_length=512)
    normal_fit_parameter: str = Field(alias="normalFitParameter", max_length=200)

    @model_validator(mode="after")
    def selected_parameter_rules_are_complete(self) -> _ParameterAnalysisView:
        if len(self.analyses) != len(set(self.analyses)):
            raise ValueError("parameter analyses must be unique")
        selected_refs = {
            DatasetParameterAnalysisType.BOX_PLOT: self.box_plot,
            DatasetParameterAnalysisType.HISTOGRAM: self.histogram,
            DatasetParameterAnalysisType.NORMAL_FIT: self.normal_fit,
            DatasetParameterAnalysisType.CAPABILITY: self.capability,
        }
        if any(
            analysis in self.analyses and not reference.rule_code
            for analysis, reference in selected_refs.items()
        ):
            raise ValueError("selected parameter analysis requires an exact rule")
        return self


class _CorrelationRule(_ExactRule):
    method: ParameterCorrelationMethod


class _ParameterRelationshipView(_StrictViewModel):
    x_parameter: str = Field(alias="xParameter", max_length=200)
    y_parameters: list[str] = Field(alias="yParameters", max_length=5)
    analyses: list[ParameterRelationshipAnalysis] = Field(min_length=1, max_length=3)
    group_by: ParameterRelationshipGroupBy = Field(alias="groupBy")
    max_points: int = Field(alias="maxPoints", ge=100, le=20_000)
    correlation: _CorrelationRule
    scatter_y: str = Field(alias="scatterY", max_length=200)
    scatter_dataset: str = Field(alias="scatterDataset", max_length=512)
    trend_parameter: str = Field(alias="trendParameter", max_length=200)
    correlation_scope: str = Field(alias="correlationScope", max_length=512)
    display_groups: list[str] = Field(alias="displayGroups", max_length=50)
    point_visibility: list[Literal["IN_SPEC", "OUT_OF_SPEC"]] = Field(
        alias="pointVisibility", max_length=2
    )

    @model_validator(mode="after")
    def selected_relationship_rule_is_complete(self) -> _ParameterRelationshipView:
        if len(self.analyses) != len(set(self.analyses)):
            raise ValueError("relationship analyses must be unique")
        if ParameterRelationshipAnalysis.CORRELATION in self.analyses and (
            not self.x_parameter
            or not self.y_parameters
            or not self.correlation.rule_code
        ):
            raise ValueError(
                "selected correlation requires X/Y parameters and an exact rule"
            )
        return self


class _SpatialView(_StrictViewModel):
    mode: SpatialAnalysisMode
    parameter: str = Field(max_length=200)
    max_points: int = Field(alias="maxPoints", ge=100, le=50_000)
    rule: _ExactRule
    color_scale: Literal["ROBUST", "FULL"] = Field(alias="colorScale")
    symbol_size: Literal[8, 12, 18] = Field(alias="symbolSize")
    show_missing: bool = Field(alias="showMissing")

    @model_validator(mode="after")
    def selected_spatial_rule_is_complete(self) -> _SpatialView:
        if self.mode == SpatialAnalysisMode.ZONE_COMPARISON and not self.rule.rule_code:
            raise ValueError("zone comparison requires an exact rule")
        return self


class _QualityView(_StrictViewModel):
    analysis: QualityAnalysisType | None
    parameter: str = Field(max_length=200)
    group_by: QualityGroupBy | None = Field(alias="groupBy")
    rule: _ExactRule
    spc_order: SpcOrderField | None = Field(alias="spcOrder")
    spc_phase: SpcPhase | None = Field(alias="spcPhase")
    bin_type: QualityBinType | None = Field(alias="binType")
    spc_display_group: str = Field(alias="spcDisplayGroup", max_length=512)
    distribution_display_group: str = Field(
        alias="distributionDisplayGroup", max_length=512
    )
    margin_display_group: str = Field(alias="marginDisplayGroup", max_length=512)
    cooccurrence_display_group: str = Field(
        alias="cooccurrenceDisplayGroup", max_length=512
    )
    sbl_display_bin: str = Field(alias="sblDisplayBin", max_length=512)
    syl_display_dataset: str = Field(alias="sylDisplayDataset", max_length=512)
    percent_axis_mode: Literal["AUTO", "FIXED_0_100"] = Field(alias="percentAxisMode")

    @model_validator(mode="after")
    def selected_quality_rule_is_complete(self) -> _QualityView:
        if self.analysis is None:
            return self
        if self.group_by is None or not self.rule.rule_code:
            raise ValueError(
                "selected quality analysis requires groupBy and exact rule"
            )
        parameter_methods = {
            QualityAnalysisType.PAT_ROBUST_IQR,
            QualityAnalysisType.SPC_I_MR,
            QualityAnalysisType.MARGIN_OOS,
            QualityAnalysisType.PASS_FAIL_DISTRIBUTION,
        }
        if self.analysis in parameter_methods and not self.parameter:
            raise ValueError("selected quality analysis requires a parameter")
        bin_methods = {
            QualityAnalysisType.BIN_COOCCURRENCE,
            QualityAnalysisType.SBL_GROUPED_LIMIT,
        }
        if self.analysis in bin_methods and self.bin_type is None:
            raise ValueError("selected Bin analysis requires an exact Bin type")
        if (
            self.analysis == QualityAnalysisType.SBL_GROUPED_LIMIT
            and self.bin_type == QualityBinType.ALL_MAPPED_FAILURE
        ):
            raise ValueError("SBL requires one physical Bin type")
        if self.analysis == QualityAnalysisType.SPC_I_MR and (
            self.spc_order is None or self.spc_phase is None
        ):
            raise ValueError("SPC requires explicit order and phase")
        if self.group_by == QualityGroupBy.CONDITION and self.analysis in {
            QualityAnalysisType.BIN_COOCCURRENCE,
            QualityAnalysisType.SBL_GROUPED_LIMIT,
            QualityAnalysisType.SYL_GROUPED_LIMIT,
        }:
            raise ValueError("selected quality analysis cannot infer a condition")
        return self


class _WaferSummaryView(_StrictViewModel):
    sort_by: Literal["DATASET", "LOT", "WAFER", "UNIT_COUNT", "YIELD"] = Field(
        alias="sortBy"
    )
    sort_direction: Literal["ASC", "DESC"] = Field(alias="sortDirection")


class _AnalysisComponents(_StrictViewModel):
    overview_risk: _OverviewRiskView = Field(alias="overviewRisk")
    detail: _DetailView
    parameter_analysis: _ParameterAnalysisView = Field(alias="parameterAnalysis")
    parameter_relationship: _ParameterRelationshipView = Field(
        alias="parameterRelationship"
    )
    spatial: _SpatialView
    quality: _QualityView
    wafer_summary: _WaferSummaryView = Field(alias="waferSummary")


class _PersistedAnalysisViewState(_StrictViewModel):
    contract_version: Literal[ANALYSIS_VIEW_STATE_CONTRACT_VERSION]
    components: _AnalysisComponents


def _normalized_requirement(
    rule: _ExactRule,
    algorithm_code: str,
    parameters: tuple[str | None, ...],
) -> AnalysisRuleRequirement:
    if not rule.rule_code or not rule.version_code:
        raise ValueError("selected analysis requires an exact rule reference")
    normalized_parameters = tuple(dict.fromkeys(parameters or (None,)))
    return AnalysisRuleRequirement(
        rule.rule_code,
        rule.version_code,
        algorithm_code,
        normalized_parameters,
    )


def _deduplicate_requirements(
    requirements: list[AnalysisRuleRequirement],
) -> tuple[AnalysisRuleRequirement, ...]:
    grouped: dict[tuple[str, str, str], list[str | None]] = {}
    algorithms_by_identity: dict[tuple[str, str], str] = {}
    for requirement in requirements:
        identity = (requirement.rule_code, requirement.version_code)
        previous_algorithm = algorithms_by_identity.setdefault(
            identity, requirement.algorithm_code
        )
        if previous_algorithm != requirement.algorithm_code:
            raise ValueError(
                "one exact rule version cannot represent multiple algorithms"
            )
        values = grouped.setdefault((*identity, requirement.algorithm_code), [])
        for parameter in requirement.parameters:
            if parameter not in values:
                values.append(parameter)
    return tuple(
        AnalysisRuleRequirement(code, version, algorithm, tuple(parameters))
        for (code, version, algorithm), parameters in sorted(
            grouped.items(), key=lambda item: item[0]
        )
    )


def required_rules_from_analysis_view_state(
    chart_config: dict[str, Any], context_parameters: tuple[str, ...]
) -> tuple[AnalysisRuleRequirement, ...]:
    """Extract only selected exact Rules from the versioned Saved view contract.

    Legacy Saved Analyses without ``analysis_view_state`` remain readable and have
    no additional requirements. Once the versioned field is present it is parsed
    strictly; malformed or incomplete selected analysis state is never ignored.
    """

    raw = chart_config.get("analysis_view_state")
    if raw is None:
        return ()
    state = _PersistedAnalysisViewState.model_validate(raw)
    components = state.components
    requirements: list[AnalysisRuleRequirement] = []

    overview = components.overview_risk
    for analysis in overview.analyses:
        if analysis == "CAPABILITY":
            requirements.append(
                _normalized_requirement(
                    overview.capability,
                    overview.capability.method.value,
                    (overview.parameter,),
                )
            )
        elif analysis == "PAT_ROBUST_IQR":
            requirements.append(
                _normalized_requirement(
                    overview.pat,
                    quality_rule_algorithm(QualityAnalysisType(analysis)),
                    (overview.parameter,),
                )
            )
        elif analysis == "SPC_I_MR":
            requirements.append(
                _normalized_requirement(
                    overview.spc,
                    quality_rule_algorithm(QualityAnalysisType(analysis)),
                    (overview.parameter,),
                )
            )
        elif analysis == "MARGIN_OOS":
            requirements.append(
                _normalized_requirement(
                    overview.margin,
                    quality_rule_algorithm(QualityAnalysisType(analysis)),
                    (overview.parameter,),
                )
            )
        elif analysis == "SBL_GROUPED_LIMIT":
            requirements.append(
                _normalized_requirement(
                    overview.sbl,
                    quality_rule_algorithm(QualityAnalysisType(analysis)),
                    (overview.sbl.bin_type.value,),
                )
            )
        else:
            requirements.append(
                _normalized_requirement(
                    overview.syl,
                    quality_rule_algorithm(QualityAnalysisType.SYL_GROUPED_LIMIT),
                    (None,),
                )
            )

    parameter = components.parameter_analysis
    selected_parameter = set(parameter.analyses)
    if selected_parameter.difference({DatasetParameterAnalysisType.DESCRIPTIVE}) and (
        not context_parameters
    ):
        raise ValueError(
            "selected parameter analysis requires context parameters for Rule scope"
        )
    parameter_scopes: tuple[str | None, ...] = tuple(context_parameters)
    if DatasetParameterAnalysisType.BOX_PLOT in selected_parameter:
        requirements.append(
            _normalized_requirement(
                parameter.box_plot, "TUKEY_BOX_V1", parameter_scopes
            )
        )
    if DatasetParameterAnalysisType.HISTOGRAM in selected_parameter:
        requirements.append(
            _normalized_requirement(
                parameter.histogram, "EQUAL_WIDTH_HISTOGRAM_V1", parameter_scopes
            )
        )
    if DatasetParameterAnalysisType.NORMAL_FIT in selected_parameter:
        requirements.append(
            _normalized_requirement(
                parameter.normal_fit, "NORMAL_FIT_MLE_V1", parameter_scopes
            )
        )
    if DatasetParameterAnalysisType.CAPABILITY in selected_parameter:
        requirements.append(
            _normalized_requirement(
                parameter.capability,
                parameter.capability.method.value,
                parameter_scopes,
            )
        )

    relationship = components.parameter_relationship
    if ParameterRelationshipAnalysis.CORRELATION in relationship.analyses:
        requirements.append(
            _normalized_requirement(
                relationship.correlation,
                relationship.correlation.method.value,
                (relationship.x_parameter, *relationship.y_parameters),
            )
        )

    spatial = components.spatial
    if spatial.mode == SpatialAnalysisMode.ZONE_COMPARISON:
        requirements.append(
            _normalized_requirement(
                spatial.rule,
                "WAFER_ZONE_GEOMETRY_V2",
                (spatial.parameter or None,),
            )
        )

    quality = components.quality
    if quality.analysis is not None:
        quality_parameter = (
            quality.bin_type.value
            if quality.analysis
            in {
                QualityAnalysisType.BIN_COOCCURRENCE,
                QualityAnalysisType.SBL_GROUPED_LIMIT,
            }
            and quality.bin_type is not None
            else quality.parameter
            if quality.analysis
            in {
                QualityAnalysisType.PAT_ROBUST_IQR,
                QualityAnalysisType.SPC_I_MR,
                QualityAnalysisType.MARGIN_OOS,
                QualityAnalysisType.PASS_FAIL_DISTRIBUTION,
            }
            else None
        )
        requirements.append(
            _normalized_requirement(
                quality.rule,
                quality_rule_algorithm(quality.analysis),
                (quality_parameter,),
            )
        )

    return _deduplicate_requirements(requirements)


def deduplicate_analysis_rule_requirements(
    requirements: list[AnalysisRuleRequirement],
) -> tuple[AnalysisRuleRequirement, ...]:
    return _deduplicate_requirements(requirements)
