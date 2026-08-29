from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.quick_capacity import QuickCapacityPolicy


class QuickAnalysisStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class CreateQuickPatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_root_code: str = Field(min_length=2, max_length=128)
    source_relative_path: str = Field(default=".", max_length=1000)
    source_manifest_mode: str = Field(min_length=1, max_length=64)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class NewQuickAnalysisSession:
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_json: str
    source_manifest_sha256: str
    source_file_count: int
    source_total_bytes: int
    retention_mode: str
    cleaner_release_id: int
    expires_at_utc: datetime
    reserved_bytes: int = 0


@dataclass(frozen=True, slots=True)
class QuickAnalysisSession:
    analysis_session_id: int
    owner_user_id: int
    owner_login: str
    owner_name: str
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_sha256: str
    source_file_count: int
    source_total_bytes: int
    retention_mode: str
    cleaner_release_id: int
    status: QuickAnalysisStatus
    job_id: int | None
    job_status: str | None
    parameter_count: int | None
    record_count: int | None
    summary: dict[str, Any] | None
    result_file_name: str | None
    result_size_bytes: int | None
    error_code: str | None
    error_message: str | None
    expires_at_utc: datetime
    created_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    reserved_bytes: int = 0
    cleanup_status: str = "RETAINED"


@dataclass(frozen=True, slots=True)
class QuickAnalysisPage:
    items: tuple[QuickAnalysisSession, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class QuickAnalysisWorkItem:
    analysis_session_id: int
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_json: str
    source_manifest_sha256: str
    cleaner_release_id: int
    expires_at_utc: datetime
    status: QuickAnalysisStatus


@dataclass(frozen=True, slots=True)
class QuickAnalysisArtifact:
    role: str
    path: str
    size_bytes: int
    sha256: str


class InMemoryQuickAnalysisService:
    def __init__(self, capacity: QuickCapacityPolicy | None = None) -> None:
        self._items: dict[int, QuickAnalysisSession] = {}
        self._work: dict[int, QuickAnalysisWorkItem] = {}
        self._artifacts: dict[int, tuple[QuickAnalysisArtifact, ...]] = {}
        self._next_id = 1
        self._lock = Lock()
        self._capacity = capacity

    def create(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> QuickAnalysisSession:
        with self._lock:
            if self._capacity is not None:
                active = tuple(
                    item
                    for item in self._items.values()
                    if item.status in {
                        QuickAnalysisStatus.QUEUED,
                        QuickAnalysisStatus.RUNNING,
                    }
                )
                self._capacity.ensure_quota(
                    global_used_bytes=sum(item.reserved_bytes for item in active),
                    user_used_bytes=sum(
                        item.reserved_bytes
                        for item in active
                        if item.owner_user_id == principal.user_id
                    ),
                    reservation_bytes=request.reserved_bytes,
                )
            now = datetime.now(UTC)
            item = QuickAnalysisSession(
                analysis_session_id=self._next_id,
                owner_user_id=principal.user_id,
                owner_login=principal.login_name,
                owner_name=principal.display_name,
                analysis_type=request.analysis_type,
                test_stage=request.test_stage,
                factory_code=request.factory_code,
                source_root_code=request.source_root_code,
                source_relative_path=request.source_relative_path,
                source_manifest_mode=request.source_manifest_mode,
                source_manifest_sha256=request.source_manifest_sha256,
                source_file_count=request.source_file_count,
                source_total_bytes=request.source_total_bytes,
                retention_mode=request.retention_mode,
                cleaner_release_id=request.cleaner_release_id,
                status=QuickAnalysisStatus.QUEUED,
                job_id=None,
                job_status="QUEUED",
                parameter_count=None,
                record_count=None,
                summary=None,
                result_file_name=None,
                result_size_bytes=None,
                error_code=None,
                error_message=None,
                expires_at_utc=request.expires_at_utc,
                created_at_utc=now,
                reserved_bytes=request.reserved_bytes,
            )
            self._items[item.analysis_session_id] = item
            self._work[item.analysis_session_id] = QuickAnalysisWorkItem(
                item.analysis_session_id,
                item.analysis_type,
                item.test_stage,
                item.factory_code,
                item.source_root_code,
                item.source_relative_path,
                request.source_manifest_mode,
                request.source_manifest_json,
                request.source_manifest_sha256,
                request.cleaner_release_id,
                request.expires_at_utc,
                QuickAnalysisStatus.QUEUED,
            )
            self._next_id += 1
            return item

    def attach_job(self, analysis_session_id: int, job_id: int) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(item, job_id=job_id)

    def list_for_principal(
        self, principal: Principal
    ) -> tuple[QuickAnalysisSession, ...]:
        items = self._items.values()
        if "SYSTEM_ADMIN" not in principal.roles:
            items = (item for item in items if item.owner_user_id == principal.user_id)
        return tuple(
            self._effective(item)
            for item in sorted(
                items, key=lambda item: item.analysis_session_id, reverse=True
            )
        )

    def list_page_for_principal(
        self,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        status: QuickAnalysisStatus | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> QuickAnalysisPage:
        items = self.list_for_principal(principal)
        filtered = tuple(
            item
            for item in items
            if (status is None or item.status == status)
            and (from_utc is None or item.created_at_utc >= from_utc)
            and (to_utc is None or item.created_at_utc < to_utc)
        )
        offset = (page - 1) * page_size
        return QuickAnalysisPage(
            items=filtered[offset : offset + page_size],
            total=len(filtered),
            page=page,
            page_size=page_size,
        )

    def get_for_principal(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisSession:
        item = self._required(analysis_session_id)
        if (
            "SYSTEM_ADMIN" not in principal.roles
            and item.owner_user_id != principal.user_id
        ):
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在或无权访问", 404
            )
        return self._effective(item)

    def worker_session_info(self, analysis_session_id: int) -> QuickAnalysisWorkItem:
        try:
            return self._work[analysis_session_id]
        except KeyError as exc:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在", 404
            ) from exc

    def mark_running(self, analysis_session_id: int) -> None:
        with self._lock:
            now = datetime.now(UTC)
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(
                item,
                status=QuickAnalysisStatus.RUNNING,
                job_status="RUNNING",
                started_at_utc=item.started_at_utc or now,
                error_code=None,
                error_message=None,
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.RUNNING
            )

    def record_success(
        self,
        analysis_session_id: int,
        job_id: int,
        *,
        parameter_count: int,
        record_count: int | None,
        summary: dict[str, Any],
        artifacts: tuple[QuickAnalysisArtifact, ...],
    ) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            report = next(
                (artifact for artifact in artifacts if artifact.role == "pat_report"),
                None,
            )
            if report is None:
                raise ValueError("pat_report artifact is required")
            self._artifacts[analysis_session_id] = artifacts
            self._items[analysis_session_id] = replace(
                item,
                job_id=job_id,
                status=QuickAnalysisStatus.SUCCESS,
                job_status="SUCCESS",
                parameter_count=parameter_count,
                record_count=record_count,
                summary=summary,
                result_file_name=Path(report.path).name,
                result_size_bytes=report.size_bytes,
                finished_at_utc=datetime.now(UTC),
                error_code=None,
                error_message=None,
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.SUCCESS
            )

    def mark_failed(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(
                item,
                status=QuickAnalysisStatus.FAILED,
                job_status="FAILED",
                error_code=error_code,
                error_message=error_message,
                finished_at_utc=datetime.now(UTC),
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.FAILED
            )

    def result_artifact(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisArtifact:
        session = self.get_for_principal(analysis_session_id, principal)
        if session.status == QuickAnalysisStatus.EXPIRED:
            raise DomainError("QUICK_RESULT_EXPIRED", "PAT 结果已过期", 410)
        artifact = next(
            (
                item
                for item in self._artifacts.get(analysis_session_id, ())
                if item.role == "pat_report"
            ),
            None,
        )
        if artifact is None:
            raise DomainError("QUICK_RESULT_NOT_FOUND", "PAT 结果尚不可下载", 404)
        return artifact

    def _required(self, analysis_session_id: int) -> QuickAnalysisSession:
        try:
            return self._items[analysis_session_id]
        except KeyError as exc:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在", 404
            ) from exc

    @staticmethod
    def _effective(item: QuickAnalysisSession) -> QuickAnalysisSession:
        if (
            item.status == QuickAnalysisStatus.SUCCESS
            and item.expires_at_utc <= datetime.now(UTC)
        ):
            return replace(item, status=QuickAnalysisStatus.EXPIRED)
        return item


class QuickAnalysisService(Protocol):
    def create(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> QuickAnalysisSession: ...

    def attach_job(self, analysis_session_id: int, job_id: int) -> None: ...

    def list_for_principal(
        self, principal: Principal
    ) -> tuple[QuickAnalysisSession, ...]: ...

    def list_page_for_principal(
        self,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        status: QuickAnalysisStatus | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> QuickAnalysisPage: ...

    def get_for_principal(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisSession: ...

    def worker_session_info(self, analysis_session_id: int) -> QuickAnalysisWorkItem: ...

    def mark_running(self, analysis_session_id: int) -> None: ...

    def record_success(
        self,
        analysis_session_id: int,
        job_id: int,
        *,
        parameter_count: int,
        record_count: int | None,
        summary: dict[str, Any],
        artifacts: tuple[QuickAnalysisArtifact, ...],
    ) -> None: ...

    def mark_failed(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None: ...

    def result_artifact(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisArtifact: ...
