from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.jobs import JobStatus
from app.domain.operations import (
    ConsistencyIssueCounts,
    OperationalStatusCount,
    RecentFailedJob,
    SystemConsistencySummary,
)

_INTENT_STATUSES = ("STAGED", "FINALIZED", "ABORTED")
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _iso_utc(value: Any) -> str:
    if not isinstance(value, datetime):
        raise DomainError(
            "OPERATIONS_SNAPSHOT_INVALID",
            "系统一致性快照时间无效",
            503,
        )
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalized_counts(
    rows: list[Mapping[str, Any]], ordered_statuses: tuple[str, ...]
) -> tuple[OperationalStatusCount, ...]:
    counts = {str(row["status"]): int(row["item_count"]) for row in rows}
    known = [
        OperationalStatusCount(status=status, count=counts.pop(status, 0))
        for status in ordered_statuses
    ]
    known.extend(
        OperationalStatusCount(status=status, count=counts[status])
        for status in sorted(counts)
    )
    return tuple(known)


def _safe_error_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return code if _SAFE_ERROR_CODE.fullmatch(code) else "UNCLASSIFIED_FAILURE"


class SqlOperationsService:
    def __init__(self, engine: Engine, *, environment: str = "unknown") -> None:
        self._engine = engine
        self._environment = _safe_runtime_label(environment)

    def consistency_summary(
        self, *, recent_failure_limit: int = 5
    ) -> SystemConsistencySummary:
        if recent_failure_limit < 1 or recent_failure_limit > 20:
            raise DomainError(
                "OPERATIONS_LIMIT_INVALID",
                "最近失败任务数量必须在 1 到 20 之间",
                422,
            )

        try:
            with self._engine.connect() as connection:
                metadata = (
                    connection.execute(
                        text(
                            "SELECT CAST(SYSUTCDATETIME() AS datetime2(3)) "
                            "AS observed_at_utc,"
                            "CAST(DB_NAME() AS nvarchar(128)) AS database_name,"
                            "CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) "
                            "AS database_server,"
                            "CAST((SELECT version_num FROM alembic_version) "
                            "AS nvarchar(128)) AS schema_revision,"
                            "CASE WHEN COL_LENGTH("
                            "N'ingestion.processing_job',N'finalize_protocol') "
                            "IS NOT NULL AND OBJECT_ID("
                            "N'ingestion.initial_import_finalize_intent',N'U') "
                            "IS NOT NULL AND OBJECT_ID("
                            "N'ingestion.processing_run_input_file',N'U') "
                            "IS NOT NULL THEN 1 ELSE 0 END AS atomic_schema_ready"
                        )
                    )
                    .mappings()
                    .one()
                )
                schema_revision = str(metadata["schema_revision"] or "").strip()
                if not schema_revision:
                    raise DomainError(
                        "OPERATIONS_SCHEMA_UNKNOWN",
                        "无法识别当前数据库版本",
                        503,
                    )

                job_rows = (
                    connection.execute(
                        text(
                            "SELECT status,COUNT_BIG(*) AS item_count "
                            "FROM ingestion.processing_job GROUP BY status"
                        )
                    )
                    .mappings()
                    .all()
                )
                atomic_schema_ready = bool(metadata["atomic_schema_ready"])
                active_atomic_count: int | None = None
                intent_counts: tuple[OperationalStatusCount, ...] | None = None
                issue_counts = ConsistencyIssueCounts(None, None)
                if atomic_schema_ready:
                    active_atomic_count = int(
                        connection.execute(
                            text(
                                "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
                                "WHERE job_type='INITIAL_IMPORT' "
                                "AND finalize_protocol='ATOMIC_V1' "
                                "AND status IN('QUEUED','RUNNING','NEEDS_INPUT')"
                            )
                        ).scalar_one()
                    )
                    intent_rows = (
                        connection.execute(
                            text(
                                "SELECT status,COUNT_BIG(*) AS item_count FROM "
                                "ingestion.initial_import_finalize_intent "
                                "GROUP BY status"
                            )
                        )
                        .mappings()
                        .all()
                    )
                    intent_counts = _normalized_counts(intent_rows, _INTENT_STATUSES)
                    issue_row = (
                        connection.execute(text(_CONSISTENCY_ISSUE_SQL))
                        .mappings()
                        .one()
                    )
                    issue_counts = ConsistencyIssueCounts(
                        batch_job_intent=int(
                            issue_row["batch_job_intent_anomaly_count"]
                        ),
                        dataset_current=int(
                            issue_row["dataset_current_anomaly_count"]
                        ),
                    )

                unknown_count = int(
                    connection.execute(
                        text(
                            "SELECT COUNT_BIG(DISTINCT unit_id) "
                            "FROM analytics.v_current_unit_result "
                            "WHERE overall_result='UNKNOWN'"
                        )
                    ).scalar_one()
                )
                failed_rows = (
                    connection.execute(
                        text(
                            "SELECT TOP (:limit) j.job_id,j.job_type,"
                            "lt.action_type AS lifecycle_action_type,"
                            "j.import_batch_id,b.business_domain,b.test_stage,"
                            "j.error_code,j.attempt_count,"
                            "COALESCE(j.finished_at_utc,j.requested_at_utc) "
                            "AS failed_at_utc "
                            "FROM ingestion.processing_job j LEFT JOIN "
                            "ingestion.lifecycle_job_target lt ON lt.job_id=j.job_id "
                            "LEFT JOIN ingestion.import_batch b ON b.import_batch_id="
                            "j.import_batch_id WHERE j.status='FAILED' "
                            "ORDER BY COALESCE(j.finished_at_utc,"
                            "j.requested_at_utc) DESC,j.job_id DESC"
                        ),
                        {"limit": recent_failure_limit},
                    )
                    .mappings()
                    .all()
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "OPERATIONS_SNAPSHOT_UNAVAILABLE",
                "系统一致性快照暂时不可用",
                503,
            ) from exc

        anomalies = sum(
            count
            for count in (
                issue_counts.batch_job_intent,
                issue_counts.dataset_current,
            )
            if count is not None
        )
        if not atomic_schema_ready:
            overall_state = "SCHEMA_UPGRADE_REQUIRED"
            management_message = "原子发布一致性检查尚未就绪，请先完成数据库升级。"
        elif anomalies:
            overall_state = "ATTENTION_REQUIRED"
            management_message = (
                f"发现 {anomalies} 项一致性异常，建议暂停扩大灰度范围并由运维复核。"
            )
        else:
            overall_state = "HEALTHY"
            management_message = "未发现发布链路一致性异常，可继续按计划灰度。"

        recent_failed_jobs = tuple(
            RecentFailedJob(
                job_id=int(row["job_id"]),
                job_type=str(row["job_type"]),
                lifecycle_action_type=(
                    str(row["lifecycle_action_type"])
                    if row.get("lifecycle_action_type") is not None
                    else None
                ),
                import_batch_id=(
                    int(row["import_batch_id"])
                    if row["import_batch_id"] is not None
                    else None
                ),
                business_domain=(
                    str(row["business_domain"])
                    if row["business_domain"] is not None
                    else None
                ),
                test_stage=(
                    str(row["test_stage"])
                    if row["test_stage"] is not None
                    else None
                ),
                error_code=_safe_error_code(row["error_code"]),
                attempt_count=int(row["attempt_count"]),
                failed_at_utc=_iso_utc(row["failed_at_utc"]),
            )
            for row in failed_rows
        )
        return SystemConsistencySummary(
            observed_at_utc=_iso_utc(metadata["observed_at_utc"]),
            database_ready=True,
            schema_revision=schema_revision,
            atomic_schema_ready=atomic_schema_ready,
            overall_state=overall_state,
            management_message=management_message,
            job_status_counts=_normalized_counts(
                job_rows, tuple(status.value for status in JobStatus)
            ),
            active_atomic_initial_import_count=active_atomic_count,
            intent_status_counts=intent_counts,
            issue_counts=issue_counts,
            current_unknown_result_count=unknown_count,
            recent_failed_jobs=recent_failed_jobs,
            environment=self._environment,
            database_name=_safe_runtime_label(metadata.get("database_name")),
            database_server=_safe_runtime_label(metadata.get("database_server")),
        )


def _safe_runtime_label(value: Any) -> str:
    label = str(value or "").strip()
    if not label or len(label) > 256 or any(
        character in label for character in ("\r", "\n", "\0", ";", "@")
    ):
        return "unknown"
    return label


_CONSISTENCY_ISSUE_SQL = """
SELECT
    (
        SELECT COUNT_BIG(*) FROM (
            SELECT i.job_id
            FROM ingestion.initial_import_finalize_intent i
            JOIN ingestion.processing_job j ON j.job_id=i.job_id
            JOIN ingestion.import_batch b ON b.import_batch_id=i.import_batch_id
            JOIN ingestion.processing_run pr
              ON pr.processing_run_id=i.processing_run_id
            JOIN dataset.dataset_version dv
              ON dv.dataset_version_id=i.dataset_version_id
            WHERE j.job_type<>'INITIAL_IMPORT'
               OR j.finalize_protocol<>'ATOMIC_V1'
               OR ISNULL(j.import_batch_id,-1)<>i.import_batch_id
               OR pr.job_id<>i.job_id
               OR ISNULL(dv.input_batch_id,-1)<>i.import_batch_id
               OR NOT EXISTS(
                    SELECT 1 FROM dataset.dataset_version_run dvr
                    WHERE dvr.dataset_version_id=i.dataset_version_id
                      AND dvr.processing_run_id=i.processing_run_id
               )
               OR (SELECT COUNT_BIG(*) FROM ingestion.import_batch_file ibf
                   WHERE ibf.import_batch_id=i.import_batch_id)<>
                  (SELECT COUNT_BIG(*) FROM ingestion.processing_run_input_file rif
                   WHERE rif.processing_run_id=i.processing_run_id)
               OR EXISTS(
                    SELECT 1 FROM ingestion.processing_run_input_file rif
                    JOIN ingestion.import_batch_file ibf
                      ON ibf.import_batch_file_id=rif.import_batch_file_id
                    WHERE rif.processing_run_id=i.processing_run_id
                      AND ibf.import_batch_id<>i.import_batch_id
               )
               OR (i.status='STAGED' AND (
                    j.status<>'RUNNING' OR b.status<>'PROCESSING'
                    OR pr.status<>'READY' OR pr.is_current<>0
                    OR dv.status<>'DRAFT' OR dv.is_current<>0
               ))
               OR (i.status='FINALIZED' AND (
                    j.status<>'SUCCESS'
                    OR (dv.status='PUBLISHED' AND (
                        dv.is_current<>1 OR pr.status<>'PUBLISHED'
                        OR pr.is_current<>1
                    ))
                    OR (dv.status='SUPERSEDED' AND (
                        dv.is_current<>0 OR pr.status<>'SUPERSEDED'
                        OR pr.is_current<>0
                    ))
                    OR (dv.status='ARCHIVED' AND (
                        dv.is_current<>0 OR pr.status<>'SUPERSEDED'
                        OR pr.is_current<>0
                    ))
                    OR dv.status NOT IN('PUBLISHED','SUPERSEDED','ARCHIVED')
               ))
               OR (i.status='ABORTED' AND (
                    j.status NOT IN('FAILED','CANCELLED')
                    OR pr.status<>'FAILED' OR pr.is_current<>0
                    OR dv.status<>'ARCHIVED' OR dv.is_current<>0
               ))
            UNION
            SELECT j.job_id
            FROM ingestion.processing_job j
            WHERE j.job_type='INITIAL_IMPORT'
              AND j.finalize_protocol='ATOMIC_V1'
              AND j.status='SUCCESS'
              AND NOT EXISTS(
                  SELECT 1 FROM ingestion.initial_import_finalize_intent i
                  WHERE i.job_id=j.job_id AND i.status='FINALIZED'
              )
        ) batch_job_intent_problem
    ) AS batch_job_intent_anomaly_count,
    (
        SELECT COUNT_BIG(*) FROM (
            SELECT dv.dataset_id
            FROM dataset.dataset_version dv
            JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id
            GROUP BY dv.dataset_id,d.lifecycle_status
            HAVING SUM(CASE WHEN dv.is_current=1 THEN 1 ELSE 0 END)>1
                OR SUM(CASE WHEN dv.is_current=1
                             AND dv.status<>'PUBLISHED' THEN 1 ELSE 0 END)>0
                OR (d.lifecycle_status='ACTIVE'
                    AND SUM(CASE WHEN dv.status='PUBLISHED' THEN 1 ELSE 0 END)>0
                    AND SUM(CASE WHEN dv.status='PUBLISHED'
                                  AND dv.is_current=1 THEN 1 ELSE 0 END)<>1)
                OR (d.lifecycle_status='ARCHIVED' AND (
                    SUM(CASE WHEN dv.is_current=1 THEN 1 ELSE 0 END)<>0
                    OR SUM(CASE WHEN dv.status='PUBLISHED' THEN 1 ELSE 0 END)<>0
                ))
            UNION
            SELECT DISTINCT dv.dataset_id
            FROM dataset.dataset_version dv
            JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id
            WHERE dv.status='PUBLISHED' AND dv.is_current=1
              AND (d.lifecycle_status<>'ACTIVE' OR
                  NOT EXISTS(
                      SELECT 1 FROM dataset.dataset_version_run dvr
                      WHERE dvr.dataset_version_id=dv.dataset_version_id
                  )
                  OR EXISTS(
                      SELECT 1 FROM dataset.dataset_version_run dvr
                      JOIN ingestion.processing_run pr
                        ON pr.processing_run_id=dvr.processing_run_id
                      WHERE dvr.dataset_version_id=dv.dataset_version_id
                        AND (pr.status<>'PUBLISHED' OR pr.is_current<>1)
                  )
              )
        ) dataset_current_problem
    ) AS dataset_current_anomaly_count
"""
