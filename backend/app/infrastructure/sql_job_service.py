from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

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
attempt_count, max_attempts, parent_job_id
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
        parent_job_id=row.get("parent_job_id"),
    )


def _external_idempotency_key(user_id: int, raw_key: str) -> str:
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"api:{user_id}:{digest}"


def _lease_tokens_equal(stored: str | None, supplied: str) -> bool:
    try:
        return UUID(str(stored)) == UUID(supplied)
    except (AttributeError, TypeError, ValueError):
        return False


def _assert_idempotent_job_scope(
    existing: Job,
    request: CreateJobRequest,
    principal: Principal,
) -> None:
    matches = (
        existing.requested_by_user_id == principal.user_id
        and existing.requested_by == principal.login_name
        and existing.source_file_id == request.source_file_id
        and existing.import_batch_id == request.import_batch_id
        and existing.analysis_session_id == request.analysis_session_id
        and existing.cleaner_release_id == request.cleaner_release_id
        and existing.job_type == request.job_type
        and existing.trigger_type == request.trigger_type
        and existing.reason == request.reason
        and existing.max_attempts == request.max_attempts
    )
    if not matches:
        raise DomainError(
            "JOB_IDEMPOTENCY_SCOPE_CONFLICT",
            "幂等键已用于不同的任务范围，请更换幂等键",
            409,
        )


_QUEUEABLE_INITIAL_IMPORT_BATCH_STATUSES = frozenset(
    {"RECEIVED", "PROCESSED", "FAILED"}
)


def _raise_initial_import_batch_state_conflict(batch_status: str) -> None:
    if batch_status == "NEEDS_INPUT":
        raise DomainError(
            "LOT_INPUT_RESOLUTION_REQUIRED",
            "该批次正在等待Lot补录，请使用专用补录入口保存并恢复任务",
            409,
        )
    if batch_status in {"QUEUED", "PROCESSING"}:
        raise DomainError(
            "BATCH_ALREADY_ACTIVE",
            "该批次已有排队中或处理中的任务，不能重复提交",
            409,
        )
    raise DomainError(
        "BATCH_QUEUE_STATE_CONFLICT",
        f"当前批次状态不能进入正式导入队列：{batch_status}",
        409,
    )


def _mark_exhausted_initial_import_batches_failed(
    connection: Connection,
    exhausted_rows: list[Mapping[str, Any]],
    now: datetime,
) -> None:
    batch_ids = {
        int(row["import_batch_id"])
        for row in exhausted_rows
        if row["job_type"] == JobType.INITIAL_IMPORT.value
        and row["import_batch_id"] is not None
    }
    for batch_id in batch_ids:
        connection.execute(
            text(
                "UPDATE b SET status='FAILED',completed_at_utc=:now "
                "FROM ingestion.import_batch b WHERE b.import_batch_id=:batch "
                "AND b.status IN('QUEUED','PROCESSING') AND NOT EXISTS("
                "SELECT 1 FROM ingestion.processing_job active_job "
                "WHERE active_job.import_batch_id=b.import_batch_id "
                "AND active_job.job_type='INITIAL_IMPORT' "
                "AND active_job.status IN('QUEUED','RUNNING'))"
            ),
            {"now": now, "batch": batch_id},
        )


class SqlJobService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _create_with_connection(
        self,
        connection: Connection,
        request: CreateJobRequest,
        *,
        principal: Principal | None = None,
    ) -> Job:
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
        if request.idempotency_key:
            existing_row = (
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
            if existing_row is not None:
                existing = _to_job(existing_row)
                if principal is not None:
                    _assert_idempotent_job_scope(existing, request, principal)
                return existing
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

    def create(self, request: CreateJobRequest) -> Job:
        with self._engine.begin() as connection:
            return self._create_with_connection(connection, request)

    def create_for_principal(
        self, request: CreateJobRequest, principal: Principal
    ) -> Job:
        trusted = request.model_copy(
            update={
                "requested_by": principal.login_name,
                "requested_by_user_id": principal.user_id,
                "idempotency_key": (
                    _external_idempotency_key(
                        principal.user_id,
                        request.idempotency_key,
                    )
                    if request.idempotency_key
                    else None
                ),
            }
        )
        with self._engine.begin() as connection:
            if "SYSTEM_ADMIN" not in principal.roles:
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
            return self._create_with_connection(
                connection,
                trusted,
                principal=principal,
            )

    def create_initial_import_for_batch(
        self,
        request: CreateJobRequest,
        principal: Principal,
        *,
        allowed_batch_statuses: tuple[str, ...],
    ) -> Job:
        if request.job_type != JobType.INITIAL_IMPORT or request.import_batch_id is None:
            raise ValueError(
                "create_initial_import_for_batch requires an INITIAL_IMPORT batch job"
            )
        allowed = frozenset(status.strip().upper() for status in allowed_batch_statuses)
        if not allowed or not allowed.issubset(
            _QUEUEABLE_INITIAL_IMPORT_BATCH_STATUSES
        ):
            raise ValueError("invalid INITIAL_IMPORT source batch statuses")
        trusted = request.model_copy(
            update={
                "requested_by": principal.login_name,
                "requested_by_user_id": principal.user_id,
            }
        )
        with self._engine.begin() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT status,owner_user_id FROM ingestion.import_batch "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE import_batch_id=:batch"
                    ),
                    {"batch": request.import_batch_id},
                )
                .mappings()
                .one_or_none()
            )
            owner_user_id = batch["owner_user_id"] if batch is not None else None
            if batch is None or (
                "SYSTEM_ADMIN" not in principal.roles
                and (
                    owner_user_id is None
                    or int(owner_user_id) != principal.user_id
                )
            ):
                raise DomainError(
                    "BATCH_NOT_FOUND", "批次不存在或无权访问", 404
                )
            batch_status = str(batch["status"]).strip().upper()
            if batch_status not in allowed:
                _raise_initial_import_batch_state_conflict(batch_status)
            updated = connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='QUEUED',"
                    "completed_at_utc=NULL WHERE import_batch_id=:batch "
                    "AND status=:expected_status"
                ),
                {
                    "batch": request.import_batch_id,
                    "expected_status": batch_status,
                },
            )
            if updated.rowcount != 1:
                raise DomainError(
                    "BATCH_STATE_CONFLICT",
                    "上传任务状态已变化，无法进入处理队列",
                    409,
                )
            return self._create_with_connection(
                connection,
                trusted,
                principal=principal,
            )

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
            exhausted_rows = (
                connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET status='FAILED',"
                    "finished_at_utc=:now,error_code='MAX_ATTEMPTS_EXCEEDED',"
                    "error_message=N'Worker租约多次失效，已达到最大尝试次数',"
                    "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL "
                    "OUTPUT INSERTED.job_type,INSERTED.import_batch_id "
                    f"WHERE job_type IN ({type_placeholders}) "
                    "AND attempt_count>=max_attempts AND ("
                    "status='QUEUED' OR (status='RUNNING' AND lease_expires_at_utc<:now))"
                ),
                {"now": now, **type_parameters},
            )
                .mappings()
                .all()
            )
            _mark_exhausted_initial_import_batches_failed(
                connection,
                exhausted_rows,
                now,
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

    def pause_leased_for_input(
        self,
        job_id: int,
        lease_token: str,
        *,
        field_code: str,
        files: tuple[str, ...],
        message: str,
    ) -> Job:
        if field_code != "LOT_ID":
            raise ValueError("only LOT_ID input requests are supported")
        requested_names = tuple(dict.fromkeys(item.strip() for item in files if item.strip()))
        if not requested_names:
            raise ValueError("input-required files are required")
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._engine.begin() as connection:
            current_row = (
                connection.execute(
                    text(
                        f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                        "WITH (UPDLOCK,ROWLOCK) WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if current_row is None:
                raise DomainError("JOB_NOT_FOUND", f"job {job_id} was not found", 404)
            current = _to_job(current_row)
            if (
                current.status != JobStatus.RUNNING
                or not _lease_tokens_equal(current.lease_token, lease_token)
                or current.import_batch_id is None
            ):
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            batch_status = connection.execute(
                text(
                    "SELECT status FROM ingestion.import_batch WITH (UPDLOCK,ROWLOCK) "
                    "WHERE import_batch_id=:batch"
                ),
                {"batch": current.import_batch_id},
            ).scalar_one_or_none()
            if batch_status != "PROCESSING":
                raise DomainError(
                    "BATCH_NOT_PROCESSING",
                    "上传任务不在可暂停的处理中状态",
                    409,
                )
            receipt_rows = (
                connection.execute(
                    text(
                        "SELECT r.receipt_id,r.original_file_name FROM ingestion.import_batch_file ibf "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                        "WHERE ibf.import_batch_id=:batch ORDER BY ibf.ordinal_no"
                    ),
                    {"batch": current.import_batch_id},
                )
                .mappings()
                .all()
            )
            matched_rows: dict[int, Mapping[str, Any]] = {}
            for requested_name in requested_names:
                candidates = [
                    row
                    for row in receipt_rows
                    if str(row["original_file_name"]).casefold()
                    == requested_name.casefold()
                ]
                if not candidates:
                    candidates = [
                        row
                        for row in receipt_rows
                        if Path(str(row["original_file_name"])).name.casefold()
                        == requested_name.casefold()
                    ]
                if not candidates:
                    raise DomainError(
                        "INPUT_REQUEST_FILE_NOT_REGISTERED",
                        f"Cleaner请求补录的文件未登记在当前任务：{requested_name}",
                        409,
                    )
                for row in candidates:
                    matched_rows[int(row["receipt_id"])] = row
            evidence = {
                "field_code": field_code,
                "files": list(requested_names),
                "message": message,
            }
            for receipt_id, row in matched_rows.items():
                connection.execute(
                    text(
                        "INSERT ingestion.processing_input_request("
                        "job_id,import_batch_id,receipt_id,field_code,status,prompt,evidence_json) "
                        "VALUES(:job,:batch,:receipt,'LOT_ID','OPEN',:prompt,:evidence)"
                    ),
                    {
                        "job": job_id,
                        "batch": current.import_batch_id,
                        "receipt": receipt_id,
                        "prompt": message[:500],
                        "evidence": json.dumps(
                            {**evidence, "original_file_name": row["original_file_name"]},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                )
            updated = (
                connection.execute(
                    text(
                        f"UPDATE ingestion.processing_job SET status='NEEDS_INPUT',"
                        "finished_at_utc=:now,error_code='LOT_ID_REQUIRED',error_message=:message,"
                        "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL "
                        f"OUTPUT {', '.join('INSERTED.' + item.strip() for item in JOB_COLUMNS.split(','))} "
                        "WHERE job_id=:job AND status='RUNNING' "
                        "AND lease_token=CONVERT(uniqueidentifier,:lease_token)"
                    ),
                    {
                        "now": now,
                        "message": message[-2000:],
                        "job": job_id,
                        "lease_token": lease_token,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if updated is None:
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            batch_updated = connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='NEEDS_INPUT',completed_at_utc=NULL "
                    "WHERE import_batch_id=:batch AND status='PROCESSING'"
                ),
                {"batch": current.import_batch_id},
            )
            if batch_updated.rowcount != 1:
                raise DomainError(
                    "BATCH_STATE_CONFLICT", "上传任务状态已变化，无法暂停等待补录", 409
                )
            connection.execute(
                text(
                    "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                    "before_json,after_json,reason,correlation_id,actor_user_id) VALUES("
                    ":actor,'JOB_INPUT_REQUIRED','ingestion.processing_job',:entity,"
                    ":before_json,:after_json,:reason,:correlation,NULL)"
                ),
                {
                    "actor": f"worker:{current.lease_owner or 'unknown'}"[:128],
                    "entity": str(job_id),
                    "before_json": json.dumps(
                        {"job_status": "RUNNING", "batch_status": "PROCESSING"},
                        separators=(",", ":"),
                    ),
                    "after_json": json.dumps(
                        {
                            "job_status": "NEEDS_INPUT",
                            "batch_status": "NEEDS_INPUT",
                            "field_code": field_code,
                            "receipt_ids": sorted(matched_rows),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "reason": message[:1000],
                    "correlation": f"job:{job_id}",
                },
            )
        return _to_job(updated)

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
