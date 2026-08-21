from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine, text

from app.domain.auth import Principal
from app.domain.production_data import ProductionResultRow, ProductionUploadRow, StoredUpload


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


class SqlProductionDataService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register_upload(self, principal: Principal, factory_code: str, files: Sequence[StoredUpload], remark: str | None) -> int:
        with self._engine.begin() as connection:
            batch_id = int(connection.execute(text(
                "INSERT ingestion.import_batch(source_channel,uploaded_by,status,metadata_json,owner_user_id,business_domain,test_stage,factory_code,batch_name,remark) "
                "OUTPUT INSERTED.import_batch_id VALUES('WEB',:login,'RECEIVED',:metadata,:owner,'PRODUCTION','CP',:factory,:name,:remark)"
            ), {"login": principal.login_name, "owner": principal.user_id, "factory": factory_code, "name": files[0].original_name if len(files) == 1 else f"CP上传（{len(files)}个文件）", "remark": remark, "metadata": json.dumps({"uploader_user_id": principal.user_id}, ensure_ascii=False)}).scalar_one())
            for ordinal, item in enumerate(files, start=1):
                source_id = connection.execute(text("SELECT source_file_id FROM ingestion.source_file WHERE sha256=:sha256"), {"sha256": item.sha256}).scalar_one_or_none()
                duplicate = source_id is not None
                if source_id is None:
                    source_id = int(connection.execute(text(
                        "INSERT ingestion.source_file(sha256,file_size,canonical_storage_uri,metadata_json) OUTPUT INSERTED.source_file_id "
                        "VALUES(:sha256,:size,:uri,:metadata)"
                    ), {"sha256": item.sha256, "size": item.size_bytes, "uri": str(item.path), "metadata": json.dumps({"extension": item.path.suffix.lower()}, ensure_ascii=False)}).scalar_one())
                receipt_id = int(connection.execute(text(
                    "INSERT ingestion.source_file_receipt(source_file_id,import_batch_id,original_file_name,received_by,received_channel,is_duplicate_receipt,metadata_json) "
                    "OUTPUT INSERTED.receipt_id VALUES(:source,:batch,:name,:login,'WEB',:duplicate,:metadata)"
                ), {"source": source_id, "batch": batch_id, "name": item.original_name, "login": principal.login_name, "duplicate": duplicate, "metadata": json.dumps({"owner_user_id": principal.user_id, "receipt_storage_uri": str(item.path)}, ensure_ascii=False)}).scalar_one())
                connection.execute(text(
                    "INSERT ingestion.import_batch_file(import_batch_id,receipt_id,file_role,ordinal_no,required_flag,detected_format_code,detected_profile_version,detection_evidence_json) "
                    "VALUES(:batch,:receipt,'DETAIL',:ordinal,1,'HUAHONG_DCP','existing-release',:evidence)"
                ), {"batch": batch_id, "receipt": receipt_id, "ordinal": ordinal, "evidence": json.dumps({"factory": factory_code, "stage": "CP"}, ensure_ascii=False)})
        return batch_id

    def mark_processing(self, batch_id: int, principal: Principal) -> int:
        with self._engine.begin() as connection:
            job_id = int(connection.execute(text(
                "INSERT ingestion.processing_job(source_file_id,job_type,trigger_type,requested_by,status,import_batch_id,reason,metadata_json) "
                "OUTPUT INSERTED.job_id VALUES(NULL,'PARSE','AUTO',:login,'RUNNING',:batch,N'上传后自动调用现有CP清洗程序',:metadata)"
            ), {"login": principal.login_name, "batch": batch_id, "metadata": json.dumps({"requested_by_user_id": principal.user_id})}).scalar_one())
            connection.execute(text("UPDATE ingestion.import_batch SET status='PROCESSING' WHERE import_batch_id=:batch"), {"batch": batch_id})
        return job_id

    def mark_failed(self, batch_id: int, job_id: int, message: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(text("UPDATE ingestion.processing_job SET status='FAILED',finished_at_utc=SYSUTCDATETIME(),error_code='CLEANER_FAILED',error_message=:message WHERE job_id=:job"), {"message": message[-4000:], "job": job_id})
            connection.execute(text("UPDATE ingestion.import_batch SET status='FAILED',completed_at_utc=SYSUTCDATETIME() WHERE import_batch_id=:batch"), {"batch": batch_id})

    def record_cp_result(self, batch_id: int, job_id: int, result: dict) -> None:
        with self._engine.begin() as connection:
            connection.execute(text(
                "INSERT ingestion.processing_result_summary(import_batch_id,job_id,data_name,product_name,lot_id,wafer_count,factory_code,output_uri,test_item_count,unit_count,pass_count,yield_rate,status,data_type,artifact_manifest_json) "
                "VALUES(:batch,:job,:name,:product,:lot,:wafers,:factory,:output,:items,:units,:passes,:yield_rate,'PROCESSED','CP',:manifest)"
            ), {"batch": batch_id, "job": job_id, "name": result["data_name"], "product": result.get("product_name"), "lot": result.get("lot_id"), "wafers": result.get("wafer_count"), "factory": result["factory_code"], "output": result["output_uri"], "items": result.get("test_item_count"), "units": result.get("unit_count"), "passes": result.get("pass_count"), "yield_rate": result.get("yield_rate"), "manifest": json.dumps(result.get("artifacts", []), ensure_ascii=False)})
            connection.execute(text("UPDATE ingestion.processing_job SET status='SUCCESS',finished_at_utc=SYSUTCDATETIME() WHERE job_id=:job"), {"job": job_id})
            connection.execute(text("UPDATE ingestion.import_batch SET status='PROCESSED',completed_at_utc=SYSUTCDATETIME() WHERE import_batch_id=:batch"), {"batch": batch_id})

    @staticmethod
    def _scope() -> str:
        return "(b.owner_user_id=:user_id OR EXISTS(SELECT 1 FROM iam.data_scope_grant g WHERE (g.user_id=:user_id OR g.role_id IN(SELECT role_id FROM iam.user_role WHERE user_id=:user_id)) AND (g.expires_at_utc IS NULL OR g.expires_at_utc>SYSUTCDATETIME()) AND g.scope_type='GLOBAL' AND g.permission_mode IN('READ','WRITE','GOVERN','EXPORT')))"

    def list_uploads(self, principal: Principal) -> tuple[ProductionUploadRow, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT b.import_batch_id,ibf.ordinal_no,r.original_file_name,s.file_size,b.factory_code,b.started_at_utc,b.completed_at_utc,u.login_name,u.display_name,b.status "
                "FROM ingestion.import_batch b JOIN iam.app_user u ON u.user_id=b.owner_user_id JOIN ingestion.import_batch_file ibf ON ibf.import_batch_id=b.import_batch_id "
                "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                "WHERE b.business_domain='PRODUCTION' AND b.test_stage='CP' AND " + self._scope() + " ORDER BY b.import_batch_id DESC,ibf.ordinal_no"
            ), {"user_id": principal.user_id}).mappings().all()
        return tuple(ProductionUploadRow(int(r["import_batch_id"]), int(r["ordinal_no"]), str(r["original_file_name"]), str(r["original_file_name"]).rsplit('.',1)[-1].lower() if '.' in str(r["original_file_name"]) else '', int(r["file_size"] or 0), str(r["factory_code"] or ''), _iso(r["started_at_utc"]) or '', _iso(r["completed_at_utc"]), str(r["login_name"]), str(r["display_name"]), str(r["status"])) for r in rows)

    def list_results(self, principal: Principal) -> tuple[ProductionResultRow, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT s.* FROM ingestion.processing_result_summary s JOIN ingestion.import_batch b ON b.import_batch_id=s.import_batch_id "
                "WHERE b.business_domain='PRODUCTION' AND b.test_stage='CP' AND " + self._scope() + " ORDER BY s.result_summary_id DESC"
            ), {"user_id": principal.user_id}).mappings().all()
        return tuple(ProductionResultRow(int(r["result_summary_id"]), int(r["import_batch_id"]), str(r["data_name"]), r["product_name"], r["lot_id"], int(r["wafer_count"]) if r["wafer_count"] is not None else None, str(r["factory_code"] or ''), int(r["test_item_count"]) if r["test_item_count"] is not None else None, int(r["unit_count"]) if r["unit_count"] is not None else None, int(r["pass_count"]) if r["pass_count"] is not None else None, float(r["yield_rate"]) if r["yield_rate"] is not None else None, str(r["status"]), str(r["data_type"]), _iso(r["created_at_utc"]) or '') for r in rows)
