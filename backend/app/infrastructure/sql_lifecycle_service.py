from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal, has_global_data_access
from app.domain.lifecycle import (
    LifecycleArtifact,
    LifecycleArtifactDownload,
    LifecycleExportStatus,
    LifecycleInputFile,
    LifecycleJobReceipt,
    LifecycleWorkerContext,
    TemporaryArtifactInput,
)
from app.infrastructure.formal_artifact_files import (
    ManagedJobPathPolicy,
    UnsafeFormalArtifactPath,
)
from app.infrastructure.sql_visibility import (
    data_read_scope_sql,
    visibility_parameters,
)

_FACTORY_ALIASES = {
    "HH": "HUAHONG",
    "华虹": "HUAHONG",
    "JT": "JETECH",
    "捷特": "JETECH",
    "立昂微": "LION",
    "国宇": "GUOYU",
    "国宇FRD": "GUOYU",
    "日月新": "RIYUEXIN",
    "ASE": "RIYUEGUANG",
    "日月光": "RIYUEGUANG",
    "电基": "DIANJI",
}

_DATASET_APPLOCK_SQL = (
    "DECLARE @tms_lifecycle_resource nvarchar(255)="
    "N'TMS:LIFECYCLE:DATASET:'+CONVERT(nvarchar(20),:dataset); "
    "DECLARE @tms_lifecycle_lock_result int; "
    "EXEC @tms_lifecycle_lock_result=sys.sp_getapplock "
    "@Resource=@tms_lifecycle_resource,@LockMode='Exclusive',"
    "@LockOwner='Transaction',@LockTimeout=10000; "
    "IF @tms_lifecycle_lock_result<0 "
    "RAISERROR('TMS lifecycle Dataset lock unavailable.',16,1); "
)

_JOB_DATASET_APPLOCK_SQL = (
    "DECLARE @tms_lifecycle_dataset_id bigint=("
    "SELECT dataset_id FROM ingestion.lifecycle_job_target WHERE job_id=:job); "
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


def _factory(value: str) -> str:
    normalized = value.strip().upper()
    return _FACTORY_ALIASES.get(normalized, normalized)


def _external_key(action: str, user_id: int, raw_key: str) -> str:
    digest = hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()
    return f"a5:{action.lower()}:{user_id}:{digest}"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _valid_lease_token(value: str) -> str:
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DomainError("JOB_LEASE_INVALID", "Worker Lease Token 无效", 409) from exc


def _same_lease_token(stored: object, supplied: str) -> bool:
    try:
        return UUID(str(stored)) == UUID(supplied)
    except (AttributeError, TypeError, ValueError):
        return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requester_dataset_access_sql(
    *,
    dataset_alias: str,
    requester_expression: str,
    lock_grants: bool,
) -> str:
    """Worker-side Dataset ACL including global administrator roles."""

    hint = " WITH (UPDLOCK,HOLDLOCK)" if lock_grants else ""
    return (
        f"(EXISTS(SELECT 1 FROM iam.app_user lifecycle_user{hint} "
        f"WHERE lifecycle_user.user_id={requester_expression} "
        "AND lifecycle_user.status='ACTIVE') AND ("
        f"EXISTS(SELECT 1 FROM iam.user_role lifecycle_ur{hint} "
        f"JOIN iam.role lifecycle_role{hint} ON lifecycle_role.role_id=lifecycle_ur.role_id "
        f"WHERE lifecycle_ur.user_id={requester_expression} "
        "AND lifecycle_role.role_code IN ('SYSTEM_ADMIN','DATA_DOMAIN_ADMIN')) OR "
        f"({dataset_alias}.access_scope='PERSONAL' "
        f"AND {dataset_alias}.owner_user_id={requester_expression}) OR "
        f"({dataset_alias}.access_scope='DOMAIN' AND EXISTS(SELECT 1 "
        f"FROM iam.data_domain_grant lifecycle_grant{hint} "
        f"JOIN iam.data_domain lifecycle_domain{hint} "
        "ON lifecycle_domain.data_domain_id=lifecycle_grant.data_domain_id "
        f"WHERE lifecycle_grant.data_domain_id={dataset_alias}.data_domain_id "
        f"AND lifecycle_grant.user_id={requester_expression} "
        "AND lifecycle_grant.status='ACTIVE' AND lifecycle_domain.active=1 "
        "AND (lifecycle_grant.expires_at_utc IS NULL "
        "OR lifecycle_grant.expires_at_utc>SYSUTCDATETIME())))))"
    )


class SqlLifecycleService:
    def __init__(
        self,
        engine: Engine,
        path_policy: ManagedJobPathPolicy,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._path_policy = path_policy
        self._fault_injector = fault_injector

    def _inject(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def create_export(
        self,
        dataset_id: int,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt:
        if not principal.can("EXPORT_DATA"):
            raise DomainError("PERMISSION_DENIED", "缺少权限：EXPORT_DATA", 403)
        return self._create_action(
            dataset_id=dataset_id,
            action="EXPORT_LATEST",
            principal=principal,
            reason=None,
            idempotency_key=idempotency_key,
            require_parent=False,
        )

    def create_archive(
        self,
        dataset_id: int,
        reason: str,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt:
        normalized_reason = reason.strip()
        if len(normalized_reason) < 8:
            raise DomainError(
                "ARCHIVE_REASON_REQUIRED", "归档原因至少需要8个字符", 422
            )
        return self._create_action(
            dataset_id=dataset_id,
            action="DELETE_TASK",
            principal=principal,
            reason=normalized_reason,
            idempotency_key=idempotency_key,
            require_parent=False,
        )

    def create_reprocess(
        self,
        dataset_id: int,
        reason: str,
        idempotency_key: str,
        principal: Principal,
    ) -> LifecycleJobReceipt:
        if not principal.can("TASK_CREATE"):
            raise DomainError("PERMISSION_DENIED", "缺少权限：TASK_CREATE", 403)
        normalized_reason = reason.strip()
        if len(normalized_reason) < 8:
            raise DomainError(
                "REPROCESS_REASON_REQUIRED", "重处理原因至少需要8个字符", 422
            )
        return self._create_action(
            dataset_id=dataset_id,
            action="REPROCESS_UPDATE",
            principal=principal,
            reason=normalized_reason,
            idempotency_key=idempotency_key,
            require_parent=True,
        )

    def _create_action(
        self,
        *,
        dataset_id: int,
        action: str,
        principal: Principal,
        reason: str | None,
        idempotency_key: str,
        require_parent: bool,
    ) -> LifecycleJobReceipt:
        if dataset_id < 1:
            raise DomainError("DATASET_ID_INVALID", "Dataset ID 无效", 422)
        internal_key = _external_key(action, principal.user_id, idempotency_key)
        with self._engine.begin() as connection:
            existing = self._existing_receipt(connection, internal_key)
            if existing is not None:
                if (
                    existing.dataset_id != dataset_id
                    or existing.action_type != action
                    or existing.idempotency_key != internal_key
                ):
                    raise DomainError(
                        "LIFECYCLE_IDEMPOTENCY_CONFLICT",
                        "幂等键已用于其他 Dataset 或操作",
                        409,
                    )
                scope_allowed = connection.execute(
                    text(
                        "SELECT CASE WHEN t.requested_by_user_id=:user_id "
                        "AND ((t.action_type='EXPORT_LATEST' AND "
                        + data_read_scope_sql(
                            access_scope_column="d.access_scope",
                            owner_column="d.owner_user_id",
                            data_domain_column="d.data_domain_id",
                        )
                        + ") OR (t.action_type<>'EXPORT_LATEST' AND "
                        "(:is_admin=1 OR (d.access_scope='PERSONAL' "
                        "AND d.owner_user_id=:user_id)))) "
                        "THEN 1 ELSE 0 END "
                        "FROM ingestion.lifecycle_job_target t "
                        "JOIN dataset.dataset d ON d.dataset_id=t.dataset_id "
                        "WHERE t.job_id=:job"
                    ),
                    visibility_parameters(principal)
                    | {
                        "job": existing.job_id,
                    },
                ).scalar_one()
                if int(scope_allowed) != 1:
                    raise DomainError(
                        "LIFECYCLE_IDEMPOTENCY_CONFLICT",
                        "幂等键不属于当前 Owner 或 Dataset 已转移",
                        403,
                    )
                return existing

            target = self._current_target(
                connection,
                dataset_id,
                principal,
                allow_domain_read=action == "EXPORT_LATEST",
            )
            active_jobs = int(target.get("active_lifecycle_job_count") or 0)
            active_mutations = int(
                target.get("active_lifecycle_mutation_count") or 0
            )
            if (
                action in {"DELETE_TASK", "REPROCESS_UPDATE"} and active_jobs
            ) or (action == "EXPORT_LATEST" and active_mutations):
                raise DomainError(
                    "LIFECYCLE_ACTION_IN_PROGRESS",
                    "该 Dataset 已有未完成的导出、重处理或归档任务",
                    409,
                )
            parent_job_id = self._parent_job_id(
                connection,
                int(target["dataset_version_id"]),
                required=require_parent or action == "EXPORT_LATEST",
            )
            release_id: int | None = None
            if action in {"EXPORT_LATEST", "REPROCESS_UPDATE"}:
                release_id = self._latest_release(
                    connection,
                    str(target["test_stage"]),
                    _factory(str(target["factory_code"])),
                    parent_job_id,
                )
            job_type = (
                "INITIAL_IMPORT" if action == "REPROCESS_UPDATE" else action
            )
            finalize_protocol = (
                "ATOMIC_V1" if action == "REPROCESS_UPDATE" else "LEGACY"
            )
            if action == "REPROCESS_UPDATE":
                self._queue_reprocess_batch(
                    connection,
                    int(target["input_batch_id"]),
                    str(target["batch_status"]),
                )
            inserted = (
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job("
                        "source_file_id,import_batch_id,analysis_session_id,"
                        "cleaner_release_id,job_type,trigger_type,requested_by,"
                        "requested_by_user_id,parent_job_id,reason,status,"
                        "idempotency_key,max_attempts,finalize_protocol) "
                        "OUTPUT INSERTED.job_id,INSERTED.status "
                        "VALUES(NULL,:batch,NULL,:release,:job_type,'API',:login,"
                        ":user_id,:parent,:reason,'QUEUED',:idempotency_key,"
                        "3,:finalize_protocol)"
                    ),
                    {
                        "batch": int(target["input_batch_id"]),
                        "release": release_id,
                        "job_type": job_type,
                        "login": principal.login_name,
                        "user_id": principal.user_id,
                        "parent": parent_job_id,
                        "reason": reason,
                        "idempotency_key": internal_key,
                        "finalize_protocol": finalize_protocol,
                    },
                )
                .mappings()
                .one()
            )
            job_id = int(inserted["job_id"])
            connection.execute(
                text(
                    "INSERT ingestion.lifecycle_job_target("
                    "job_id,dataset_id,target_dataset_version_id,action_type,"
                    "requested_by_user_id,request_reason) VALUES("
                    ":job,:dataset,:version,:action,:user_id,:reason)"
                ),
                {
                    "job": job_id,
                    "dataset": dataset_id,
                    "version": int(target["dataset_version_id"]),
                    "action": action,
                    "user_id": principal.user_id,
                    "reason": reason,
                },
            )
            self._audit(
                connection,
                actor=principal.login_name,
                actor_user_id=principal.user_id,
                operation="LIFECYCLE_TASK_CREATE",
                entity_type="ingestion.processing_job",
                entity_id=str(job_id),
                before=None,
                after={
                    "job_id": job_id,
                    "job_type": job_type,
                    "action_type": action,
                    "dataset_id": dataset_id,
                    "dataset_version_id": int(target["dataset_version_id"]),
                    "import_batch_id": int(target["input_batch_id"]),
                    "cleaner_release_id": release_id,
                    "parent_job_id": parent_job_id,
                    "status": "QUEUED",
                },
                reason=reason or "Explicit latest export request",
            )
            self._inject("after_lifecycle_task_create")
            return LifecycleJobReceipt(
                job_id=job_id,
                job_type=job_type,
                dataset_id=dataset_id,
                dataset_version_id=int(target["dataset_version_id"]),
                action_type=action,
                status=str(inserted["status"]),
                import_batch_id=int(target["input_batch_id"]),
                cleaner_release_id=release_id,
                parent_job_id=parent_job_id,
                idempotency_key=internal_key,
                created=True,
            )

    def _existing_receipt(
        self, connection: Connection, internal_key: str
    ) -> LifecycleJobReceipt | None:
        row = (
            connection.execute(
                text(
                    "SELECT j.job_id,j.job_type,j.status,j.import_batch_id,j.cleaner_release_id,"
                    "j.parent_job_id,j.idempotency_key,t.dataset_id,"
                    "t.target_dataset_version_id,t.action_type "
                    "FROM ingestion.processing_job j WITH (UPDLOCK,HOLDLOCK) "
                    "LEFT JOIN ingestion.lifecycle_job_target t WITH (HOLDLOCK) "
                    "ON t.job_id=j.job_id WHERE j.idempotency_key=:key"
                ),
                {"key": internal_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["dataset_id"] is None:
            raise DomainError(
                "LIFECYCLE_IDEMPOTENCY_CONFLICT",
                "幂等键已被非 Lifecycle Job 占用",
                409,
            )
        return LifecycleJobReceipt(
            job_id=int(row["job_id"]),
            job_type=str(row["job_type"]),
            dataset_id=int(row["dataset_id"]),
            dataset_version_id=int(row["target_dataset_version_id"]),
            action_type=str(row["action_type"]),
            status=str(row["status"]),
            import_batch_id=int(row["import_batch_id"]),
            cleaner_release_id=(
                int(row["cleaner_release_id"])
                if row["cleaner_release_id"] is not None
                else None
            ),
            parent_job_id=(
                int(row["parent_job_id"])
                if row["parent_job_id"] is not None
                else None
            ),
            idempotency_key=str(row["idempotency_key"]),
            created=False,
        )

    @staticmethod
    def _current_target(
        connection: Connection,
        dataset_id: int,
        principal: Principal,
        *,
        allow_domain_read: bool,
    ) -> Mapping[str, Any]:
        row = (
            connection.execute(
                text(
                    _DATASET_APPLOCK_SQL
                    +
                    "SELECT d.dataset_id,d.owner_user_id,d.access_scope,"
                    "d.data_domain_id,d.test_stage,CASE WHEN "
                    + data_read_scope_sql(
                        access_scope_column="d.access_scope",
                        owner_column="d.owner_user_id",
                        data_domain_column="d.data_domain_id",
                    )
                    + " THEN 1 ELSE 0 END AS can_read,"
                    "d.lifecycle_status,dv.dataset_version_id,dv.input_batch_id,"
                    "b.test_stage AS batch_test_stage,b.factory_code,"
                    "b.status AS batch_status,"
                    "(SELECT COUNT_BIG(*) FROM ingestion.import_batch_file ibf "
                    "WHERE ibf.import_batch_id=b.import_batch_id) AS input_file_count,"
                    "(SELECT COUNT_BIG(*) FROM ingestion.lifecycle_job_target active_target "
                    "WITH (UPDLOCK,HOLDLOCK) JOIN ingestion.processing_job active_job "
                    "WITH (UPDLOCK,HOLDLOCK) ON active_job.job_id=active_target.job_id "
                    "WHERE active_target.dataset_id=d.dataset_id "
                    "AND active_job.status IN('QUEUED','RUNNING','STAGED','NEEDS_INPUT')) "
                    "AS active_lifecycle_job_count,"
                    "(SELECT COUNT_BIG(*) FROM ingestion.lifecycle_job_target mutation_target "
                    "WITH (UPDLOCK,HOLDLOCK) JOIN ingestion.processing_job mutation_job "
                    "WITH (UPDLOCK,HOLDLOCK) ON mutation_job.job_id=mutation_target.job_id "
                    "WHERE mutation_target.dataset_id=d.dataset_id "
                    "AND mutation_target.action_type IN('REPROCESS_UPDATE','DELETE_TASK') "
                    "AND mutation_job.status IN('QUEUED','RUNNING','STAGED','NEEDS_INPUT')) "
                    "AS active_lifecycle_mutation_count "
                    "FROM dataset.dataset d WITH (UPDLOCK,HOLDLOCK) "
                    "JOIN dataset.dataset_version dv WITH (UPDLOCK,HOLDLOCK) "
                    "ON dv.dataset_id=d.dataset_id AND dv.status='PUBLISHED' "
                    "AND dv.is_current=1 "
                    "JOIN ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK) "
                    "ON b.import_batch_id=dv.input_batch_id "
                    "WHERE d.dataset_id=:dataset"
                ),
                visibility_parameters(principal) | {"dataset": dataset_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "CURRENT_DATASET_NOT_FOUND",
                "Dataset 不存在或没有 Current Published Version",
                404,
            )
        if allow_domain_read and not bool(row["can_read"]):
            raise DomainError(
                "DATASET_SCOPE_DENIED", "不能导出无权访问的 Dataset", 403
            )
        if not allow_domain_read and not has_global_data_access(principal) and (
            str(row["access_scope"]) != "PERSONAL"
            or int(row["owner_user_id"]) != principal.user_id
        ):
            raise DomainError(
                "DATASET_SCOPE_DENIED", "只能操作本人 PERSONAL Dataset", 403
            )
        if str(row["lifecycle_status"]) != "ACTIVE":
            raise DomainError("DATASET_ARCHIVED", "Dataset 已归档", 409)
        stage = str(row["test_stage"] or "").strip().upper()
        batch_stage = str(row["batch_test_stage"] or "").strip().upper()
        if stage not in {"CP", "FT"} or stage != batch_stage:
            raise DomainError(
                "DATASET_INPUT_SCOPE_INVALID",
                "Dataset 与其 Input Batch 测试阶段不一致",
                409,
            )
        if not str(row["factory_code"] or "").strip():
            raise DomainError(
                "DATASET_INPUT_SCOPE_INVALID", "Input Batch 缺少工厂标识", 409
            )
        if int(row["input_file_count"] or 0) < 1:
            raise DomainError(
                "INPUT_BATCH_EMPTY", "Current Dataset 的 Input Batch 没有已登记文件", 409
            )
        return row

    @staticmethod
    def _queue_reprocess_batch(
        connection: Connection, import_batch_id: int, batch_status: str
    ) -> None:
        status = batch_status.strip().upper()
        if status == "NEEDS_INPUT":
            raise DomainError(
                "LOT_INPUT_RESOLUTION_REQUIRED",
                "该批次正在等待Lot补录，请使用专用补录入口",
                409,
            )
        if status in {"QUEUED", "PROCESSING"}:
            raise DomainError(
                "BATCH_ALREADY_ACTIVE",
                "该批次已有排队中或处理中的任务",
                409,
            )
        if status not in {"PROCESSED", "FAILED"}:
            raise DomainError(
                "BATCH_REPROCESS_NOT_ALLOWED",
                f"当前批次状态不能重处理：{status}",
                409,
            )
        updated = connection.execute(
            text(
                "UPDATE ingestion.import_batch SET status='QUEUED',"
                "completed_at_utc=NULL WHERE import_batch_id=:batch "
                "AND status=:expected_status"
            ),
            {"batch": import_batch_id, "expected_status": status},
        )
        if updated.rowcount != 1:
            raise DomainError(
                "BATCH_STATE_CONFLICT",
                "Input Batch 状态已变化，无法进入重处理队列",
                409,
            )

    @staticmethod
    def _parent_job_id(
        connection: Connection, dataset_version_id: int, *, required: bool
    ) -> int | None:
        rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT r.job_id FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.processing_run r "
                    "ON r.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version"
                ),
                {"version": dataset_version_id},
            )
            .scalars()
            .all()
        )
        values = tuple(int(value) for value in rows)
        if len(values) == 1:
            return values[0]
        if required:
            raise DomainError(
                "REPROCESS_PARENT_AMBIGUOUS",
                "Current Dataset Version 无法唯一追溯原始 Job，拒绝重处理",
                409,
            )
        return None

    @staticmethod
    def _latest_release(
        connection: Connection,
        test_stage: str,
        factory_code: str,
        parent_job_id: int | None,
    ) -> int:
        if parent_job_id is None:
            raise DomainError(
                "CLEANER_PROFILE_LINEAGE_MISSING",
                "Current Dataset Version 无法追溯原始 Cleaner 格式合同",
                409,
            )
        value = connection.execute(
            text(
                "SELECT TOP (1) candidate.cleaner_release_id "
                "FROM ingestion.processing_job original_job WITH (HOLDLOCK) "
                "JOIN ingestion.cleaner_release original_release WITH (HOLDLOCK) "
                "ON original_release.cleaner_release_id=original_job.cleaner_release_id "
                "JOIN ingestion.format_profile fp WITH (HOLDLOCK) "
                "ON fp.format_profile_id=original_release.format_profile_id "
                "JOIN ingestion.cleaner_release candidate WITH (HOLDLOCK) "
                "ON candidate.format_profile_id=original_release.format_profile_id "
                "WHERE original_job.job_id=:parent AND candidate.status='RELEASED' "
                "AND fp.status='RELEASED' AND fp.test_stage=:stage "
                "AND fp.factory_code=:factory "
                "AND candidate.input_contract_version="
                "original_release.input_contract_version "
                "ORDER BY candidate.approved_at_utc DESC,"
                "candidate.created_at_utc DESC,candidate.cleaner_release_id DESC"
            ),
            {
                "parent": parent_job_id,
                "stage": test_stage.strip().upper(),
                "factory": factory_code,
            },
        ).scalar_one_or_none()
        if value is None:
            raise DomainError(
                "CLEANER_RELEASE_NOT_AVAILABLE",
                f"{test_stage}/{factory_code} 没有已发布 Cleaner",
                409,
            )
        return int(value)

    def worker_context(
        self, job_id: int, lease_token: str, action_type: str
    ) -> LifecycleWorkerContext:
        lease = _valid_lease_token(lease_token)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        _JOB_DATASET_APPLOCK_SQL
                        +
                        "SELECT j.job_id,j.job_type,j.import_batch_id,"
                        "j.cleaner_release_id,t.action_type,t.dataset_id,"
                        "t.target_dataset_version_id,t.requested_by_user_id,"
                        "t.request_reason,d.test_stage,d.lifecycle_status,"
                        "dv.status AS version_status,dv.is_current,"
                        "CASE WHEN "
                        + _requester_dataset_access_sql(
                            dataset_alias="d",
                            requester_expression="t.requested_by_user_id",
                            lock_grants=True,
                        )
                        + " THEN 1 ELSE 0 END AS can_execute,"
                        "b.test_stage AS batch_test_stage,b.factory_code "
                        "FROM ingestion.processing_job j WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN ingestion.lifecycle_job_target t WITH (HOLDLOCK) "
                        "ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d WITH (HOLDLOCK) "
                        "ON d.dataset_id=t.dataset_id "
                        "JOIN dataset.dataset_version dv WITH (HOLDLOCK) "
                        "ON dv.dataset_version_id=t.target_dataset_version_id "
                        "JOIN ingestion.import_batch b WITH (HOLDLOCK) "
                        "ON b.import_batch_id=j.import_batch_id "
                        "AND dv.input_batch_id=j.import_batch_id "
                        "WHERE j.job_id=:job AND j.status='RUNNING' "
                        "AND j.lease_token=CONVERT(uniqueidentifier,:lease) "
                        "AND j.lease_expires_at_utc>=SYSUTCDATETIME()"
                    ),
                    {"job": job_id, "lease": lease},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError(
                    "JOB_LEASE_LOST", "Lifecycle Job 不存在或 Lease 已失效", 409
                )
            if str(row["job_type"]) != action_type or str(row["action_type"]) != action_type:
                raise DomainError(
                    "LIFECYCLE_JOB_SCOPE_MISMATCH", "Lifecycle Job 类型与目标不一致", 409
                )
            if action_type in {"EXPORT_LATEST", "REPROCESS_UPDATE"} and (
                str(row["lifecycle_status"]) != "ACTIVE"
                or str(row["version_status"]) != "PUBLISHED"
                or not bool(row["is_current"])
            ):
                raise DomainError(
                    "LIFECYCLE_TARGET_DRIFTED",
                    "Lifecycle Job 目标已不是 Active Current Dataset",
                    409,
                )
            if action_type == "EXPORT_LATEST" and not bool(row["can_execute"]):
                raise DomainError(
                    "LIFECYCLE_EXPORT_ACCESS_REVOKED",
                    "Export Job requester no longer has access to its Dataset",
                    409,
                )
            stage = str(row["test_stage"] or "").strip().upper()
            if stage != str(row["batch_test_stage"] or "").strip().upper():
                raise DomainError(
                    "LIFECYCLE_TARGET_DRIFTED", "Dataset 与 Input Batch 阶段已漂移", 409
                )
            files = self._input_files(
                connection, int(row["import_batch_id"]), stage
            )
            return LifecycleWorkerContext(
                job_id=int(row["job_id"]),
                action_type=str(row["action_type"]),
                dataset_id=int(row["dataset_id"]),
                dataset_version_id=int(row["target_dataset_version_id"]),
                import_batch_id=int(row["import_batch_id"]),
                test_stage=stage,
                factory_code=_factory(str(row["factory_code"])),
                requested_by_user_id=int(row["requested_by_user_id"]),
                request_reason=(
                    str(row["request_reason"])
                    if row["request_reason"] is not None
                    else None
                ),
                files=files,
            )

    @staticmethod
    def _input_files(
        connection: Connection, import_batch_id: int, test_stage: str
    ) -> tuple[LifecycleInputFile, ...]:
        rows = (
            connection.execute(
                text(
                    "SELECT ibf.import_batch_file_id,r.original_file_name,"
                    "r.metadata_json,s.canonical_storage_uri,s.sha256,"
                    "lot.value_text AS lot_id_override "
                    "FROM ingestion.import_batch_file ibf "
                    "JOIN ingestion.source_file_receipt r "
                    "ON r.receipt_id=ibf.receipt_id "
                    "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                    "OUTER APPLY(SELECT TOP (1) e.value_text "
                    "FROM ingestion.field_enrichment e "
                    "WHERE e.import_batch_id=ibf.import_batch_id "
                    "AND e.test_stage=:stage AND e.field_code='LOT_ID' "
                    "AND e.action='FILL' AND e.is_current=1 "
                    "AND (e.source_file_id=r.source_file_id OR e.source_file_id IS NULL) "
                    "ORDER BY CASE WHEN e.source_file_id=r.source_file_id "
                    "THEN 0 ELSE 1 END,e.enrichment_id DESC) lot "
                    "WHERE ibf.import_batch_id=:batch ORDER BY ibf.ordinal_no"
                ),
                {"batch": import_batch_id, "stage": test_stage},
            )
            .mappings()
            .all()
        )
        files: list[LifecycleInputFile] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise DomainError(
                    "INPUT_RECEIPT_METADATA_INVALID",
                    "Input Batch 回执元数据无法解析",
                    409,
                ) from exc
            storage_uri = metadata.get("receipt_storage_uri") or row["canonical_storage_uri"]
            if not storage_uri:
                raise DomainError(
                    "INPUT_FILE_NOT_AVAILABLE", "Input Batch 文件缺少物理位置", 409
                )
            files.append(
                LifecycleInputFile(
                    import_batch_file_id=int(row["import_batch_file_id"]),
                    original_file_name=str(row["original_file_name"]),
                    storage_uri=str(storage_uri),
                    expected_sha256=(
                        str(row["sha256"]) if row["sha256"] is not None else None
                    ),
                    lot_id_override=(
                        str(row["lot_id_override"])
                        if row["lot_id_override"] is not None
                        else None
                    ),
                )
            )
        if not files:
            raise DomainError("INPUT_BATCH_EMPTY", "Input Batch 没有已登记文件", 409)
        return tuple(files)

    def record_export_artifacts(
        self,
        job_id: int,
        lease_token: str,
        artifacts: tuple[TemporaryArtifactInput, ...],
        expires_at_utc: datetime,
    ) -> tuple[LifecycleArtifact, ...]:
        if not artifacts:
            raise DomainError("EXPORT_ARTIFACT_EMPTY", "Cleaner 未生成导出文件", 409)
        lease = _valid_lease_token(lease_token)
        expires = _as_utc(expires_at_utc)
        now = datetime.now(UTC)
        if expires <= now or (expires - now).days > 30:
            raise DomainError("EXPORT_TTL_INVALID", "导出文件 TTL 无效", 409)
        validated: list[tuple[TemporaryArtifactInput, Path]] = []
        for artifact in artifacts:
            if not artifact.role.strip() or len(artifact.role.strip()) > 64:
                raise DomainError(
                    "EXPORT_ARTIFACT_CONTRACT_INVALID", "导出文件 Role 无效", 409
                )
            try:
                path = self._path_policy.require_artifact(
                    job_id, artifact.path, must_exist=True
                )
            except (FileNotFoundError, UnsafeFormalArtifactPath) as exc:
                raise DomainError(
                    "EXPORT_ARTIFACT_PATH_INVALID",
                    "导出文件不在该 Job 受管目录或不存在",
                    409,
                ) from exc
            if len(str(path)) > 1000 or len(path.name) > 500:
                raise DomainError(
                    "EXPORT_ARTIFACT_CONTRACT_INVALID", "导出文件路径超出登记上限", 409
                )
            if artifact.size_bytes < 0 or path.stat().st_size != artifact.size_bytes:
                raise DomainError(
                    "EXPORT_ARTIFACT_INTEGRITY_MISMATCH", "导出文件大小与登记值不一致", 409
                )
            sha256 = artifact.sha256.strip().lower()
            if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
                raise DomainError(
                    "EXPORT_ARTIFACT_INTEGRITY_MISMATCH", "导出文件 SHA256 无效", 409
                )
            if _hash_file(path) != sha256:
                raise DomainError(
                    "EXPORT_ARTIFACT_INTEGRITY_MISMATCH", "导出文件 SHA256 不一致", 409
                )
            validated.append((artifact, path))

        with self._engine.begin() as connection:
            scope = connection.execute(
                text(
                    _JOB_DATASET_APPLOCK_SQL
                    +
                    "SELECT CASE WHEN "
                    + _requester_dataset_access_sql(
                        dataset_alias="d",
                        requester_expression="t.requested_by_user_id",
                        lock_grants=True,
                    )
                    + " THEN 1 ELSE 0 END "
                    "FROM ingestion.processing_job j WITH (UPDLOCK,HOLDLOCK) "
                    "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                    "JOIN dataset.dataset d WITH (UPDLOCK,HOLDLOCK) "
                    "ON d.dataset_id=t.dataset_id "
                    "WHERE j.job_id=:job AND j.job_type='EXPORT_LATEST' "
                    "AND t.action_type='EXPORT_LATEST' AND j.status='RUNNING' "
                    "AND j.lease_token=CONVERT(uniqueidentifier,:lease) "
                    "AND j.lease_expires_at_utc>=SYSUTCDATETIME()"
                ),
                {"job": job_id, "lease": lease},
            ).scalar_one_or_none()
            if scope is None:
                raise DomainError("JOB_LEASE_LOST", "Export Job Lease 已失效", 409)
            if int(scope) != 1:
                raise DomainError(
                    "LIFECYCLE_EXPORT_ACCESS_REVOKED",
                    "Export Job requester no longer has access to its Dataset",
                    409,
                )
            for artifact, path in validated:
                connection.execute(
                    text(
                        "IF NOT EXISTS(SELECT 1 FROM ingestion.processing_artifact "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE job_id=:job "
                        "AND artifact_role=:role AND sha256=:sha256) "
                        "INSERT ingestion.processing_artifact("
                        "job_id,processing_run_id,artifact_role,file_name,storage_uri,"
                        "file_size,sha256,temporary_flag,expires_at_utc,physical_status) "
                        "VALUES(:job,NULL,:role,:file_name,:storage_uri,:file_size,"
                        ":sha256,1,:expires,'PRESENT')"
                    ),
                    {
                        "job": job_id,
                        "role": artifact.role.strip(),
                        "file_name": path.name,
                        "storage_uri": str(path),
                        "file_size": artifact.size_bytes,
                        "sha256": artifact.sha256.lower(),
                        "expires": expires.replace(tzinfo=None),
                    },
                )
            self._inject("after_export_artifact_insert")
            rows = (
                connection.execute(
                    text(
                        "SELECT processing_artifact_id,job_id,artifact_role,file_name,"
                        "file_size,sha256,expires_at_utc,physical_status "
                        "FROM ingestion.processing_artifact WHERE job_id=:job "
                        "AND temporary_flag=1 ORDER BY processing_artifact_id"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .all()
            )
            terminal = connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET status='SUCCESS',"
                    "finished_at_utc=SYSUTCDATETIME(),error_code=NULL,"
                    "error_message=NULL,lease_token=NULL,lease_owner=NULL,"
                    "lease_expires_at_utc=NULL,heartbeat_at_utc=NULL "
                    "WHERE job_id=:job AND status='RUNNING' "
                    "AND lease_token=CONVERT(uniqueidentifier,:lease) "
                    "AND lease_expires_at_utc>=SYSUTCDATETIME() AND EXISTS("
                    "SELECT 1 FROM ingestion.lifecycle_job_target finalize_t "
                    "JOIN dataset.dataset finalize_d WITH (UPDLOCK,HOLDLOCK) "
                    "ON finalize_d.dataset_id=finalize_t.dataset_id "
                    "WHERE finalize_t.job_id=:job AND "
                    + _requester_dataset_access_sql(
                        dataset_alias="finalize_d",
                        requester_expression="finalize_t.requested_by_user_id",
                        lock_grants=True,
                    )
                    + ")"
                ),
                {"job": job_id, "lease": lease},
            )
            if terminal.rowcount != 1:
                raise DomainError(
                    "JOB_LEASE_LOST",
                    "Export Job 终态与 Artifact 无法原子提交",
                    409,
                )
            self._inject("after_export_job_success")
        return tuple(self._artifact(row) for row in rows)

    def archive_dataset_leased(self, job_id: int, lease_token: str) -> None:
        lease = _valid_lease_token(lease_token)
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        _JOB_DATASET_APPLOCK_SQL
                        +
                        "SELECT j.job_id,j.job_type,j.status,j.requested_by,"
                        "j.requested_by_user_id,j.lease_token,j.lease_expires_at_utc,"
                        "t.action_type,t.dataset_id,t.target_dataset_version_id,"
                        "t.request_reason,d.lifecycle_status,d.owner_user_id,"
                        "d.archived_at_utc,d.archived_by_user_id,d.archive_reason,"
                        "dv.status AS version_status,dv.is_current,dv.version_no "
                        "FROM ingestion.processing_job j WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN ingestion.lifecycle_job_target t WITH (HOLDLOCK) "
                        "ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d WITH (UPDLOCK,HOLDLOCK) "
                        "ON d.dataset_id=t.dataset_id "
                        "JOIN dataset.dataset_version dv WITH (UPDLOCK,HOLDLOCK) "
                        "ON dv.dataset_version_id=t.target_dataset_version_id "
                        "WHERE j.job_id=:job"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError("LIFECYCLE_JOB_NOT_FOUND", "归档 Job 不存在", 404)
            if str(row["job_type"]) != "DELETE_TASK" or str(row["action_type"]) != "DELETE_TASK":
                raise DomainError(
                    "LIFECYCLE_JOB_SCOPE_MISMATCH", "该 Job 不是 Dataset 归档任务", 409
                )
            run_rows = (
                connection.execute(
                    text(
                        "SELECT r.processing_run_id,r.status,r.is_current "
                        "FROM dataset.dataset_version_run dvr WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN ingestion.processing_run r WITH (UPDLOCK,HOLDLOCK) "
                        "ON r.processing_run_id=dvr.processing_run_id "
                        "WHERE dvr.dataset_version_id=:version "
                        "ORDER BY r.processing_run_id"
                    ),
                    {"version": int(row["target_dataset_version_id"])},
                )
                .mappings()
                .all()
            )
            if not run_rows:
                raise DomainError(
                    "ARCHIVE_RUN_LINEAGE_MISSING",
                    "Dataset Version 没有 Processing Run 血缘，拒绝归档",
                    409,
                )
            run_ids = tuple(int(item["processing_run_id"]) for item in run_rows)
            shared_run = connection.execute(
                text(
                    "SELECT TOP (1) target.processing_run_id "
                    "FROM dataset.dataset_version_run target WITH (HOLDLOCK) "
                    "JOIN dataset.dataset_version_run other_link WITH (HOLDLOCK) "
                    "ON other_link.processing_run_id=target.processing_run_id "
                    "AND other_link.dataset_version_id<>target.dataset_version_id "
                    "JOIN dataset.dataset_version other_version WITH (HOLDLOCK) "
                    "ON other_version.dataset_version_id=other_link.dataset_version_id "
                    "JOIN dataset.dataset other_dataset WITH (HOLDLOCK) "
                    "ON other_dataset.dataset_id=other_version.dataset_id "
                    "WHERE target.dataset_version_id=:version "
                    "AND other_version.status='PUBLISHED' "
                    "AND other_version.is_current=1 "
                    "AND other_dataset.lifecycle_status='ACTIVE'"
                ),
                {"version": int(row["target_dataset_version_id"])},
            ).scalar_one_or_none()
            if shared_run is not None:
                raise DomainError(
                    "ARCHIVE_RUN_SHARED_WITH_ACTIVE_CURRENT",
                    "Processing Run 被其他 Active Current Dataset Version 共享，拒绝归档",
                    409,
                )
            if str(row["status"]) == "SUCCESS":
                if (
                    str(row["lifecycle_status"]) == "ARCHIVED"
                    and str(row["version_status"]) == "ARCHIVED"
                    and not bool(row["is_current"])
                    and all(
                        str(item["status"]) == "SUPERSEDED"
                        and not bool(item["is_current"])
                        for item in run_rows
                    )
                ):
                    return
                raise DomainError(
                    "ARCHIVE_STATE_INCONSISTENT", "归档 Job 与 Dataset 状态不一致", 409
                )
            now = datetime.now(UTC).replace(tzinfo=None)
            if (
                str(row["status"]) != "RUNNING"
                or not _same_lease_token(row["lease_token"], lease)
                or row["lease_expires_at_utc"] is None
                or _as_utc(row["lease_expires_at_utc"]) < datetime.now(UTC)
            ):
                raise DomainError("JOB_LEASE_LOST", "归档 Job Lease 已失效", 409)
            reason = str(row["request_reason"] or "").strip()
            if len(reason) < 8:
                raise DomainError("ARCHIVE_REASON_REQUIRED", "归档 Job 缺少有效原因", 409)
            if (
                str(row["lifecycle_status"]) != "ACTIVE"
                or str(row["version_status"]) != "PUBLISHED"
                or not bool(row["is_current"])
            ):
                raise DomainError(
                    "ARCHIVE_TARGET_DRIFTED", "Dataset 已不是请求时的 Current 版本", 409
                )
            if any(
                str(item["status"]) != "PUBLISHED" or not bool(item["is_current"])
                for item in run_rows
            ):
                raise DomainError(
                    "ARCHIVE_RUN_STATE_DRIFTED",
                    "Dataset Version 的 Processing Run 不是 Current Published 状态",
                    409,
                )
            version_update = connection.execute(
                text(
                    "UPDATE dataset.dataset_version SET status='ARCHIVED',is_current=0 "
                    "WHERE dataset_version_id=:version AND dataset_id=:dataset "
                    "AND status='PUBLISHED' AND is_current=1"
                ),
                {
                    "version": int(row["target_dataset_version_id"]),
                    "dataset": int(row["dataset_id"]),
                },
            )
            if version_update.rowcount != 1:
                raise DomainError(
                    "ARCHIVE_TARGET_DRIFTED", "Current Dataset Version 已发生变化", 409
                )
            self._inject("after_archive_version_update")
            run_update = connection.execute(
                text(
                    "UPDATE r SET status='SUPERSEDED',is_current=0 "
                    "FROM ingestion.processing_run r "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.processing_run_id=r.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version "
                    "AND r.status='PUBLISHED' AND r.is_current=1"
                ),
                {"version": int(row["target_dataset_version_id"])},
            )
            if run_update.rowcount != len(run_ids):
                raise DomainError(
                    "ARCHIVE_RUN_STATE_DRIFTED",
                    "Processing Run Current 状态已发生变化",
                    409,
                )
            self._inject("after_archive_run_update")
            connection.execute(
                text(
                    "UPDATE ingestion.processing_result_summary SET status='ARCHIVED' "
                    "WHERE dataset_id=:dataset AND dataset_version_no=:version_no "
                    "AND status='PROCESSED'"
                ),
                {
                    "dataset": int(row["dataset_id"]),
                    "version_no": int(row["version_no"]),
                },
            )
            self._inject("after_archive_result_summary_update")
            dataset_update = connection.execute(
                text(
                    "UPDATE dataset.dataset SET lifecycle_status='ARCHIVED',"
                    "archived_at_utc=:now,archived_by_user_id=:actor,"
                    "archive_reason=:reason WHERE dataset_id=:dataset "
                    "AND lifecycle_status='ACTIVE'"
                ),
                {
                    "now": now,
                    "actor": int(row["requested_by_user_id"]),
                    "reason": reason,
                    "dataset": int(row["dataset_id"]),
                },
            )
            if dataset_update.rowcount != 1:
                raise DomainError(
                    "ARCHIVE_TARGET_DRIFTED", "Dataset 逻辑状态已发生变化", 409
                )
            self._inject("after_archive_dataset_update")
            self._audit(
                connection,
                actor=str(row["requested_by"]),
                actor_user_id=int(row["requested_by_user_id"]),
                operation="DATASET_LOGICAL_ARCHIVE",
                entity_type="dataset.dataset",
                entity_id=str(row["dataset_id"]),
                before={
                    "lifecycle_status": "ACTIVE",
                    "dataset_version_id": int(row["target_dataset_version_id"]),
                    "dataset_version_status": "PUBLISHED",
                    "is_current": True,
                    "processing_runs": [
                        {
                            "processing_run_id": run_id,
                            "status": "PUBLISHED",
                            "is_current": True,
                        }
                        for run_id in run_ids
                    ],
                },
                after={
                    "lifecycle_status": "ARCHIVED",
                    "dataset_version_id": int(row["target_dataset_version_id"]),
                    "dataset_version_status": "ARCHIVED",
                    "is_current": False,
                    "processing_runs": [
                        {
                            "processing_run_id": run_id,
                            "status": "SUPERSEDED",
                            "is_current": False,
                        }
                        for run_id in run_ids
                    ],
                    "canonical_facts_deleted": False,
                    "source_deleted": False,
                    "input_batch_changed": False,
                    "result_summary_status": "ARCHIVED",
                },
                reason=reason,
            )
            terminal = connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET status='SUCCESS',"
                    "finished_at_utc=:now,error_code=NULL,error_message=NULL,"
                    "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,"
                    "heartbeat_at_utc=NULL WHERE job_id=:job AND status='RUNNING' "
                    "AND lease_token=CONVERT(uniqueidentifier,:lease)"
                ),
                {"now": now, "job": job_id, "lease": lease},
            )
            if terminal.rowcount != 1:
                raise DomainError("JOB_LEASE_LOST", "归档 Job 终态写入失败", 409)
            self._inject("after_archive_job_success")

    def artifact_download(
        self,
        job_id: int,
        artifact_id: int,
        principal: Principal,
    ) -> LifecycleArtifactDownload:
        if not principal.can("EXPORT_DATA"):
            raise DomainError("PERMISSION_DENIED", "缺少权限：EXPORT_DATA", 403)
        with self._engine.connect() as connection:
            identity = (
                connection.execute(
                    text(
                        "SELECT a.processing_artifact_id,a.job_id,j.status,j.job_type,"
                        "t.action_type FROM ingestion.processing_artifact a "
                        "JOIN ingestion.processing_job j ON j.job_id=a.job_id "
                        "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d ON d.dataset_id=t.dataset_id "
                        "WHERE a.processing_artifact_id=:artifact AND a.job_id=:job "
                        "AND j.job_type='EXPORT_LATEST' "
                        "AND t.action_type='EXPORT_LATEST' AND "
                        + data_read_scope_sql(
                            access_scope_column="d.access_scope",
                            owner_column="d.owner_user_id",
                            data_domain_column="d.data_domain_id",
                        )
                    ),
                    visibility_parameters(principal)
                    | {"artifact": artifact_id, "job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if identity is None:
                raise DomainError("EXPORT_ARTIFACT_NOT_FOUND", "导出文件不存在", 404)
            row = (
                connection.execute(
                    text(
                        "SELECT a.processing_artifact_id,a.job_id,a.file_name,"
                        "a.storage_uri,a.file_size,a.sha256,a.temporary_flag,"
                        "a.expires_at_utc,a.physical_status,j.status,j.job_type,"
                        "t.action_type "
                        "FROM ingestion.processing_artifact a "
                        "JOIN ingestion.processing_job j ON j.job_id=a.job_id "
                        "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d ON d.dataset_id=t.dataset_id "
                        "WHERE a.processing_artifact_id=:artifact AND a.job_id=:job "
                        "AND j.job_type='EXPORT_LATEST' "
                        "AND t.action_type='EXPORT_LATEST' AND "
                        + data_read_scope_sql(
                            access_scope_column="d.access_scope",
                            owner_column="d.owner_user_id",
                            data_domain_column="d.data_domain_id",
                        )
                    ),
                    visibility_parameters(principal)
                    | {"artifact": artifact_id, "job": job_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError("EXPORT_ARTIFACT_NOT_FOUND", "导出文件不存在", 404)
        if (
            str(row["status"]) != "SUCCESS"
            or not bool(row["temporary_flag"])
            or str(row["physical_status"]) != "PRESENT"
        ):
            raise DomainError("EXPORT_ARTIFACT_UNAVAILABLE", "导出文件当前不可用", 409)
        expires = row["expires_at_utc"]
        if expires is None or _as_utc(expires) <= datetime.now(UTC):
            raise DomainError("EXPORT_ARTIFACT_EXPIRED", "导出文件已过期", 410)
        try:
            path = self._path_policy.require_artifact(
                job_id, str(row["storage_uri"]), must_exist=True
            )
        except FileNotFoundError as exc:
            raise DomainError("EXPORT_ARTIFACT_MISSING", "导出文件物理不存在", 410) from exc
        except UnsafeFormalArtifactPath as exc:
            raise DomainError(
                "EXPORT_ARTIFACT_PATH_INVALID", "导出文件路径越界或包含链接", 409
            ) from exc
        if path.stat().st_size != int(row["file_size"]) or _hash_file(path) != str(row["sha256"]).lower():
            raise DomainError(
                "EXPORT_ARTIFACT_INTEGRITY_MISMATCH", "导出文件与登记的完整性信息不一致", 409
            )
        return LifecycleArtifactDownload(
            path=path,
            file_name=str(row["file_name"]),
            media_type=self._path_policy.media_type(path),
        )

    def export_status(
        self, job_id: int, principal: Principal
    ) -> LifecycleExportStatus:
        if not principal.can("EXPORT_DATA"):
            raise DomainError("PERMISSION_DENIED", "缺少权限：EXPORT_DATA", 403)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT j.job_id,j.status,j.error_code,j.cleaner_release_id,"
                        "t.dataset_id,t.target_dataset_version_id,t.action_type "
                        "FROM ingestion.processing_job j "
                        "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d ON d.dataset_id=t.dataset_id "
                        "WHERE j.job_id=:job AND j.job_type='EXPORT_LATEST' "
                        "AND t.action_type='EXPORT_LATEST' AND "
                        + data_read_scope_sql(
                            access_scope_column="d.access_scope",
                            owner_column="d.owner_user_id",
                            data_domain_column="d.data_domain_id",
                        )
                    ),
                    visibility_parameters(principal) | {"job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError("EXPORT_JOB_NOT_FOUND", "Export Job 不存在", 404)
            artifact_rows = (
                connection.execute(
                    text(
                        "SELECT a.processing_artifact_id,a.job_id,a.artifact_role,"
                        "a.file_name,a.file_size,a.sha256,a.expires_at_utc,"
                        "a.physical_status FROM ingestion.processing_artifact a "
                        "JOIN ingestion.processing_job j ON j.job_id=a.job_id "
                        "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                        "JOIN dataset.dataset d ON d.dataset_id=t.dataset_id "
                        "WHERE a.job_id=:job AND a.temporary_flag=1 AND "
                        + data_read_scope_sql(
                            access_scope_column="d.access_scope",
                            owner_column="d.owner_user_id",
                            data_domain_column="d.data_domain_id",
                        )
                        + " ORDER BY a.processing_artifact_id"
                    ),
                    visibility_parameters(principal) | {"job": job_id},
                )
                .mappings()
                .all()
            )
        artifacts = tuple(self._artifact(item) for item in artifact_rows)
        status = str(row["status"])
        now = datetime.now(UTC)
        present = tuple(
            item
            for item in artifacts
            if item.physical_status == "PRESENT" and item.expires_at_utc > now
        )
        expires = max(
            (item.expires_at_utc for item in artifacts),
            default=None,
        )
        if status in {"FAILED", "CANCELLED"}:
            availability = "FAILED"
        elif status in {"QUEUED", "RUNNING", "NEEDS_INPUT"}:
            availability = "PROCESSING"
        elif present:
            availability = "READY"
        elif artifacts and all(item.expires_at_utc <= now for item in artifacts):
            availability = "EXPIRED"
        elif artifacts:
            availability = "CLEANED"
        else:
            availability = "UNAVAILABLE"
        return LifecycleExportStatus(
            job_id=int(row["job_id"]),
            dataset_id=int(row["dataset_id"]),
            dataset_version_id=int(row["target_dataset_version_id"]),
            cleaner_release_id=int(row["cleaner_release_id"]),
            status=status,
            error_code=(
                str(row["error_code"]) if row["error_code"] is not None else None
            ),
            availability=availability,
            expires_at_utc=expires,
            artifacts=artifacts,
        )

    @staticmethod
    def _artifact(row: Mapping[str, Any]) -> LifecycleArtifact:
        return LifecycleArtifact(
            processing_artifact_id=int(row["processing_artifact_id"]),
            job_id=int(row["job_id"]),
            artifact_role=str(row["artifact_role"]),
            file_name=str(row["file_name"]),
            file_size=int(row["file_size"]),
            sha256=str(row["sha256"]),
            expires_at_utc=_as_utc(row["expires_at_utc"]),
            physical_status=str(row["physical_status"]),
        )

    @staticmethod
    def _audit(
        connection: Connection,
        *,
        actor: str,
        actor_user_id: int,
        operation: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any],
        reason: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                "before_json,after_json,reason,correlation_id,actor_user_id) "
                "VALUES(:actor,:operation,:entity_type,:entity_id,:before_json,"
                ":after_json,:reason,:correlation_id,:actor_user_id)"
            ),
            {
                "actor": actor[:128],
                "operation": operation[:64],
                "entity_type": entity_type[:128],
                "entity_id": entity_id[:128],
                "before_json": (
                    json.dumps(before, ensure_ascii=False, separators=(",", ":"))
                    if before is not None
                    else None
                ),
                "after_json": json.dumps(
                    after, ensure_ascii=False, separators=(",", ":")
                ),
                "reason": reason[:1000],
                "correlation_id": str(uuid4()),
                "actor_user_id": actor_user_id,
            },
        )
