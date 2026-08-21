from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.jobs import (
    ALLOWED_JOB_TRANSITIONS,
    CreateJobRequest,
    Job,
    JobStatus,
    JobType,
    TransitionJobRequest,
    TriggerType,
)


JOB_COLUMNS = """
job_id, source_file_id, import_batch_id, cleaner_release_id, job_type,
trigger_type, requested_by, reason, status, requested_at_utc,
started_at_utc, finished_at_utc, error_code, error_message
"""


def _to_job(row: Mapping[str, Any]) -> Job:
    return Job(
        job_id=row["job_id"],
        source_file_id=row["source_file_id"],
        import_batch_id=row["import_batch_id"],
        cleaner_release_id=row["cleaner_release_id"],
        job_type=JobType(row["job_type"]),
        trigger_type=TriggerType(row["trigger_type"]),
        requested_by=row["requested_by"],
        reason=row["reason"],
        status=JobStatus(row["status"]),
        requested_at_utc=row["requested_at_utc"].replace(tzinfo=UTC),
        started_at_utc=(
            row["started_at_utc"].replace(tzinfo=UTC)
            if row["started_at_utc"] is not None
            else None
        ),
        finished_at_utc=(
            row["finished_at_utc"].replace(tzinfo=UTC)
            if row["finished_at_utc"] is not None
            else None
        ),
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


class SqlJobService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, request: CreateJobRequest) -> Job:
        statement = text(
            f"""
            INSERT ingestion.processing_job(
                source_file_id, import_batch_id, cleaner_release_id,
                job_type, trigger_type, requested_by, reason, status
            )
            OUTPUT {', '.join('INSERTED.' + item.strip() for item in JOB_COLUMNS.split(','))}
            VALUES(
                :source_file_id, :import_batch_id, :cleaner_release_id,
                :job_type, :trigger_type, :requested_by, :reason, 'QUEUED'
            )
            """
        )
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    statement,
                    {
                        "source_file_id": request.source_file_id,
                        "import_batch_id": request.import_batch_id,
                        "cleaner_release_id": request.cleaner_release_id,
                        "job_type": request.job_type.value,
                        "trigger_type": request.trigger_type.value,
                        "requested_by": request.requested_by,
                        "reason": request.reason,
                    },
                )
                .mappings()
                .one()
            )
        return _to_job(row)

    def get(self, job_id: int) -> Job:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                        "WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                code="JOB_NOT_FOUND",
                message=f"job {job_id} was not found",
                status_code=404,
            )
        return _to_job(row)

    def transition(self, job_id: int, request: TransitionJobRequest) -> Job:
        with self._engine.begin() as connection:
            current_row = (
                connection.execute(
                    text(
                        f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                        "WITH (UPDLOCK, ROWLOCK) WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if current_row is None:
                raise DomainError(
                    code="JOB_NOT_FOUND",
                    message=f"job {job_id} was not found",
                    status_code=404,
                )
            current = _to_job(current_row)
            if request.target_status not in ALLOWED_JOB_TRANSITIONS[current.status]:
                raise DomainError(
                    code="INVALID_JOB_TRANSITION",
                    message=(
                        f"cannot transition job {job_id} from {current.status} "
                        f"to {request.target_status}"
                    ),
                    status_code=409,
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            terminal = request.target_status in {
                JobStatus.SUCCESS,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
            row = (
                connection.execute(
                    text(
                        f"""
                        UPDATE ingestion.processing_job
                        SET status=:status,
                            started_at_utc=CASE WHEN :status='RUNNING'
                                THEN :now ELSE started_at_utc END,
                            finished_at_utc=CASE WHEN :terminal=1
                                THEN :now ELSE finished_at_utc END,
                            error_code=:error_code,
                            error_message=:error_message
                        OUTPUT {', '.join('INSERTED.' + item.strip() for item in JOB_COLUMNS.split(','))}
                        WHERE job_id=:job_id
                        """
                    ),
                    {
                        "status": request.target_status.value,
                        "terminal": int(terminal),
                        "now": now,
                        "error_code": request.error_code,
                        "error_message": request.error_message,
                        "job_id": job_id,
                    },
                )
                .mappings()
                .one()
            )
        return _to_job(row)
