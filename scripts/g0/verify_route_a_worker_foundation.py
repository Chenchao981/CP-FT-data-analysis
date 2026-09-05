from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.auth import Principal
from app.domain.jobs import CreateJobRequest, JobStatus, JobType
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.workers.route_a_worker import DatabaseJobWorker, RouteAInitialImportHandler


def main() -> None:
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    token = uuid4().hex
    work_parent = (ROOT / "artifacts" / "route_a_worker_verification").resolve()
    work_root = (work_parent / token).resolve()
    if work_parent not in work_root.parents:
        raise RuntimeError(
            "verification work path escaped the approved artifacts directory"
        )

    engine = create_engine(database_url)
    queue = SqlJobService(engine)
    stage_data = SqlStageDataService(engine)
    registry = SqlCleanerRegistry(engine)
    batch_id: int | None = None
    receipt_id: int | None = None
    import_job_id: int | None = None
    lease_job_id: int | None = None
    previous_current_run_id: int | None = None
    previous_current_version_ids: tuple[int, ...] = ()
    try:
        with engine.begin() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == "sql2014_0028", revision
            route_b_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s "
                    "ON s.schema_id=t.schema_id WHERE s.name='analysis' "
                    "AND t.name IN('run','unit','test_item','measurement')"
                )
            ).scalar_one()
            assert route_b_count == 0, route_b_count
            assert (
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM sys.tables WHERE object_id=OBJECT_ID('analysis.saved_analysis')"
                    )
                ).scalar_one()
                == 1
            )
            owner_id = connection.execute(
                text(
                    "SELECT TOP (1) user_id FROM iam.app_user WHERE status='ACTIVE' ORDER BY user_id"
                )
            ).scalar_one()
            source = (
                connection.execute(
                    text(
                        "SELECT TOP (1) source_file_id,canonical_storage_uri FROM ingestion.source_file "
                        "WHERE canonical_storage_uri LIKE '%.zip' ORDER BY source_file_id"
                    )
                )
                .mappings()
                .one()
            )
            if not Path(source["canonical_storage_uri"]).is_file():
                raise FileNotFoundError(source["canonical_storage_uri"])
            previous_current_run_id = connection.execute(
                text(
                    "SELECT processing_run_id FROM ingestion.processing_run "
                    "WHERE source_file_id=:source AND status='PUBLISHED' "
                    "AND is_current=1"
                ),
                {"source": source["source_file_id"]},
            ).scalar_one_or_none()
            if previous_current_run_id is not None:
                previous_current_version_ids = tuple(
                    int(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT dv.dataset_version_id "
                            "FROM dataset.dataset_version dv "
                            "JOIN dataset.dataset_version_run dvr "
                            "ON dvr.dataset_version_id=dv.dataset_version_id "
                            "WHERE dvr.processing_run_id=:run "
                            "AND dv.status='PUBLISHED' AND dv.is_current=1"
                        ),
                        {"run": previous_current_run_id},
                    )
                )
            batch_id = connection.execute(
                text(
                    "INSERT ingestion.import_batch(source_channel,uploaded_by,status,owner_user_id,"
                    "business_domain,test_stage,factory_code,batch_name,metadata_json) "
                    "OUTPUT INSERTED.import_batch_id VALUES('SYSTEM','route-a-verification','RECEIVED',"
                    ":owner,'ENGINEERING','CP','huahong',:name,'{}')"
                ),
                {"owner": owner_id, "name": f"Route A verification {token}"},
            ).scalar_one()
            receipt_id = connection.execute(
                text(
                    "INSERT ingestion.source_file_receipt(source_file_id,import_batch_id,"
                    "original_file_name,received_by,received_channel,is_duplicate_receipt,metadata_json) "
                    "OUTPUT INSERTED.receipt_id VALUES(:source,:batch,:name,'route-a-verification',"
                    "'SYSTEM',1,:metadata)"
                ),
                {
                    "source": source["source_file_id"],
                    "batch": batch_id,
                    "name": Path(source["canonical_storage_uri"]).name,
                    "metadata": json.dumps(
                        {"receipt_storage_uri": source["canonical_storage_uri"]},
                        ensure_ascii=False,
                    ),
                },
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT ingestion.import_batch_file(import_batch_id,receipt_id,file_role,"
                    "ordinal_no,required_flag,detected_format_code,detected_profile_version,"
                    "detection_evidence_json) VALUES(:batch,:receipt,'DETAIL',1,1,"
                    "'HUAHONG_DCP_EXISTING','route-a-v1','{}')"
                ),
                {"batch": batch_id, "receipt": receipt_id},
            )

        cp_release = registry.latest_released("CP", "HUAHONG")
        import_job = queue.create_initial_import_for_batch(
            CreateJobRequest(
                import_batch_id=batch_id,
                cleaner_release_id=cp_release.cleaner_release_id,
                job_type=JobType.INITIAL_IMPORT,
                trigger_type="SYSTEM",
                requested_by="route-a-verification",
                requested_by_user_id=owner_id,
                idempotency_key=f"route-a-import:{token}",
            ),
            Principal(
                user_id=int(owner_id),
                login_name="route-a-verification",
                display_name="Route A Verification",
                roles=(),
                permissions=frozenset(),
            ),
            allowed_batch_statuses=("RECEIVED",),
        )
        import_job_id = import_job.job_id
        worker = DatabaseJobWorker(
            queue,
            {
                JobType.INITIAL_IMPORT: RouteAInitialImportHandler(
                    registry,
                    stage_data,
                    CpCsvTripletWriter(engine),
                    FtXlsxScatterWriter(engine),
                    work_root=work_root,
                    finalizer=queue,
                )
            },
            worker_id=f"route-a-verification-{token[:8]}",
            lease_for=timedelta(minutes=2),
            heartbeat_every=timedelta(seconds=10),
        )
        finished = worker.run_once()
        assert finished is not None and finished.status == JobStatus.SUCCESS, finished

        with engine.connect() as connection:
            summary = (
                connection.execute(
                    text(
                        "SELECT unit_count,test_item_count,status,dataset_id,dataset_version_no "
                        "FROM ingestion.processing_result_summary "
                        "WHERE job_id=:job"
                    ),
                    {"job": import_job_id},
                )
                .mappings()
                .one()
            )
            assert summary["status"] == "PROCESSED"
            assert int(summary["unit_count"] or 0) > 0
            assert int(summary["test_item_count"] or 0) > 0
            assert int(summary["dataset_id"] or 0) > 0
            assert int(summary["dataset_version_no"] or 0) > 0
            artifact_roles = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT artifact_role FROM ingestion.processing_artifact WHERE job_id=:job"
                    ),
                    {"job": import_job_id},
                )
            }
            assert artifact_roles == {"cleaned", "yield", "spec"}, artifact_roles
            atomic_state = (
                connection.execute(
                    text(
                        "SELECT i.status AS intent_status,dv.status AS version_status,"
                        "dv.is_current,pr.status AS run_status,"
                        "pr.is_current AS run_is_current,j.status AS job_status,"
                        "b.status AS batch_status,"
                        "(SELECT COUNT(*) FROM ingestion.import_batch_file "
                        "WHERE import_batch_id=b.import_batch_id) AS batch_file_count,"
                        "(SELECT COUNT(*) FROM ingestion.processing_run_input_file ri "
                        "WHERE ri.processing_run_id=pr.processing_run_id) AS lineage_count,"
                        "(SELECT COUNT(*) FROM ingestion.processing_run_input_file ri "
                        "WHERE ri.processing_run_id=pr.processing_run_id "
                        "AND ri.lineage_basis<>'WRITER_VERIFIED') AS unverified_count "
                        "FROM ingestion.initial_import_finalize_intent i "
                        "JOIN ingestion.processing_job j ON j.job_id=i.job_id "
                        "JOIN ingestion.import_batch b ON b.import_batch_id=i.import_batch_id "
                        "JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=i.processing_run_id "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_version_id=i.dataset_version_id "
                        "WHERE i.job_id=:job"
                    ),
                    {"job": import_job_id},
                )
                .mappings()
                .one()
            )
            assert dict(atomic_state) == {
                "intent_status": "FINALIZED",
                "version_status": "PUBLISHED",
                "is_current": True,
                "run_status": "PUBLISHED",
                "run_is_current": True,
                "job_status": "SUCCESS",
                "batch_status": "PROCESSED",
                "batch_file_count": 1,
                "lineage_count": 1,
                "unverified_count": 0,
            }, atomic_state

        lease_request = CreateJobRequest(
            source_file_id=int(source["source_file_id"]),
            job_type=JobType.OTHER,
            trigger_type="SYSTEM",
            requested_by="route-a-verification",
            requested_by_user_id=owner_id,
            idempotency_key=f"route-a-lease:{token}",
            max_attempts=3,
        )
        lease_job = queue.create(lease_request)
        lease_job_id = lease_job.job_id
        assert queue.create(lease_request).job_id == lease_job_id
        assert worker.run_once() is None
        assert queue.get(lease_job_id).status == JobStatus.QUEUED
        first_claim = queue.claim_next(
            "worker-a", timedelta(minutes=1), (JobType.OTHER,)
        )
        assert first_claim is not None and first_claim.job_id == lease_job_id
        assert (
            queue.claim_next("worker-b", timedelta(minutes=1), (JobType.OTHER,)) is None
        )
        queue.heartbeat(
            lease_job_id, first_claim.lease_token or "", timedelta(minutes=1)
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET lease_expires_at_utc=DATEADD(second,-1,SYSUTCDATETIME()) "
                    "WHERE job_id=:job"
                ),
                {"job": lease_job_id},
            )
        recovered = queue.claim_next("worker-b", timedelta(minutes=1), (JobType.OTHER,))
        assert recovered is not None and recovered.job_id == lease_job_id
        assert recovered.attempt_count == 2
        queue.finish_leased(
            lease_job_id,
            recovered.lease_token or "",
            JobStatus.SUCCESS,
        )
        print("route_a_schema=PASS")
        print("route_a_cleaner_registry=PASS")
        print("route_a_initial_worker=PASS")
        print("route_a_atomic_finalize=PASS")
        print("route_a_worker_lease_recovery=PASS")
    finally:
        with engine.begin() as connection:
            if import_job_id is not None:
                connection.execute(
                    text(
                        "DELETE ingestion.initial_import_finalize_intent "
                        "WHERE job_id=:job"
                    ),
                    {"job": import_job_id},
                )
                run_ids = [
                    int(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT processing_run_id FROM ingestion.processing_run WHERE job_id=:job"
                        ),
                        {"job": import_job_id},
                    )
                ]
                version_ids = [
                    int(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT dataset_version_id FROM dataset.dataset_version WHERE input_batch_id=:batch"
                        ),
                        {"batch": batch_id},
                    )
                ]
                for version_id in version_ids:
                    connection.execute(
                        text(
                            "DELETE dataset.dataset_version_run WHERE dataset_version_id=:version"
                        ),
                        {"version": version_id},
                    )
                connection.execute(
                    text("DELETE dataset.dataset_version WHERE input_batch_id=:batch"),
                    {"batch": batch_id},
                )
                dataset_ids = [
                    int(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT dataset_id FROM dataset.dataset WHERE dataset_code=:code"
                        ),
                        {"code": f"CP-BATCH-{batch_id}"},
                    )
                ]
                connection.execute(
                    text("DELETE ingestion.processing_artifact WHERE job_id=:job"),
                    {"job": import_job_id},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.processing_result_summary WHERE job_id=:job"
                    ),
                    {"job": import_job_id},
                )
                for run_id in run_ids:
                    connection.execute(
                        text(
                            "DELETE ingestion.processing_run_input_file "
                            "WHERE processing_run_id=:run"
                        ),
                        {"run": run_id},
                    )
                    connection.execute(
                        text(
                            "DELETE m FROM test.measurement m JOIN test.unit_result ur ON ur.unit_id=m.unit_id "
                            "JOIN test.test_run tr ON tr.run_id=ur.run_id WHERE tr.processing_run_id=:run"
                        ),
                        {"run": run_id},
                    )
                    connection.execute(
                        text(
                            "DELETE ur FROM test.unit_result ur JOIN test.test_run tr ON tr.run_id=ur.run_id "
                            "WHERE tr.processing_run_id=:run"
                        ),
                        {"run": run_id},
                    )
                    connection.execute(
                        text("DELETE test.test_run WHERE processing_run_id=:run"),
                        {"run": run_id},
                    )
                    connection.execute(
                        text(
                            "DELETE ingestion.processing_run WHERE processing_run_id=:run"
                        ),
                        {"run": run_id},
                    )
                if previous_current_run_id is not None:
                    restored_run = connection.execute(
                        text(
                            "UPDATE ingestion.processing_run SET status='PUBLISHED',"
                            "is_current=1 WHERE processing_run_id=:run "
                            "AND status='SUPERSEDED' AND is_current=0"
                        ),
                        {"run": previous_current_run_id},
                    )
                    if restored_run.rowcount != 1:
                        raise RuntimeError(
                            "failed to restore the pre-verification current run"
                        )
                    for version_id in previous_current_version_ids:
                        restored_version = connection.execute(
                            text(
                                "UPDATE dataset.dataset_version "
                                "SET status='PUBLISHED',is_current=1 "
                                "WHERE dataset_version_id=:version "
                                "AND status='SUPERSEDED' AND is_current=0"
                            ),
                            {"version": version_id},
                        )
                        if restored_version.rowcount != 1:
                            raise RuntimeError(
                                "failed to restore a pre-verification Dataset Current"
                            )
                for dataset_id in dataset_ids:
                    connection.execute(
                        text("DELETE dataset.dataset WHERE dataset_id=:dataset"),
                        {"dataset": dataset_id},
                    )
            for job_id in (lease_job_id, import_job_id):
                if job_id is not None:
                    connection.execute(
                        text(
                            "DELETE governance.audit_log "
                            "WHERE correlation_id=:correlation"
                        ),
                        {"correlation": f"job:{job_id}"},
                    )
                    connection.execute(
                        text("DELETE ingestion.processing_job WHERE job_id=:job"),
                        {"job": job_id},
                    )
            if receipt_id is not None:
                connection.execute(
                    text(
                        "DELETE ingestion.import_batch_file WHERE receipt_id=:receipt"
                    ),
                    {"receipt": receipt_id},
                )
                connection.execute(
                    text(
                        "DELETE ingestion.source_file_receipt WHERE receipt_id=:receipt"
                    ),
                    {"receipt": receipt_id},
                )
            if batch_id is not None:
                connection.execute(
                    text("DELETE ingestion.import_batch WHERE import_batch_id=:batch"),
                    {"batch": batch_id},
                )
        if work_root.exists():
            shutil.rmtree(work_root)
        engine.dispose()


if __name__ == "__main__":
    main()
