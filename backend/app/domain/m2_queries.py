from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class M2PageFilters:
    page: int
    page_size: int
    business_domain: str | None = None
    test_stage: str | None = None
    factory_code: str | None = None
    status: str | None = None
    product_name: str | None = None
    lot_id: str | None = None
    wafer_id: str | None = None
    import_batch_id: int | None = None
    cleaner_version: str | None = None
    owner_login: str | None = None
    from_utc: datetime | None = None
    to_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class M2Page:
    items: tuple[object, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class StageUploadPageItem:
    import_batch_id: int
    sequence_no: int
    receipt_id: int
    original_file_name: str
    extension: str
    size_bytes: int
    factory_code: str
    upload_time_utc: str
    completion_time_utc: str | None
    uploader_login: str
    uploader_name: str
    status: str
    source_file_id: int
    latest_job_id: int | None
    error_code: str | None
    error_message: str | None
    action_required: str | None
    queue_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class StageResultPageItem:
    result_summary_id: int
    import_batch_id: int
    job_id: int | None
    data_name: str
    product_name: str | None
    lot_id: str | None
    wafer_count: int | None
    factory_code: str
    test_item_count: int | None
    unit_count: int | None
    pass_count: int | None
    yield_rate: float | None
    status: str
    data_type: str
    dataset_id: int | None
    dataset_version_no: int | None
    created_at_utc: str


@dataclass(frozen=True, slots=True)
class CurrentDatasetCatalogItem:
    dataset_id: int
    dataset_version_id: int
    version_no: int
    import_batch_id: int
    job_id: int | None
    processing_run_id: int | None
    product_name: str | None
    lot_id: str | None
    lot_count: int
    factory_code: str
    business_domain: str
    test_stage: str
    status: str
    unit_count: int | None
    pass_count: int | None
    yield_rate: float | None
    source_file_count: int
    processed_at_utc: str
    owner_login: str
    owner_name: str
    cleaner_version: str | None
    can_archive: bool


@dataclass(frozen=True, slots=True)
class JobSafeSummary:
    job_id: int
    job_type: str
    lifecycle_action_type: str | None
    status: str
    import_batch_id: int | None
    parent_job_id: int | None
    requested_at_utc: str
    started_at_utc: str | None
    finished_at_utc: str | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class JobSafeDetails(JobSafeSummary):
    source_file_id: int | None
    analysis_session_id: int | None
    cleaner_release_id: int | None
    trigger_type: str
    requested_by: str | None
    reason: str | None
    not_before_utc: str | None
    heartbeat_at_utc: str | None
    lease_expires_at_utc: str | None
    finalize_protocol: str
    queue_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class CleanerReleaseSummary:
    cleaner_release_id: int
    cleaner_code: str
    cleaner_version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class BatchIdentitySummary:
    import_batch_id: int
    business_domain: str
    test_stage: str
    factory_code: str
    status: str
    source_file_count: int


@dataclass(frozen=True, slots=True)
class FinalizeIntentSummary:
    status: str
    staged_at_utc: str | None
    finalized_at_utc: str | None
    aborted_at_utc: str | None


@dataclass(frozen=True, slots=True)
class ProcessingRunSummary:
    processing_run_id: int
    status: str
    started_at_utc: str | None
    finished_at_utc: str | None


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    dataset_id: int
    dataset_version_id: int
    version_no: int
    status: str
    is_current: bool


@dataclass(frozen=True, slots=True)
class JobTimelineEvent:
    event_code: str
    status: str
    occurred_at_utc: str


@dataclass(frozen=True, slots=True)
class AvailableAction:
    code: str
    label: str
    enabled: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class SourceLineageSummary:
    source_file_id: int
    ordinal_no: int
    original_file_name: str
    file_size: int
    sha256: str | None
    lineage_basis: str


@dataclass(frozen=True, slots=True)
class JobDetails:
    job: JobSafeDetails
    parent: JobSafeSummary | None
    children: tuple[JobSafeSummary, ...]
    release: CleanerReleaseSummary | None
    batch: BatchIdentitySummary | None
    intent: FinalizeIntentSummary | None
    run: ProcessingRunSummary | None
    dataset: DatasetSummary | None
    timeline: tuple[JobTimelineEvent, ...]
    actions: tuple[AvailableAction, ...]
    sources: tuple[SourceLineageSummary, ...] = ()


class M2QueryService(Protocol):
    def list_uploads_page(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        filters: M2PageFilters,
    ) -> M2Page: ...

    def list_results_page(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        filters: M2PageFilters,
    ) -> M2Page: ...

    def list_current_datasets(
        self, principal: Principal, filters: M2PageFilters
    ) -> M2Page: ...

    def get_job_details(self, principal: Principal, job_id: int) -> JobDetails: ...
