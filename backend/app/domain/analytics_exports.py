from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.analytics import (
    AnalyticsContextRequest,
    AnalyticsDetailSort,
    AnalyticsDetailView,
    AnalyticsMeasurementFilter,
    AnalyticsSortDirection,
    StrictAnalyticsRequest,
)
from app.domain.analytics_export_analysis import (
    resolve_analytics_export_analysis_config,
)
from app.domain.saved_analyses import (
    MAX_CONFIG_TOP_LEVEL_FIELDS,
    SavedAnalysisRuleContext,
    validate_analysis_presentation_config,
)

ANALYTICS_EXPORT_CONTRACT_VERSION = "ANALYTICS_EXPORT_V1"
ANALYTICS_EXPORT_WORKER_CONTRACT_VERSION = "ANALYTICS_EXPORT_WORKER_V1"
ANALYSIS_VIEW_CONTRACT_VERSION = "ANALYSIS_VIEW_STATE_V1"
CURRENT_PAGE_DETAIL_PAYLOAD_KEY = "current_page_detail_state"


class AnalyticsExportEvaluationType(StrEnum):
    """Persisted V1 measurement-evaluation identities accepted by Detail export."""

    SPEC = "SPEC"
    PAT = "PAT"
    SBL = "SBL"
    SAFE_LAUNCH = "SAFE_LAUNCH"
    OTHER = "OTHER"


class AnalyticsExportEvaluationResult(StrEnum):
    """Persisted V1 measurement-evaluation outcomes accepted by Detail export."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    NO_MATCH = "NO_MATCH"
    CONFIG_AMBIGUOUS = "CONFIG_AMBIGUOUS"
    INVALID_VALUE = "INVALID_VALUE"


class AnalyticsExportScope(StrEnum):
    CURRENT_PAGE = "CURRENT_PAGE"
    FILTERED_RESULT = "FILTERED_RESULT"
    FULL_DATASET = "FULL_DATASET"
    REPORT = "REPORT"


class AnalyticsExportFormat(StrEnum):
    PNG = "PNG"
    CSV = "CSV"
    XLSX = "XLSX"
    BIN_TXT = "BIN_TXT"
    HTML = "HTML"
    PDF = "PDF"


class AnalyticsExportStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class AnalyticsExportAvailability(StrEnum):
    PENDING_GENERATION = "PENDING_GENERATION"
    GENERATING = "GENERATING"
    ARTIFACT_METADATA_READY = "ARTIFACT_METADATA_READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INTEGRITY_BLOCKED = "INTEGRITY_BLOCKED"


@dataclass(frozen=True, slots=True)
class AnalyticsExportTemplateContract:
    template_code: str
    template_version: str
    scopes: frozenset[AnalyticsExportScope]
    formats: frozenset[AnalyticsExportFormat]
    test_stages: frozenset[str]


_REPORT_FORMATS = frozenset(
    {
        AnalyticsExportFormat.PNG,
        AnalyticsExportFormat.CSV,
        AnalyticsExportFormat.XLSX,
        AnalyticsExportFormat.HTML,
        AnalyticsExportFormat.PDF,
    }
)
_DATA_FORMATS = frozenset(
    {
        AnalyticsExportFormat.CSV,
        AnalyticsExportFormat.XLSX,
        AnalyticsExportFormat.BIN_TXT,
    }
)
_DATA_SCOPES = frozenset(
    {
        AnalyticsExportScope.CURRENT_PAGE,
        AnalyticsExportScope.FILTERED_RESULT,
        AnalyticsExportScope.FULL_DATASET,
    }
)
_REPORT_SCOPE = frozenset({AnalyticsExportScope.REPORT})
_CP_FT = frozenset({"CP", "FT"})


def _template(
    code: str,
    *,
    scopes: frozenset[AnalyticsExportScope],
    formats: frozenset[AnalyticsExportFormat],
    stages: frozenset[str] = _CP_FT,
) -> AnalyticsExportTemplateContract:
    return AnalyticsExportTemplateContract(code, "v1", scopes, formats, stages)


ANALYTICS_EXPORT_TEMPLATE_REGISTRY = MappingProxyType(
    {
        (item.template_code, item.template_version): item
        for item in (
            _template("ANALYTICS_DETAIL", scopes=_DATA_SCOPES, formats=_DATA_FORMATS),
            _template("PARAMETER_DETAIL", scopes=_DATA_SCOPES, formats=_DATA_FORMATS),
            _template(
                "ANALYTICS_OVERVIEW", scopes=_REPORT_SCOPE, formats=_REPORT_FORMATS
            ),
            _template(
                "PARAMETER_ANALYSIS", scopes=_REPORT_SCOPE, formats=_REPORT_FORMATS
            ),
            _template(
                "PARAMETER_RELATIONSHIP", scopes=_REPORT_SCOPE, formats=_REPORT_FORMATS
            ),
            _template(
                "SPATIAL_ANALYSIS",
                scopes=_REPORT_SCOPE,
                formats=_REPORT_FORMATS,
                stages=frozenset({"CP"}),
            ),
            _template(
                "FT_QUALITY",
                scopes=_REPORT_SCOPE,
                formats=_REPORT_FORMATS,
                stages=frozenset({"FT"}),
            ),
            _template(
                "WAFER_SUMMARY",
                scopes=_REPORT_SCOPE,
                formats=_REPORT_FORMATS,
                stages=frozenset({"CP"}),
            ),
        )
    }
)


def resolve_analytics_export_template(
    template_code: str,
    template_version: str,
    export_scope: AnalyticsExportScope | str,
    export_format: AnalyticsExportFormat | str,
    *,
    test_stage: str | None = None,
) -> AnalyticsExportTemplateContract:
    contract = ANALYTICS_EXPORT_TEMPLATE_REGISTRY.get((template_code, template_version))
    if contract is None:
        raise ValueError("analytics export template code/version is not registered")
    normalized_scope = AnalyticsExportScope(export_scope)
    normalized_format = AnalyticsExportFormat(export_format)
    if normalized_scope not in contract.scopes:
        raise ValueError(
            "analytics export template does not support the requested scope"
        )
    if normalized_format not in contract.formats:
        raise ValueError(
            "analytics export template does not support the requested format"
        )
    if test_stage is not None and test_stage not in contract.test_stages:
        raise ValueError("analytics export template does not support this test stage")
    return contract


class AnalyticsExportCurrentPageEvaluationFilter(StrictAnalyticsRequest):
    """Closed V1 filter shape; SQL/predicate expressions are intentionally absent."""

    evaluation_type: AnalyticsExportEvaluationType
    evaluation_results: list[AnalyticsExportEvaluationResult] = Field(
        min_length=1, max_length=6
    )
    rule_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    rule_version: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )

    @field_validator("evaluation_results")
    @classmethod
    def evaluation_results_are_unique(
        cls, value: list[AnalyticsExportEvaluationResult]
    ) -> list[AnalyticsExportEvaluationResult]:
        if len(value) != len(set(value)):
            raise ValueError("evaluation results must be unique")
        return value

    @model_validator(mode="after")
    def rule_identity_is_complete_or_explicitly_unversioned(
        self,
    ) -> AnalyticsExportCurrentPageEvaluationFilter:
        if (self.rule_code is None) != (self.rule_version is None):
            raise ValueError(
                "evaluation rule_code and rule_version must be supplied together"
            )
        return self


class AnalyticsExportCurrentPageDetailState(BaseModel):
    """Exact, bounded UI Detail state required to reproduce CURRENT_PAGE."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    view: AnalyticsDetailView
    sort_by: AnalyticsDetailSort = Field(alias="sortBy")
    sort_direction: AnalyticsSortDirection = Field(alias="sortDirection")
    evaluation_filter: AnalyticsExportCurrentPageEvaluationFilter | None = None
    measurement_filter: AnalyticsMeasurementFilter | None = None


def freeze_current_page_detail_state(
    export_scope: AnalyticsExportScope | str,
    chart_config: dict[str, Any],
    display_config: dict[str, Any],
) -> AnalyticsExportCurrentPageDetailState | None:
    """Validate the public state once and return the typed payload to persist."""

    if AnalyticsExportScope(export_scope) != AnalyticsExportScope.CURRENT_PAGE:
        return None
    detail, _ = resolve_current_page_detail_state(chart_config, display_config)
    return detail


def replay_stored_current_page_detail_state(
    payload: dict[str, Any],
    *,
    export_scope: AnalyticsExportScope | str,
    chart_config: dict[str, Any],
    display_config: dict[str, Any],
) -> AnalyticsExportCurrentPageDetailState | None:
    """Reconcile a typed payload, while accepting pre-extension V1 envelopes."""

    normalized_scope = AnalyticsExportScope(export_scope)
    frozen_raw = payload.get(CURRENT_PAGE_DETAIL_PAYLOAD_KEY)
    if normalized_scope != AnalyticsExportScope.CURRENT_PAGE:
        if frozen_raw is not None:
            raise ValueError("non-CURRENT_PAGE export contains a Detail state")
        return None

    public_state, _ = resolve_current_page_detail_state(chart_config, display_config)
    if CURRENT_PAGE_DETAIL_PAYLOAD_KEY not in payload:
        # Backward-compatible replay of an existing ANALYTICS_EXPORT_V1 Job.
        return public_state
    frozen_state = AnalyticsExportCurrentPageDetailState.model_validate(frozen_raw)
    if frozen_state != public_state:
        raise ValueError("stored CURRENT_PAGE Detail state does not reconcile")
    return frozen_state


def resolve_current_page_detail_state(
    chart_config: dict[str, Any], display_config: dict[str, Any]
) -> tuple[AnalyticsExportCurrentPageDetailState, int]:
    """Fail closed unless the queued presentation can reproduce the UI page."""

    view_state = chart_config.get("analysis_view_state")
    if not isinstance(view_state, dict) or set(view_state) != {
        "contract_version",
        "components",
    }:
        raise ValueError(
            "CURRENT_PAGE requires one exact versioned analysis_view_state"
        )
    if view_state.get("contract_version") != ANALYSIS_VIEW_CONTRACT_VERSION:
        raise ValueError("CURRENT_PAGE analysis_view_state contract is unsupported")
    components = view_state.get("components")
    try:
        if not isinstance(components, dict):
            raise TypeError("analysis_view_state components must be an object")
        detail = AnalyticsExportCurrentPageDetailState.model_validate(
            components.get("detail")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("CURRENT_PAGE Detail state is invalid") from exc

    focus_dataset_id = display_config.get("focus_dataset_id")
    if (
        isinstance(focus_dataset_id, bool)
        or not isinstance(focus_dataset_id, int)
        or focus_dataset_id <= 0
    ):
        raise ValueError("CURRENT_PAGE requires one exact focus_dataset_id")
    return detail, focus_dataset_id


class CreateAnalyticsExportRequest(AnalyticsContextRequest):
    """Strict server-rendered export request; no paths, SQL or formulas are accepted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: Literal[ANALYTICS_EXPORT_CONTRACT_VERSION] = (
        ANALYTICS_EXPORT_CONTRACT_VERSION
    )
    export_scope: AnalyticsExportScope
    export_format: AnalyticsExportFormat
    template_code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    template_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    )
    rule_context: SavedAnalysisRuleContext
    chart_config: dict[str, Any] = Field(
        default_factory=dict, max_length=MAX_CONFIG_TOP_LEVEL_FIELDS
    )
    display_config: dict[str, Any] = Field(
        default_factory=dict, max_length=MAX_CONFIG_TOP_LEVEL_FIELDS
    )
    artifact_ttl_hours: int = Field(default=24, ge=1, le=168)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$",
    )
    page: int | None = Field(default=None, ge=1)
    page_size: int | None = Field(default=None, ge=1, le=200)
    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_is_printable(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("export reason must contain printable text only")
        return normalized

    @model_validator(mode="after")
    def scope_and_format_are_compatible(self) -> CreateAnalyticsExportRequest:
        validate_analysis_presentation_config(self.chart_config, self.display_config)
        if self.export_scope == AnalyticsExportScope.CURRENT_PAGE:
            if self.page is None or self.page_size is None:
                raise ValueError("CURRENT_PAGE requires page and page_size")
            _, focus_dataset_id = resolve_current_page_detail_state(
                self.chart_config, self.display_config
            )
            if focus_dataset_id not in {item.dataset_id for item in self.datasets}:
                raise ValueError(
                    "CURRENT_PAGE focus_dataset_id must belong to the selected context"
                )
            if (
                self.display_config.get("page") != self.page
                or self.display_config.get("page_size") != self.page_size
            ):
                raise ValueError(
                    "CURRENT_PAGE page bounds must match the frozen display configuration"
                )
        elif self.page is not None or self.page_size is not None:
            raise ValueError("page and page_size are valid only for CURRENT_PAGE")

        resolve_analytics_export_template(
            self.template_code,
            self.template_version,
            self.export_scope,
            self.export_format,
        )
        if self.export_scope == AnalyticsExportScope.REPORT:
            resolve_analytics_export_analysis_config(
                self.template_code, self.chart_config
            )
        return self


class CancelAnalyticsExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirmation: Literal["CANCEL"]
    expected_row_version: str = Field(pattern=r"^[0-9A-Fa-f]{16}$")
    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("expected_row_version")
    @classmethod
    def row_version_is_canonical(cls, value: str) -> str:
        return value.upper()

    @field_validator("reason")
    @classmethod
    def reason_is_printable(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("cancellation reason must contain printable text only")
        return normalized


@dataclass(frozen=True, slots=True)
class AnalyticsExportDatasetRecord:
    dataset_version_id: int
    dataset_id: int
    version_no: int
    ordinal_no: int
    test_stage: str


@dataclass(frozen=True, slots=True)
class AnalyticsExportRecord:
    export_job_id: int
    requested_by: int
    contract_version: str
    worker_contract_version: str
    generation_mode: str
    status: str
    export_scope: str
    export_format: str
    template_code: str
    template_version: str
    datasets: tuple[AnalyticsExportDatasetRecord, ...]
    filters: dict[str, list[str]]
    parameters: tuple[str, ...]
    filter_hash: str
    context_hash: str
    rule_context: SavedAnalysisRuleContext
    chart_config: dict[str, Any]
    display_config: dict[str, Any]
    presentation_hash: str
    artifact_ttl_hours: int
    page: int | None
    page_size: int | None
    idempotency_key: str
    request_reason_sha256: str
    requested_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    exported_row_count: int | None
    row_version: str
    idempotent_replay: bool
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsExportPage:
    """One access-visible page of export history.

    ``total`` counts every access-visible job occupying a pagination slot, including
    jobs whose stored context fails integrity validation. Such jobs are excluded
    from ``items`` and reported explicitly for this page only.
    """

    items: tuple[AnalyticsExportRecord, ...]
    total: int
    page: int
    page_size: int
    integrity_blocked_job_ids: tuple[int, ...] = ()
    integrity_blocked_count: int = 0


@dataclass(frozen=True, slots=True)
class AnalyticsExportArtifactMetadata:
    export_artifact_id: int
    file_name: str
    mime_type: str
    file_size: int
    sha256: str
    created_at_utc: str
    expires_at_utc: str


@dataclass(frozen=True, slots=True)
class AnalyticsExportDownloadMetadata:
    export_job_id: int
    job_status: str
    availability: str
    download_enabled: bool
    reason_code: str
    artifacts: tuple[AnalyticsExportArtifactMetadata, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsExportDownloadTarget:
    """Internal-only file target. API responses must never serialize this object."""

    path: Path
    file_name: str
    mime_type: str


class AnalyticsExportService(Protocol):
    def create(
        self, request: CreateAnalyticsExportRequest, principal
    ) -> AnalyticsExportRecord: ...

    def list_page(
        self, principal, *, page: int, page_size: int
    ) -> AnalyticsExportPage: ...

    def get(self, export_job_id: int, principal) -> AnalyticsExportRecord: ...

    def download_metadata(
        self, export_job_id: int, principal
    ) -> AnalyticsExportDownloadMetadata: ...

    def resolve_download(
        self, export_job_id: int, export_artifact_id: int, principal
    ) -> AnalyticsExportDownloadTarget: ...

    def cancel(
        self,
        export_job_id: int,
        request: CancelAnalyticsExportRequest,
        principal,
    ) -> AnalyticsExportRecord: ...
