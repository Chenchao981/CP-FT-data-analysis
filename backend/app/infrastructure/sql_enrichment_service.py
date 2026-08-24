from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.enrichments import CreateFieldEnrichmentRequest, FieldEnrichmentRecord


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


class SqlFieldEnrichmentService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _access_scope() -> str:
        return "(owner_user_id=:user_id OR EXISTS(SELECT 1 FROM iam.user_role ur JOIN iam.role r ON r.role_id=ur.role_id WHERE ur.user_id=:user_id AND r.role_code='SYSTEM_ADMIN'))"

    def create(
        self, request: CreateFieldEnrichmentRequest, principal: Principal
    ) -> FieldEnrichmentRecord:
        with self._engine.begin() as connection:
            batch_stage = connection.execute(
                text(
                    "SELECT test_stage FROM ingestion.import_batch "
                    "WHERE import_batch_id=:batch_id AND " + self._access_scope()
                ),
                {"batch_id": request.import_batch_id, "user_id": principal.user_id},
            ).scalar_one_or_none()
            if batch_stage is None:
                raise DomainError(
                    "IMPORT_BATCH_NOT_FOUND", "上传任务不存在或无权访问", 404
                )
            if str(batch_stage) != request.test_stage.value:
                raise DomainError(
                    "ENRICHMENT_STAGE_MISMATCH", "补录Stage与上传任务不一致", 409
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
                connection.execute(
                    text(
                        "UPDATE ingestion.field_enrichment SET is_current=0 "
                        "WHERE enrichment_id=:enrichment_id"
                    ),
                    {"enrichment_id": previous},
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
                        + self._access_scope().replace(
                            "owner_user_id", "b.owner_user_id"
                        )
                        + ") "
                        "AND is_current=1 ORDER BY test_stage,field_code,source_file_id"
                    ),
                    {"batch_id": import_batch_id, "user_id": principal.user_id},
                )
                .mappings()
                .all()
            )
        return tuple(_record(row) for row in rows)
