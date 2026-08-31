from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAULT_POINTS = (
    "after_previous_current_superseded",
    "after_new_version_published",
    "after_run_published",
    "after_result_persisted",
    "after_batch_completed",
    "after_job_completed",
    "after_intent_finalized",
)


class InjectedFinalizeFault(RuntimeError):
    pass


def _setup_stage(engine, token: str) -> dict[str, Any]:
    lease_token = str(uuid4())
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
        if identity["database_name"] != "TMS_G0_DEV":
            raise RuntimeError(
                f"destructive E2E is restricted to TMS_G0_DEV: {identity}"
            )
        if identity["revision"] != "sql2014_0023":
            raise RuntimeError(f"unexpected schema revision: {identity['revision']}")
        owner = (
            connection.execute(
                text(
                    "SELECT TOP (1) user_id,login_name FROM iam.app_user "
                    "WHERE status='ACTIVE' ORDER BY user_id"
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
                    "AND fp.test_stage='CP' ORDER BY cr.cleaner_release_id DESC"
                )
            ).scalar_one()
        )
        source_file_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.source_file(sha256,file_size,canonical_storage_uri,"
                    "metadata_json) OUTPUT INSERTED.source_file_id "
                    "VALUES(:sha,1,:uri,:metadata)"
                ),
                {
                    "sha": token * 2,
                    "uri": f"verification://atomic/{token}",
                    "metadata": json.dumps({"verification": "atomic-rollback"}),
                },
            ).scalar_one()
        )
        parser_profile_id = int(
            connection.execute(
                text(
                    "SELECT TOP (1) parser_profile_id FROM ingestion.parser_profile "
                    "WHERE active=1 ORDER BY parser_profile_id"
                )
            ).scalar_one()
        )
        batch_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.import_batch(source_channel,uploaded_by,status,"
                    "owner_user_id,business_domain,test_stage,factory_code,batch_name,metadata_json) "
                    "OUTPUT INSERTED.import_batch_id VALUES('SYSTEM',:login,'PROCESSING',"
                    ":owner,'ENGINEERING','CP','HUAHONG',:name,:metadata)"
                ),
                {
                    "login": owner["login_name"],
                    "owner": int(owner["user_id"]),
                    "name": f"Atomic rollback E2E {token}",
                    "metadata": json.dumps({"verification": "atomic-rollback"}),
                },
            ).scalar_one()
        )
        receipt_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.source_file_receipt(source_file_id,import_batch_id,"
                    "original_file_name,received_by,received_channel,is_duplicate_receipt,metadata_json) "
                    "OUTPUT INSERTED.receipt_id VALUES(:source,:batch,:name,:login,'SYSTEM',1,'{}')"
                ),
                {
                    "source": source_file_id,
                    "batch": batch_id,
                    "name": f"atomic-{token}.bin",
                    "login": owner["login_name"],
                },
            ).scalar_one()
        )
        batch_file_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.import_batch_file(import_batch_id,receipt_id,file_role,"
                    "ordinal_no,required_flag,detection_evidence_json) "
                    "OUTPUT INSERTED.import_batch_file_id "
                    "VALUES(:batch,:receipt,'DETAIL',1,1,'{}')"
                ),
                {"batch": batch_id, "receipt": receipt_id},
            ).scalar_one()
        )
        job_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.processing_job(import_batch_id,cleaner_release_id,job_type,"
                    "trigger_type,requested_by,requested_by_user_id,reason,status,idempotency_key,"
                    "started_at_utc,lease_token,lease_owner,lease_expires_at_utc,heartbeat_at_utc,"
                    "attempt_count,max_attempts,finalize_protocol) OUTPUT INSERTED.job_id VALUES("
                    ":batch,:release,'INITIAL_IMPORT','SYSTEM',:login,:owner,"
                    "'Real SQL atomic rollback verification','RUNNING',:key,SYSUTCDATETIME(),"
                    "CONVERT(uniqueidentifier,:lease),'atomic-sql-e2e',DATEADD(minute,10,SYSUTCDATETIME()),"
                    "SYSUTCDATETIME(),1,3,'ATOMIC_V1')"
                ),
                {
                    "batch": batch_id,
                    "release": release_id,
                    "login": owner["login_name"],
                    "owner": int(owner["user_id"]),
                    "key": f"atomic-sql-e2e:{token}",
                    "lease": lease_token,
                },
            ).scalar_one()
        )
        summary = {
            "data_name": f"Atomic rollback {token}",
            "product_name": None,
            "lot_id": f"LOT-{token[:8]}",
            "wafer_count": 1,
            "factory_code": "HUAHONG",
            "output_uri": f"verification://atomic/{token}",
            "test_item_count": 1,
            "unit_count": 1,
            "pass_count": 1,
            "yield_rate": 1.0,
            "data_type": "CP",
            "artifacts": [],
        }
        processing_run_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
                    "parser_version,canonical_model_version,status,is_current,row_count_input,"
                    "unit_count_output,measurement_count_output,started_at_utc,finished_at_utc,"
                    "metadata_json) OUTPUT INSERTED.processing_run_id VALUES("
                    ":job,:source,:parser,'atomic-e2e','1.0','READY',0,1,1,1,"
                    "SYSUTCDATETIME(),SYSUTCDATETIME(),:metadata)"
                ),
                {
                    "job": job_id,
                    "source": source_file_id,
                    "parser": parser_profile_id,
                    "metadata": json.dumps(
                        {"atomic_finalize_summary": summary}, ensure_ascii=False
                    ),
                },
            ).scalar_one()
        )
        dataset_id = int(
            connection.execute(
                text(
                    "INSERT dataset.dataset(dataset_code,dataset_name,dataset_type,test_stage,"
                    "owner_user_id) OUTPUT INSERTED.dataset_id VALUES("
                    ":code,:name,'CP_DETAIL','CP',:owner)"
                ),
                {
                    "code": f"ATOMIC-E2E-{token}",
                    "name": f"Atomic rollback E2E {token}",
                    "owner": int(owner["user_id"]),
                },
            ).scalar_one()
        )
        previous_version_id = int(
            connection.execute(
                text(
                    "INSERT dataset.dataset_version(dataset_id,version_no,input_batch_id,"
                    "canonical_model_version,status,is_current,row_count,unit_count,measurement_count,"
                    "published_by,published_at_utc) OUTPUT INSERTED.dataset_version_id VALUES("
                    ":dataset,1,:batch,'1.0','PUBLISHED',1,1,1,1,:owner,SYSUTCDATETIME())"
                ),
                {
                    "dataset": dataset_id,
                    "batch": batch_id,
                    "owner": int(owner["user_id"]),
                },
            ).scalar_one()
        )
        previous_processing_run_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
                    "parser_version,canonical_model_version,status,is_current,row_count_input,"
                    "unit_count_output,measurement_count_output,started_at_utc,finished_at_utc,"
                    "metadata_json) OUTPUT INSERTED.processing_run_id VALUES("
                    ":job,:source,:parser,'atomic-e2e-old','1.0','PUBLISHED',1,1,1,1,"
                    "SYSUTCDATETIME(),SYSUTCDATETIME(),'{}')"
                ),
                {
                    "job": job_id,
                    "source": source_file_id,
                    "parser": parser_profile_id,
                },
            ).scalar_one()
        )
        connection.execute(
            text(
                "INSERT dataset.dataset_version_run(dataset_version_id,processing_run_id,"
                "run_role,ordinal_no) VALUES(:version,:run,'PRIMARY',1)"
            ),
            {"version": previous_version_id, "run": previous_processing_run_id},
        )
        dataset_version_id = int(
            connection.execute(
                text(
                    "INSERT dataset.dataset_version(dataset_id,version_no,input_batch_id,"
                    "canonical_model_version,status,is_current,row_count,unit_count,measurement_count) "
                    "OUTPUT INSERTED.dataset_version_id VALUES("
                    ":dataset,2,:batch,'1.0','DRAFT',0,1,1,1)"
                ),
                {"dataset": dataset_id, "batch": batch_id},
            ).scalar_one()
        )
        connection.execute(
            text(
                "INSERT dataset.dataset_version_run(dataset_version_id,processing_run_id,"
                "run_role,ordinal_no) VALUES(:version,:run,'PRIMARY',1)"
            ),
            {"version": dataset_version_id, "run": processing_run_id},
        )
        connection.execute(
            text(
                "INSERT ingestion.processing_run_input_file(processing_run_id,"
                "import_batch_file_id,lineage_basis) VALUES(:run,:batch_file,'WRITER_VERIFIED')"
            ),
            {"run": processing_run_id, "batch_file": batch_file_id},
        )
        connection.execute(
            text(
                "INSERT ingestion.initial_import_finalize_intent(job_id,import_batch_id,"
                "processing_run_id,dataset_version_id,input_manifest_sha256,input_manifest_json,"
                "status,staged_attempt_count,staged_at_utc) VALUES("
                ":job,:batch,:run,:version,:sha,:manifest,'STAGED',1,SYSUTCDATETIME())"
            ),
            {
                "job": job_id,
                "batch": batch_id,
                "run": processing_run_id,
                "version": dataset_version_id,
                "sha": "a" * 64,
                "manifest": json.dumps({"verification": token}),
            },
        )
    return {
        "batch_id": batch_id,
        "source_file_id": source_file_id,
        "receipt_id": receipt_id,
        "batch_file_id": batch_file_id,
        "job_id": job_id,
        "processing_run_id": processing_run_id,
        "previous_processing_run_id": previous_processing_run_id,
        "dataset_id": dataset_id,
        "previous_version_id": previous_version_id,
        "dataset_version_id": dataset_version_id,
        "lease_token": lease_token,
        "summary": summary,
    }


def _assert_rolled_back(engine, ids: dict[str, Any]) -> None:
    with engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    "SELECT j.status AS job_status,b.status AS batch_status,"
                    "i.status AS intent_status,pr.status AS run_status,"
                    "pr.is_current AS run_current,"
                    "old_pr.status AS old_run_status,old_pr.is_current AS old_run_current,"
                    "old.status AS old_status,old.is_current AS old_current,"
                    "new.status AS new_status,new.is_current AS new_current,"
                    "new.published_at_utc AS new_published_at,"
                    "(SELECT COUNT(*) FROM ingestion.processing_result_summary "
                    "WHERE job_id=j.job_id) AS result_count "
                    "FROM ingestion.processing_job j "
                    "JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id "
                    "JOIN ingestion.initial_import_finalize_intent i ON i.job_id=j.job_id "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=i.processing_run_id "
                    "JOIN ingestion.processing_run old_pr ON old_pr.processing_run_id=:old_run "
                    "JOIN dataset.dataset_version old ON old.dataset_version_id=:old "
                    "JOIN dataset.dataset_version new ON new.dataset_version_id=i.dataset_version_id "
                    "WHERE j.job_id=:job"
                ),
                {
                    "job": ids["job_id"],
                    "old": ids["previous_version_id"],
                    "old_run": ids["previous_processing_run_id"],
                },
            )
            .mappings()
            .one()
        )
    expected = {
        "job_status": "RUNNING",
        "batch_status": "PROCESSING",
        "intent_status": "STAGED",
        "run_status": "READY",
        "run_current": False,
        "old_run_status": "PUBLISHED",
        "old_run_current": True,
        "old_status": "PUBLISHED",
        "old_current": True,
        "new_status": "DRAFT",
        "new_current": False,
        "new_published_at": None,
        "result_count": 0,
    }
    if dict(state) != expected:
        raise RuntimeError(f"atomic rollback state mismatch: {dict(state)}")


def _assert_finalized(engine, ids: dict[str, Any]) -> None:
    with engine.connect() as connection:
        state = (
            connection.execute(
                text(
                    "SELECT j.status AS job_status,b.status AS batch_status,"
                    "i.status AS intent_status,pr.status AS run_status,"
                    "pr.is_current AS run_current,"
                    "pr.supersedes_processing_run_id,"
                    "old_pr.status AS old_run_status,old_pr.is_current AS old_run_current,"
                    "old.status AS old_status,old.is_current AS old_current,"
                    "new.status AS new_status,new.is_current AS new_current,"
                    "(SELECT COUNT(*) FROM ingestion.processing_result_summary "
                    "WHERE job_id=j.job_id AND status='PROCESSED') AS result_count "
                    "FROM ingestion.processing_job j "
                    "JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id "
                    "JOIN ingestion.initial_import_finalize_intent i ON i.job_id=j.job_id "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=i.processing_run_id "
                    "JOIN ingestion.processing_run old_pr ON old_pr.processing_run_id=:old_run "
                    "JOIN dataset.dataset_version old ON old.dataset_version_id=:old "
                    "JOIN dataset.dataset_version new ON new.dataset_version_id=i.dataset_version_id "
                    "WHERE j.job_id=:job"
                ),
                {
                    "job": ids["job_id"],
                    "old": ids["previous_version_id"],
                    "old_run": ids["previous_processing_run_id"],
                },
            )
            .mappings()
            .one()
        )
    expected = {
        "job_status": "SUCCESS",
        "batch_status": "PROCESSED",
        "intent_status": "FINALIZED",
        "run_status": "PUBLISHED",
        "run_current": True,
        "supersedes_processing_run_id": ids["previous_processing_run_id"],
        "old_run_status": "SUPERSEDED",
        "old_run_current": False,
        "old_status": "SUPERSEDED",
        "old_current": False,
        "new_status": "PUBLISHED",
        "new_current": True,
        "result_count": 1,
    }
    if dict(state) != expected:
        raise RuntimeError(f"atomic recovery state mismatch: {dict(state)}")


def _cleanup(engine, ids: dict[str, Any]) -> None:
    if not ids:
        return
    with engine.begin() as connection:
        connection.execute(
            text("DELETE governance.audit_log WHERE correlation_id=:correlation"),
            {"correlation": f"job:{ids['job_id']}"},
        )
        connection.execute(
            text("DELETE ingestion.processing_result_summary WHERE job_id=:job"),
            {"job": ids["job_id"]},
        )
        connection.execute(
            text("DELETE ingestion.initial_import_finalize_intent WHERE job_id=:job"),
            {"job": ids["job_id"]},
        )
        connection.execute(
            text(
                "DELETE dvr FROM dataset.dataset_version_run dvr "
                "JOIN dataset.dataset_version dv "
                "ON dv.dataset_version_id=dvr.dataset_version_id "
                "WHERE dv.dataset_id=:dataset"
            ),
            {"dataset": ids["dataset_id"]},
        )
        connection.execute(
            text(
                "DELETE ingestion.processing_run_input_file "
                "WHERE processing_run_id=:run"
            ),
            {"run": ids["processing_run_id"]},
        )
        connection.execute(
            text("DELETE ingestion.processing_artifact WHERE job_id=:job"),
            {"job": ids["job_id"]},
        )
        connection.execute(
            text("DELETE ingestion.processing_run WHERE job_id=:job"),
            {"job": ids["job_id"]},
        )
        connection.execute(
            text("DELETE dataset.dataset_version WHERE dataset_id=:dataset"),
            {"dataset": ids["dataset_id"]},
        )
        connection.execute(
            text("DELETE dataset.dataset WHERE dataset_id=:dataset"),
            {"dataset": ids["dataset_id"]},
        )
        connection.execute(
            text("DELETE ingestion.processing_job WHERE job_id=:job"),
            {"job": ids["job_id"]},
        )
        connection.execute(
            text(
                "DELETE ingestion.import_batch_file WHERE import_batch_file_id=:batch_file"
            ),
            {"batch_file": ids["batch_file_id"]},
        )
        connection.execute(
            text("DELETE ingestion.source_file_receipt WHERE receipt_id=:receipt"),
            {"receipt": ids["receipt_id"]},
        )
        connection.execute(
            text("DELETE ingestion.import_batch WHERE import_batch_id=:batch"),
            {"batch": ids["batch_id"]},
        )
        connection.execute(
            text("DELETE ingestion.source_file WHERE source_file_id=:source"),
            {"source": ids["source_file_id"]},
        )


def main() -> None:
    from app.infrastructure.sql_job_service import SqlJobService

    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    completed: list[str] = []
    try:
        for fault_point in FAULT_POINTS:
            ids: dict[str, Any] = {}
            try:
                ids = _setup_stage(engine, uuid4().hex)

                def inject(point: str, expected_fault: str = fault_point) -> None:
                    if point == expected_fault:
                        raise InjectedFinalizeFault(point)

                service = SqlJobService(engine, fault_injector=inject)
                try:
                    service.finalize_initial_import(
                        job_id=ids["job_id"],
                        lease_token=ids["lease_token"],
                        processing_run_id=ids["processing_run_id"],
                        dataset_version_id=ids["dataset_version_id"],
                        summary=ids["summary"],
                    )
                except InjectedFinalizeFault as exc:
                    if str(exc) != fault_point:
                        raise
                else:
                    raise RuntimeError(f"fault point did not fire: {fault_point}")
                _assert_rolled_back(engine, ids)
                completed.append(fault_point)
            finally:
                _cleanup(engine, ids)

        recovery_ids: dict[str, Any] = {}
        try:
            recovery_ids = _setup_stage(engine, uuid4().hex)
            recovered_lease = str(uuid4())
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ingestion.processing_job SET "
                        "lease_token=CONVERT(uniqueidentifier,:lease),"
                        "lease_owner='atomic-recovery-e2e',"
                        "lease_expires_at_utc=DATEADD(minute,10,SYSUTCDATETIME()),"
                        "heartbeat_at_utc=SYSUTCDATETIME(),attempt_count=2 "
                        "WHERE job_id=:job AND status='RUNNING'"
                    ),
                    {"job": recovery_ids["job_id"], "lease": recovered_lease},
                )
            recovered = SqlJobService(engine).finalize_staged_initial_import_if_present(
                job_id=recovery_ids["job_id"],
                lease_token=recovered_lease,
            )
            if recovered is None or recovered.status.value != "SUCCESS":
                raise RuntimeError(f"staged recovery did not finalize: {recovered}")
            _assert_finalized(engine, recovery_ids)
        finally:
            _cleanup(engine, recovery_ids)
        print(f"atomic_finalize_sql_rollback=PASS fault_points={len(completed)}")
        print("atomic_finalize_staged_recovery=PASS cleaner_rerun=0")
        print("atomic_finalize_sql_cleanup=PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
