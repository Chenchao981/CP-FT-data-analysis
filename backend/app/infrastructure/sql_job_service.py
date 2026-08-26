from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
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
job_id, source_file_id, import_batch_id, analysis_session_id, cleaner_release_id, job_type,
trigger_type, requested_by, requested_by_user_id, reason, status, requested_at_utc,
started_at_utc, finished_at_utc, error_code, error_message, idempotency_key,
not_before_utc, lease_token, lease_owner, lease_expires_at_utc, heartbeat_at_utc,
attempt_count, max_attempts
"""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC)


def _to_job(row: Mapping[str, Any]) -> Job:
    return Job(
        job_id=row["job_id"],
        source_file_id=row["source_file_id"],
        import_batch_id=row["import_batch_id"],
        analysis_session_id=row["analysis_session_id"],
        cleaner_release_id=row["cleaner_release_id"],
        job_type=JobType(row["job_type"]),
        trigger_type=TriggerType(row["trigger_type"]),
        requested_by=row["requested_by"],
        requested_by_user_id=row["requested_by_user_id"],
        reason=row["reason"],
        status=JobStatus(row["status"]),
        requested_at_utc=_utc(row["requested_at_utc"]),
        started_at_utc=_utc(row["started_at_utc"]),
        finished_at_utc=_utc(row["finished_at_utc"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        idempotency_key=row["idempotency_key"],
        not_before_utc=_utc(row["not_before_utc"]),
        lease_token=str(row["lease_token"]) if row["lease_token"] is not None else None,
        lease_owner=row["lease_owner"],
        lease_expires_at_utc=_utc(row["lease_expires_at_utc"]),
        heartbeat_at_utc=_utc(row["heartbeat_at_utc"]),
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
    )


class SqlJobService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, request: CreateJobRequest) -> Job:
        statement = text(
            f"""
            INSERT ingestion.processing_job(
                source_file_id, import_batch_id, analysis_session_id, cleaner_release_id,
                job_type, trigger_type, requested_by, requested_by_user_id,
                reason, status, idempotency_key, max_attempts
            )
            OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
            VALUES(
                :source_file_id, :import_batch_id, :analysis_session_id, :cleaner_release_id,
                :job_type, :trigger_type, :requested_by, :requested_by_user_id,
                :reason, 'QUEUED', :idempotency_key, :max_attempts
            )
            """
        )
        with self._engine.begin() as connection:
            if request.idempotency_key:
                existing = (
                    connection.execute(
                        text(
                            f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                            "WITH (UPDLOCK,HOLDLOCK) WHERE idempotency_key=:key"
                        ),
                        {"key": request.idempotency_key},
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    return _to_job(existing)
            row = (
                connection.execute(
                    statement,
                    {
                        "source_file_id": request.source_file_id,
                        "import_batch_id": request.import_batch_id,
                        "analysis_session_id": request.analysis_session_id,
                        "cleaner_release_id": request.cleaner_release_id,
                        "job_type": request.job_type.value,
                        "trigger_type": request.trigger_type.value,
                        "requested_by": request.requested_by,
                        "requested_by_user_id": request.requested_by_user_id,
                        "reason": request.reason,
                        "idempotency_key": request.idempotency_key,
                        "max_attempts": request.max_attempts,
                    },
                )
                .mappings()
                .one()
            )
        return _to_job(row)

    def create_for_principal(
        self, request: CreateJobRequest, principal: Principal
    ) -> Job:
        if "SYSTEM_ADMIN" not in principal.roles:
            with self._engine.connect() as connection:
                if request.import_batch_id is not None:
                    allowed = connection.execute(
                        text(
                            "SELECT 1 FROM ingestion.import_batch "
                            "WHERE import_batch_id=:batch AND owner_user_id=:user_id"
                        ),
                        {
                            "batch": request.import_batch_id,
                            "user_id": principal.user_id,
                        },
                    ).scalar_one_or_none()
                elif request.source_file_id is not None:
                    allowed = connection.execute(
                        text(
                            "SELECT TOP (1) 1 FROM ingestion.source_file_receipt r "
                            "JOIN ingestion.import_batch b ON b.import_batch_id=r.import_batch_id "
                            "WHERE r.source_file_id=:source AND b.owner_user_id=:user_id"
                        ),
                        {
                            "source": request.source_file_id,
                            "user_id": principal.user_id,
                        },
                    ).scalar_one_or_none()
                else:
                    allowed = connection.execute(
                        text(
                            "SELECT 1 FROM workspace.analysis_session "
                            "WHERE analysis_session_id=:session AND owner_user_id=:user_id"
                        ),
                        {
                            "session": request.analysis_session_id,
                            "user_id": principal.user_id,
                        },
                    ).scalar_one_or_none()
            if allowed is None:
                raise DomainError(
                    "JOB_INPUT_NOT_FOUND", "任务输入不存在或无权访问", 404
                )
        trusted = request.model_copy(
            update={
                "requested_by": principal.login_name,
                "requested_by_user_id": principal.user_id,
            }
        )
        return self.create(trusted)

    def claim_next(
        self,
        worker_id: str,
        lease_for: timedelta,
        accepted_job_types: tuple[JobType, ...],
    ) -> Job | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease_for must be positive")
        if not accepted_job_types:
            raise ValueError("accepted_job_types is required")
        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + lease_for
        token = uuid4()
        type_parameters = {
            f"job_type_{index}": job_type.value
            for index, job_type in enumerate(accepted_job_types)
        }
        type_placeholders = ",".join(f":{name}" for name in type_parameters)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET status='FAILED',"
                    "finished_at_utc=:now,error_code='MAX_ATTEMPTS_EXCEEDED',"
                    "error_message=N'Worker租约多次失效，已达到最大尝试次数',"
                    "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL "
                    f"WHERE job_type IN ({type_placeholders}) "
                    "AND attempt_count>=max_attempts AND ("
                    "status='QUEUED' OR (status='RUNNING' AND lease_expires_at_utc<:now))"
                ),
                {"now": now, **type_parameters},
            )
            job_id = connection.execute(
                text(
                    f"""
                    SELECT TOP (1) job_id
                    FROM ingestion.processing_job WITH (UPDLOCK,READPAST,ROWLOCK)
                    WHERE job_type IN ({type_placeholders})
                      AND attempt_count<max_attempts
                      AND (
                        (status='QUEUED' AND not_before_utc<=:now)
                        OR
                        (status='RUNNING' AND lease_expires_at_utc<:now)
                      )
                    ORDER BY requested_at_utc,job_id
                    """
                ),
                {"now": now, **type_parameters},
            ).scalar_one_or_none()
            if job_id is None:
                return None
            row = (
                connection.execute(
                    text(
                        f"""
                        UPDATE ingestion.processing_job
                        SET status='RUNNING',
                            started_at_utc=COALESCE(started_at_utc,:now),
                            finished_at_utc=NULL,
                            lease_token=:token,
                            lease_owner=:worker_id,
                            lease_expires_at_utc=:expires,
                            heartbeat_at_utc=:now,
                            attempt_count=attempt_count+1,
                            error_code=NULL,
                            error_message=NULL
                        OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
                        WHERE job_id=:job_id
                        """
                    ),
                    {
                        "now": now,
                        "token": token,
                        "worker_id": worker_id.strip(),
                        "expires": expires,
                        "job_id": job_id,
                    },
                )
                .mappings()
                .one()
            )
        return _to_job(row)

    def heartbeat(self, job_id: int, lease_token: str, lease_for: timedelta) -> Job:
        if lease_for.total_seconds() <= 0:
            raise ValueError("lease_for must be positive")
        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + lease_for
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"""
                        UPDATE ingestion.processing_job
                        SET heartbeat_at_utc=:now,lease_expires_at_utc=:expires
                        OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
                        WHERE job_id=:job_id AND status='RUNNING'
                          AND lease_token=CONVERT(uniqueidentifier,:lease_token)
                        """
                    ),
                    {
                        "now": now,
                        "expires": expires,
                        "job_id": job_id,
                        "lease_token": lease_token,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
            )
        return _to_job(row)

    def finish_leased(
        self,
        job_id: int,
        lease_token: str,
        target_status: JobStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Job:
        if target_status not in {
            JobStatus.SUCCESS,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise ValueError("leased jobs can only finish in a terminal state")
        if target_status == JobStatus.FAILED and not error_code:
            raise ValueError("error_code is required for FAILED jobs")
        if target_status != JobStatus.FAILED and (error_code or error_message):
            raise ValueError("error details are only valid for FAILED jobs")
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        f"""
                        UPDATE ingestion.processing_job
                        SET status=:status,finished_at_utc=:now,
                            error_code=:error_code,error_message=:error_message,
                            lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL
                        OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
                        WHERE job_id=:job_id AND status='RUNNING'
                          AND lease_token=CONVERT(uniqueidentifier,:lease_token)
                        """
                    ),
                    {
                        "status": target_status.value,
                        "now": now,
                        "error_code": error_code,
                        "error_message": error_message,
                        "job_id": job_id,
                        "lease_token": lease_token,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
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

    def get_for_principal(self, job_id: int, principal: Principal) -> Job:
        if "SYSTEM_ADMIN" in principal.roles:
            return self.get(job_id)
        with self._engine.connect() as connection:
            allowed = connection.execute(
                text(
                    "SELECT TOP (1) 1 FROM ingestion.processing_job j "
                    "WHERE j.job_id=:job_id AND ("
                    "EXISTS(SELECT 1 FROM ingestion.import_batch b WHERE "
                    "b.import_batch_id=j.import_batch_id AND b.owner_user_id=:user_id) OR "
                    "EXISTS(SELECT 1 FROM ingestion.source_file_receipt r "
                    "JOIN ingestion.import_batch b ON b.import_batch_id=r.import_batch_id "
                    "WHERE r.source_file_id=j.source_file_id AND b.owner_user_id=:user_id) OR "
                    "EXISTS(SELECT 1 FROM workspace.analysis_session s WHERE "
                    "s.analysis_session_id=j.analysis_session_id AND s.owner_user_id=:user_id))"
                ),
                {"job_id": job_id, "user_id": principal.user_id},
            ).scalar_one_or_none()
        if allowed is None:
            raise DomainError("JOB_NOT_FOUND", "任务不存在或无权访问", 404)
        return self.get(job_id)

    def transition_for_principal(
        self,
        job_id: int,
        request: TransitionJobRequest,
        principal: Principal,
    ) -> Job:
        self.get_for_principal(job_id, principal)
        return self.transition(job_id, request)

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
                        OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
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
