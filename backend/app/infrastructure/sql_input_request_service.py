from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.input_requests import (
    LotResolutionResult,
    ProcessingInputRequestFile,
    ProcessingInputRequestSummary,
    ResolveLotInputRequests,
)


class SqlProcessingInputRequestService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _scope() -> str:
        return "(:is_admin=1 OR b.owner_user_id=:user_id)"

    def list_open(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        import_batch_id: int,
    ) -> ProcessingInputRequestSummary:
        with self._engine.connect() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT b.import_batch_id,b.status,latest.job_id AS latest_job_id "
                        "FROM ingestion.import_batch b "
                        "OUTER APPLY(SELECT TOP (1) j.job_id FROM ingestion.processing_job j "
                        "WHERE j.import_batch_id=b.import_batch_id "
                        "ORDER BY j.requested_at_utc DESC,j.job_id DESC) latest "
                        "WHERE b.import_batch_id=:batch AND b.business_domain=:domain "
                        "AND b.test_stage=:stage AND "
                        + self._scope()
                    ),
                    {
                        "batch": import_batch_id,
                        "domain": business_domain,
                        "stage": test_stage,
                        "user_id": principal.user_id,
                        "is_admin": "SYSTEM_ADMIN" in principal.roles,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if batch is None:
                raise DomainError(
                    "IMPORT_BATCH_NOT_FOUND", "上传任务不存在或无权访问", 404
                )
            rows = (
                connection.execute(
                    text(
                        "SELECT pir.input_request_id,pir.job_id,r.source_file_id,"
                        "r.original_file_name,pir.field_code,pir.prompt "
                        "FROM ingestion.processing_input_request pir "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=pir.receipt_id "
                        "WHERE pir.import_batch_id=:batch AND pir.status='OPEN' "
                        "ORDER BY pir.input_request_id"
                    ),
                    {"batch": import_batch_id},
                )
                .mappings()
                .all()
            )
        field_codes = {str(row["field_code"]) for row in rows}
        if field_codes and field_codes != {"LOT_ID"}:
            raise DomainError(
                "INPUT_REQUEST_FIELD_UNSUPPORTED", "当前只支持补录LOT_ID", 409
            )
        prompt = str(rows[0]["prompt"]) if rows else "当前没有待补录的批次号"
        latest_job_id = (
            int(batch["latest_job_id"])
            if batch["latest_job_id"] is not None
            else None
        )
        return ProcessingInputRequestSummary(
            import_batch_id=int(batch["import_batch_id"]),
            status=str(batch["status"]),
            field_code="LOT_ID",
            prompt=prompt,
            latest_job_id=latest_job_id,
            requests=tuple(
                ProcessingInputRequestFile(
                    input_request_id=int(row["input_request_id"]),
                    source_file_id=int(row["source_file_id"]),
                    original_file_name=str(row["original_file_name"]),
                )
                for row in rows
            ),
        )

    def resolve(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        import_batch_id: int,
        request: ResolveLotInputRequests,
    ) -> LotResolutionResult:
        resolutions = {item.input_request_id: item.lot_id for item in request.resolutions}
        parameters: dict[str, Any] = {
            "batch": import_batch_id,
            "domain": business_domain,
            "stage": test_stage,
            "user_id": principal.user_id,
            "is_admin": "SYSTEM_ADMIN" in principal.roles,
        }
        request_names = []
        for index, request_id in enumerate(resolutions):
            name = f"request_{index}"
            parameters[name] = request_id
            request_names.append(f":{name}")
        with self._engine.begin() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT b.import_batch_id,b.status,b.owner_user_id,b.business_domain,"
                        "b.test_stage,b.factory_code "
                        "FROM ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE b.import_batch_id=:batch AND b.business_domain=:domain "
                        "AND b.test_stage=:stage AND "
                        + self._scope()
                    ),
                    parameters,
                )
                .mappings()
                .one_or_none()
            )
            if batch is None:
                raise DomainError(
                    "IMPORT_BATCH_NOT_FOUND", "上传任务不存在或无权访问", 404
                )
            rows = (
                connection.execute(
                    text(
                        "SELECT pir.input_request_id,pir.job_id,pir.import_batch_id,pir.receipt_id,"
                        "pir.field_code,pir.status,pir.resolved_enrichment_id,r.source_file_id,"
                        "r.original_file_name,j.status AS job_status,j.cleaner_release_id,j.max_attempts,"
                        "e.value_text AS resolved_lot "
                        "FROM ingestion.processing_input_request pir WITH (UPDLOCK,HOLDLOCK) "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=pir.receipt_id "
                        "JOIN ingestion.processing_job j ON j.job_id=pir.job_id "
                        "LEFT JOIN ingestion.field_enrichment e ON e.enrichment_id=pir.resolved_enrichment_id "
                        "WHERE pir.import_batch_id=:batch AND pir.input_request_id IN ("
                        + ",".join(request_names)
                        + ") ORDER BY pir.input_request_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            if len(rows) != len(resolutions):
                raise DomainError(
                    "INPUT_REQUEST_NOT_FOUND",
                    "补录请求不存在、已不属于当前任务或无权访问",
                    404,
                )
            if any(str(row["field_code"]) != "LOT_ID" for row in rows):
                raise DomainError(
                    "INPUT_REQUEST_FIELD_UNSUPPORTED", "当前只支持补录LOT_ID", 409
                )
            blocked_jobs = {int(row["job_id"]) for row in rows}
            if len(blocked_jobs) != 1:
                raise DomainError(
                    "INPUT_REQUEST_JOB_MISMATCH", "补录请求不属于同一个阻塞任务", 409
                )
            blocked_job_id = next(iter(blocked_jobs))

            statuses = {str(row["status"]) for row in rows}
            if statuses == {"RESOLVED"}:
                for row in rows:
                    requested_lot = resolutions[int(row["input_request_id"])]
                    if str(row["resolved_lot"] or "") != requested_lot:
                        raise DomainError(
                            "INPUT_REQUEST_ALREADY_RESOLVED",
                            "补录请求已使用不同Lot解决",
                            409,
                        )
                resume_job_id = connection.execute(
                    text(
                        "SELECT job_id FROM ingestion.processing_job "
                        "WHERE parent_job_id=:blocked AND job_type='INITIAL_IMPORT'"
                    ),
                    {"blocked": blocked_job_id},
                ).scalar_one_or_none()
                if resume_job_id is None:
                    raise DomainError(
                        "INPUT_RESUME_JOB_MISSING",
                        "补录已解决但恢复任务不存在，请联系管理员",
                        409,
                    )
                return LotResolutionResult(
                    import_batch_id,
                    int(resume_job_id),
                    "QUEUED",
                )
            if statuses != {"OPEN"}:
                raise DomainError(
                    "INPUT_REQUEST_STATE_CONFLICT", "补录请求状态不一致，请刷新后重试", 409
                )
            if str(batch["status"]) != "NEEDS_INPUT" or any(
                str(row["job_status"]) != "NEEDS_INPUT" for row in rows
            ):
                raise DomainError(
                    "INPUT_REQUEST_STATE_CONFLICT", "任务已不处于等待补录状态", 409
                )
            all_open_ids = {
                int(value)
                for value in connection.execute(
                    text(
                        "SELECT input_request_id FROM ingestion.processing_input_request "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE import_batch_id=:batch AND status='OPEN'"
                    ),
                    {"batch": import_batch_id},
                ).scalars()
            }
            if all_open_ids != set(resolutions):
                raise DomainError(
                    "INPUT_REQUESTS_INCOMPLETE",
                    "必须一次解决当前任务的全部Lot补录请求",
                    409,
                )
            source_lots: dict[int, str] = {}
            for row in rows:
                source_file_id = int(row["source_file_id"])
                lot_id = resolutions[int(row["input_request_id"])]
                previous_lot = source_lots.get(source_file_id)
                if previous_lot is not None and previous_lot != lot_id:
                    raise DomainError(
                        "LOT_RESOLUTION_CONFLICT",
                        "同一源文件不能补录不同Lot",
                        409,
                    )
                source_lots[source_file_id] = lot_id

            if str(batch["factory_code"] or "").strip().casefold() in {
                "riyuexin",
                "riyueguang",
            } and any(
                re.fullmatch(r"[A-Z0-9]{4}-\d{4}", lot_id) is None
                for lot_id in source_lots.values()
            ):
                raise DomainError(
                    "LOT_ID_FORMAT_INVALID",
                    "当前FT厂家Lot格式应为4位字母或数字、连字符和4位数字，例如FA54-9744",
                    422,
                )

            for source_file_id in source_lots:
                existing_enrichment = (
                    connection.execute(
                        text(
                            "SELECT TOP (1) enrichment_id,value_text "
                            "FROM ingestion.field_enrichment WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE import_batch_id=:batch AND test_stage=:stage "
                            "AND field_code='LOT_ID' AND action='FILL' AND is_current=1 "
                            "AND (source_file_id=:source OR source_file_id IS NULL) "
                            "ORDER BY CASE WHEN source_file_id=:source THEN 0 ELSE 1 END,"
                            "enrichment_id DESC"
                        ),
                        {
                            "batch": import_batch_id,
                            "stage": test_stage,
                            "source": source_file_id,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_enrichment is not None:
                    raise DomainError(
                        "LOT_ID_ALREADY_SUPPLIED",
                        "该源文件已有生效Lot，补录流程不得覆盖",
                        409,
                    )
                parsed_lot = connection.execute(
                    text(
                        "SELECT TOP (1) tr.lot_id FROM ingestion.processing_run pr "
                        "JOIN ingestion.processing_job existing_job ON existing_job.job_id=pr.job_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=pr.processing_run_id "
                        "WHERE existing_job.import_batch_id=:batch "
                        "AND pr.source_file_id=:source "
                        "AND NULLIF(LTRIM(RTRIM(tr.lot_id)),'') IS NOT NULL"
                    ),
                    {"batch": import_batch_id, "source": source_file_id},
                ).scalar_one_or_none()
                if parsed_lot is not None:
                    raise DomainError(
                        "LOT_ID_ALREADY_PARSED",
                        "该源文件已解析出Lot，补录流程不得覆盖",
                        409,
                    )

            enrichment_ids: dict[int, int] = {}
            for source_file_id, lot_id in source_lots.items():
                enrichment_ids[source_file_id] = int(
                    connection.execute(
                        text(
                            "INSERT ingestion.field_enrichment("
                            "import_batch_id,source_file_id,test_stage,field_code,action,value_text,"
                            "entered_by,reason,is_current,supersedes_enrichment_id) "
                            "OUTPUT INSERTED.enrichment_id VALUES("
                            ":batch,:source,:stage,'LOT_ID','FILL',:lot,:user_id,:reason,1,NULL)"
                        ),
                        {
                            "batch": import_batch_id,
                            "source": source_file_id,
                            "stage": test_stage,
                            "lot": lot_id,
                            "user_id": principal.user_id,
                            "reason": request.reason,
                        },
                    ).scalar_one()
                )
            resolved_ids: list[int] = []
            for row in rows:
                input_request_id = int(row["input_request_id"])
                source_file_id = int(row["source_file_id"])
                updated = connection.execute(
                    text(
                        "UPDATE ingestion.processing_input_request SET status='RESOLVED',"
                        "resolved_enrichment_id=:enrichment,resolved_by=:user_id,"
                        "resolved_at_utc=SYSUTCDATETIME() "
                        "WHERE input_request_id=:request AND status='OPEN'"
                    ),
                    {
                        "enrichment": enrichment_ids[source_file_id],
                        "user_id": principal.user_id,
                        "request": input_request_id,
                    },
                )
                if updated.rowcount != 1:
                    raise DomainError(
                        "INPUT_REQUEST_STATE_CONFLICT",
                        "补录请求已被其他操作更新，请刷新后重试",
                        409,
                    )
                resolved_ids.append(input_request_id)

            blocked = rows[0]
            cleaner_release_id = blocked["cleaner_release_id"]
            if cleaner_release_id is None:
                raise DomainError(
                    "INPUT_RESUME_RELEASE_MISSING",
                    "阻塞任务没有可复用的Cleaner Release",
                    409,
                )
            idempotency_key = f"input-resume:{blocked_job_id}"
            resume_job_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job("
                        "source_file_id,import_batch_id,cleaner_release_id,parent_job_id,job_type,"
                        "trigger_type,requested_by,requested_by_user_id,reason,status,idempotency_key,max_attempts) "
                        "OUTPUT INSERTED.job_id VALUES(NULL,:batch,:release,:parent,'INITIAL_IMPORT',"
                        "'MANUAL',:login,:user_id,:reason,'QUEUED',:idempotency,:max_attempts)"
                    ),
                    {
                        "batch": import_batch_id,
                        "release": cleaner_release_id,
                        "parent": blocked_job_id,
                        "login": principal.login_name,
                        "user_id": principal.user_id,
                        "reason": "Lot补录完成后恢复原Cleaner Release",
                        "idempotency": idempotency_key,
                        "max_attempts": int(blocked["max_attempts"]),
                    },
                ).scalar_one()
            )
            batch_updated = connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='QUEUED',completed_at_utc=NULL "
                    "WHERE import_batch_id=:batch AND status='NEEDS_INPUT'"
                ),
                {"batch": import_batch_id},
            )
            if batch_updated.rowcount != 1:
                raise DomainError(
                    "BATCH_STATE_CONFLICT", "上传任务状态已变化，无法恢复执行", 409
                )
            connection.execute(
                text(
                    "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                    "before_json,after_json,reason,correlation_id,actor_user_id) VALUES("
                    ":actor,'LOT_INPUT_RESOLVED','ingestion.import_batch',:entity,"
                    ":before_json,:after_json,:reason,:correlation,:user_id)"
                ),
                {
                    "actor": principal.login_name[:128],
                    "entity": str(import_batch_id),
                    "before_json": json.dumps(
                        {
                            "batch_status": "NEEDS_INPUT",
                            "blocked_job_id": blocked_job_id,
                        },
                        separators=(",", ":"),
                    ),
                    "after_json": json.dumps(
                        {
                            "batch_status": "QUEUED",
                            "resume_job_id": resume_job_id,
                            "resolved_request_ids": sorted(resolved_ids),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "reason": request.reason,
                    "correlation": f"job:{blocked_job_id}",
                    "user_id": principal.user_id,
                },
            )
        return LotResolutionResult(import_batch_id, resume_job_id, "QUEUED")
