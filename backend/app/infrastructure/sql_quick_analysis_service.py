from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.quick_analysis import (
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
    QuickAnalysisPage,
    QuickAnalysisSession,
    QuickAnalysisStatus,
    QuickAnalysisWorkItem,
)
from app.domain.quick_capacity import QuickCapacityPolicy

SESSION_SELECT = """
SELECT
    s.analysis_session_id,s.owner_user_id,u.login_name AS owner_login,
    u.display_name AS owner_name,s.analysis_type,s.test_stage,s.factory_code,
    s.source_root_code,s.source_relative_path,s.source_manifest_mode,
    s.source_manifest_sha256,s.source_file_count,s.source_total_bytes,
    s.retention_mode,s.cleaner_release_id,s.reserved_bytes,s.cleanup_status,
    CASE WHEN s.status='SUCCESS' AND s.expires_at_utc<=SYSUTCDATETIME()
         THEN 'EXPIRED'
         WHEN s.status IN('QUEUED','RUNNING') AND j.status IN('FAILED','CANCELLED')
         THEN j.status ELSE s.status END AS effective_status,
    j.job_id,j.status AS job_status,s.parameter_count,s.record_count,s.summary_json,
    a.file_name AS result_file_name,a.file_size AS result_size_bytes,
    COALESCE(s.error_code,j.error_code) AS effective_error_code,
    COALESCE(s.error_message,j.error_message) AS effective_error_message,
    s.expires_at_utc,s.created_at_utc,s.started_at_utc,s.finished_at_utc
FROM workspace.analysis_session s
JOIN iam.app_user u ON u.user_id=s.owner_user_id
LEFT JOIN ingestion.processing_job j ON j.analysis_session_id=s.analysis_session_id
OUTER APPLY (
    SELECT TOP (1) pa.file_name,pa.file_size
    FROM ingestion.processing_artifact pa
    WHERE pa.job_id=j.job_id AND pa.artifact_role='pat_report'
      AND pa.physical_status='PRESENT'
    ORDER BY pa.processing_artifact_id DESC
) a
"""


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sql_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=None)
        if value.tzinfo is None
        else value.astimezone(UTC).replace(tzinfo=None)
    )


def _to_session(row: Mapping[str, Any]) -> QuickAnalysisSession:
    summary = json.loads(row["summary_json"]) if row["summary_json"] else None
    return QuickAnalysisSession(
        analysis_session_id=int(row["analysis_session_id"]),
        owner_user_id=int(row["owner_user_id"]),
        owner_login=str(row["owner_login"]),
        owner_name=str(row["owner_name"]),
        analysis_type=str(row["analysis_type"]),
        test_stage=str(row["test_stage"]),
        factory_code=str(row["factory_code"]),
        source_root_code=str(row["source_root_code"]),
        source_relative_path=str(row["source_relative_path"]),
        source_manifest_mode=str(row["source_manifest_mode"]),
        source_manifest_sha256=str(row["source_manifest_sha256"]),
        source_file_count=int(row["source_file_count"]),
        source_total_bytes=int(row["source_total_bytes"]),
        retention_mode=str(row["retention_mode"]),
        cleaner_release_id=int(row["cleaner_release_id"]),
        status=QuickAnalysisStatus(row["effective_status"]),
        job_id=int(row["job_id"]) if row["job_id"] is not None else None,
        job_status=str(row["job_status"]) if row["job_status"] else None,
        parameter_count=(
            int(row["parameter_count"])
            if row["parameter_count"] is not None
            else None
        ),
        record_count=int(row["record_count"]) if row["record_count"] is not None else None,
        summary=summary,
        result_file_name=(
            str(row["result_file_name"]) if row["result_file_name"] else None
        ),
        result_size_bytes=(
            int(row["result_size_bytes"])
            if row["result_size_bytes"] is not None
            else None
        ),
        error_code=(
            str(row["effective_error_code"])
            if row["effective_error_code"]
            else None
        ),
        error_message=(
            str(row["effective_error_message"])
            if row["effective_error_message"]
            else None
        ),
        expires_at_utc=_utc(row["expires_at_utc"]),
        created_at_utc=_utc(row["created_at_utc"]),
        started_at_utc=_utc(row["started_at_utc"]),
        finished_at_utc=_utc(row["finished_at_utc"]),
        reserved_bytes=int(row["reserved_bytes"]),
        cleanup_status=str(row["cleanup_status"]),
    )


class SqlQuickAnalysisService:
    def __init__(
        self, engine: Engine, *, capacity: QuickCapacityPolicy | None = None
    ) -> None:
        self._engine = engine
        self._capacity = capacity

    def create(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> QuickAnalysisSession:
        with self._engine.begin() as connection:
            if self._capacity is not None:
                lock_result = connection.execute(
                    text(
                        "DECLARE @result int; "
                        "EXEC @result=sys.sp_getapplock "
                        "@Resource='workspace.quick_capacity',@LockMode='Exclusive',"
                        "@LockOwner='Transaction',@LockTimeout=5000; SELECT @result"
                    )
                ).scalar_one()
                if int(lock_result) < 0:
                    raise DomainError(
                        "QUICK_CAPACITY_LOCK_TIMEOUT",
                        "快速分析容量检查繁忙，请稍后重试",
                        409,
                    )
                usage = (
                    connection.execute(
                        text(
                            "SELECT "
                            "(SELECT COALESCE(SUM(s.reserved_bytes),0) "
                            "FROM workspace.analysis_session s WHERE "
                            "s.status IN('QUEUED','RUNNING') OR ("
                            "s.status IN('FAILED','CANCELLED') AND "
                            "s.cleanup_status<>'CLEANED'))+"
                            "(SELECT COALESCE(SUM(pa.file_size),0) FROM "
                            "ingestion.processing_artifact pa JOIN "
                            "ingestion.processing_job j ON j.job_id=pa.job_id "
                            "WHERE j.analysis_session_id IS NOT NULL AND "
                            "pa.physical_status IN('PRESENT','BLOCKED','ERROR')) "
                            "AS global_used_bytes,"
                            "(SELECT COALESCE(SUM(s.reserved_bytes),0) FROM "
                            "workspace.analysis_session s WHERE s.owner_user_id=:owner "
                            "AND (s.status IN('QUEUED','RUNNING') OR ("
                            "s.status IN('FAILED','CANCELLED') AND "
                            "s.cleanup_status<>'CLEANED')))+"
                            "(SELECT COALESCE(SUM(pa.file_size),0) FROM "
                            "ingestion.processing_artifact pa JOIN "
                            "ingestion.processing_job j ON j.job_id=pa.job_id JOIN "
                            "workspace.analysis_session s ON "
                            "s.analysis_session_id=j.analysis_session_id WHERE "
                            "s.owner_user_id=:owner AND "
                            "pa.physical_status IN('PRESENT','BLOCKED','ERROR')) "
                            "AS user_used_bytes"
                        ),
                        {"owner": principal.user_id},
                    )
                    .mappings()
                    .one()
                )
                self._capacity.ensure_quota(
                    global_used_bytes=int(usage["global_used_bytes"]),
                    user_used_bytes=int(usage["user_used_bytes"]),
                    reservation_bytes=request.reserved_bytes,
                )
            session_id = int(
                connection.execute(
                    text(
                        "INSERT workspace.analysis_session("
                        "owner_user_id,analysis_type,test_stage,factory_code,"
                        "source_root_code,source_relative_path,source_manifest_mode,"
                        "source_manifest_json,source_manifest_sha256,source_file_count,"
                        "source_total_bytes,retention_mode,cleaner_release_id,status,"
                        "expires_at_utc,reserved_bytes) "
                        "OUTPUT INSERTED.analysis_session_id VALUES("
                        ":owner,:analysis_type,:stage,:factory,:root_code,:relative_path,"
                        ":manifest_mode,:manifest_json,:manifest_sha,:file_count,"
                        ":total_bytes,:retention_mode,:release_id,'QUEUED',:expires,"
                        ":reserved_bytes)"
                    ),
                    {
                        "owner": principal.user_id,
                        "analysis_type": request.analysis_type,
                        "stage": request.test_stage,
                        "factory": request.factory_code,
                        "root_code": request.source_root_code,
                        "relative_path": request.source_relative_path,
                        "manifest_mode": request.source_manifest_mode,
                        "manifest_json": request.source_manifest_json,
                        "manifest_sha": request.source_manifest_sha256,
                        "file_count": request.source_file_count,
                        "total_bytes": request.source_total_bytes,
                        "retention_mode": request.retention_mode,
                        "release_id": request.cleaner_release_id,
                        "expires": request.expires_at_utc.replace(tzinfo=None),
                        "reserved_bytes": request.reserved_bytes,
                    },
                ).scalar_one()
            )
        return self.get_for_principal(session_id, principal)

    def attach_job(self, analysis_session_id: int, job_id: int) -> None:
        with self._engine.begin() as connection:
            exists = connection.execute(
                text(
                    "SELECT 1 FROM ingestion.processing_job WHERE job_id=:job "
                    "AND analysis_session_id=:session"
                ),
                {"job": job_id, "session": analysis_session_id},
            ).scalar_one_or_none()
        if exists is None:
            raise DomainError(
                "QUICK_ANALYSIS_JOB_MISMATCH",
                "快速分析任务与会话关联不一致",
                409,
            )

    def list_for_principal(
        self, principal: Principal
    ) -> tuple[QuickAnalysisSession, ...]:
        scope = "" if "SYSTEM_ADMIN" in principal.roles else "WHERE s.owner_user_id=:owner"
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        SESSION_SELECT
                        + scope
                        + " ORDER BY s.created_at_utc DESC,s.analysis_session_id DESC"
                    ),
                    {"owner": principal.user_id},
                )
                .mappings()
                .all()
            )
        return tuple(_to_session(row) for row in rows)

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
        clauses: list[str] = []
        parameters: dict[str, object] = {
            "owner": principal.user_id,
            "offset": (page - 1) * page_size,
            "page_size": page_size,
        }
        if "SYSTEM_ADMIN" not in principal.roles:
            clauses.append("scoped.owner_user_id=:owner")
        if status is not None:
            clauses.append("scoped.effective_status=:status")
            parameters["status"] = status.value
        if from_utc is not None:
            clauses.append("scoped.created_at_utc>=:from_utc")
            parameters["from_utc"] = _sql_utc(from_utc)
        if to_utc is not None:
            clauses.append("scoped.created_at_utc<:to_utc")
            parameters["to_utc"] = _sql_utc(to_utc)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        scoped = " FROM (" + SESSION_SELECT + ") scoped"
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text("SELECT COUNT_BIG(*)" + scoped + where), parameters
                ).scalar_one()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT scoped.*"
                        + scoped
                        + where
                        + " ORDER BY scoped.created_at_utc DESC,"
                        "scoped.analysis_session_id DESC "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
        return QuickAnalysisPage(
            items=tuple(_to_session(row) for row in rows),
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_for_principal(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisSession:
        scope = (
            ""
            if "SYSTEM_ADMIN" in principal.roles
            else " AND s.owner_user_id=:owner"
        )
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        SESSION_SELECT
                        + " WHERE s.analysis_session_id=:session"
                        + scope
                    ),
                    {"session": analysis_session_id, "owner": principal.user_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在或无权访问", 404
            )
        return _to_session(row)

    def worker_session_info(self, analysis_session_id: int) -> QuickAnalysisWorkItem:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT analysis_session_id,analysis_type,test_stage,factory_code,"
                        "source_root_code,source_relative_path,source_manifest_mode,"
                        "source_manifest_json,source_manifest_sha256,cleaner_release_id,"
                        "expires_at_utc,status FROM workspace.analysis_session "
                        "WHERE analysis_session_id=:session"
                    ),
                    {"session": analysis_session_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在", 404
            )
        return QuickAnalysisWorkItem(
            int(row["analysis_session_id"]),
            str(row["analysis_type"]),
            str(row["test_stage"]),
            str(row["factory_code"]),
            str(row["source_root_code"]),
            str(row["source_relative_path"]),
            str(row["source_manifest_mode"]),
            str(row["source_manifest_json"]),
            str(row["source_manifest_sha256"]),
            int(row["cleaner_release_id"]),
            _utc(row["expires_at_utc"]),
            QuickAnalysisStatus(row["status"]),
        )

    def mark_running(self, analysis_session_id: int) -> None:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET status='RUNNING',"
                    "started_at_utc=COALESCE(started_at_utc,SYSUTCDATETIME()),"
                    "finished_at_utc=NULL,error_code=NULL,error_message=NULL "
                    "WHERE analysis_session_id=:session AND status IN('QUEUED','RUNNING')"
                ),
                {"session": analysis_session_id},
            ).rowcount
        if updated != 1:
            raise DomainError(
                "QUICK_ANALYSIS_STATE_INVALID",
                "快速分析会话当前状态不能开始运行",
                409,
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
        if not any(item.role == "pat_report" for item in artifacts):
            raise ValueError("pat_report artifact is required")
        with self._engine.begin() as connection:
            session = (
                connection.execute(
                    text(
                        "SELECT expires_at_utc FROM workspace.analysis_session s "
                        "WHERE s.analysis_session_id=:session AND s.status='RUNNING' "
                        "AND EXISTS(SELECT 1 FROM ingestion.processing_job j WHERE "
                        "j.job_id=:job AND j.analysis_session_id=s.analysis_session_id)"
                    ),
                    {"session": analysis_session_id, "job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if session is None:
                raise DomainError(
                    "QUICK_ANALYSIS_JOB_MISMATCH",
                    "快速分析结果与运行任务不一致",
                    409,
                )
            for artifact in artifacts:
                connection.execute(
                    text(
                        "IF NOT EXISTS(SELECT 1 FROM ingestion.processing_artifact "
                        "WHERE job_id=:job AND artifact_role=:role AND sha256=:sha) "
                        "INSERT ingestion.processing_artifact("
                        "job_id,processing_run_id,artifact_role,file_name,storage_uri,"
                        "file_size,sha256,temporary_flag,expires_at_utc) VALUES("
                        ":job,NULL,:role,:file_name,:uri,:size,:sha,1,:expires)"
                    ),
                    {
                        "job": job_id,
                        "role": artifact.role,
                        "file_name": artifact.path.replace("\\", "/").rsplit("/", 1)[-1],
                        "uri": artifact.path,
                        "size": artifact.size_bytes,
                        "sha": artifact.sha256,
                        "expires": session["expires_at_utc"],
                    },
                )
            connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET status='SUCCESS',"
                    "parameter_count=:parameters,record_count=:records,summary_json=:summary,"
                    "finished_at_utc=SYSUTCDATETIME(),error_code=NULL,error_message=NULL "
                    "WHERE analysis_session_id=:session"
                ),
                {
                    "parameters": parameter_count,
                    "records": record_count,
                    "summary": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                    "session": analysis_session_id,
                },
            )

    def mark_failed(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET status='FAILED',"
                    "finished_at_utc=SYSUTCDATETIME(),error_code=:code,error_message=:message,"
                    "reserved_bytes=CASE WHEN EXISTS(SELECT 1 FROM "
                    "ingestion.processing_job j WHERE j.analysis_session_id=:session) "
                    "THEN reserved_bytes ELSE 0 END "
                    "WHERE analysis_session_id=:session AND status IN('QUEUED','RUNNING')"
                ),
                {
                    "session": analysis_session_id,
                    "code": error_code[:64],
                    "message": error_message[-4000:],
                },
            )

    def result_artifact(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisArtifact:
        scope = (
            ""
            if "SYSTEM_ADMIN" in principal.roles
            else " AND s.owner_user_id=:owner"
        )
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT TOP (1) pa.artifact_role,pa.storage_uri,pa.file_size,pa.sha256,"
                        "s.expires_at_utc FROM workspace.analysis_session s "
                        "JOIN ingestion.processing_job j ON j.analysis_session_id=s.analysis_session_id "
                        "JOIN ingestion.processing_artifact pa ON pa.job_id=j.job_id "
                        "WHERE s.analysis_session_id=:session AND s.status='SUCCESS' "
                        "AND pa.artifact_role='pat_report' "
                        "AND pa.physical_status='PRESENT'"
                        + scope
                        + " ORDER BY pa.processing_artifact_id DESC"
                    ),
                    {"session": analysis_session_id, "owner": principal.user_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError("QUICK_RESULT_NOT_FOUND", "PAT 结果尚不可下载", 404)
        expires = _utc(row["expires_at_utc"])
        if expires is not None and expires <= datetime.now(UTC):
            raise DomainError("QUICK_RESULT_EXPIRED", "PAT 结果已过期", 410)
        return QuickAnalysisArtifact(
            str(row["artifact_role"]),
            str(row["storage_uri"]),
            int(row["file_size"]),
            str(row["sha256"]),
        )
