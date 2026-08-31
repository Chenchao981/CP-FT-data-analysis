from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeAlias

from app.domain.analytics import AnalyticsContextRequest
from app.domain.analytics_exports import (
    AnalyticsExportCurrentPageDetailState,
    AnalyticsExportFormat,
    AnalyticsExportScope,
)
from app.domain.saved_analyses import SavedAnalysisRuleContext

ExportCell: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class AnalyticsExportWorkItem:
    export_job_id: int
    requested_by: int
    export_scope: AnalyticsExportScope
    export_format: AnalyticsExportFormat
    template_code: str
    template_version: str
    context: AnalyticsContextRequest
    dataset_version_ids: tuple[int, ...]
    test_stage: str
    filter_hash: str
    context_hash: str
    rule_context: SavedAnalysisRuleContext
    chart_config: dict[str, Any]
    display_config: dict[str, Any]
    presentation_hash: str
    artifact_ttl_hours: int
    page: int | None
    page_size: int | None
    requested_at_utc: datetime
    lease_token: str
    lease_owner: str
    lease_expires_at_utc: datetime
    attempt_count: int
    current_page_detail_state: AnalyticsExportCurrentPageDetailState | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsExportTable:
    columns: tuple[str, ...]
    rows: Iterator[tuple[ExportCell, ...]]


@dataclass(frozen=True, slots=True)
class RenderedAnalyticsExport:
    path: Path
    file_name: str
    mime_type: str
    file_size: int
    sha256: str
    exported_row_count: int


class AnalyticsExportWorkerRepository(Protocol):
    def claim_next(self) -> AnalyticsExportWorkItem | None: ...

    def heartbeat(self, work_item: AnalyticsExportWorkItem) -> None: ...

    def complete(
        self,
        work_item: AnalyticsExportWorkItem,
        artifact: RenderedAnalyticsExport,
        *,
        expires_at_utc: datetime,
    ) -> None: ...

    def fail(
        self,
        work_item: AnalyticsExportWorkItem,
        *,
        error_code: str,
        error_message: str,
    ) -> None: ...


class AnalyticsExportContentSource(Protocol):
    def table(self, work_item: AnalyticsExportWorkItem) -> AnalyticsExportTable: ...
