from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import DomainError


class JobType(StrEnum):
    INITIAL_IMPORT = "INITIAL_IMPORT"
    EXPORT_LATEST = "EXPORT_LATEST"
    REPROCESS_UPDATE = "REPROCESS_UPDATE"
    DELETE_TASK = "DELETE_TASK"
    QUICK_PAT = "QUICK_PAT"
    # Historical values remain readable during the forward migration.
    PARSE = "PARSE"
    REPROCESS = "REPROCESS"
    REEVALUATE = "REEVALUATE"
    OTHER = "OTHER"


class TriggerType(StrEnum):
    MANUAL = "MANUAL"
    AUTO = "AUTO"
    API = "API"
    SCHEDULED = "SCHEDULED"
    SYSTEM = "SYSTEM"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_file_id: int | None = Field(default=None, gt=0)
    import_batch_id: int | None = Field(default=None, gt=0)
    analysis_session_id: int | None = Field(default=None, gt=0)
    cleaner_release_id: int | None = Field(default=None, gt=0)
    job_type: JobType = JobType.PARSE
    trigger_type: TriggerType = TriggerType.MANUAL
    requested_by: str = Field(min_length=1, max_length=128)
    requested_by_user_id: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def validate_input(self) -> CreateJobRequest:
        input_count = sum(
            value is not None
            for value in (
                self.source_file_id,
                self.import_batch_id,
                self.analysis_session_id,
            )
        )
        if input_count != 1:
            raise ValueError(
                "exactly one of source_file_id, import_batch_id or "
                "analysis_session_id is required"
            )
        cleaner_jobs = {
            JobType.INITIAL_IMPORT,
            JobType.EXPORT_LATEST,
            JobType.REPROCESS_UPDATE,
            JobType.QUICK_PAT,
            JobType.PARSE,
            JobType.REPROCESS,
        }
        if self.job_type in cleaner_jobs and self.cleaner_release_id is None:
            raise ValueError("cleaner_release_id is required for Cleaner jobs")
        return self


class TransitionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_status: JobStatus
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_failure(self) -> TransitionJobRequest:
        if self.target_status == JobStatus.FAILED and not self.error_code:
            raise ValueError("error_code is required when target_status is FAILED")
        if self.target_status != JobStatus.FAILED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("error fields are only allowed for FAILED")
        return self


@dataclass(frozen=True, slots=True)
class Job:
    job_id: int
    source_file_id: int | None
    import_batch_id: int | None
    analysis_session_id: int | None
    cleaner_release_id: int | None
    job_type: JobType
    trigger_type: TriggerType
    requested_by: str
    requested_by_user_id: int | None
    reason: str | None
    status: JobStatus
    requested_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: str | None = None
    not_before_utc: datetime | None = None
    lease_token: str | None = None
    lease_owner: str | None = None
    lease_expires_at_utc: datetime | None = None
    heartbeat_at_utc: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3


ALLOWED_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.SUCCESS: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class InMemoryJobService:
    def __init__(self) -> None:
        self._items: dict[int, Job] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, request: CreateJobRequest) -> Job:
        with self._lock:
            job = Job(
                job_id=self._next_id,
                source_file_id=request.source_file_id,
                import_batch_id=request.import_batch_id,
                analysis_session_id=request.analysis_session_id,
                cleaner_release_id=request.cleaner_release_id,
                job_type=request.job_type,
                trigger_type=request.trigger_type,
                requested_by=request.requested_by,
                requested_by_user_id=request.requested_by_user_id,
                reason=request.reason,
                status=JobStatus.QUEUED,
                requested_at_utc=datetime.now(UTC),
                idempotency_key=request.idempotency_key,
                not_before_utc=datetime.now(UTC),
                max_attempts=request.max_attempts,
            )
            self._items[job.job_id] = job
            self._next_id += 1
            return job

    def get(self, job_id: int) -> Job:
        try:
            return self._items[job_id]
        except KeyError as exc:
            raise DomainError(
                code="JOB_NOT_FOUND",
                message=f"job {job_id} was not found",
                status_code=404,
            ) from exc

    def transition(self, job_id: int, request: TransitionJobRequest) -> Job:
        with self._lock:
            current = self.get(job_id)
            if request.target_status not in ALLOWED_JOB_TRANSITIONS[current.status]:
                raise DomainError(
                    code="INVALID_JOB_TRANSITION",
                    message=(
                        f"cannot transition job {job_id} from {current.status} "
                        f"to {request.target_status}"
                    ),
                    status_code=409,
                )
            now = datetime.now(UTC)
            updated = replace(
                current,
                status=request.target_status,
                started_at_utc=(
                    now
                    if request.target_status == JobStatus.RUNNING
                    else current.started_at_utc
                ),
                finished_at_utc=(
                    now
                    if request.target_status
                    in {JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED}
                    else current.finished_at_utc
                ),
                error_code=request.error_code,
                error_message=request.error_message,
            )
            self._items[job_id] = updated
            return updated


class JobService(Protocol):
    def create(self, request: CreateJobRequest) -> Job: ...

    def get(self, job_id: int) -> Job: ...

    def transition(self, job_id: int, request: TransitionJobRequest) -> Job: ...


class WorkerJobQueue(Protocol):
    def claim_next(
        self,
        worker_id: str,
        lease_for: timedelta,
        accepted_job_types: tuple[JobType, ...],
    ) -> Job | None: ...

    def heartbeat(self, job_id: int, lease_token: str, lease_for: timedelta) -> Job: ...

    def finish_leased(
        self,
        job_id: int,
        lease_token: str,
        target_status: JobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Job: ...
