from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.m2_queries import (
    AvailableAction,
    BatchIdentitySummary,
    CleanerReleaseSummary,
    CurrentDatasetCatalogItem,
    DatasetSummary,
    FinalizeIntentSummary,
    JobDetails,
    JobSafeDetails,
    JobSafeSummary,
    JobTimelineEvent,
    M2Page,
    M2PageFilters,
    ProcessingRunSummary,
    SourceLineageSummary,
    StageResultPageItem,
    StageUploadPageItem,
)
from app.infrastructure.sql_visibility import (
    batch_read_scope_sql,
    can_manage_sql,
    current_dataset_read_scope_sql,
    formal_result_read_scope_sql,
    quick_read_scope_sql,
    quick_write_scope_sql,
    visibility_parameters,
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DomainError("M2_QUERY_CONTRACT_INVALID", "查询结果时间字段无效", 503)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _scope_parameters(principal: Principal) -> dict[str, object]:
    return visibility_parameters(principal)


def _safe_error_message(value: Any) -> str | None:
    if value is None:
        return None
    message = str(value).strip()
    if not message:
        return None
    lowered = message.lower()
    sensitive_markers = (
        "password",
        "pwd=",
        "secret",
        "token=",
        "authorization:",
        "tms_database_url",
        ":\\",
        ":/",
        "/",
        "\\\\",
        "://",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return "处理失败；详细日志仅供管理员在受控主机查看。"
    return message[:500]


def _safe_error_code(value: Any) -> str | None:
    if value is None:
        return None
    code = str(value).strip().upper()
    if not code:
        return None
    if len(code) > 64 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in code
    ):
        return "UNCLASSIFIED_FAILURE"
    return code


def _where(
    filters: M2PageFilters,
    *,
    date_column: str,
    factory_column: str | None = None,
    status_column: str | None = None,
    product_column: str | None = None,
    lot_column: str | None = None,
    domain_column: str | None = None,
    stage_column: str | None = None,
    wafer_column: str | None = None,
    batch_column: str | None = None,
    cleaner_column: str | None = None,
    owner_column: str | None = None,
) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    parameters: dict[str, object] = {
        "offset": (filters.page - 1) * filters.page_size,
        "page_size": filters.page_size,
    }
    candidates = (
        (filters.business_domain, domain_column, "business_domain"),
        (filters.test_stage, stage_column, "test_stage"),
        (filters.factory_code, factory_column, "factory_code"),
        (filters.status, status_column, "status"),
    )
    for value, column, parameter_name in candidates:
        if value is not None and column is not None:
            clauses.append(f"{column}=:{parameter_name}")
            parameters[parameter_name] = value
    if filters.product_name is not None and product_column is not None:
        clauses.append(f"{product_column} LIKE :product_name ESCAPE '\\'")
        parameters["product_name"] = _like(filters.product_name)
    if filters.lot_id is not None and lot_column is not None:
        clauses.append(f"{lot_column} LIKE :lot_id ESCAPE '\\'")
        parameters["lot_id"] = _like(filters.lot_id)
    if filters.wafer_id is not None and wafer_column is not None:
        clauses.append(f"{wafer_column} LIKE :wafer_id ESCAPE '\\'")
        parameters["wafer_id"] = _like(filters.wafer_id)
    if filters.import_batch_id is not None and batch_column is not None:
        clauses.append(f"{batch_column}=:import_batch_id")
        parameters["import_batch_id"] = filters.import_batch_id
    if filters.cleaner_version is not None and cleaner_column is not None:
        clauses.append(f"{cleaner_column} LIKE :cleaner_version ESCAPE '\\'")
        parameters["cleaner_version"] = _like(filters.cleaner_version)
    if filters.owner_login is not None and owner_column is not None:
        clauses.append(f"{owner_column} LIKE :owner_login ESCAPE '\\'")
        parameters["owner_login"] = _like(filters.owner_login)
    if filters.from_utc is not None:
        clauses.append(f"{date_column}>=:from_utc")
        parameters["from_utc"] = filters.from_utc
    if filters.to_utc is not None:
        clauses.append(f"{date_column}<:to_utc")
        parameters["to_utc"] = filters.to_utc
    return (" AND " + " AND ".join(clauses) if clauses else ""), parameters


class SqlM2QueryService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_uploads_page(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        filters: M2PageFilters,
    ) -> M2Page:
        where, parameters = _where(
            filters,
            date_column="b.started_at_utc",
            factory_column="b.factory_code",
            status_column="b.status",
        )
        parameters.update(
            _scope_parameters(principal)
            | {"business_domain": business_domain, "test_stage": test_stage}
        )
        base_where = (
            " WHERE b.business_domain=:business_domain "
            "AND b.test_stage=:test_stage "
            "AND " + batch_read_scope_sql(batch_alias="b") + where
        )
        base_from = (
            " FROM ingestion.import_batch b "
            "JOIN iam.app_user u ON u.user_id=b.owner_user_id "
            "JOIN ingestion.import_batch_file ibf "
            "ON ibf.import_batch_id=b.import_batch_id "
            "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
            "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
        )
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text("SELECT COUNT_BIG(*)" + base_from + base_where), parameters
                ).scalar_one()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT b.import_batch_id,ibf.ordinal_no,r.receipt_id,"
                        "r.source_file_id,r.original_file_name,s.file_size,"
                        "r.is_duplicate_receipt,"
                        "b.factory_code,b.started_at_utc,b.completed_at_utc,"
                        "u.login_name,u.display_name,b.status,latest.job_id "
                        "AS latest_job_id,latest.error_code,latest.error_message,"
                        "latest.queue_age_seconds,CASE WHEN "
                        + can_manage_sql(owner_column="b.owner_user_id")
                        + " THEN 1 ELSE 0 END AS can_manage"
                        + base_from
                        + "OUTER APPLY(SELECT TOP (1) j.job_id,j.error_code,"
                        "j.error_message,CASE WHEN j.status='QUEUED' AND "
                        "j.not_before_utc<=SYSUTCDATETIME() THEN "
                        "DATEDIFF(second,j.not_before_utc,SYSUTCDATETIME()) "
                        "END AS queue_age_seconds FROM ingestion.processing_job j "
                        "WHERE j.import_batch_id=b.import_batch_id "
                        "ORDER BY j.job_id DESC) latest"
                        + base_where
                        + " ORDER BY b.import_batch_id DESC,ibf.ordinal_no "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
        return M2Page(
            items=tuple(self._upload_item(row) for row in rows),
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def list_results_page(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        filters: M2PageFilters,
    ) -> M2Page:
        where, parameters = _where(
            filters,
            date_column="s.created_at_utc",
            factory_column="s.factory_code",
            status_column="s.status",
            product_column="s.product_name",
            lot_column="s.lot_id",
        )
        parameters.update(
            _scope_parameters(principal)
            | {"business_domain": business_domain, "test_stage": test_stage}
        )
        base = (
            " FROM ingestion.processing_result_summary s "
            "JOIN ingestion.import_batch b ON b.import_batch_id=s.import_batch_id "
            "JOIN iam.app_user u ON u.user_id=b.owner_user_id "
            "WHERE b.business_domain=:business_domain "
            "AND b.test_stage=:test_stage "
            "AND "
            + formal_result_read_scope_sql(summary_alias="s", batch_alias="b")
            + where
        )
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text("SELECT COUNT_BIG(*)" + base), parameters
                ).scalar_one()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT s.result_summary_id,s.import_batch_id,s.job_id,"
                        "s.data_name,s.product_name,s.lot_id,s.wafer_count,"
                        "s.factory_code,s.test_item_count,s.unit_count,"
                        "s.pass_count,s.yield_rate,s.status,s.data_type,"
                        "s.dataset_id,s.dataset_version_no,s.created_at_utc,"
                        "u.login_name,u.display_name,"
                        "CASE WHEN "
                        + can_manage_sql(owner_column="b.owner_user_id")
                        + " THEN 1 ELSE 0 END AS can_manage"
                        + base
                        + " ORDER BY s.result_summary_id DESC "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
        return M2Page(
            items=tuple(self._result_item(row) for row in rows),
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def list_current_datasets(
        self, principal: Principal, filters: M2PageFilters
    ) -> M2Page:
        where, parameters = _where(
            filters,
            date_column="dv.published_at_utc",
            factory_column="b.factory_code",
            status_column="dv.status",
            product_column="COALESCE(product_enrichment.value_text,p.product_name,summary_row.product_name)",
            domain_column="b.business_domain",
            stage_column="d.test_stage",
            wafer_column="wafer_scope.wafer_id",
            batch_column="dv.input_batch_id",
            cleaner_column="cr.cleaner_version",
            owner_column="owner_user.login_name",
        )
        if filters.lot_id is not None:
            where += (
                " AND EXISTS("
                "SELECT 1 FROM dataset.dataset_version_run lot_filter_dvr "
                "JOIN test.test_run lot_filter_tr ON "
                "lot_filter_tr.processing_run_id=lot_filter_dvr.processing_run_id "
                "WHERE lot_filter_dvr.dataset_version_id=dv.dataset_version_id "
                "AND lot_filter_tr.lot_id LIKE :lot_id ESCAPE '\\')"
            )
            parameters["lot_id"] = _like(filters.lot_id)
        parameters.update(_scope_parameters(principal))
        parameters.setdefault("wafer_id", None)
        base = _CURRENT_DATASET_FROM + (
            " WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
            "AND "
            + current_dataset_read_scope_sql(
                dataset_alias="d", version_alias="dv", batch_alias="b"
            )
            + where
        )
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text("SELECT COUNT_BIG(*)" + base), parameters
                ).scalar_one()
            )
            rows = (
                connection.execute(
                    text(
                        _CURRENT_DATASET_COLUMNS
                        + base
                        + " ORDER BY dv.published_at_utc DESC,dv.dataset_version_id DESC "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
        return M2Page(
            items=tuple(self._current_dataset_item(row) for row in rows),
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def get_job_details(self, principal: Principal, job_id: int) -> JobDetails:
        parameters = _scope_parameters(principal) | {"job_id": job_id}
        with self._engine.connect() as connection:
            row = (
                connection.execute(text(_JOB_DETAILS_SQL), parameters)
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError("JOB_NOT_FOUND", "任务不存在或无权访问", 404)
            link_rows = (
                connection.execute(
                    text(_JOB_LINKS_SQL),
                    parameters | {"parent_job_id": row["parent_job_id"]},
                )
                .mappings()
                .all()
            )
            chain = (
                connection.execute(text(_JOB_PUBLISH_CHAIN_SQL), {"job_id": job_id})
                .mappings()
                .one_or_none()
            )
            can_manage = bool(row["can_manage"])
            chain = self._visible_publish_chain(chain, can_manage=can_manage)
            source_rows: list[Mapping[str, Any]] = []
            if (
                can_manage
                and chain is not None
                and chain["processing_run_id"] is not None
            ):
                source_rows = (
                    connection.execute(
                        text(_JOB_RUN_SOURCE_LINEAGE_SQL),
                        {"processing_run_id": chain["processing_run_id"]},
                    )
                    .mappings()
                    .all()
                )
            if can_manage and not source_rows and row["import_batch_id"] is not None:
                source_rows = (
                    connection.execute(
                        text(_JOB_BATCH_SOURCE_LINEAGE_SQL),
                        {"import_batch_id": row["import_batch_id"]},
                    )
                    .mappings()
                    .all()
                )
        parent = None
        children: list[JobSafeSummary] = []
        for link_row in link_rows:
            link = self._job_summary(link_row)
            if row["parent_job_id"] is not None and link.job_id == int(
                row["parent_job_id"]
            ):
                parent = link
            elif link_row["parent_job_id"] == job_id:
                children.append(link)
        intent = self._intent(chain) if chain is not None else None
        run = self._run(chain) if chain is not None else None
        dataset = self._dataset(chain) if chain is not None else None
        return JobDetails(
            job=self._job_details(row),
            parent=parent,
            children=tuple(children),
            release=self._release(row),
            batch=self._batch(row),
            intent=intent,
            run=run,
            dataset=dataset,
            timeline=self._timeline(row, chain),
            actions=self._available_actions(
                principal, row, dataset, can_manage=can_manage
            ),
            sources=tuple(self._source_lineage(item) for item in source_rows),
        )

    @staticmethod
    def _visible_publish_chain(
        chain: Mapping[str, Any] | None, *, can_manage: bool
    ) -> Mapping[str, Any] | None:
        if chain is None or can_manage:
            return chain
        if str(chain["version_status"]) == "PUBLISHED" and bool(chain["is_current"]):
            return chain
        return None

    @staticmethod
    def _upload_item(row: Mapping[str, Any]) -> StageUploadPageItem:
        file_name = str(row["original_file_name"])
        return StageUploadPageItem(
            import_batch_id=int(row["import_batch_id"]),
            sequence_no=int(row["ordinal_no"]),
            receipt_id=int(row["receipt_id"]),
            original_file_name=file_name,
            extension=file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "",
            size_bytes=int(row["file_size"] or 0),
            factory_code=str(row["factory_code"] or ""),
            upload_time_utc=_iso(row["started_at_utc"]) or "",
            completion_time_utc=_iso(row["completed_at_utc"]),
            uploader_login=str(row["login_name"]),
            uploader_name=str(row["display_name"]),
            status=str(row["status"]),
            source_file_id=int(row["source_file_id"]),
            latest_job_id=_optional_int(row["latest_job_id"]),
            error_code=_safe_error_code(row["error_code"]),
            error_message=_safe_error_message(row["error_message"]),
            action_required=("LOT_ID" if str(row["status"]) == "NEEDS_INPUT" else None),
            queue_age_seconds=_optional_int(row["queue_age_seconds"]),
            is_duplicate_receipt=bool(row["is_duplicate_receipt"]),
            can_manage=bool(row["can_manage"]),
            can_download_source=bool(row["can_manage"]),
        )

    @staticmethod
    def _result_item(row: Mapping[str, Any]) -> StageResultPageItem:
        return StageResultPageItem(
            result_summary_id=int(row["result_summary_id"]),
            import_batch_id=int(row["import_batch_id"]),
            job_id=_optional_int(row["job_id"]),
            data_name=str(row["data_name"]),
            product_name=(str(row["product_name"]) if row["product_name"] else None),
            lot_id=(str(row["lot_id"]) if row["lot_id"] else None),
            wafer_count=_optional_int(row["wafer_count"]),
            factory_code=str(row["factory_code"] or ""),
            test_item_count=_optional_int(row["test_item_count"]),
            unit_count=_optional_int(row["unit_count"]),
            pass_count=_optional_int(row["pass_count"]),
            yield_rate=(
                float(row["yield_rate"]) if row["yield_rate"] is not None else None
            ),
            status=str(row["status"]),
            data_type=str(row["data_type"]),
            dataset_id=_optional_int(row["dataset_id"]),
            dataset_version_no=_optional_int(row["dataset_version_no"]),
            created_at_utc=_iso(row["created_at_utc"]) or "",
            can_manage=bool(row["can_manage"]),
            uploader_login=str(row["login_name"]),
            uploader_name=str(row["display_name"]),
        )

    @staticmethod
    def _current_dataset_item(row: Mapping[str, Any]) -> CurrentDatasetCatalogItem:
        return CurrentDatasetCatalogItem(
            dataset_id=int(row["dataset_id"]),
            dataset_version_id=int(row["dataset_version_id"]),
            version_no=int(row["version_no"]),
            import_batch_id=int(row["import_batch_id"]),
            job_id=_optional_int(row["job_id"]),
            processing_run_id=_optional_int(row["processing_run_id"]),
            product_name=(str(row["product_name"]) if row["product_name"] else None),
            lot_id=(str(row["lot_id"]) if row["lot_id"] else None),
            lot_count=int(row["lot_count"] or 0),
            factory_code=str(row["factory_code"] or ""),
            business_domain=str(row["business_domain"]),
            test_stage=str(row["test_stage"]),
            status=str(row["status"]),
            unit_count=_optional_int(row["unit_count"]),
            pass_count=_optional_int(row["pass_count"]),
            yield_rate=(
                float(row["yield_rate"]) if row["yield_rate"] is not None else None
            ),
            source_file_count=int(row["source_file_count"] or 0),
            processed_at_utc=_iso(row["processed_at_utc"]) or "",
            owner_login=str(row["owner_login"]),
            owner_name=str(row["owner_name"]),
            cleaner_version=(
                str(row["cleaner_version"])
                if row.get("cleaner_version") is not None
                else None
            ),
            can_edit_product=bool(row["can_edit_product"]),
            can_export=bool(row["can_export"]),
            can_reprocess=bool(row["can_reprocess"]),
            can_archive=bool(row["can_archive"]),
        )

    @staticmethod
    def _job_summary(row: Mapping[str, Any]) -> JobSafeSummary:
        return JobSafeSummary(
            job_id=int(row["job_id"]),
            job_type=str(row["job_type"]),
            lifecycle_action_type=(
                str(row["lifecycle_action_type"])
                if row.get("lifecycle_action_type") is not None
                else None
            ),
            status=str(row["status"]),
            import_batch_id=_optional_int(row["import_batch_id"]),
            parent_job_id=_optional_int(row["parent_job_id"]),
            requested_at_utc=_iso(row["requested_at_utc"]) or "",
            started_at_utc=_iso(row["started_at_utc"]),
            finished_at_utc=_iso(row["finished_at_utc"]),
            error_code=_safe_error_code(row["error_code"]),
            error_message=_safe_error_message(row["error_message"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
        )

    @classmethod
    def _job_details(cls, row: Mapping[str, Any]) -> JobSafeDetails:
        return JobSafeDetails(
            job_id=int(row["job_id"]),
            job_type=str(row["job_type"]),
            lifecycle_action_type=(
                str(row["lifecycle_action_type"])
                if row.get("lifecycle_action_type") is not None
                else None
            ),
            status=str(row["status"]),
            import_batch_id=_optional_int(row["import_batch_id"]),
            parent_job_id=_optional_int(row["parent_job_id"]),
            requested_at_utc=_iso(row["requested_at_utc"]) or "",
            started_at_utc=_iso(row["started_at_utc"]),
            finished_at_utc=_iso(row["finished_at_utc"]),
            error_code=_safe_error_code(row["error_code"]),
            error_message=_safe_error_message(row["error_message"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            source_file_id=_optional_int(row["source_file_id"]),
            analysis_session_id=_optional_int(row["analysis_session_id"]),
            cleaner_release_id=_optional_int(row["cleaner_release_id"]),
            trigger_type=str(row["trigger_type"]),
            requested_by=(str(row["requested_by"]) if row["requested_by"] else None),
            reason=_safe_error_message(row["reason"]),
            not_before_utc=_iso(row["not_before_utc"]),
            heartbeat_at_utc=_iso(row["heartbeat_at_utc"]),
            lease_expires_at_utc=_iso(row["lease_expires_at_utc"]),
            finalize_protocol=str(row["finalize_protocol"]),
            queue_age_seconds=_optional_int(row["queue_age_seconds"]),
        )

    @staticmethod
    def _release(row: Mapping[str, Any]) -> CleanerReleaseSummary | None:
        if row["cleaner_release_id"] is None:
            return None
        return CleanerReleaseSummary(
            cleaner_release_id=int(row["cleaner_release_id"]),
            cleaner_code=str(row["cleaner_code"] or ""),
            cleaner_version=str(row["cleaner_version"] or ""),
            content_sha256=str(row["code_checksum"] or ""),
        )

    @staticmethod
    def _batch(row: Mapping[str, Any]) -> BatchIdentitySummary | None:
        if row["import_batch_id"] is None:
            return None
        return BatchIdentitySummary(
            import_batch_id=int(row["import_batch_id"]),
            business_domain=str(row["business_domain"] or ""),
            test_stage=str(row["test_stage"] or ""),
            factory_code=str(row["factory_code"] or ""),
            status=str(row["batch_status"]),
            source_file_count=int(row["source_file_count"] or 0),
        )

    @staticmethod
    def _source_lineage(row: Mapping[str, Any]) -> SourceLineageSummary:
        return SourceLineageSummary(
            source_file_id=int(row["source_file_id"]),
            ordinal_no=int(row["ordinal_no"]),
            original_file_name=str(row["original_file_name"]),
            file_size=int(row["file_size"] or 0),
            sha256=(str(row["sha256"]) if row["sha256"] else None),
            lineage_basis=str(row["lineage_basis"]),
        )

    @staticmethod
    def _intent(row: Mapping[str, Any]) -> FinalizeIntentSummary | None:
        if row["intent_status"] is None:
            return None
        return FinalizeIntentSummary(
            status=str(row["intent_status"]),
            staged_at_utc=_iso(row["staged_at_utc"]),
            finalized_at_utc=_iso(row["finalized_at_utc"]),
            aborted_at_utc=_iso(row["aborted_at_utc"]),
        )

    @staticmethod
    def _run(row: Mapping[str, Any]) -> ProcessingRunSummary:
        return ProcessingRunSummary(
            processing_run_id=int(row["processing_run_id"]),
            status=str(row["run_status"]),
            started_at_utc=_iso(row["run_started_at_utc"]),
            finished_at_utc=_iso(row["run_finished_at_utc"]),
        )

    @staticmethod
    def _dataset(row: Mapping[str, Any]) -> DatasetSummary | None:
        if row["dataset_id"] is None or row["dataset_version_id"] is None:
            return None
        return DatasetSummary(
            dataset_id=int(row["dataset_id"]),
            dataset_version_id=int(row["dataset_version_id"]),
            version_no=int(row["version_no"]),
            status=str(row["version_status"]),
            is_current=bool(row["is_current"]),
        )

    @staticmethod
    def _timeline(
        job: Mapping[str, Any], chain: Mapping[str, Any] | None
    ) -> tuple[JobTimelineEvent, ...]:
        raw: list[tuple[datetime, str, str]] = []
        raw.append((job["requested_at_utc"], "JOB_QUEUED", "QUEUED"))
        if job["started_at_utc"] is not None:
            raw.append((job["started_at_utc"], "JOB_STARTED", "RUNNING"))
        if chain is not None and chain["run_started_at_utc"] is not None:
            raw.append(
                (chain["run_started_at_utc"], "RUN_STARTED", str(chain["run_status"]))
            )
        if chain is not None and chain["staged_at_utc"] is not None:
            raw.append((chain["staged_at_utc"], "PUBLISH_STAGED", "STAGED"))
        if chain is not None and chain["finalized_at_utc"] is not None:
            raw.append((chain["finalized_at_utc"], "PUBLISH_FINALIZED", "FINALIZED"))
        if chain is not None and chain["aborted_at_utc"] is not None:
            raw.append((chain["aborted_at_utc"], "PUBLISH_ABORTED", "ABORTED"))
        if chain is not None and chain["run_finished_at_utc"] is not None:
            raw.append(
                (
                    chain["run_finished_at_utc"],
                    "RUN_FINISHED",
                    str(chain["run_status"]),
                )
            )
        if job["finished_at_utc"] is not None:
            raw.append((job["finished_at_utc"], "JOB_FINISHED", str(job["status"])))
        raw.sort(key=lambda item: item[0])
        return tuple(
            JobTimelineEvent(event, status, _iso(occurred) or "")
            for occurred, event, status in raw
        )

    @staticmethod
    def _available_actions(
        principal: Principal,
        job: Mapping[str, Any],
        dataset: DatasetSummary | None,
        *,
        can_manage: bool,
    ) -> tuple[AvailableAction, ...]:
        actions: list[AvailableAction] = []
        if (
            can_manage
            and job["source_file_count"]
            and job["import_batch_id"] is not None
            and principal.can("DATASET_READ")
        ):
            actions.append(AvailableAction("DOWNLOAD_SOURCE", "查看源文件", True, None))
        if (
            dataset is not None
            and dataset.status == "PUBLISHED"
            and (can_manage or dataset.is_current)
        ):
            actions.append(AvailableAction("VIEW_RESULT", "查看结果", True, None))
        if (
            can_manage
            and principal.can("TASK_CREATE")
            and job["import_batch_id"] is not None
        ):
            if str(job["batch_status"]) == "NEEDS_INPUT":
                actions.append(
                    AvailableAction("RESOLVE_LOT_INPUT", "补录 Lot", True, None)
                )
            elif str(job["batch_status"]) in {"FAILED", "PROCESSED"} and str(
                job["status"]
            ) in {"FAILED", "SUCCESS"}:
                actions.append(
                    AvailableAction("REPROCESS_BATCH", "重新处理", True, None)
                )
        if (
            can_manage
            and principal.can("TASK_RETRY")
            and str(job["job_type"]) != "INITIAL_IMPORT"
            and str(job["status"]) in {"QUEUED", "RUNNING"}
        ):
            actions.append(AvailableAction("CANCEL_JOB", "取消任务", True, None))
        return tuple(actions)


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


_CURRENT_DATASET_COLUMNS = """
SELECT d.dataset_id,dv.dataset_version_id,dv.version_no,
       dv.input_batch_id AS import_batch_id,pr.job_id,
       selected_run.processing_run_id,
       COALESCE(product_enrichment.value_text,p.product_name,summary_row.product_name) AS product_name,
       lot_scope.lot_id,lot_scope.lot_count,
       b.factory_code,b.business_domain,d.test_stage,dv.status,
       summary_row.unit_count,summary_row.pass_count,summary_row.yield_rate,
       (SELECT COUNT_BIG(*) FROM ingestion.processing_run_input_file rif
        WHERE rif.processing_run_id=selected_run.processing_run_id)
        AS source_file_count,
       COALESCE(pr.finished_at_utc,dv.published_at_utc) AS processed_at_utc,
       owner_user.login_name AS owner_login,owner_user.display_name AS owner_name,
       cr.cleaner_version,
       CASE WHEN d.access_scope='PERSONAL' AND d.owner_user_id=:user_id THEN 1 ELSE 0 END AS can_edit_product,
       CASE WHEN d.access_scope='PERSONAL' AND d.owner_user_id=:user_id THEN 1 ELSE 0 END AS can_export,
       CASE WHEN d.access_scope='PERSONAL' AND d.owner_user_id=:user_id THEN 1 ELSE 0 END AS can_reprocess,
       CASE WHEN d.access_scope='PERSONAL' AND d.owner_user_id=:user_id THEN 1 ELSE 0 END AS can_archive
"""

_CURRENT_DATASET_FROM = """
 FROM dataset.dataset d
 JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id
 JOIN ingestion.import_batch b ON b.import_batch_id=dv.input_batch_id
 JOIN iam.app_user owner_user ON owner_user.user_id=d.owner_user_id
 LEFT JOIN mdm.product p ON p.product_id=d.product_id
 OUTER APPLY(
     SELECT TOP (1) dvr.processing_run_id
     FROM dataset.dataset_version_run dvr
     WHERE dvr.dataset_version_id=dv.dataset_version_id
     ORDER BY dvr.ordinal_no,dvr.processing_run_id
 ) selected_run
 LEFT JOIN ingestion.processing_run pr
   ON pr.processing_run_id=selected_run.processing_run_id
 LEFT JOIN ingestion.processing_job pj
   ON pj.job_id=pr.job_id
 LEFT JOIN ingestion.cleaner_release cr
   ON cr.cleaner_release_id=pj.cleaner_release_id
 OUTER APPLY(
     SELECT TOP (1) fe.value_text
     FROM ingestion.field_enrichment fe
     WHERE fe.import_batch_id=dv.input_batch_id
       AND fe.source_file_id IS NULL
       AND fe.test_stage=d.test_stage
       AND fe.field_code='PRODUCT_CODE'
       AND fe.action='FILL' AND fe.is_current=1
     ORDER BY fe.enrichment_id DESC
 ) product_enrichment
 OUTER APPLY(
     SELECT CASE WHEN COUNT(DISTINCT tr.lot_id)=1 THEN MIN(tr.lot_id) END AS lot_id,
            COUNT(DISTINCT tr.lot_id) AS lot_count
     FROM dataset.dataset_version_run lot_dvr
     JOIN test.test_run tr ON tr.processing_run_id=lot_dvr.processing_run_id
     WHERE lot_dvr.dataset_version_id=dv.dataset_version_id
       AND tr.lot_id IS NOT NULL
 ) lot_scope
 OUTER APPLY(
     SELECT TOP (1) tr.wafer_id
     FROM dataset.dataset_version_run dvr
     JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id
     WHERE dvr.dataset_version_id=dv.dataset_version_id
       AND tr.wafer_id IS NOT NULL
       AND (:wafer_id IS NULL OR tr.wafer_id LIKE :wafer_id ESCAPE '\\')
     ORDER BY tr.wafer_id
 ) wafer_scope
 OUTER APPLY(
     SELECT TOP (1) s.product_name,s.unit_count,s.pass_count,s.yield_rate
     FROM ingestion.processing_result_summary s
     WHERE s.dataset_id=d.dataset_id
       AND s.dataset_version_no=dv.version_no
       AND s.status='PROCESSED'
     ORDER BY s.result_summary_id DESC
 ) summary_row
"""

_JOB_DETAILS_SQL = (
    """
SELECT j.job_id,j.source_file_id,j.import_batch_id,j.analysis_session_id,
       j.cleaner_release_id,j.job_type,lt.action_type AS lifecycle_action_type,
       j.trigger_type,j.requested_by,j.reason,
       j.status,j.requested_at_utc,j.started_at_utc,j.finished_at_utc,
       j.error_code,j.error_message,j.not_before_utc,j.heartbeat_at_utc,
       j.lease_expires_at_utc,
       j.attempt_count,j.max_attempts,j.parent_job_id,j.finalize_protocol,
       CASE WHEN j.status='QUEUED' AND j.not_before_utc<=SYSUTCDATETIME() THEN
         DATEDIFF(second,j.not_before_utc,SYSUTCDATETIME()) END
         AS queue_age_seconds,
       CASE WHEN j.import_batch_id IS NOT NULL THEN
         (SELECT COUNT_BIG(*) FROM ingestion.import_batch_file ibf
         WHERE ibf.import_batch_id=j.import_batch_id)
         WHEN j.source_file_id IS NOT NULL THEN 1 ELSE 0 END AS source_file_count,
        CASE WHEN (b.access_scope='PERSONAL' AND b.owner_user_id=:user_id)
                OR """
    + quick_write_scope_sql(session_alias="ws")
    + """
               OR (b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL
                  AND j.requested_by_user_id=:user_id)
            THEN 1 ELSE 0 END AS can_manage,
       cr.cleaner_code,cr.cleaner_version,cr.code_checksum,
       b.batch_name,b.business_domain,b.test_stage,b.factory_code,
       b.status AS batch_status
FROM ingestion.processing_job j
LEFT JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id
LEFT JOIN workspace.analysis_session ws
  ON ws.analysis_session_id=j.analysis_session_id
LEFT JOIN ingestion.cleaner_release cr
  ON cr.cleaner_release_id=j.cleaner_release_id
LEFT JOIN ingestion.lifecycle_job_target lt ON lt.job_id=j.job_id
WHERE j.job_id=:job_id AND (
    """
    + quick_read_scope_sql(session_alias="ws")
    + """ OR
    (b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL
     AND j.requested_by_user_id=:user_id) OR
    (j.import_batch_id IS NOT NULL AND
     """
    + batch_read_scope_sql(batch_alias="b")
    + """)
)
"""
)

_JOB_LINKS_SQL = (
    """
SELECT j.job_id,j.import_batch_id,j.parent_job_id,j.job_type,
       lt.action_type AS lifecycle_action_type,j.status,
       j.requested_at_utc,j.started_at_utc,j.finished_at_utc,j.error_code,
       j.error_message,j.attempt_count,j.max_attempts
FROM ingestion.processing_job j
LEFT JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id
LEFT JOIN workspace.analysis_session ws
  ON ws.analysis_session_id=j.analysis_session_id
LEFT JOIN ingestion.lifecycle_job_target lt ON lt.job_id=j.job_id
WHERE (j.job_id=:parent_job_id OR j.parent_job_id=:job_id)
  AND ("""
    + quick_read_scope_sql(session_alias="ws")
    + """ OR
      (b.import_batch_id IS NULL AND ws.analysis_session_id IS NULL
       AND j.requested_by_user_id=:user_id) OR
      (j.import_batch_id IS NOT NULL AND
       """
    + batch_read_scope_sql(batch_alias="b")
    + """))
ORDER BY j.job_id
"""
)

_JOB_PUBLISH_CHAIN_SQL = """
SELECT TOP (1) i.status AS intent_status,pr.processing_run_id,
       pr.status AS run_status,pr.started_at_utc AS run_started_at_utc,
       pr.finished_at_utc AS run_finished_at_utc,
       d.dataset_id,dv.dataset_version_id,dv.version_no,
       dv.status AS version_status,dv.is_current,i.staged_at_utc,
       i.finalized_at_utc,i.aborted_at_utc
FROM ingestion.processing_run pr
LEFT JOIN ingestion.initial_import_finalize_intent i
  ON i.processing_run_id=pr.processing_run_id AND i.job_id=pr.job_id
LEFT JOIN dataset.dataset_version_run dvr
  ON dvr.processing_run_id=pr.processing_run_id
LEFT JOIN dataset.dataset_version dv
  ON dv.dataset_version_id=COALESCE(i.dataset_version_id,dvr.dataset_version_id)
LEFT JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id
WHERE pr.job_id=:job_id
ORDER BY CASE WHEN i.job_id IS NULL THEN 1 ELSE 0 END,
         pr.processing_run_id DESC,dv.dataset_version_id DESC
"""

_JOB_RUN_SOURCE_LINEAGE_SQL = """
SELECT sf.source_file_id,ibf.ordinal_no,r.original_file_name,sf.file_size,
       sf.sha256,rif.lineage_basis
FROM ingestion.processing_run_input_file rif
JOIN ingestion.import_batch_file ibf
  ON ibf.import_batch_file_id=rif.import_batch_file_id
JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id
JOIN ingestion.source_file sf ON sf.source_file_id=r.source_file_id
WHERE rif.processing_run_id=:processing_run_id
ORDER BY ibf.ordinal_no,ibf.import_batch_file_id
"""

_JOB_BATCH_SOURCE_LINEAGE_SQL = """
SELECT sf.source_file_id,ibf.ordinal_no,r.original_file_name,sf.file_size,
       sf.sha256,CAST('BATCH_RECEIPT_NOT_WRITER_VERIFIED' AS varchar(40))
       AS lineage_basis
FROM ingestion.import_batch_file ibf
JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id
JOIN ingestion.source_file sf ON sf.source_file_id=r.source_file_id
WHERE ibf.import_batch_id=:import_batch_id
ORDER BY ibf.ordinal_no,ibf.import_batch_file_id
"""
