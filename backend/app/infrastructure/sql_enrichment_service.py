from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.enrichments import CreateFieldEnrichmentRequest, FieldEnrichmentRecord
from app.infrastructure.sql_visibility import (
    batch_read_scope_sql,
    batch_write_scope_sql,
    visibility_parameters,
)


def _record(row: Mapping[str, Any]) -> FieldEnrichmentRecord:
    return FieldEnrichmentRecord(
        enrichment_id=int(row["enrichment_id"]),
        import_batch_id=int(row["import_batch_id"]),
        source_file_id=int(row["source_file_id"])
        if row["source_file_id"] is not None
        else None,
        test_stage=str(row["test_stage"]),
        field_code=str(row["field_code"]),
        action=str(row["action"]),
        value_text=str(row["value_text"]) if row["value_text"] is not None else None,
        entered_by=int(row["entered_by"]),
        reason=str(row["reason"]),
        is_current=bool(row["is_current"]),
    )


def _assert_direct_lot_enrichment_allowed(
    batch_status: str, *, has_open_lot_request: bool
) -> None:
    normalized_status = batch_status.strip().upper()
    if normalized_status == "NEEDS_INPUT" or has_open_lot_request:
        raise DomainError(
            "LOT_INPUT_RESOLUTION_REQUIRED",
            "该批次存在待处理的Lot请求，请使用专用 input-requests/resolve 补录入口",
            409,
        )
    if normalized_status in {"QUEUED", "PROCESSING"}:
        raise DomainError(
            "LOT_ENRICHMENT_BATCH_ACTIVE",
            "批次正在排队或处理中，不能从通用补录入口修改Lot",
            409,
        )


class SqlFieldEnrichmentService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _access_scope(access_mode: str, *, batch_alias: str = "b") -> str:
        normalized = access_mode.strip().upper()
        if normalized == "READ":
            return batch_read_scope_sql(batch_alias=batch_alias)
        if normalized == "WRITE":
            return batch_write_scope_sql(batch_alias=batch_alias)
        raise ValueError("enrichment access mode must be READ or WRITE")

    def create(
        self, request: CreateFieldEnrichmentRequest, principal: Principal
    ) -> FieldEnrichmentRecord:
        with self._engine.begin() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT b.test_stage,b.factory_code,b.status FROM ingestion.import_batch b "
                        "WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE b.import_batch_id=:batch_id AND "
                        + self._access_scope("WRITE")
                    ),
                    visibility_parameters(principal)
                    | {"batch_id": request.import_batch_id},
                )
                .mappings()
                .one_or_none()
            )
            if batch is None:
                raise DomainError(
                    "IMPORT_BATCH_NOT_FOUND", "上传任务不存在或无权访问", 404
                )
            if str(batch["test_stage"]) != request.test_stage.value:
                raise DomainError(
                    "ENRICHMENT_STAGE_MISMATCH", "补录Stage与上传任务不一致", 409
                )
            if request.field_code == "LOT_ID":
                has_open_lot_request = (
                    connection.execute(
                        text(
                            "SELECT TOP (1) 1 FROM ingestion.processing_input_request "
                            "WITH (UPDLOCK,HOLDLOCK) WHERE import_batch_id=:batch_id "
                            "AND field_code='LOT_ID' AND status='OPEN'"
                        ),
                        {"batch_id": request.import_batch_id},
                    ).scalar_one_or_none()
                    is not None
                )
                _assert_direct_lot_enrichment_allowed(
                    str(batch["status"]),
                    has_open_lot_request=has_open_lot_request,
                )
            if (
                request.field_code == "LOT_ID"
                and str(batch["factory_code"] or "").strip().casefold()
                in {"riyuexin", "riyueguang"}
                and re.fullmatch(r"[A-Z0-9]{4}-\d{4}", request.value_text or "") is None
            ):
                raise DomainError(
                    "LOT_ID_FORMAT_INVALID",
                    "当前FT厂家Lot格式应为4位字母或数字、连字符和4位数字，例如FA54-9744",
                    422,
                )
            if request.source_file_id is not None:
                linked = connection.execute(
                    text(
                        "SELECT TOP 1 1 FROM ingestion.source_file_receipt sfr "
                        "LEFT JOIN ingestion.import_batch_file ibf ON ibf.receipt_id=sfr.receipt_id "
                        "WHERE sfr.source_file_id=:source_file_id AND "
                        "(sfr.import_batch_id=:batch_id OR ibf.import_batch_id=:batch_id)"
                    ),
                    {
                        "source_file_id": request.source_file_id,
                        "batch_id": request.import_batch_id,
                    },
                ).scalar_one_or_none()
                if linked is None:
                    raise DomainError(
                        "SOURCE_FILE_NOT_IN_BATCH",
                        "source file is not linked to the declared import batch",
                        409,
                    )
            previous = connection.execute(
                text(
                    "SELECT enrichment_id FROM ingestion.field_enrichment WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE import_batch_id=:batch_id AND "
                    "((source_file_id IS NULL AND :source_file_id IS NULL) OR source_file_id=:source_file_id) "
                    "AND test_stage=:test_stage AND field_code=:field_code AND is_current=1"
                ),
                {
                    "batch_id": request.import_batch_id,
                    "source_file_id": request.source_file_id,
                    "test_stage": request.test_stage.value,
                    "field_code": request.field_code,
                },
            ).scalar_one_or_none()
            if previous is not None:
                if request.field_code == "LOT_ID":
                    raise DomainError(
                        "LOT_ID_ALREADY_SUPPLIED",
                        "该范围已有生效Lot，补录不得覆盖",
                        409,
                    )
                connection.execute(
                    text(
                        "UPDATE ingestion.field_enrichment SET is_current=0 "
                        "WHERE enrichment_id=:enrichment_id"
                    ),
                    {"enrichment_id": previous},
                )
            if request.field_code == "LOT_ID":
                parsed_lot = connection.execute(
                    text(
                        "SELECT TOP (1) tr.lot_id FROM ingestion.processing_run pr "
                        "JOIN ingestion.processing_job j ON j.job_id=pr.job_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=pr.processing_run_id "
                        "WHERE j.import_batch_id=:batch_id "
                        "AND (:source_file_id IS NULL OR pr.source_file_id=:source_file_id) "
                        "AND NULLIF(LTRIM(RTRIM(tr.lot_id)),'') IS NOT NULL"
                    ),
                    {
                        "batch_id": request.import_batch_id,
                        "source_file_id": request.source_file_id,
                    },
                ).scalar_one_or_none()
                if parsed_lot is not None:
                    raise DomainError(
                        "LOT_ID_ALREADY_PARSED",
                        "该范围已解析出Lot，补录不得覆盖",
                        409,
                    )
            row = (
                connection.execute(
                    text(
                        "INSERT ingestion.field_enrichment("
                        "import_batch_id,source_file_id,test_stage,field_code,action,value_text,"
                        "entered_by,reason,is_current,supersedes_enrichment_id) OUTPUT "
                        "INSERTED.enrichment_id,INSERTED.import_batch_id,INSERTED.source_file_id,"
                        "INSERTED.test_stage,INSERTED.field_code,INSERTED.action,INSERTED.value_text,"
                        "INSERTED.entered_by,INSERTED.reason,INSERTED.is_current VALUES("
                        ":import_batch_id,:source_file_id,:test_stage,:field_code,:action,:value_text,"
                        ":entered_by,:reason,1,:supersedes)"
                    ),
                    {
                        **request.model_dump(mode="json"),
                        "supersedes": previous,
                    },
                )
                .mappings()
                .one()
            )
        return _record(row)

    def list_current(
        self, import_batch_id: int, principal: Principal
    ) -> tuple[FieldEnrichmentRecord, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT enrichment_id,import_batch_id,source_file_id,test_stage,field_code,"
                        "action,value_text,entered_by,reason,is_current "
                        "FROM ingestion.field_enrichment WHERE import_batch_id=:batch_id "
                        "AND EXISTS(SELECT 1 FROM ingestion.import_batch b WHERE "
                        "b.import_batch_id=ingestion.field_enrichment.import_batch_id AND "
                        + self._access_scope("READ")
                        + ") "
                        "AND is_current=1 ORDER BY test_stage,field_code,source_file_id"
                    ),
                    visibility_parameters(principal) | {"batch_id": import_batch_id},
                )
                .mappings()
                .all()
            )
        return tuple(_record(row) for row in rows)
