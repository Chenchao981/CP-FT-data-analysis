from __future__ import annotations

import argparse
import socket
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.jobs import CreateJobRequest, JobStatus, JobType
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.database import get_engine
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.workers.route_a_worker import DatabaseJobWorker, RouteAInitialImportHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Route A for an existing upload batch")
    parser.add_argument("batch_id", type=int)
    args = parser.parse_args()
    engine = get_engine()
    registry = SqlCleanerRegistry(engine)
    queue = SqlJobService(engine)
    stage_data = SqlStageDataService(engine)
    with engine.connect() as connection:
        batch = (
            connection.execute(
                text(
                    "SELECT import_batch_id,test_stage,factory_code,owner_user_id,uploaded_by "
                    "FROM ingestion.import_batch WHERE import_batch_id=:batch"
                ),
                {"batch": args.batch_id},
            )
            .mappings()
            .one()
        )
    release = registry.latest_released(str(batch["test_stage"]), str(batch["factory_code"]))
    job = queue.create(
        CreateJobRequest(
            import_batch_id=args.batch_id,
            cleaner_release_id=release.cleaner_release_id,
            job_type=JobType.INITIAL_IMPORT,
            trigger_type="SYSTEM",
            requested_by=str(batch["uploaded_by"] or "route-a-backfill"),
            requested_by_user_id=int(batch["owner_user_id"]),
            idempotency_key=f"route-a-backfill:{args.batch_id}:{release.cleaner_release_id}",
        )
    )
    worker = DatabaseJobWorker(
        queue,
        {
            JobType.INITIAL_IMPORT: RouteAInitialImportHandler(
                registry, stage_data, CpCsvTripletWriter(engine)
            )
        },
        worker_id=f"{socket.gethostname()}-route-a-backfill",
        lease_for=timedelta(minutes=10),
        heartbeat_every=timedelta(seconds=30),
    )
    finished = worker.run_once()
    if finished is None or finished.job_id != job.job_id or finished.status != JobStatus.SUCCESS:
        raise RuntimeError(f"Route A backfill failed: {finished}")
    with engine.connect() as connection:
        summary = (
            connection.execute(
                text(
                    "SELECT result_summary_id,dataset_id,dataset_version_no,unit_count,pass_count,yield_rate "
                    "FROM ingestion.processing_result_summary WHERE job_id=:job"
                ),
                {"job": job.job_id},
            )
            .mappings()
            .one()
        )
    print(dict(summary))


if __name__ == "__main__":
    main()
