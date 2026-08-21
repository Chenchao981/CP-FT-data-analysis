from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol

from pydantic import ConfigDict, BaseModel, Field, model_validator

from app.core.errors import DomainError


class JobType(StrEnum):
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
    cleaner_release_id: int | None = Field(default=None, gt=0)
    job_type: JobType = JobType.PARSE
    trigger_type: TriggerType = TriggerType.MANUAL
    requested_by: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_input(self) -> "CreateJobRequest":
        if (self.source_file_id is None) == (self.import_batch_id is None):
            raise ValueError("exactly one of source_file_id or import_batch_id is required")
        if self.job_type in {JobType.PARSE, JobType.REPROCESS} and self.cleaner_release_id is None:
            raise ValueError("cleaner_release_id is required for parse or reprocess jobs")
        return self


class TransitionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_status: JobStatus
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_failure(self) -> "TransitionJobRequest":
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
    cleaner_release_id: int | None
    job_type: JobType
    trigger_type: TriggerType
    requested_by: str
    reason: str | None
    status: JobStatus
    requested_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


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
                cleaner_release_id=request.cleaner_release_id,
                job_type=request.job_type,
                trigger_type=request.trigger_type,
                requested_by=request.requested_by,
                reason=request.reason,
                status=JobStatus.QUEUED,
                requested_at_utc=datetime.now(UTC),
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
                    now if request.target_status == JobStatus.RUNNING else current.started_at_utc
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
