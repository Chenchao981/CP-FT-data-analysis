from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class StoredUpload:
    original_name: str
    path: Path
    size_bytes: int
    sha256: str
    source_metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class FormalSourceManifestPreview:
    root_code: str
    relative_path: str
    mode: str
    recursive: bool
    file_count: int
    total_bytes: int
    sha: str
    allowed_suffixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageUploadRow:
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
    latest_job_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    action_required: Literal["LOT_ID"] | None = None
    is_duplicate_receipt: bool = False
    can_manage: bool = False
    can_download_source: bool = False


@dataclass(frozen=True, slots=True)
class BatchFileInfo:
    receipt_id: int
    original_file_name: str
    storage_uri: str
    source_file_id: int
    expected_sha256: str | None
    lot_id_override: str | None = None
    is_duplicate_receipt: bool = False


@dataclass(frozen=True, slots=True)
class BatchInfo:
    import_batch_id: int
    factory_code: str
    status: str
    files: tuple[BatchFileInfo, ...]
    can_manage: bool = False


@dataclass(frozen=True, slots=True)
class WorkerBatchInfo:
    import_batch_id: int
    business_domain: str
    test_stage: str
    factory_code: str
    files: tuple[BatchFileInfo, ...]


@dataclass(frozen=True, slots=True)
class StageResultRow:
    result_summary_id: int
    import_batch_id: int
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
    can_manage: bool = False
    uploader_login: str = ""
    uploader_name: str = ""


class StageDataService(Protocol):
    def register_upload(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        factory_code: str,
        files: Sequence[StoredUpload],
        remark: str | None,
    ) -> int: ...

    def mark_processing(self, batch_id: int, principal: Principal) -> int: ...

    def mark_queued(self, batch_id: int) -> None: ...

    def mark_failed(self, batch_id: int, job_id: int, message: str) -> None: ...

    def record_result(self, batch_id: int, job_id: int, result: dict) -> None: ...

    def list_uploads(
        self, principal: Principal, business_domain: str, test_stage: str
    ) -> tuple[StageUploadRow, ...]: ...

    def list_results(
        self, principal: Principal, business_domain: str, test_stage: str
    ) -> tuple[StageResultRow, ...]: ...

    def get_batch_info(
        self, principal: Principal, business_domain: str, test_stage: str, batch_id: int
    ) -> BatchInfo | None: ...

    def archive_previous_results(self, batch_id: int) -> None: ...
