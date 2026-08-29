from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.auth import Principal


class ExportLatestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ArchiveDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirmation: Literal["ARCHIVE"]
    reason: str = Field(min_length=8, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReprocessUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirmation: Literal["REPROCESS"]
    reason: str = Field(min_length=8, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128)


@dataclass(frozen=True, slots=True)
class LifecycleJobReceipt:
    job_id: int
    job_type: str
    dataset_id: int
    dataset_version_id: int
    action_type: str
    status: str
    import_batch_id: int
    cleaner_release_id: int | None
    parent_job_id: int | None
    idempotency_key: str
    created: bool


@dataclass(frozen=True, slots=True)
class LifecycleInputFile:
    import_batch_file_id: int
    original_file_name: str
    storage_uri: str
    expected_sha256: str | None
    lot_id_override: str | None


@dataclass(frozen=True, slots=True)
class LifecycleWorkerContext:
    job_id: int
    action_type: str
    dataset_id: int
    dataset_version_id: int
    import_batch_id: int
    test_stage: str
    factory_code: str
    requested_by_user_id: int
    request_reason: str | None
    files: tuple[LifecycleInputFile, ...]


@dataclass(frozen=True, slots=True)
class LifecycleArtifact:
    processing_artifact_id: int
    job_id: int
    artifact_role: str
    file_name: str
    file_size: int
    sha256: str
    expires_at_utc: datetime
    physical_status: str


@dataclass(frozen=True, slots=True)
class TemporaryArtifactInput:
    role: str
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LifecycleArtifactDownload:
    path: Path
    file_name: str
    media_type: str


@dataclass(frozen=True, slots=True)
class LifecycleExportStatus:
    job_id: int
    dataset_id: int
    dataset_version_id: int
    cleaner_release_id: int
    status: str
    error_code: str | None
    availability: str
    expires_at_utc: datetime | None
    artifacts: tuple[LifecycleArtifact, ...]


class LifecycleService(Protocol):
    def create_export(
        self,
        dataset_id: int,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt: ...

    def create_archive(
        self,
        dataset_id: int,
        reason: str,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt: ...

    def create_reprocess(
        self,
        dataset_id: int,
        reason: str,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt: ...

    def worker_context(
        self, job_id: int, lease_token: str, action_type: str
    ) -> LifecycleWorkerContext: ...

    def record_export_artifacts(
        self,
        job_id: int,
        lease_token: str,
        artifacts: tuple[TemporaryArtifactInput, ...],
        expires_at_utc: datetime,
    ) -> tuple[LifecycleArtifact, ...]: ...

    def archive_dataset_leased(self, job_id: int, lease_token: str) -> None: ...

    def artifact_download(
        self,
        job_id: int,
        artifact_id: int,
        principal: Principal,
    ) -> LifecycleArtifactDownload: ...

    def export_status(
        self, job_id: int, principal: Principal
    ) -> LifecycleExportStatus: ...
