from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import Field, field_validator, model_validator

from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsCounts,
    AnalyticsDatasetContext,
    AnalyticsDatasetReference,
    AnalyticsFilters,
    AnalyticsFilterSummary,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
    StrictAnalyticsRequest,
)


class ParameterRelationshipAnalysis(StrEnum):
    SCATTER = "SCATTER"
    TREND = "TREND"
    CORRELATION = "CORRELATION"


class ParameterRelationshipGroupBy(StrEnum):
    DATASET = "DATASET"
    TEST_BATCH = "TEST_BATCH"
    LOT = "LOT"
    WAFER = "WAFER"
    SOURCE = "SOURCE"
    TESTER = "TESTER"
    PROGRAM = "PROGRAM"
    CONDITION = "CONDITION"


class ParameterCorrelationMethod(StrEnum):
    PEARSON_PAIRWISE_V1 = "PEARSON_PAIRWISE_V1"


class ParameterCorrelationConfig(StrictAnalyticsRequest):
    method: ParameterCorrelationMethod | None = None
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    version_code: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @model_validator(mode="after")
    def method_and_rule_are_paired(self) -> ParameterCorrelationConfig:
        supplied = (
            self.method is not None,
            self.rule_code is not None,
            self.version_code is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError(
                "correlation method, rule_code and version_code must be supplied together"
            )
        return self


class ParameterRelationshipRequest(StrictAnalyticsRequest):
    datasets: list[AnalyticsDatasetReference] = Field(min_length=1, max_length=8)
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    x_parameter: str = Field(min_length=1, max_length=200)
    y_parameters: list[str] = Field(min_length=1, max_length=5)
    analyses: list[ParameterRelationshipAnalysis] = Field(
        default_factory=lambda: [ParameterRelationshipAnalysis.SCATTER],
        min_length=1,
        max_length=3,
    )
    group_by: ParameterRelationshipGroupBy = ParameterRelationshipGroupBy.DATASET
    max_points: int = Field(default=10_000, ge=100, le=20_000)
    correlation: ParameterCorrelationConfig = Field(
        default_factory=ParameterCorrelationConfig
    )

    @field_validator("datasets")
    @classmethod
    def datasets_are_unique(
        cls, value: list[AnalyticsDatasetReference]
    ) -> list[AnalyticsDatasetReference]:
        dataset_ids = [item.dataset_id for item in value]
        identities = [(item.dataset_id, item.version_no) for item in value]
        if len(dataset_ids) != len(set(dataset_ids)) or len(identities) != len(
            set(identities)
        ):
            raise ValueError("each dataset may appear only once")
        return value

    @field_validator("x_parameter")
    @classmethod
    def x_parameter_is_bounded(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("x_parameter must be non-empty")
        return normalized

    @field_validator("y_parameters")
    @classmethod
    def y_parameters_are_unique_and_bounded(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in normalized):
            raise ValueError("y_parameters must be non-empty and bounded")
        if len(normalized) != len(set(normalized)):
            raise ValueError("y_parameters must be unique")
        return normalized

    @field_validator("analyses")
    @classmethod
    def analyses_are_unique(
        cls, value: list[ParameterRelationshipAnalysis]
    ) -> list[ParameterRelationshipAnalysis]:
        if len(value) != len(set(value)):
            raise ValueError("relationship analyses must be unique")
        return value

    @model_validator(mode="after")
    def x_and_y_are_distinct(self) -> ParameterRelationshipRequest:
        if self.x_parameter in self.y_parameters:
            raise ValueError("x_parameter must not also appear in y_parameters")
        correlation_selected = (
            ParameterRelationshipAnalysis.CORRELATION in self.analyses
        )
        if correlation_selected and self.correlation.rule_code is None:
            raise ValueError("CORRELATION requires an exact rule version")
        if not correlation_selected and self.correlation.rule_code is not None:
            raise ValueError("correlation rule reference requires CORRELATION analysis")
        return self

    @property
    def parameters(self) -> tuple[str, ...]:
        return (self.x_parameter, *self.y_parameters)


@dataclass(frozen=True, slots=True)
class ParameterRelationshipIdentity:
    name: str
    canonical_parameter_code: str | None
    step_code: str
    sequence_no: int
    unit: str | None
    program_lsl: float | None
    program_usl: float | None
    test_condition: str | None
    formal_lsl: float | None = None
    formal_usl: float | None = None
    formal_lower_operator: str | None = None
    formal_upper_operator: str | None = None
    formal_spec_status: str = "NO_SPEC"
    formal_spec_reason_codes: tuple[str, ...] = ()
    formal_spec_versions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParameterScatterPoint:
    dataset_id: int
    version_no: int
    group_key: str
    x_parameter: str
    y_parameter: str
    x_value: float
    y_value: float
    x_out_of_spec: bool
    y_out_of_spec: bool
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class ParameterTrendPoint:
    dataset_id: int
    version_no: int
    group_key: str
    parameter: str
    sequence: int
    ordinal: int
    source_sequence: int | None
    run_id: int
    ordered_at: str | None
    value: float
    out_of_spec: bool
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class ParameterCorrelationResult:
    dataset_id: int
    version_no: int
    group_key: str
    x_parameter: str
    y_parameter: str
    sample_count: int
    coefficient: float | None
    status: str
    reason_code: str | None
    method: str
    rule_code: str


@dataclass(frozen=True, slots=True)
class ParameterRelationshipItem:
    dataset_id: int
    version_no: int
    group_key: str
    identities: tuple[ParameterRelationshipIdentity, ...]
    scatter_points: tuple[ParameterScatterPoint, ...]
    trend_points: tuple[ParameterTrendPoint, ...]
    correlations: tuple[ParameterCorrelationResult, ...]


@dataclass(frozen=True, slots=True)
class ParameterRelationshipResult:
    contract_version: str
    dataset_context: AnalyticsDatasetContext
    filter_summary: AnalyticsFilterSummary
    rule_context: AnalyticsRuleContext
    capabilities: tuple[AnalyticsCapability, ...]
    counts: AnalyticsCounts
    sampling_summary: AnalyticsSamplingSummary
    group_by: str
    trend_order_basis: str
    items: tuple[ParameterRelationshipItem, ...]
    warnings: tuple[str, ...]
    computed_at: str


class ParameterRelationshipService(Protocol):
    def relationship(
        self, request: ParameterRelationshipRequest
    ) -> ParameterRelationshipResult: ...
