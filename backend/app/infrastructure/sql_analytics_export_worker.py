from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsContextRequest,
    AnalyticsDatasetReference,
    AnalyticsFilters,
)
from app.domain.analytics_export_worker import (
    AnalyticsExportWorkItem,
    RenderedAnalyticsExport,
)
from app.domain.analytics_exports import (
    ANALYTICS_EXPORT_CONTRACT_VERSION,
    CURRENT_PAGE_DETAIL_PAYLOAD_KEY,
    AnalyticsExportFormat,
    AnalyticsExportScope,
    replay_stored_current_page_detail_state,
    resolve_analytics_export_template,
)
from app.domain.saved_analyses import (
    SavedAnalysisRuleContext,
    canonical_json,
    saved_analysis_hashes,
    validate_analysis_presentation_config,
)
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy

_MIME_TYPES = {
    "CSV": {"text/csv; charset=utf-8"},
    "XLSX": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "BIN_TXT": {"text/plain"},
    "HTML": {"text/html; charset=utf-8"},
    "PDF": {"application/pdf"},
    "PNG": {"image/png"},
}


def _utc_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainError(
            "ANALYTICS_EXPORT_INTEGRITY_ERROR",
            f"stored export {field} is invalid",
            409,
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAnalyticsExportWorkerRepository:
    """Atomically claim and finalize the existing delivery.export_job fact chain."""

    def __init__(
        self,
        engine: Engine,
        path_policy: AnalyticsExportPathPolicy,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> None:
        normalized = worker_id.strip()
        if not normalized or len(normalized) > 100:
            raise ValueError("analytics export worker_id must be 1..100 characters")
        if lease_seconds < 30 or lease_seconds > 3600:
            raise ValueError(
                "analytics export lease_seconds must be between 30 and 3600"
            )
        self._engine = engine
        self._path_policy = path_policy
        self._worker_id = normalized
        self._lease_seconds = lease_seconds

    def claim_next(self) -> AnalyticsExportWorkItem | None:
        lease_token = str(uuid4())
        with self._engine.begin() as connection:
            self._fail_one_exhausted(connection)
            claim = (
                connection.execute(
                    text(
                        ";WITH candidate AS(SELECT TOP (1) export_job_id,status,"
                        "started_at_utc,finished_at_utc,error_message,attempt_count,"
                        "max_attempts,lease_token,lease_owner,lease_expires_at_utc,"
                        "heartbeat_at_utc FROM delivery.export_job "
                        "WITH (ROWLOCK,READPAST,UPDLOCK) WHERE contract_version="
                        ":contract_version AND attempt_count<max_attempts AND ("
                        "status='QUEUED' OR (status='RUNNING' AND "
                        "lease_expires_at_utc<=SYSUTCDATETIME())) ORDER BY "
                        "CASE WHEN status='QUEUED' THEN 0 ELSE 1 END,"
                        "requested_at_utc,export_job_id) UPDATE candidate SET "
                        "status='RUNNING',started_at_utc=COALESCE(started_at_utc,"
                        "SYSUTCDATETIME()),finished_at_utc=NULL,error_message=NULL,"
                        "attempt_count=attempt_count+1,lease_token=:lease_token,"
                        "lease_owner=:lease_owner,heartbeat_at_utc=SYSUTCDATETIME(),"
                        "lease_expires_at_utc=DATEADD(second,:lease_seconds,"
                        "SYSUTCDATETIME()) OUTPUT INSERTED.export_job_id,"
                        "DELETED.status AS previous_status,"
                        "DELETED.lease_owner AS previous_lease_owner,"
                        "DELETED.attempt_count AS previous_attempt_count,"
                        "INSERTED.attempt_count,INSERTED.lease_expires_at_utc"
                    ),
                    {
                        "contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION,
                        "lease_token": lease_token,
                        "lease_owner": self._worker_id,
                        "lease_seconds": self._lease_seconds,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if claim is None:
                return None
            export_job_id = int(claim["export_job_id"])
            try:
                work_item = self._load_work_item(
                    connection,
                    export_job_id,
                    expected_lease_token=lease_token,
                )
                existing_artifacts = int(
                    connection.execute(
                        text(
                            "SELECT COUNT_BIG(*) FROM delivery.export_artifact "
                            "WHERE export_job_id=:export_job_id"
                        ),
                        {"export_job_id": export_job_id},
                    ).scalar_one()
                )
                if existing_artifacts:
                    raise DomainError(
                        "ANALYTICS_EXPORT_ARTIFACT_STATE_INVALID",
                        "a newly claimed export already has registered artifacts",
                        409,
                    )
            except DomainError as exc:
                self._fail_claimed_in_transaction(
                    connection,
                    export_job_id,
                    lease_token=lease_token,
                    error_code=exc.code,
                    error_message=exc.message,
                )
                return None
            recovered = str(claim["previous_status"]) == "RUNNING"
            self._audit(
                connection,
                work_item.export_job_id,
                (
                    "ANALYTICS_EXPORT_WORKER_RECOVERED"
                    if recovered
                    else "ANALYTICS_EXPORT_WORKER_CLAIM"
                ),
                before={
                    "status": str(claim["previous_status"]),
                    "attempt_count": int(claim["previous_attempt_count"]),
                    "lease_owner": claim["previous_lease_owner"],
                },
                after={
                    "status": "RUNNING",
                    "worker_id": self._worker_id,
                    "attempt_count": int(claim["attempt_count"]),
                    "lease_expires_at_utc": _utc_datetime(
                        claim["lease_expires_at_utc"],
                        field="lease_expires_at_utc",
                    ).isoformat(),
                },
                reason=(
                    "Expired Analytics Export lease recovered with a new fencing token"
                    if recovered
                    else "Atomic SQL Server export Worker claim"
                ),
            )
            return work_item

    def _fail_one_exhausted(self, connection: Connection) -> None:
        row = (
            connection.execute(
                text(
                    ";WITH exhausted AS(SELECT TOP (1) export_job_id,status,"
                    "finished_at_utc,error_message,attempt_count,max_attempts,"
                    "lease_token,lease_owner,lease_expires_at_utc,heartbeat_at_utc "
                    "FROM delivery.export_job WITH (ROWLOCK,READPAST,UPDLOCK) "
                    "WHERE contract_version=:contract_version AND status='RUNNING' "
                    "AND lease_expires_at_utc<=SYSUTCDATETIME() "
                    "AND attempt_count>=max_attempts ORDER BY lease_expires_at_utc,"
                    "export_job_id) UPDATE exhausted SET status='FAILED',"
                    "finished_at_utc=SYSUTCDATETIME(),error_message="
                    "'ANALYTICS_EXPORT_RETRY_EXHAUSTED: expired Worker lease exhausted attempts',"
                    "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,"
                    "heartbeat_at_utc=NULL OUTPUT INSERTED.export_job_id,"
                    "DELETED.lease_owner,DELETED.attempt_count,DELETED.max_attempts"
                ),
                {"contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        self._audit(
            connection,
            int(row["export_job_id"]),
            "ANALYTICS_EXPORT_WORKER_RETRY_EXHAUSTED",
            before={
                "status": "RUNNING",
                "lease_owner": row["lease_owner"],
                "attempt_count": int(row["attempt_count"]),
                "max_attempts": int(row["max_attempts"]),
            },
            after={"status": "FAILED"},
            reason="Expired Analytics Export lease exhausted retry attempts",
        )

    def _fail_claimed_in_transaction(
        self,
        connection: Connection,
        export_job_id: int,
        *,
        lease_token: str,
        error_code: str,
        error_message: str,
    ) -> None:
        safe_code = error_code[:64]
        safe_message = " ".join(error_message.split())[:900]
        changed = connection.execute(
            text(
                "UPDATE delivery.export_job SET status='FAILED',"
                "finished_at_utc=SYSUTCDATETIME(),error_message=:error_message,"
                "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,"
                "heartbeat_at_utc=NULL WHERE export_job_id=:export_job_id "
                "AND status='RUNNING' AND lease_token=:lease_token "
                "AND lease_owner=:lease_owner "
                "AND lease_expires_at_utc>=SYSUTCDATETIME()"
            ),
            {
                "error_message": f"{safe_code}: {safe_message}",
                "export_job_id": export_job_id,
                "lease_token": lease_token,
                "lease_owner": self._worker_id,
            },
        ).rowcount
        if changed != 1:
            raise DomainError(
                "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                "the analytics export changed while rejecting its stored context",
                409,
            )
        self._audit(
            connection,
            export_job_id,
            "ANALYTICS_EXPORT_WORKER_FAILED",
            before={"status": "RUNNING"},
            after={"status": "FAILED", "error_code": safe_code},
            reason=safe_message,
        )

    def _job_row(
        self,
        connection: Connection,
        export_job_id: int,
        *,
        expected_lease_token: str,
    ) -> Mapping[str, Any]:
        row = (
            connection.execute(
                text(
                    "SELECT export_job_id,requested_by,dataset_version_id,"
                    "export_scope,export_format,template_code,template_version,"
                    "filter_json,status,requested_at_utc,contract_version,filter_hash,"
                    "context_hash,rule_context_json,lease_token,lease_owner,"
                    "lease_expires_at_utc,attempt_count FROM delivery.export_job "
                    "WHERE export_job_id=:export_job_id"
                ),
                {"export_job_id": export_job_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or str(row["status"]) != "RUNNING":
            raise DomainError(
                "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                "the claimed analytics export is no longer RUNNING",
                409,
            )
        if str(row["contract_version"]) != ANALYTICS_EXPORT_CONTRACT_VERSION:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the claimed export has an incompatible contract version",
                409,
            )
        try:
            stored_token = str(UUID(str(row["lease_token"])))
            expected_token = str(UUID(expected_lease_token))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                "the claimed analytics export has an invalid fencing token",
                409,
            ) from exc
        if (
            stored_token != expected_token
            or str(row["lease_owner"]) != self._worker_id
            or _utc_datetime(row["lease_expires_at_utc"], field="lease_expires_at_utc")
            <= datetime.now(UTC)
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                "the claimed analytics export fencing lease is no longer owned",
                409,
            )
        return row

    @staticmethod
    def _dataset_rows(
        connection: Connection, export_job_id: int
    ) -> tuple[Mapping[str, Any], ...]:
        rows = tuple(
            connection.execute(
                text(
                    "SELECT ejd.dataset_version_id,ejd.ordinal_no,dv.dataset_id,"
                    "dv.version_no,dv.status,dv.is_current,d.test_stage "
                    "FROM delivery.export_job_dataset ejd "
                    "JOIN dataset.dataset_version dv "
                    "ON dv.dataset_version_id=ejd.dataset_version_id "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "WHERE ejd.export_job_id=:export_job_id ORDER BY ejd.ordinal_no"
                ),
                {"export_job_id": export_job_id},
            )
            .mappings()
            .all()
        )
        ordinals = [int(row["ordinal_no"]) for row in rows]
        if not 1 <= len(rows) <= 8 or ordinals != list(range(1, len(rows) + 1)):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the export does not have one to eight ordered Dataset Versions",
                409,
            )
        stages = {str(row["test_stage"]) for row in rows}
        if len(stages) != 1 or not stages.issubset({"CP", "FT"}):
            raise DomainError(
                "ANALYTICS_EXPORT_STAGE_INCOMPATIBLE",
                "the export Dataset Versions do not share one CP or FT stage",
                409,
            )
        if any(str(row["status"]) != "PUBLISHED" for row in rows):
            raise DomainError(
                "ANALYTICS_EXPORT_DATASET_UNPUBLISHED",
                "one fixed Dataset Version is no longer published",
                409,
            )
        return rows

    def _load_work_item(
        self,
        connection: Connection,
        export_job_id: int,
        *,
        expected_lease_token: str,
    ) -> AnalyticsExportWorkItem:
        row = self._job_row(
            connection,
            export_job_id,
            expected_lease_token=expected_lease_token,
        )
        datasets = self._dataset_rows(connection, export_job_id)
        if int(row["dataset_version_id"]) != int(datasets[0]["dataset_version_id"]):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "legacy primary Dataset does not match export ordinal one",
                409,
            )
        try:
            payload = json.loads(str(row["filter_json"]))
            expected_v1_keys = {
                "artifact_ttl_hours",
                "chart_config",
                "display_config",
                "filters",
                "page",
                "page_size",
                "parameters",
                "presentation_hash",
                "request_reason_sha256",
            }
            if not isinstance(payload, dict):
                raise TypeError("stored filter envelope keys are invalid")
            payload_keys = frozenset(payload)
            if payload_keys not in {
                frozenset(expected_v1_keys),
                frozenset(expected_v1_keys | {CURRENT_PAGE_DETAIL_PAYLOAD_KEY}),
            }:
                raise ValueError("stored filter envelope keys are invalid")
            export_scope = AnalyticsExportScope(str(row["export_scope"]))
            export_format = AnalyticsExportFormat(str(row["export_format"]))
            context = AnalyticsContextRequest(
                datasets=[
                    AnalyticsDatasetReference(
                        dataset_id=int(item["dataset_id"]),
                        version_no=int(item["version_no"]),
                    )
                    for item in datasets
                ],
                filters=AnalyticsFilters.model_validate(payload["filters"]),
                parameters=payload["parameters"],
            )
            hashes = saved_analysis_hashes(context)
            chart_config = payload["chart_config"]
            display_config = payload["display_config"]
            if not isinstance(chart_config, dict) or not isinstance(
                display_config, dict
            ):
                raise TypeError("stored presentation configuration is invalid")
            presentation_hash = validate_analysis_presentation_config(
                chart_config, display_config
            )
            if str(payload["presentation_hash"]) != presentation_hash:
                raise ValueError("stored presentation hash does not reconcile")
            current_page_detail_state = replay_stored_current_page_detail_state(
                payload,
                export_scope=export_scope,
                chart_config=chart_config,
                display_config=display_config,
            )
            rule_context = SavedAnalysisRuleContext.model_validate_json(
                str(row["rule_context_json"])
            )
            artifact_ttl_hours = int(payload["artifact_ttl_hours"])
            page = int(payload["page"]) if payload["page"] is not None else None
            page_size = (
                int(payload["page_size"]) if payload["page_size"] is not None else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the stored export context is invalid",
                409,
            ) from exc
        if not 1 <= artifact_ttl_hours <= 168:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the stored export TTL is invalid",
                409,
            )
        if export_scope == AnalyticsExportScope.CURRENT_PAGE:
            if page is None or page_size is None or not 1 <= page_size <= 200:
                raise DomainError(
                    "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                    "the stored current-page export is invalid",
                    409,
                )
        elif page is not None or page_size is not None:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the stored export page scope is invalid",
                409,
            )
        if (
            str(row["filter_hash"]) != hashes.filter_hash
            or str(row["context_hash"]) != hashes.context_hash
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the stored export hashes do not reconcile",
                409,
            )
        try:
            resolve_analytics_export_template(
                str(row["template_code"]),
                str(row["template_version"]),
                export_scope,
                export_format,
                test_stage=str(datasets[0]["test_stage"]),
            )
        except ValueError as exc:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "the stored export template contract is invalid",
                409,
            ) from exc
        return AnalyticsExportWorkItem(
            export_job_id=export_job_id,
            requested_by=int(row["requested_by"]),
            export_scope=export_scope,
            export_format=export_format,
            template_code=str(row["template_code"]),
            template_version=str(row["template_version"]),
            context=context,
            dataset_version_ids=tuple(
                int(item["dataset_version_id"]) for item in datasets
            ),
            test_stage=str(datasets[0]["test_stage"]),
            filter_hash=hashes.filter_hash,
            context_hash=hashes.context_hash,
            rule_context=rule_context,
            chart_config=chart_config,
            display_config=display_config,
            presentation_hash=presentation_hash,
            artifact_ttl_hours=artifact_ttl_hours,
            page=page,
            page_size=page_size,
            requested_at_utc=_utc_datetime(
                row["requested_at_utc"], field="requested_at_utc"
            ),
            lease_token=str(UUID(str(row["lease_token"]))),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at_utc=_utc_datetime(
                row["lease_expires_at_utc"], field="lease_expires_at_utc"
            ),
            attempt_count=int(row["attempt_count"]),
            current_page_detail_state=current_page_detail_state,
        )

    def heartbeat(self, work_item: AnalyticsExportWorkItem) -> None:
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_job SET heartbeat_at_utc="
                    "SYSUTCDATETIME(),lease_expires_at_utc=DATEADD(second,"
                    ":lease_seconds,SYSUTCDATETIME()) WHERE "
                    "export_job_id=:export_job_id AND status='RUNNING' "
                    "AND contract_version=:contract_version "
                    "AND lease_token=:lease_token AND lease_owner=:lease_owner "
                    "AND lease_expires_at_utc>=SYSUTCDATETIME()"
                ),
                {
                    "lease_seconds": self._lease_seconds,
                    "export_job_id": work_item.export_job_id,
                    "contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION,
                    "lease_token": work_item.lease_token,
                    "lease_owner": self._worker_id,
                },
            ).rowcount
        if changed != 1:
            raise DomainError(
                "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                "the analytics export Worker fencing lease was lost",
                409,
            )

    def complete(
        self,
        work_item: AnalyticsExportWorkItem,
        artifact: RenderedAnalyticsExport,
        *,
        expires_at_utc: datetime,
    ) -> None:
        expires = _utc_datetime(expires_at_utc, field="expires_at_utc")
        now = datetime.now(UTC)
        if expires <= now:
            raise DomainError(
                "ANALYTICS_EXPORT_TTL_INVALID",
                "analytics export expiry must be in the future",
                409,
            )
        if artifact.mime_type not in _MIME_TYPES[work_item.export_format.value]:
            raise DomainError(
                "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                "rendered artifact MIME type does not match the requested format",
                409,
            )
        identity = self._path_policy.identify(work_item.export_job_id, artifact.path)
        if (
            identity.file_name != artifact.file_name
            or identity.file_size != artifact.file_size
            or identity.sha256 != artifact.sha256
            or artifact.exported_row_count < 0
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                "rendered artifact metadata does not match the managed file",
                409,
            )
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT status,lease_token,lease_owner,lease_expires_at_utc "
                        "FROM delivery.export_job WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE export_job_id=:export_job_id AND "
                        "contract_version=:contract_version"
                    ),
                    {
                        "export_job_id": work_item.export_job_id,
                        "contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or str(row["status"]) != "RUNNING"
                or str(row["lease_token"]).lower() != work_item.lease_token.lower()
                or str(row["lease_owner"]) != self._worker_id
                or _utc_datetime(
                    row["lease_expires_at_utc"], field="lease_expires_at_utc"
                )
                < datetime.now(UTC)
            ):
                raise DomainError(
                    "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                    "the analytics export is no longer owned by the Worker",
                    409,
                )
            artifact_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM delivery.export_artifact "
                        "WHERE export_job_id=:export_job_id"
                    ),
                    {"export_job_id": work_item.export_job_id},
                ).scalar_one()
            )
            if artifact_count:
                raise DomainError(
                    "ANALYTICS_EXPORT_ARTIFACT_STATE_INVALID",
                    "the analytics export already has a registered artifact",
                    409,
                )
            connection.execute(
                text(
                    "INSERT delivery.export_artifact(export_job_id,file_name,mime_type,"
                    "storage_uri,file_size,sha256,expires_at_utc) VALUES("
                    ":export_job_id,:file_name,:mime_type,:storage_uri,:file_size,"
                    ":sha256,:expires_at_utc)"
                ),
                {
                    "export_job_id": work_item.export_job_id,
                    "file_name": identity.file_name,
                    "mime_type": artifact.mime_type,
                    "storage_uri": str(identity.path),
                    "file_size": identity.file_size,
                    "sha256": identity.sha256,
                    "expires_at_utc": expires,
                },
            )
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_job SET status='SUCCESS',"
                    "finished_at_utc=SYSUTCDATETIME(),error_message=NULL,"
                    "exported_row_count=:exported_row_count,lease_token=NULL,"
                    "lease_owner=NULL,lease_expires_at_utc=NULL,heartbeat_at_utc=NULL "
                    "WHERE export_job_id=:export_job_id AND status='RUNNING' "
                    "AND lease_token=:lease_token AND lease_owner=:lease_owner "
                    "AND lease_expires_at_utc>=SYSUTCDATETIME()"
                ),
                {
                    "exported_row_count": artifact.exported_row_count,
                    "export_job_id": work_item.export_job_id,
                    "lease_token": work_item.lease_token,
                    "lease_owner": self._worker_id,
                },
            ).rowcount
            if changed != 1:
                raise DomainError(
                    "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                    "the analytics export changed while finalizing",
                    409,
                )
            self._audit(
                connection,
                work_item.export_job_id,
                "ANALYTICS_EXPORT_WORKER_SUCCESS",
                before={"status": "RUNNING"},
                after={
                    "status": "SUCCESS",
                    "exported_row_count": artifact.exported_row_count,
                    "file_name": identity.file_name,
                    "file_size": identity.file_size,
                    "sha256": identity.sha256,
                    "expires_at_utc": expires.isoformat(),
                },
                reason="Managed analytics artifact generated and verified",
            )

    def fail(
        self,
        work_item: AnalyticsExportWorkItem,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        safe_code = error_code[:64]
        safe_message = " ".join(error_message.split())[:900]
        stored = f"{safe_code}: {safe_message}"
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_job SET status='FAILED',"
                    "finished_at_utc=SYSUTCDATETIME(),error_message=:error_message,"
                    "lease_token=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,"
                    "heartbeat_at_utc=NULL WHERE export_job_id=:export_job_id "
                    "AND status='RUNNING' AND lease_token=:lease_token "
                    "AND lease_owner=:lease_owner "
                    "AND lease_expires_at_utc>=SYSUTCDATETIME()"
                ),
                {
                    "error_message": stored,
                    "export_job_id": work_item.export_job_id,
                    "lease_token": work_item.lease_token,
                    "lease_owner": self._worker_id,
                },
            ).rowcount
            if changed != 1:
                raise DomainError(
                    "ANALYTICS_EXPORT_WORKER_CLAIM_LOST",
                    "the analytics export changed while recording failure",
                    409,
                )
            self._audit(
                connection,
                work_item.export_job_id,
                "ANALYTICS_EXPORT_WORKER_FAILED",
                before={"status": "RUNNING"},
                after={"status": "FAILED", "error_code": safe_code},
                reason=safe_message,
            )

    def _audit(
        self,
        connection: Connection,
        export_job_id: int,
        operation: str,
        *,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        reason: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,actor_user_id,operation,entity_type,"
                "entity_id,before_json,after_json,reason) VALUES(:actor,NULL,:operation,"
                "'ANALYTICS_EXPORT',:entity_id,:before_json,:after_json,:reason)"
            ),
            {
                "actor": self._worker_id,
                "operation": operation,
                "entity_id": str(export_job_id),
                "before_json": canonical_json(before) if before is not None else None,
                "after_json": canonical_json(after) if after is not None else None,
                "reason": reason,
            },
        )
