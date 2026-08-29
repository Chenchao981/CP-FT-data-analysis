from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.stage_data import (
    BatchFileInfo,
    BatchInfo,
    StageResultRow,
    StageUploadRow,
    StoredUpload,
    WorkerBatchInfo,
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


class SqlStageDataService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register_upload(
        self,
        principal: Principal,
        business_domain: str,
        test_stage: str,
        factory_code: str,
        files: Sequence[StoredUpload],
        remark: str | None,
    ) -> int:
        stage_label = test_stage.upper()
        catalog_metadata = [
            item.source_metadata
            for item in files
            if item.source_metadata is not None
        ]
        source_channel = "SOURCE_CATALOG" if catalog_metadata else "WEB"
        batch_metadata: dict[str, object] = {"uploader_user_id": principal.user_id}
        if catalog_metadata:
            first = catalog_metadata[0]
            batch_metadata["source_catalog"] = {
                key: first[key]
                for key in (
                    "source_root_code",
                    "source_relative_path",
                    "source_manifest_mode",
                    "source_manifest_sha256",
                    "source_file_count",
                    "source_total_bytes",
                )
                if key in first
            }
        batch_name = (
            files[0].original_name
            if len(files) == 1
            else f"{stage_label}上传（{len(files)}个文件）"
        )
        with self._engine.begin() as connection:
            batch_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch(source_channel,uploaded_by,status,metadata_json,owner_user_id,business_domain,test_stage,factory_code,batch_name,remark) "
                        "OUTPUT INSERTED.import_batch_id VALUES(:source_channel,:login,'RECEIVED',:metadata,:owner,:domain,:stage,:factory,:name,:remark)"
                    ),
                    {
                        "login": principal.login_name,
                        "source_channel": source_channel,
                        "owner": principal.user_id,
                        "domain": business_domain,
                        "stage": stage_label,
                        "factory": factory_code,
                        "name": batch_name,
                        "remark": remark,
                        "metadata": json.dumps(
                            batch_metadata, ensure_ascii=False
                        ),
                    },
                ).scalar_one()
            )
            for ordinal, item in enumerate(files, start=1):
                source_id = connection.execute(
                    text(
                        "SELECT source_file_id FROM ingestion.source_file WHERE sha256=:sha256"
                    ),
                    {"sha256": item.sha256},
                ).scalar_one_or_none()
                duplicate = source_id is not None
                if source_id is None:
                    source_id = int(
                        connection.execute(
                            text(
                                "INSERT ingestion.source_file(sha256,file_size,canonical_storage_uri,metadata_json) OUTPUT INSERTED.source_file_id "
                                "VALUES(:sha256,:size,:uri,:metadata)"
                            ),
                            {
                                "sha256": item.sha256,
                                "size": item.size_bytes,
                                "uri": str(item.path),
                                "metadata": json.dumps(
                                    {"extension": item.path.suffix.lower()},
                                    ensure_ascii=False,
                                ),
                            },
                        ).scalar_one()
                    )
                receipt_id = int(
                    connection.execute(
                        text(
                            "INSERT ingestion.source_file_receipt(source_file_id,import_batch_id,original_file_name,received_by,received_channel,is_duplicate_receipt,metadata_json) "
                            "OUTPUT INSERTED.receipt_id VALUES(:source,:batch,:name,:login,:received_channel,:duplicate,:metadata)"
                        ),
                        {
                            "source": source_id,
                            "batch": batch_id,
                            "name": item.original_name,
                            "login": principal.login_name,
                            "received_channel": source_channel,
                            "duplicate": duplicate,
                            "metadata": json.dumps(
                                {
                                    "owner_user_id": principal.user_id,
                                    "receipt_storage_uri": str(item.path),
                                    **(item.source_metadata or {}),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch_file(import_batch_id,receipt_id,file_role,ordinal_no,required_flag,detected_format_code,detected_profile_version,detection_evidence_json) "
                        "VALUES(:batch,:receipt,'DETAIL',:ordinal,1,NULL,NULL,:evidence)"
                    ),
                    {
                        "batch": batch_id,
                        "receipt": receipt_id,
                        "ordinal": ordinal,
                        "evidence": json.dumps(
                            {
                                "business_domain": business_domain,
                                "test_stage": stage_label,
                                "factory": factory_code,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
        return batch_id

    def mark_processing(self, batch_id: int, principal: Principal) -> int:
        with self._engine.begin() as connection:
            job_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job(source_file_id,job_type,trigger_type,requested_by,status,import_batch_id,reason,metadata_json) "
                        "OUTPUT INSERTED.job_id VALUES(NULL,'PARSE','AUTO',:login,'RUNNING',:batch,N'上传后自动调用现有清洗程序',:metadata)"
                    ),
                    {
                        "login": principal.login_name,
                        "batch": batch_id,
                        "metadata": json.dumps(
                            {"requested_by_user_id": principal.user_id}
                        ),
                    },
                ).scalar_one()
            )
            connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='PROCESSING' WHERE import_batch_id=:batch"
                ),
                {"batch": batch_id},
            )
        return job_id

    def mark_queued(self, batch_id: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='QUEUED' "
                    "WHERE import_batch_id=:batch "
                    "AND status='RECEIVED'"
                ),
                {"batch": batch_id},
            )

    def worker_batch_info(self, batch_id: int) -> WorkerBatchInfo:
        with self._engine.connect() as connection:
            batch = (
                connection.execute(
                    text(
                        "SELECT import_batch_id,business_domain,test_stage,factory_code "
                        "FROM ingestion.import_batch WHERE import_batch_id=:batch"
                    ),
                    {"batch": batch_id},
                )
                .mappings()
                .one_or_none()
            )
            if batch is None:
                raise RuntimeError(f"upload task {batch_id} was not found")
            rows = (
                connection.execute(
                    text(
                        "SELECT r.receipt_id,r.source_file_id,r.original_file_name,r.metadata_json,"
                        "s.canonical_storage_uri AS storage_uri,s.sha256 AS expected_sha256,"
                        "lot.value_text AS lot_id_override "
                        "FROM ingestion.import_batch_file ibf "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                        "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                        "OUTER APPLY(SELECT TOP (1) e.value_text FROM ingestion.field_enrichment e "
                        "WHERE e.import_batch_id=ibf.import_batch_id AND e.test_stage=:stage "
                        "AND e.field_code='LOT_ID' AND e.action='FILL' AND e.is_current=1 "
                        "AND (e.source_file_id=r.source_file_id OR e.source_file_id IS NULL) "
                        "ORDER BY CASE WHEN e.source_file_id=r.source_file_id THEN 0 ELSE 1 END,e.enrichment_id DESC) lot "
                        "WHERE ibf.import_batch_id=:batch ORDER BY ibf.ordinal_no"
                    ),
                    {"batch": batch_id, "stage": str(batch["test_stage"])},
                )
                .mappings()
                .all()
            )
        files_list: list[BatchFileInfo] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            storage_uri = metadata.get("receipt_storage_uri") or row["storage_uri"]
            files_list.append(
                BatchFileInfo(
                    int(row["receipt_id"]),
                    str(row["original_file_name"]),
                    str(storage_uri),
                    int(row["source_file_id"]),
                    str(row["expected_sha256"])
                    if row["expected_sha256"] is not None
                    else None,
                    str(row["lot_id_override"])
                    if row["lot_id_override"] is not None
                    else None,
                )
            )
        files = tuple(files_list)
        return WorkerBatchInfo(
            int(batch["import_batch_id"]),
            str(batch["business_domain"]),
            str(batch["test_stage"]),
            str(batch["factory_code"]),
            files,
        )

    def worker_mark_processing(
        self, batch_id: int, job_id: int, lease_token: str
    ) -> None:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE b SET status='PROCESSING' FROM ingestion.import_batch b "
                    "WHERE b.import_batch_id=:batch AND b.status='QUEUED' AND EXISTS("
                    "SELECT 1 FROM ingestion.processing_job j WHERE j.job_id=:job "
                    "AND j.import_batch_id=b.import_batch_id AND j.status='RUNNING' "
                    "AND j.finalize_protocol='ATOMIC_V1' "
                    "AND j.lease_token=CONVERT(uniqueidentifier,:lease_token) "
                    "AND j.lease_expires_at_utc>=SYSUTCDATETIME())"
                ),
                {"batch": batch_id, "job": job_id, "lease_token": lease_token},
            )
            if updated.rowcount != 1:
                idempotent = connection.execute(
                    text(
                        "SELECT 1 FROM ingestion.import_batch b "
                        "JOIN ingestion.processing_job j ON j.import_batch_id=b.import_batch_id "
                        "WHERE b.import_batch_id=:batch AND b.status='PROCESSING' "
                        "AND j.job_id=:job AND j.status='RUNNING' "
                        "AND j.finalize_protocol='ATOMIC_V1' "
                        "AND j.lease_token=CONVERT(uniqueidentifier,:lease_token) "
                        "AND j.lease_expires_at_utc>=SYSUTCDATETIME()"
                    ),
                    {"batch": batch_id, "job": job_id, "lease_token": lease_token},
                ).scalar_one_or_none()
                if idempotent is None:
                    raise DomainError(
                        "BATCH_STATE_CONFLICT",
                        "上传任务状态或Worker租约已变化，不能开始处理",
                        409,
                    )

    def mark_failed(
        self, batch_id: int, job_id: int, message: str, *, finish_job: bool = True
    ) -> None:
        with self._engine.begin() as connection:
            if finish_job:
                connection.execute(
                    text(
                        "UPDATE ingestion.processing_job SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_code='CLEANER_FAILED',error_message=:message WHERE job_id=:job"
                    ),
                    {"message": message[-4000:], "job": job_id},
                )
            connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='FAILED',"
                    "completed_at_utc=SYSUTCDATETIME() WHERE import_batch_id=:batch "
                    "AND status IN('QUEUED','PROCESSING')"
                ),
                {"batch": batch_id},
            )

    def record_result(
        self, batch_id: int, job_id: int, result: dict, *, finish_job: bool = True
    ) -> None:
        with self._engine.begin() as connection:
            values = {
                "batch": batch_id,
                "job": job_id,
                "name": result["data_name"],
                "product": result.get("product_name"),
                "lot": result.get("lot_id"),
                "wafers": result.get("wafer_count"),
                "factory": result["factory_code"],
                "output": result["output_uri"],
                "items": result.get("test_item_count"),
                "units": result.get("unit_count"),
                "passes": result.get("pass_count"),
                "yield_rate": result.get("yield_rate"),
                "data_type": result.get("data_type", "CP"),
                "dataset_id": result.get("dataset_id"),
                "dataset_version_no": result.get("dataset_version_no"),
                "manifest": json.dumps(result.get("artifacts", []), ensure_ascii=False),
            }
            updated = connection.execute(
                text(
                    "UPDATE ingestion.processing_result_summary SET data_name=:name,"
                    "product_name=:product,lot_id=:lot,wafer_count=:wafers,"
                    "factory_code=:factory,output_uri=:output,test_item_count=:items,"
                    "unit_count=:units,pass_count=:passes,yield_rate=:yield_rate,"
                    "status='PROCESSED',data_type=:data_type,dataset_id=:dataset_id,"
                    "dataset_version_no=:dataset_version_no,artifact_manifest_json=:manifest "
                    "WHERE job_id=:job AND import_batch_id=:batch"
                ),
                values,
            )
            if updated.rowcount == 0:
                connection.execute(
                    text(
                        "INSERT ingestion.processing_result_summary(import_batch_id,job_id,data_name,product_name,lot_id,wafer_count,factory_code,output_uri,test_item_count,unit_count,pass_count,yield_rate,status,data_type,dataset_id,dataset_version_no,artifact_manifest_json) "
                        "VALUES(:batch,:job,:name,:product,:lot,:wafers,:factory,:output,:items,:units,:passes,:yield_rate,'PROCESSED',:data_type,:dataset_id,:dataset_version_no,:manifest)"
                    ),
                    values,
                )
            if finish_job:
                connection.execute(
                    text(
                        "UPDATE ingestion.processing_job SET status='SUCCESS',finished_at_utc=SYSUTCDATETIME() WHERE job_id=:job"
                    ),
                    {"job": job_id},
                )
            connection.execute(
                text(
                    "UPDATE ingestion.import_batch SET status='PROCESSED',completed_at_utc=SYSUTCDATETIME() WHERE import_batch_id=:batch"
                ),
                {"batch": batch_id},
            )

    def record_artifacts(
        self,
        job_id: int,
        lease_token: str,
        artifacts,
        expires_at_utc,
    ) -> None:
        with self._engine.begin() as connection:
            lease_valid = connection.execute(
                text(
                    "SELECT 1 FROM ingestion.processing_job WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE job_id=:job AND status='RUNNING' "
                    "AND finalize_protocol='ATOMIC_V1' "
                    "AND lease_token=CONVERT(uniqueidentifier,:lease_token) "
                    "AND lease_expires_at_utc>=SYSUTCDATETIME()"
                ),
                {"job": job_id, "lease_token": lease_token},
            ).scalar_one_or_none()
            if lease_valid is None:
                raise DomainError(
                    "JOB_LEASE_LOST", f"job {job_id} lease is no longer valid", 409
                )
            for artifact in artifacts:
                path = str(artifact.path)
                connection.execute(
                    text(
                        "IF NOT EXISTS(SELECT 1 FROM ingestion.processing_artifact "
                        "WHERE job_id=:job AND artifact_role=:role AND sha256=:sha) "
                        "INSERT ingestion.processing_artifact("
                        "job_id,processing_run_id,artifact_role,file_name,storage_uri,"
                        "file_size,sha256,temporary_flag,expires_at_utc) VALUES("
                        ":job,NULL,:role,:name,:uri,:size,:sha,1,:expires)"
                    ),
                    {
                        "job": job_id,
                        "role": artifact.role,
                        "name": path.replace("\\", "/").rsplit("/", 1)[-1],
                        "uri": path,
                        "size": artifact.size_bytes,
                        "sha": artifact.sha256,
                        "expires": expires_at_utc,
                    },
                )

    @staticmethod
    def _scope() -> str:
        return "(:is_admin=1 OR b.owner_user_id=:user_id)"

    def get_batch_info(
        self, principal: Principal, business_domain: str, test_stage: str, batch_id: int
    ) -> BatchInfo | None:
        with self._engine.connect() as connection:
            batch_row = (
                connection.execute(
                    text(
                        "SELECT b.import_batch_id,b.factory_code,b.status FROM ingestion.import_batch b "
                        "WHERE b.import_batch_id=:batch AND b.business_domain=:domain AND b.test_stage=:stage AND "
                        + self._scope()
                    ),
                    {
                        "user_id": principal.user_id,
                        "is_admin": "SYSTEM_ADMIN" in principal.roles,
                        "batch": batch_id,
                        "domain": business_domain,
                        "stage": test_stage,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if batch_row is None:
                return None
            file_rows = (
                connection.execute(
                    text(
                        "SELECT r.receipt_id,r.source_file_id,r.original_file_name,"
                        "s.canonical_storage_uri,s.sha256 AS expected_sha256 "
                        "FROM ingestion.import_batch_file ibf "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                        "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                        "WHERE ibf.import_batch_id=:batch ORDER BY ibf.ordinal_no"
                    ),
                    {"batch": batch_id},
                )
                .mappings()
                .all()
            )
        files = tuple(
            BatchFileInfo(
                int(r["receipt_id"]),
                str(r["original_file_name"]),
                str(r["canonical_storage_uri"]),
                int(r["source_file_id"]),
                str(r["expected_sha256"])
                if r["expected_sha256"] is not None
                else None,
            )
            for r in file_rows
        )
        return BatchInfo(
            int(batch_row["import_batch_id"]),
            str(batch_row["factory_code"] or ""),
            str(batch_row["status"]),
            files,
        )

    def archive_previous_results(self, batch_id: int) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_result_summary SET status='ARCHIVED' WHERE import_batch_id=:batch AND status='PROCESSED'"
                ),
                {"batch": batch_id},
            )

    def list_uploads(
        self, principal: Principal, business_domain: str, test_stage: str
    ) -> tuple[StageUploadRow, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT b.import_batch_id,ibf.ordinal_no,r.receipt_id,r.source_file_id,r.original_file_name,s.file_size,b.factory_code,b.started_at_utc,b.completed_at_utc,u.login_name,u.display_name,b.status,"
                        "latest.job_id AS latest_job_id,latest.error_code,latest.error_message "
                        "FROM ingestion.import_batch b JOIN iam.app_user u ON u.user_id=b.owner_user_id JOIN ingestion.import_batch_file ibf ON ibf.import_batch_id=b.import_batch_id "
                        "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                        "OUTER APPLY(SELECT TOP (1) j.job_id,j.error_code,j.error_message FROM ingestion.processing_job j "
                        "WHERE j.import_batch_id=b.import_batch_id ORDER BY j.job_id DESC) latest "
                        "WHERE b.business_domain=:domain AND b.test_stage=:stage AND "
                        + self._scope()
                        + " ORDER BY b.import_batch_id DESC,ibf.ordinal_no"
                    ),
                    {
                        "user_id": principal.user_id,
                        "is_admin": "SYSTEM_ADMIN" in principal.roles,
                        "domain": business_domain,
                        "stage": test_stage,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            StageUploadRow(
                int(r["import_batch_id"]),
                int(r["ordinal_no"]),
                int(r["receipt_id"]),
                str(r["original_file_name"]),
                str(r["original_file_name"]).rsplit(".", 1)[-1].lower()
                if "." in str(r["original_file_name"])
                else "",
                int(r["file_size"] or 0),
                str(r["factory_code"] or ""),
                _iso(r["started_at_utc"]) or "",
                _iso(r["completed_at_utc"]),
                str(r["login_name"]),
                str(r["display_name"]),
                str(r["status"]),
                int(r["source_file_id"]),
                int(r["latest_job_id"]) if r["latest_job_id"] is not None else None,
                str(r["error_code"]) if r["error_code"] is not None else None,
                str(r["error_message"])
                if r["error_message"] is not None
                else None,
                "LOT_ID" if str(r["status"]) == "NEEDS_INPUT" else None,
            )
            for r in rows
        )

    def list_results(
        self, principal: Principal, business_domain: str, test_stage: str
    ) -> tuple[StageResultRow, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT s.* FROM ingestion.processing_result_summary s JOIN ingestion.import_batch b ON b.import_batch_id=s.import_batch_id "
                        "WHERE b.business_domain=:domain AND b.test_stage=:stage AND s.status='PROCESSED' AND "
                        + self._scope()
                        + " ORDER BY s.result_summary_id DESC"
                    ),
                    {
                        "user_id": principal.user_id,
                        "is_admin": "SYSTEM_ADMIN" in principal.roles,
                        "domain": business_domain,
                        "stage": test_stage,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            StageResultRow(
                int(r["result_summary_id"]),
                int(r["import_batch_id"]),
                str(r["data_name"]),
                r["product_name"],
                r["lot_id"],
                int(r["wafer_count"]) if r["wafer_count"] is not None else None,
                str(r["factory_code"] or ""),
                int(r["test_item_count"]) if r["test_item_count"] is not None else None,
                int(r["unit_count"]) if r["unit_count"] is not None else None,
                int(r["pass_count"]) if r["pass_count"] is not None else None,
                float(r["yield_rate"]) if r["yield_rate"] is not None else None,
                str(r["status"]),
                str(r["data_type"]),
                int(r["dataset_id"]) if r["dataset_id"] is not None else None,
                int(r["dataset_version_no"])
                if r["dataset_version_no"] is not None
                else None,
                _iso(r["created_at_utc"]) or "",
            )
            for r in rows
        )
