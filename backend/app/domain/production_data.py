from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class StoredUpload:
    original_name: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProductionUploadRow:
    import_batch_id: int
    sequence_no: int
    original_file_name: str
    extension: str
    size_bytes: int
    factory_code: str
    upload_time_utc: str
    completion_time_utc: str | None
    uploader_login: str
    uploader_name: str
    status: str


@dataclass(frozen=True, slots=True)
class ProductionResultRow:
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
    created_at_utc: str


class ProductionDataService(Protocol):
    def register_upload(
        self,
        principal: Principal,
        factory_code: str,
        files: Sequence[StoredUpload],
        remark: str | None,
    ) -> int: ...

    def mark_processing(self, batch_id: int, principal: Principal) -> int: ...

    def mark_failed(self, batch_id: int, job_id: int, message: str) -> None: ...

    def record_cp_result(self, batch_id: int, job_id: int, result: dict) -> None: ...

    def list_uploads(self, principal: Principal) -> tuple[ProductionUploadRow, ...]: ...

    def list_results(self, principal: Principal) -> tuple[ProductionResultRow, ...]: ...
