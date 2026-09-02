from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal, has_global_data_access
from app.domain.jobs import (
    ALLOWED_JOB_TRANSITIONS,
    CreateJobRequest,
    Job,
    JobStatus,
    JobType,
    TransitionJobRequest,
    TriggerType,
)
from app.infrastructure.sql_visibility import (
    batch_read_scope_sql,
    batch_write_scope_sql,
    domain_grant_exists_sql,
    quick_read_scope_sql,
    quick_write_scope_sql,
    visibility_parameters,
)


def _batch_job_input_scope_sql(*, batch_alias: str = "b") -> str:
    """Authorize task creation for global administrators, owners, or domain users."""

    return (
        "(EXISTS(SELECT 1 FROM iam.app_user job_requester "
        "WITH (UPDLOCK,HOLDLOCK) WHERE job_requester.user_id=:user_id "
        "AND job_requester.status='ACTIVE') AND (:is_admin=1 OR "
        f"({batch_alias}.access_scope='PERSONAL' "
        f"AND {batch_alias}.owner_user_id=:user_id) OR "
        f"({batch_alias}.access_scope='DOMAIN' AND "
        + domain_grant_exists_sql(
            data_domain_column=f"{batch_alias}.data_domain_id",
            lock_authorization_rows=True,
        )
        + "))))"
    )

_LIFECYCLE_APPLOCK_BY_JOB_SQL = (
    "DECLARE @tms_lifecycle_dataset_id bigint=("
    "SELECT dataset_id FROM ingestion.lifecycle_job_target WHERE job_id=:job_id); "
    "IF @tms_lifecycle_dataset_id IS NOT NULL BEGIN "
    "DECLARE @tms_lifecycle_resource nvarchar(255)="
    "N'TMS:LIFECYCLE:DATASET:'+CONVERT(nvarchar(20),@tms_lifecycle_dataset_id); "
    "DECLARE @tms_lifecycle_lock_result int; "
    "EXEC @tms_lifecycle_lock_result=sys.sp_getapplock "
    "@Resource=@tms_lifecycle_resource,@LockMode='Exclusive',"
    "@LockOwner='Transaction',@LockTimeout=10000; "
    "IF @tms_lifecycle_lock_result<0 "
    "RAISERROR('TMS lifecycle Dataset lock unavailable.',16,1); END; "
)

JOB_COLUMNS = """
job_id, source_file_id, import_batch_id, analysis_session_id, cleaner_release_id, job_type,
trigger_type, requested_by, requested_by_user_id, reason, status, requested_at_utc,
started_at_utc, finished_at_utc, error_code, error_message, idempotency_key,
not_before_utc, lease_token, lease_owner, lease_expires_at_utc, heartbeat_at_utc,
attempt_count, max_attempts, parent_job_id, finalize_protocol
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
        finalize_protocol=str(row.get("finalize_protocol") or "LEGACY"),
    )


def _external_idempotency_key(user_id: int, raw_key: str) -> str:
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return f"api:{user_id}:{digest}"


def _lease_tokens_equal(stored: str | None, supplied: str) -> bool:
    try:
        return UUID(str(stored)) == UUID(supplied)
    except (AttributeError, TypeError, ValueError):
        return False


def _leased_job_is_active(job: Job, supplied: str, now: datetime) -> bool:
    expires = job.lease_expires_at_utc
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return (
        _lease_tokens_equal(job.lease_token, supplied)
        and expires is not None
        and expires >= aware_now
    )


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
    for row in exhausted_rows:
        if (
            row["job_type"] == JobType.INITIAL_IMPORT.value
            and row.get("finalize_protocol") == "ATOMIC_V1"
            and row.get("job_id") is not None
            and row.get("import_batch_id") is not None
        ):
            _abort_atomic_initial_import_stage(
                connection,
                job_id=int(row["job_id"]),
                batch_id=int(row["import_batch_id"]),
                now=now,
                error_code="MAX_ATTEMPTS_EXCEEDED",
                error_message="Worker租约多次失效，已达到最大尝试次数",
            )
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


def _abort_atomic_initial_import_stage(
    connection: Connection,
    *,
    job_id: int,
    batch_id: int,
    now: datetime,
    error_code: str,
    error_message: str,
) -> None:
    intent = (
        connection.execute(
            text(
                "SELECT import_batch_id,processing_run_id,dataset_version_id,status "
                "FROM ingestion.initial_import_finalize_intent WITH (UPDLOCK,HOLDLOCK) "
                "WHERE job_id=:job"
            ),
            {"job": job_id},
        )
        .mappings()
        .one_or_none()
    )
    if intent is not None and int(intent["import_batch_id"]) != batch_id:
        raise DomainError(
            "ATOMIC_ABORT_SCOPE_MISMATCH",
            "Finalize Intent 与待失败批次不一致，拒绝清理",
            409,
        )
    if intent is not None and str(intent["status"]) not in {"STAGED", "ABORTED"}:
        raise DomainError(
            "ATOMIC_ABORT_STATE_CONFLICT",
            "已完成发布的 Finalize Intent 不得转为 ABORTED",
            409,
        )
    if intent is not None and str(intent["status"]) == "STAGED":
        run_failed = connection.execute(
            text(
                "UPDATE ingestion.processing_run SET status='FAILED',finished_at_utc=:now "
                "WHERE processing_run_id=:run AND status='READY'"
            ),
            {"run": intent["processing_run_id"], "now": now},
        )
        if run_failed.rowcount != 1:
            raise DomainError(
                "ATOMIC_ABORT_RUN_STATE_CONFLICT",
                "STAGED Processing Run 已发生状态漂移，拒绝清理",
                409,
            )
        version_archived = connection.execute(
            text(
                "UPDATE dataset.dataset_version SET status='ARCHIVED',is_current=0 "
                "WHERE dataset_version_id=:version AND status='DRAFT' AND is_current=0"
            ),
            {"version": intent["dataset_version_id"]},
        )
        if version_archived.rowcount != 1:
            raise DomainError(
                "ATOMIC_ABORT_VERSION_STATE_CONFLICT",
                "STAGED Dataset Version 已发生状态漂移，拒绝清理",
                409,
            )
        intent_aborted = connection.execute(
            text(
                "UPDATE ingestion.initial_import_finalize_intent SET status='ABORTED',"
                "aborted_at_utc=:now,abort_error_code=:code,abort_error_message=:message "
                "WHERE job_id=:job AND status='STAGED'"
            ),
            {
                "job": job_id,
                "now": now,
                "code": error_code[:64],
                "message": error_message[-2000:],
            },
        )
        if intent_aborted.rowcount != 1:
            raise DomainError(
                "ATOMIC_ABORT_INTENT_STATE_CONFLICT",
                "Finalize Intent 已发生状态漂移，拒绝清理",
                409,
            )
    batch_failed = connection.execute(
        text(
            "UPDATE ingestion.import_batch SET status='FAILED',completed_at_utc=:now "
            "WHERE import_batch_id=:batch AND status IN('QUEUED','PROCESSING')"
        ),
        {"batch": batch_id, "now": now},
    )
    if batch_failed.rowcount != 1 and not (
        intent is not None and str(intent["status"]) == "ABORTED"
    ):
        raise DomainError(
            "ATOMIC_ABORT_BATCH_STATE_CONFLICT",
            "正式导入批次已发生状态漂移，拒绝清理",
            409,
        )


class SqlJobService:
    def __init__(
        self,
        engine: Engine,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._fault_injector = fault_injector

    def _inject_fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

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
                reason, status, idempotency_key, max_attempts, finalize_protocol
            )
            OUTPUT {", ".join("INSERTED." + item.strip() for item in JOB_COLUMNS.split(","))}
            VALUES(
                :source_file_id, :import_batch_id, :analysis_session_id, :cleaner_release_id,
                :job_type, :trigger_type, :requested_by, :requested_by_user_id,
                :reason, 'QUEUED', :idempotency_key, :max_attempts, :finalize_protocol
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
                    "finalize_protocol": (
                        "ATOMIC_V1"
                        if request.job_type == JobType.INITIAL_IMPORT
                        else "LEGACY"
                    ),
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
            if request.analysis_session_id is not None:
                allowed = connection.execute(
                    text(
                        "SELECT 1 FROM workspace.analysis_session ws "
                        "WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE ws.analysis_session_id=:session AND "
                        + quick_write_scope_sql(
                            session_alias="ws", lock_authorization_rows=True
                        )
                    ),
                    visibility_parameters(principal)
                    | {"session": request.analysis_session_id},
                ).scalar_one_or_none()
                if allowed is None:
                    raise DomainError(
                        "JOB_INPUT_NOT_FOUND", "任务输入不存在或无权访问", 404
                    )
            else:
                if request.import_batch_id is not None:
                    allowed = connection.execute(
                        text(
                            "SELECT 1 FROM ingestion.import_batch b "
                            "WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE b.import_batch_id=:batch AND "
                            + _batch_job_input_scope_sql(batch_alias="b")
                        ),
                        visibility_parameters(principal)
                        | {"batch": request.import_batch_id},
                    ).scalar_one_or_none()
                elif request.source_file_id is not None:
                    allowed = connection.execute(
                        text(
                            "SELECT TOP (1) 1 FROM ingestion.source_file_receipt r "
                            "JOIN ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK) "
                            "ON b.import_batch_id=r.import_batch_id "
                            "WHERE r.source_file_id=:source AND "
                            + _batch_job_input_scope_sql(batch_alias="b")
                        ),
                        visibility_parameters(principal)
                        | {"source": request.source_file_id},
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
        if (
            request.job_type != JobType.INITIAL_IMPORT
            or request.import_batch_id is None
        ):
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
                        "SELECT status,owner_user_id,access_scope,data_domain_id,"
                        "source_channel,uploaded_by FROM ingestion.import_batch "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE import_batch_id=:batch"
                    ),
                    {"batch": request.import_batch_id},
                )
                .mappings()
                .one_or_none()
            )
            owner_user_id = batch["owner_user_id"] if batch is not None else None
            batch_status = (
                str(batch["status"]).strip().upper() if batch is not None else ""
            )
            can_queue = batch is not None and (
                has_global_data_access(principal)
                or (
                owner_user_id is not None
                and int(owner_user_id) == principal.user_id
                )
            )
            if (
                not can_queue
                and batch is not None
                and allowed == frozenset({"RECEIVED"})
                and batch_status == "RECEIVED"
                and str(batch["access_scope"]).strip().upper() == "DOMAIN"
                and batch["data_domain_id"] is not None
                and str(batch["source_channel"]).strip().upper() == "SOURCE_CATALOG"
                and str(batch["uploaded_by"] or "").strip() == principal.login_name
            ):
                can_queue = (
                    connection.execute(
                        text(
                            "SELECT TOP (1) 1 FROM iam.data_domain_grant g "
                            "WITH (UPDLOCK,HOLDLOCK) JOIN iam.data_domain d "
                            "WITH (UPDLOCK,HOLDLOCK) "
                            "ON d.data_domain_id=g.data_domain_id "
                            "JOIN iam.app_user u WITH (UPDLOCK,HOLDLOCK) "
                            "ON u.user_id=g.user_id WHERE g.user_id=:user_id "
                            "AND g.data_domain_id=:data_domain_id "
                            "AND g.status='ACTIVE' AND d.active=1 "
                            "AND u.status='ACTIVE' "
                            "AND (g.expires_at_utc IS NULL OR "
                            "g.expires_at_utc>SYSUTCDATETIME())"
                        ),
                        {
                            "user_id": principal.user_id,
                            "data_domain_id": int(batch["data_domain_id"]),
                        },
                    ).scalar_one_or_none()
                    is not None
                )
            if not can_queue:
                raise DomainError("BATCH_NOT_FOUND", "批次不存在或无权访问", 404)
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
                        "OUTPUT INSERTED.job_id,INSERTED.job_type,INSERTED.import_batch_id,"
                        "INSERTED.finalize_protocol "
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
                          AND lease_expires_at_utc>=:now
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
            current_row = (
                connection.execute(
                    text(
                        _LIFECYCLE_APPLOCK_BY_JOB_SQL
                        + f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if current_row is None:
                raise DomainError("JOB_NOT_FOUND", f"job {job_id} was not found", 404)
            current = _to_job(current_row)
            if current.status != JobStatus.RUNNING or not _leased_job_is_active(
                current, lease_token, now
            ):
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            atomic_initial_import = (
                current.job_type == JobType.INITIAL_IMPORT
                and current.finalize_protocol == "ATOMIC_V1"
            )
            if atomic_initial_import and target_status == JobStatus.SUCCESS:
                raise DomainError(
                    "ATOMIC_FINALIZE_REQUIRED",
                    "ATOMIC_V1正式导入必须通过原子Finalizer完成",
                    409,
                )
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
                          AND lease_expires_at_utc>=:now
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
            if (
                current.job_type == JobType.INITIAL_IMPORT
                and current.import_batch_id is not None
                and target_status in {JobStatus.FAILED, JobStatus.CANCELLED}
            ):
                failure_code = error_code or "JOB_CANCELLED"
                failure_message = error_message or "任务已取消"
                if atomic_initial_import:
                    _abort_atomic_initial_import_stage(
                        connection,
                        job_id=job_id,
                        batch_id=current.import_batch_id,
                        now=now,
                        error_code=failure_code,
                        error_message=failure_message,
                    )
                else:
                    connection.execute(
                        text(
                            "UPDATE ingestion.import_batch SET status='FAILED',completed_at_utc=:now "
                            "WHERE import_batch_id=:batch AND status IN('QUEUED','PROCESSING')"
                        ),
                        {"batch": current.import_batch_id, "now": now},
                    )
        return _to_job(row)

    def finalize_staged_initial_import_if_present(
        self,
        *,
        job_id: int,
        lease_token: str,
    ) -> Job | None:
        """Finalize a durable STAGED write without rerunning a non-byte-stable Cleaner."""

        with self._engine.begin() as connection:
            staged = (
                connection.execute(
                    text(
                        "SELECT j.status AS job_status,j.finalize_protocol,j.lease_token,"
                        "CASE WHEN j.lease_expires_at_utc>SYSUTCDATETIME() THEN 1 ELSE 0 END "
                        "AS lease_is_live,i.status AS intent_status,i.processing_run_id,"
                        "i.dataset_version_id,pr.metadata_json "
                        "FROM ingestion.processing_job j "
                        "LEFT JOIN ingestion.initial_import_finalize_intent i ON i.job_id=j.job_id "
                        "LEFT JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=i.processing_run_id "
                        "WHERE j.job_id=:job"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .one_or_none()
            )
        if staged is None:
            raise DomainError("JOB_NOT_FOUND", f"job {job_id} was not found", 404)
        if staged["intent_status"] is None:
            return None
        if (
            str(staged["job_status"]) != "RUNNING"
            or str(staged["finalize_protocol"]) != "ATOMIC_V1"
            or not _lease_tokens_equal(str(staged["lease_token"]), lease_token)
            or not bool(staged["lease_is_live"])
        ):
            raise DomainError(
                "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
            )
        if str(staged["intent_status"]) != "STAGED":
            raise DomainError(
                "ATOMIC_INTENT_STATE_CONFLICT",
                "已有原子发布意图不处于可恢复的 STAGED 状态",
                409,
            )
        try:
            metadata = json.loads(str(staged["metadata_json"] or "{}"))
            summary = metadata["atomic_finalize_summary"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DomainError(
                "ATOMIC_STAGED_SUMMARY_MISSING",
                "STAGED Processing Run 缺少可审计的发布摘要，不能自动恢复",
                409,
            ) from exc
        if not isinstance(summary, dict):
            raise DomainError(
                "ATOMIC_STAGED_SUMMARY_MISSING",
                "STAGED Processing Run 的发布摘要格式无效",
                409,
            )
        return self.finalize_initial_import(
            job_id=job_id,
            lease_token=lease_token,
            processing_run_id=int(staged["processing_run_id"]),
            dataset_version_id=int(staged["dataset_version_id"]),
            summary=summary,
        )

    def finalize_initial_import(
        self,
        *,
        job_id: int,
        lease_token: str,
        processing_run_id: int,
        dataset_version_id: int,
        summary: Mapping[str, Any],
    ) -> Job:
        """Publish Dataset, Result, Batch and Job in one lease-guarded transaction."""

        now = datetime.now(UTC).replace(tzinfo=None)
        with self._engine.begin() as connection:
            current_row = (
                connection.execute(
                    text(
                        _LIFECYCLE_APPLOCK_BY_JOB_SQL
                        + f"SELECT {JOB_COLUMNS} FROM ingestion.processing_job "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE job_id=:job_id"
                    ),
                    {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if current_row is None:
                raise DomainError("JOB_NOT_FOUND", f"job {job_id} was not found", 404)
            current = _to_job(current_row)
            intent = (
                connection.execute(
                    text(
                        "SELECT i.job_id,i.import_batch_id,i.processing_run_id,"
                        "i.dataset_version_id,i.status,i.finalized_lease_token,"
                        "dv.dataset_id,dv.version_no,dv.input_batch_id,dv.status AS version_status,"
                        "dv.is_current,dv.unit_count,dv.measurement_count,dv.spec_set_id,"
                        "pr.job_id AS run_job_id,pr.status AS run_status,"
                        "pr.is_current AS run_is_current,pr.source_file_id,"
                        "pr.unit_count_output,pr.measurement_count_output,"
                        "lt.action_type AS lifecycle_action_type,"
                        "lt.dataset_id AS lifecycle_dataset_id,"
                        "lt.target_dataset_version_id AS lifecycle_target_version_id,"
                        "ld.lifecycle_status AS lifecycle_dataset_status,"
                        "ldv.status AS lifecycle_target_version_status,"
                        "ldv.is_current AS lifecycle_target_is_current,"
                        "ldv.input_batch_id AS lifecycle_target_batch_id "
                        "FROM ingestion.initial_import_finalize_intent i WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN dataset.dataset_version dv WITH (UPDLOCK,HOLDLOCK) "
                        "ON dv.dataset_version_id=i.dataset_version_id "
                        "JOIN ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK) "
                        "ON pr.processing_run_id=i.processing_run_id "
                        "LEFT JOIN ingestion.lifecycle_job_target lt WITH (UPDLOCK,HOLDLOCK) "
                        "ON lt.job_id=i.job_id "
                        "LEFT JOIN dataset.dataset ld WITH (UPDLOCK,HOLDLOCK) "
                        "ON ld.dataset_id=lt.dataset_id "
                        "LEFT JOIN dataset.dataset_version ldv WITH (UPDLOCK,HOLDLOCK) "
                        "ON ldv.dataset_version_id=lt.target_dataset_version_id "
                        "WHERE i.job_id=:job"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if (
                current.status == JobStatus.SUCCESS
                and intent is not None
                and str(intent["status"]) == "FINALIZED"
                and _lease_tokens_equal(
                    str(intent["finalized_lease_token"]), lease_token
                )
                and int(intent["processing_run_id"]) == processing_run_id
                and int(intent["dataset_version_id"]) == dataset_version_id
            ):
                return current
            if (
                current.job_type != JobType.INITIAL_IMPORT
                or current.finalize_protocol != "ATOMIC_V1"
                or current.status != JobStatus.RUNNING
                or current.import_batch_id is None
                or not _leased_job_is_active(current, lease_token, now)
            ):
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            if intent is None or str(intent["status"]) != "STAGED":
                raise DomainError(
                    "ATOMIC_STAGE_NOT_READY",
                    "正式导入尚未形成可原子发布的 STAGED 版本",
                    409,
                )
            if (
                int(intent["import_batch_id"]) != current.import_batch_id
                or int(intent["processing_run_id"]) != processing_run_id
                or int(intent["dataset_version_id"]) != dataset_version_id
                or int(intent["input_batch_id"]) != current.import_batch_id
                or int(intent["run_job_id"]) != job_id
                or str(intent["version_status"]) != "DRAFT"
                or bool(intent["is_current"])
                or str(intent["run_status"]) != "READY"
                or bool(intent["run_is_current"])
                or intent["source_file_id"] is None
            ):
                raise DomainError(
                    "ATOMIC_STAGE_SCOPE_MISMATCH",
                    "STAGED Run、Dataset Version、Job 或 Batch 关系不一致",
                    409,
                )
            lifecycle_action = intent.get("lifecycle_action_type")
            if lifecycle_action is not None and (
                str(lifecycle_action) != "REPROCESS_UPDATE"
                or intent.get("lifecycle_dataset_id") is None
                or int(intent["lifecycle_dataset_id"]) != int(intent["dataset_id"])
                or intent.get("lifecycle_target_version_id") is None
                or int(intent["lifecycle_target_version_id"]) == dataset_version_id
                or str(intent.get("lifecycle_dataset_status")) != "ACTIVE"
                or str(intent.get("lifecycle_target_version_status")) != "PUBLISHED"
                or not bool(intent.get("lifecycle_target_is_current"))
                or int(intent.get("lifecycle_target_batch_id") or -1)
                != current.import_batch_id
            ):
                raise DomainError(
                    "LIFECYCLE_TARGET_DRIFTED",
                    "显式重处理目标已归档、已换版或与本次 Canonical 不一致",
                    409,
                )
            batch = (
                connection.execute(
                    text(
                        "SELECT b.status,b.owner_user_id,b.access_scope,b.data_domain_id,"
                        "b.source_definition_id,d.access_scope AS dataset_access_scope,"
                        "d.data_domain_id AS dataset_data_domain_id,"
                        "d.source_definition_id AS dataset_source_definition_id "
                        "FROM ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN dataset.dataset d ON d.dataset_id=:dataset "
                        "WHERE b.import_batch_id=:batch"
                    ),
                    {
                        "batch": current.import_batch_id,
                        "dataset": int(intent["dataset_id"]),
                    },
                )
                .mappings()
                .one_or_none()
            )
            if (
                batch is None
                or str(batch["status"]) != "PROCESSING"
                or batch["owner_user_id"] is None
            ):
                raise DomainError(
                    "BATCH_NOT_PROCESSING",
                    "正式导入批次不在可发布的处理中状态",
                    409,
                )
            if (
                str(batch["access_scope"]) != str(batch["dataset_access_scope"])
                or batch["data_domain_id"] != batch["dataset_data_domain_id"]
                or batch["source_definition_id"]
                != batch["dataset_source_definition_id"]
            ):
                raise DomainError(
                    "DATA_ACCESS_SCOPE_MISMATCH",
                    "Dataset 与输入批次的数据归属不一致",
                    409,
                )
            links = (
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT COUNT(*) FROM ingestion.import_batch_file "
                        "WHERE import_batch_id=:batch) AS batch_file_count,"
                        "(SELECT COUNT(*) FROM ingestion.processing_run_input_file "
                        "WHERE processing_run_id=:run) AS lineage_count,"
                        "(SELECT COUNT(*) FROM ingestion.processing_run_input_file ri "
                        "JOIN ingestion.import_batch_file ibf "
                        "ON ibf.import_batch_file_id=ri.import_batch_file_id "
                        "WHERE ri.processing_run_id=:run AND ibf.import_batch_id<>:batch) AS wrong_batch_count,"
                        "(SELECT COUNT(*) FROM ingestion.processing_run_input_file "
                        "WHERE processing_run_id=:run "
                        "AND lineage_basis<>'WRITER_VERIFIED') AS unverified_lineage_count,"
                        "(SELECT COUNT(*) FROM dataset.dataset_version_run "
                        "WHERE dataset_version_id=:version AND processing_run_id=:run "
                        "AND run_role='PRIMARY') AS version_run_count"
                    ),
                    {
                        "batch": current.import_batch_id,
                        "run": processing_run_id,
                        "version": dataset_version_id,
                    },
                )
                .mappings()
                .one()
            )
            if (
                int(links["batch_file_count"]) < 1
                or int(links["lineage_count"]) != int(links["batch_file_count"])
                or int(links["wrong_batch_count"]) != 0
                or int(links["unverified_lineage_count"]) != 0
                or int(links["version_run_count"]) != 1
            ):
                raise DomainError(
                    "ATOMIC_LINEAGE_INCOMPLETE",
                    "STAGED Run 未完整绑定当前批次的全部源文件",
                    409,
                )
            if int(intent["unit_count"]) != int(intent["unit_count_output"]) or int(
                intent["measurement_count"]
            ) != int(intent["measurement_count_output"]):
                raise DomainError(
                    "ATOMIC_COUNT_RECONCILIATION_FAILED",
                    "Dataset Version 与 Processing Run 的数据量不一致",
                    409,
                )
            if summary.get("unit_count") is not None and int(
                summary["unit_count"]
            ) != int(intent["unit_count"]):
                raise DomainError(
                    "ATOMIC_SUMMARY_RECONCILIATION_FAILED",
                    "结果摘要与 Canonical Unit 数量不一致",
                    409,
                )

            previous = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset AND status='PUBLISHED' AND is_current=1 "
                    "AND dataset_version_id<>:version"
                ),
                {
                    "dataset": intent["dataset_id"],
                    "version": dataset_version_id,
                },
            ).scalar_one_or_none()
            previous_run: int | None = None
            previous_run_ids: tuple[int, ...] = ()
            if previous is not None:
                previous_run_rows = (
                    connection.execute(
                        text(
                            "SELECT pr.processing_run_id,pr.status,pr.is_current,"
                            "CASE WHEN EXISTS(SELECT 1 "
                            "FROM dataset.dataset_version_run other_dvr "
                            "JOIN dataset.dataset_version other_dv "
                            "ON other_dv.dataset_version_id=other_dvr.dataset_version_id "
                            "WHERE other_dvr.processing_run_id=pr.processing_run_id "
                            "AND other_dv.dataset_version_id<>:previous "
                            "AND other_dv.status='PUBLISHED' AND other_dv.is_current=1) "
                            "THEN 1 ELSE 0 END AS has_other_current "
                            "FROM dataset.dataset_version_run dvr WITH (UPDLOCK,HOLDLOCK) "
                            "JOIN ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK) "
                            "ON pr.processing_run_id=dvr.processing_run_id "
                            "WHERE dvr.dataset_version_id=:previous "
                            "ORDER BY CASE WHEN dvr.run_role='PRIMARY' THEN 0 ELSE 1 END,"
                            "dvr.ordinal_no,pr.processing_run_id"
                        ),
                        {"previous": previous},
                    )
                    .mappings()
                    .all()
                )
                if not previous_run_rows or any(
                    row["status"] != "PUBLISHED" or not bool(row["is_current"])
                    for row in previous_run_rows
                ):
                    raise DomainError(
                        "ATOMIC_PREVIOUS_RUN_CONFLICT",
                        "旧 Current Dataset Version 的 Processing Run 状态不一致",
                        409,
                    )
                previous_run_ids = tuple(
                    int(row["processing_run_id"]) for row in previous_run_rows
                )
                previous_runs_to_supersede = sum(
                    not bool(row["has_other_current"]) for row in previous_run_rows
                )
                previous_run = previous_run_ids[0]
                previous_updated = connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='SUPERSEDED',is_current=0 "
                        "WHERE dataset_version_id=:previous AND status='PUBLISHED' AND is_current=1"
                    ),
                    {"previous": previous},
                )
                if previous_updated.rowcount != 1:
                    raise DomainError(
                        "ATOMIC_PREVIOUS_VERSION_CONFLICT",
                        "旧 Current Dataset Version 状态已变化",
                        409,
                    )
                previous_run_updated = connection.execute(
                    text(
                        "UPDATE pr SET pr.status='SUPERSEDED',pr.is_current=0 "
                        "FROM ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN dataset.dataset_version_run dvr "
                        "ON dvr.processing_run_id=pr.processing_run_id "
                        "WHERE dvr.dataset_version_id=:previous "
                        "AND pr.status='PUBLISHED' AND pr.is_current=1 "
                        "AND NOT EXISTS(SELECT 1 "
                        "FROM dataset.dataset_version_run other_dvr "
                        "JOIN dataset.dataset_version other_dv "
                        "ON other_dv.dataset_version_id=other_dvr.dataset_version_id "
                        "WHERE other_dvr.processing_run_id=pr.processing_run_id "
                        "AND other_dv.status='PUBLISHED' AND other_dv.is_current=1)"
                    ),
                    {"previous": previous},
                )
                if previous_run_updated.rowcount != previous_runs_to_supersede:
                    raise DomainError(
                        "ATOMIC_PREVIOUS_RUN_CONFLICT",
                        "旧 Current Processing Run 状态已变化",
                        409,
                    )
            self._inject_fault("after_previous_current_superseded")
            published = connection.execute(
                text(
                    "UPDATE dataset.dataset_version SET status='PUBLISHED',is_current=1,"
                    "published_by=:owner,published_at_utc=:now,"
                    "supersedes_dataset_version_id=:previous "
                    "WHERE dataset_version_id=:version AND status='DRAFT' AND is_current=0"
                ),
                {
                    "owner": int(batch["owner_user_id"]),
                    "now": now,
                    "previous": previous,
                    "version": dataset_version_id,
                },
            )
            if published.rowcount != 1:
                raise DomainError(
                    "ATOMIC_VERSION_STATE_CONFLICT",
                    "Dataset Version 状态已变化，无法发布",
                    409,
                )
            self._inject_fault("after_new_version_published")
            run_published = connection.execute(
                text(
                    "UPDATE ingestion.processing_run SET status='PUBLISHED',is_current=1,"
                    "supersedes_processing_run_id=:previous_run,finished_at_utc=:now "
                    "WHERE processing_run_id=:run AND status='READY' AND is_current=0"
                ),
                {
                    "run": processing_run_id,
                    "previous_run": previous_run,
                    "now": now,
                },
            )
            if run_published.rowcount != 1:
                raise DomainError(
                    "ATOMIC_RUN_STATE_CONFLICT",
                    "Processing Run 状态已变化，无法发布",
                    409,
                )
            connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET processing_run_id=:run "
                    "WHERE job_id=:job"
                ),
                {"run": processing_run_id, "job": job_id},
            )
            self._inject_fault("after_run_published")

            connection.execute(
                text(
                    "UPDATE ingestion.processing_result_summary SET status='ARCHIVED' "
                    "WHERE import_batch_id=:batch AND job_id<>:job AND status='PROCESSED'"
                ),
                {"batch": current.import_batch_id, "job": job_id},
            )
            result_values = {
                "batch": current.import_batch_id,
                "job": job_id,
                "name": summary["data_name"],
                "product": summary.get("product_name"),
                "lot": summary.get("lot_id"),
                "wafers": summary.get("wafer_count"),
                "factory": summary["factory_code"],
                "output": summary["output_uri"],
                "items": summary.get("test_item_count"),
                "units": int(intent["unit_count"]),
                "passes": summary.get("pass_count"),
                "yield_rate": summary.get("yield_rate"),
                "data_type": summary.get("data_type", "CP"),
                "dataset_id": int(intent["dataset_id"]),
                "dataset_version_no": int(intent["version_no"]),
                "manifest": json.dumps(
                    summary.get("artifacts", []), ensure_ascii=False
                ),
            }
            result_updated = connection.execute(
                text(
                    "UPDATE ingestion.processing_result_summary SET data_name=:name,"
                    "product_name=:product,lot_id=:lot,wafer_count=:wafers,"
                    "factory_code=:factory,output_uri=:output,test_item_count=:items,"
                    "unit_count=:units,pass_count=:passes,yield_rate=:yield_rate,"
                    "status='PROCESSED',data_type=:data_type,dataset_id=:dataset_id,"
                    "dataset_version_no=:dataset_version_no,artifact_manifest_json=:manifest "
                    "WHERE job_id=:job AND import_batch_id=:batch"
                ),
                result_values,
            )
            if result_updated.rowcount == 0:
                connection.execute(
                    text(
                        "INSERT ingestion.processing_result_summary(import_batch_id,job_id,data_name,"
                        "product_name,lot_id,wafer_count,factory_code,output_uri,test_item_count,unit_count,"
                        "pass_count,yield_rate,status,data_type,dataset_id,dataset_version_no,artifact_manifest_json) "
                        "VALUES(:batch,:job,:name,:product,:lot,:wafers,:factory,:output,:items,:units,"
                        ":passes,:yield_rate,'PROCESSED',:data_type,:dataset_id,:dataset_version_no,:manifest)"
                    ),
                    result_values,
                )
            self._inject_fault("after_result_persisted")
            batch_updated = connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='PROCESSED',completed_at_utc=:now "
                    "WHERE import_batch_id=:batch AND status='PROCESSING'"
                ),
                {"batch": current.import_batch_id, "now": now},
            )
            if batch_updated.rowcount != 1:
                raise DomainError(
                    "BATCH_STATE_CONFLICT", "批次状态已变化，无法完成原子发布", 409
                )
            self._inject_fault("after_batch_completed")
            updated_job = (
                connection.execute(
                    text(
                        f"UPDATE ingestion.processing_job SET status='SUCCESS',finished_at_utc=:now,"
                        "error_code=NULL,error_message=NULL,lease_token=NULL,lease_owner=NULL,"
                        "lease_expires_at_utc=NULL "
                        f"OUTPUT {', '.join('INSERTED.' + item.strip() for item in JOB_COLUMNS.split(','))} "
                        "WHERE job_id=:job AND status='RUNNING' "
                        "AND finalize_protocol='ATOMIC_V1' "
                        "AND lease_token=CONVERT(uniqueidentifier,:lease_token) "
                        "AND lease_expires_at_utc>=:now"
                    ),
                    {"job": job_id, "now": now, "lease_token": lease_token},
                )
                .mappings()
                .one_or_none()
            )
            if updated_job is None:
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            self._inject_fault("after_job_completed")
            intent_updated = connection.execute(
                text(
                    "UPDATE ingestion.initial_import_finalize_intent SET status='FINALIZED',"
                    "finalized_at_utc=:now,finalized_lease_token=CONVERT(uniqueidentifier,:lease_token) "
                    "WHERE job_id=:job AND status='STAGED'"
                ),
                {"job": job_id, "now": now, "lease_token": lease_token},
            )
            if intent_updated.rowcount != 1:
                raise DomainError(
                    "ATOMIC_INTENT_STATE_CONFLICT",
                    "Finalize Intent 状态已变化，无法提交",
                    409,
                )
            self._inject_fault("after_intent_finalized")
            connection.execute(
                text(
                    "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                    "before_json,after_json,reason,correlation_id,actor_user_id) VALUES("
                    ":actor,'INITIAL_IMPORT_ATOMIC_FINALIZE','ingestion.processing_job',:entity,"
                    ":before_json,:after_json,:reason,:correlation,:owner)"
                ),
                {
                    "actor": f"worker:{current.lease_owner or 'unknown'}"[:128],
                    "entity": str(job_id),
                    "before_json": json.dumps(
                        {
                            "job_status": "RUNNING",
                            "batch_status": "PROCESSING",
                            "version_status": "DRAFT",
                        },
                        separators=(",", ":"),
                    ),
                    "after_json": json.dumps(
                        {
                            "job_status": "SUCCESS",
                            "batch_status": "PROCESSED",
                            "version_status": "PUBLISHED",
                            "dataset_version_id": dataset_version_id,
                            "processing_run_id": processing_run_id,
                        },
                        separators=(",", ":"),
                    ),
                    "reason": "ATOMIC_V1 formal import finalize",
                    "correlation": f"job:{job_id}",
                    "owner": int(batch["owner_user_id"]),
                },
            )
        return _to_job(updated_job)

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
        requested_names = tuple(
            dict.fromkeys(item.strip() for item in files if item.strip())
        )
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
                or not _leased_job_is_active(current, lease_token, now)
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
                            {
                                **evidence,
                                "original_file_name": row["original_file_name"],
                            },
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
                        "AND lease_token=CONVERT(uniqueidentifier,:lease_token) "
                        "AND lease_expires_at_utc>=:now"
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
        with self._engine.connect() as connection:
            access = (
                connection.execute(
                    text(
                        "SELECT TOP (1) CASE WHEN "
                        ":is_admin=1 OR "
                        "(b.access_scope='PERSONAL' AND b.owner_user_id=:user_id) OR "
                        + quick_write_scope_sql(session_alias="ws")
                        + " OR "
                        "(b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL "
                        "AND j.requested_by_user_id=:user_id) "
                        "THEN 1 ELSE 0 END AS can_manage,b.business_domain "
                        "FROM ingestion.processing_job j "
                        "LEFT JOIN ingestion.import_batch b "
                        "ON b.import_batch_id=j.import_batch_id "
                        "LEFT JOIN workspace.analysis_session ws "
                        "ON ws.analysis_session_id=j.analysis_session_id "
                        "WHERE j.job_id=:job_id AND ("
                        + quick_read_scope_sql(session_alias="ws")
                        + " OR "
                        "(b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL "
                        "AND j.requested_by_user_id=:user_id) OR "
                        "(j.import_batch_id IS NOT NULL AND "
                        + batch_read_scope_sql(batch_alias="b")
                        + "))"
                    ),
                    visibility_parameters(principal) | {"job_id": job_id},
                )
                .mappings()
                .one_or_none()
            )
        if access is None:
            raise DomainError("JOB_NOT_FOUND", "任务不存在或无权访问", 404)
        job = self.get(job_id)
        if bool(access["can_manage"]):
            return job
        return replace(
            job,
            source_file_id=None,
            analysis_session_id=None,
            requested_by_user_id=None,
            reason=None,
            error_message=None,
            idempotency_key=None,
            lease_token=None,
            lease_owner=None,
        )

    def transition_for_principal(
        self,
        job_id: int,
        request: TransitionJobRequest,
        principal: Principal,
    ) -> Job:
        with self._engine.connect() as connection:
            allowed = connection.execute(
                text(
                    "SELECT TOP (1) 1 FROM ingestion.processing_job j "
                    "LEFT JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=j.import_batch_id "
                    "LEFT JOIN workspace.analysis_session ws "
                    "ON ws.analysis_session_id=j.analysis_session_id "
                    "WHERE j.job_id=:job_id AND ("
                    + quick_write_scope_sql(session_alias="ws")
                    + " OR "
                    "(b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL "
                    "AND j.requested_by_user_id=:user_id) OR "
                    "(j.import_batch_id IS NOT NULL AND "
                    + batch_write_scope_sql(batch_alias="b")
                    + "))"
                ),
                visibility_parameters(principal) | {"job_id": job_id},
            ).scalar_one_or_none()
        if allowed is None:
            raise DomainError("JOB_NOT_FOUND", "任务不存在或无权操作", 404)
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
