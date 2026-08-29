from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> None:
    from app.domain.auth import Principal
    from app.domain.input_requests import LotResolutionItem, ResolveLotInputRequests
    from app.infrastructure.sql_input_request_service import (
        SqlProcessingInputRequestService,
    )

    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    token = uuid4().hex
    ids: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            identity = (
                connection.execute(
                    text(
                        "SELECT DB_NAME() AS database_name,"
                        "(SELECT version_num FROM alembic_version) AS revision"
                    )
                )
                .mappings()
                .one()
            )
            if identity != {
                "database_name": "TMS_G0_DEV",
                "revision": "sql2014_0018",
            }:
                raise RuntimeError(f"unexpected development database: {identity}")
            owner = (
                connection.execute(
                    text(
                        "SELECT TOP (1) user_id,login_name,display_name "
                        "FROM iam.app_user WHERE status='ACTIVE' ORDER BY user_id"
                    )
                )
                .mappings()
                .one()
            )
            release_id = int(
                connection.execute(
                    text(
                        "SELECT TOP (1) cr.cleaner_release_id "
                        "FROM ingestion.cleaner_release cr "
                        "JOIN ingestion.format_profile fp "
                        "ON fp.format_profile_id=cr.format_profile_id "
                        "WHERE cr.status='RELEASED' AND fp.status='RELEASED' "
                        "AND fp.test_stage='FT' AND fp.factory_code='RIYUEXIN' "
                        "ORDER BY cr.cleaner_release_id DESC"
                    )
                ).scalar_one()
            )
            source = (
                connection.execute(
                    text(
                        "SELECT TOP (1) source_file_id,canonical_storage_uri "
                        "FROM ingestion.source_file WHERE sha256 IS NOT NULL "
                        "ORDER BY source_file_id"
                    )
                )
                .mappings()
                .one()
            )
            ids["batch"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch(source_channel,uploaded_by,status,"
                        "owner_user_id,business_domain,test_stage,factory_code,batch_name,metadata_json) "
                        "OUTPUT INSERTED.import_batch_id VALUES('SYSTEM',:login,'NEEDS_INPUT',"
                        ":owner,'ENGINEERING','FT','riyuexin',:name,'{}')"
                    ),
                    {
                        "login": owner["login_name"],
                        "owner": int(owner["user_id"]),
                        "name": f"Lot resume SQL E2E {token}",
                    },
                ).scalar_one()
            )
            ids["receipt"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.source_file_receipt(source_file_id,import_batch_id,"
                        "original_file_name,received_by,received_channel,is_duplicate_receipt,metadata_json) "
                        "OUTPUT INSERTED.receipt_id VALUES(:source,:batch,:name,:login,'SYSTEM',1,'{}')"
                    ),
                    {
                        "source": int(source["source_file_id"]),
                        "batch": ids["batch"],
                        "name": Path(str(source["canonical_storage_uri"])).name
                        or "missing-lot.xlsx",
                        "login": owner["login_name"],
                    },
                ).scalar_one()
            )
            ids["batch_file"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch_file(import_batch_id,receipt_id,file_role,"
                        "ordinal_no,required_flag,detection_evidence_json) "
                        "OUTPUT INSERTED.import_batch_file_id VALUES("
                        ":batch,:receipt,'DETAIL',1,1,'{}')"
                    ),
                    {"batch": ids["batch"], "receipt": ids["receipt"]},
                ).scalar_one()
            )
            ids["blocked_job"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job(import_batch_id,cleaner_release_id,job_type,"
                        "trigger_type,requested_by,requested_by_user_id,reason,status,idempotency_key,"
                        "started_at_utc,finished_at_utc,attempt_count,max_attempts,finalize_protocol) "
                        "OUTPUT INSERTED.job_id VALUES(:batch,:release,'INITIAL_IMPORT','SYSTEM',"
                        ":login,:owner,'Lot missing E2E','NEEDS_INPUT',:key,SYSUTCDATETIME(),"
                        "SYSUTCDATETIME(),1,3,'ATOMIC_V1')"
                    ),
                    {
                        "batch": ids["batch"],
                        "release": release_id,
                        "login": owner["login_name"],
                        "owner": int(owner["user_id"]),
                        "key": f"lot-resume-blocked:{token}",
                    },
                ).scalar_one()
            )
            ids["input_request"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_input_request(job_id,import_batch_id,"
                        "receipt_id,field_code,status,prompt,evidence_json) "
                        "OUTPUT INSERTED.input_request_id VALUES("
                        ":job,:batch,:receipt,'LOT_ID','OPEN',N'请确认批次号','{}')"
                    ),
                    {
                        "job": ids["blocked_job"],
                        "batch": ids["batch"],
                        "receipt": ids["receipt"],
                    },
                ).scalar_one()
            )

        principal = Principal(
            user_id=int(owner["user_id"]),
            login_name=str(owner["login_name"]),
            display_name=str(owner["display_name"]),
            roles=(),
            permissions=frozenset(),
        )
        request = ResolveLotInputRequests(
            resolutions=[
                LotResolutionItem(
                    input_request_id=ids["input_request"], lot_id="FA54-9744"
                )
            ],
            reason="真实 SQL Lot 补录恢复协议验证",
        )
        service = SqlProcessingInputRequestService(engine)
        result = service.resolve(
            principal,
            "ENGINEERING",
            "FT",
            ids["batch"],
            request,
        )
        ids["resume_job"] = result.job_id
        repeated = service.resolve(
            principal,
            "ENGINEERING",
            "FT",
            ids["batch"],
            request,
        )
        if repeated.job_id != result.job_id:
            raise RuntimeError("Lot resolution idempotency returned a different resume Job")
        with engine.connect() as connection:
            state = (
                connection.execute(
                    text(
                        "SELECT b.status AS batch_status,pir.status AS request_status,"
                        "e.value_text,j.status AS resume_status,j.finalize_protocol,j.parent_job_id "
                        "FROM ingestion.import_batch b "
                        "JOIN ingestion.processing_input_request pir "
                        "ON pir.import_batch_id=b.import_batch_id "
                        "JOIN ingestion.field_enrichment e "
                        "ON e.enrichment_id=pir.resolved_enrichment_id "
                        "JOIN ingestion.processing_job j ON j.job_id=:resume "
                        "WHERE b.import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"], "resume": ids["resume_job"]},
                )
                .mappings()
                .one()
            )
        expected = {
            "batch_status": "QUEUED",
            "request_status": "RESOLVED",
            "value_text": "FA54-9744",
            "resume_status": "QUEUED",
            "finalize_protocol": "ATOMIC_V1",
            "parent_job_id": ids["blocked_job"],
        }
        if dict(state) != expected:
            raise RuntimeError(f"unexpected Lot resume state: {dict(state)}")
        print(
            "lot_input_resume_sql=PASS protocol=ATOMIC_V1 "
            "idempotent_same_job=PASS"
        )
    finally:
        with engine.begin() as connection:
            if "blocked_job" in ids:
                connection.execute(
                    text("DELETE governance.audit_log WHERE correlation_id=:correlation"),
                    {"correlation": f"job:{ids['blocked_job']}"},
                )
            if "batch" in ids:
                connection.execute(
                    text(
                        "DELETE ingestion.processing_input_request "
                        "WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.field_enrichment WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.processing_job WHERE import_batch_id=:batch "
                        "AND parent_job_id IS NOT NULL"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.processing_job WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.import_batch_file WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.source_file_receipt WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.import_batch WHERE import_batch_id=:batch"
                    ),
                    {"batch": ids["batch"]},
                )
        engine.dispose()
        print("lot_input_resume_sql_cleanup=PASS")


if __name__ == "__main__":
    main()
