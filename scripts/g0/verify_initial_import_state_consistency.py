from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import CreateJobRequest, JobType, TriggerType
from app.infrastructure.sql_job_service import SqlJobService


def _delete_batch(engine, batch_id: int | None) -> None:
    if batch_id is None:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE ingestion.processing_input_request WHERE import_batch_id=:batch"
            ),
            {"batch": batch_id},
        )
        connection.execute(
            text("DELETE ingestion.processing_job WHERE import_batch_id=:batch"),
            {"batch": batch_id},
        )
        connection.execute(
            text("DELETE ingestion.import_batch WHERE import_batch_id=:batch"),
            {"batch": batch_id},
        )


def _new_batch(engine, owner_id: int, login_name: str, status: str, token: str) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT ingestion.import_batch(source_channel,uploaded_by,status,"
                    "owner_user_id,business_domain,test_stage,factory_code,batch_name,"
                    "metadata_json) OUTPUT INSERTED.import_batch_id VALUES("
                    "'SYSTEM',:login,:status,:owner,'ENGINEERING','CP','huahong',"
                    ":name,:metadata)"
                ),
                {
                    "login": login_name,
                    "status": status,
                    "owner": owner_id,
                    "name": f"Initial import state verification {token}",
                    "metadata": '{"verification":true}',
                },
            ).scalar_one()
        )


def main() -> None:
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True, fast_executemany=True)
    queue = SqlJobService(engine)
    token = uuid4().hex
    concurrent_batch_id: int | None = None
    exhausted_batch_id: int | None = None
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != "sql2014_0024":
                raise RuntimeError(f"unexpected schema revision: {revision}")
            active_initial_imports = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM ingestion.processing_job WHERE "
                        "job_type='INITIAL_IMPORT' AND status IN('QUEUED','RUNNING')"
                    )
                ).scalar_one()
            )
            if active_initial_imports:
                raise RuntimeError("verification requires an idle INITIAL_IMPORT queue")
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
        owner_id = int(owner["user_id"])
        login_name = str(owner["login_name"])
        principal = Principal(
            user_id=owner_id,
            login_name=login_name,
            display_name="Initial import state verification",
            roles=(),
            permissions=frozenset(),
        )

        concurrent_batch_id = _new_batch(
            engine, owner_id, login_name, "PROCESSED", token
        )

        def submit(index: int):
            try:
                job = queue.create_initial_import_for_batch(
                    CreateJobRequest(
                        import_batch_id=concurrent_batch_id,
                        cleaner_release_id=release_id,
                        job_type=JobType.INITIAL_IMPORT,
                        trigger_type=TriggerType.MANUAL,
                        requested_by=login_name,
                        requested_by_user_id=owner_id,
                        reason="SQL concurrency verification",
                        idempotency_key=(f"state-consistency:{token}:{index}"),
                    ),
                    principal,
                    allowed_batch_statuses=("PROCESSED", "FAILED"),
                )
                return ("JOB", job.job_id)
            except DomainError as exc:
                return ("ERROR", exc.code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(submit, (1, 2)))
        if sorted(kind for kind, _value in outcomes) != ["ERROR", "JOB"]:
            raise RuntimeError(f"unexpected concurrent outcomes: {outcomes}")
        error_codes = [value for kind, value in outcomes if kind == "ERROR"]
        if error_codes != ["BATCH_ALREADY_ACTIVE"]:
            raise RuntimeError(f"unexpected concurrency error: {outcomes}")
        with engine.connect() as connection:
            concurrent_state = (
                connection.execute(
                    text(
                        "SELECT b.status,COUNT(j.job_id) AS job_count "
                        "FROM ingestion.import_batch b "
                        "LEFT JOIN ingestion.processing_job j "
                        "ON j.import_batch_id=b.import_batch_id "
                        "WHERE b.import_batch_id=:batch GROUP BY b.status"
                    ),
                    {"batch": concurrent_batch_id},
                )
                .mappings()
                .one()
            )
        if (
            concurrent_state["status"] != "QUEUED"
            or int(concurrent_state["job_count"]) != 1
        ):
            raise RuntimeError(f"unexpected atomic queue state: {concurrent_state}")
        _delete_batch(engine, concurrent_batch_id)
        concurrent_batch_id = None

        exhausted_batch_id = _new_batch(engine, owner_id, login_name, "QUEUED", token)
        with engine.begin() as connection:
            exhausted_job_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job(import_batch_id,"
                        "cleaner_release_id,job_type,trigger_type,requested_by,"
                        "requested_by_user_id,reason,status,idempotency_key,"
                        "attempt_count,max_attempts) OUTPUT INSERTED.job_id VALUES("
                        ":batch,:release,'INITIAL_IMPORT','SYSTEM',:login,:owner,"
                        "'SQL max-attempt verification','QUEUED',:key,1,1)"
                    ),
                    {
                        "batch": exhausted_batch_id,
                        "release": release_id,
                        "login": login_name,
                        "owner": owner_id,
                        "key": f"state-exhausted:{token}",
                    },
                ).scalar_one()
            )
        claimed = queue.claim_next(
            f"state-verification-{token[:8]}",
            timedelta(minutes=1),
            (JobType.INITIAL_IMPORT,),
        )
        if claimed is not None:
            raise RuntimeError(f"exhausted job was unexpectedly claimed: {claimed}")
        with engine.connect() as connection:
            exhausted_state = (
                connection.execute(
                    text(
                        "SELECT j.status AS job_status,j.error_code,b.status AS batch_status "
                        "FROM ingestion.processing_job j "
                        "JOIN ingestion.import_batch b "
                        "ON b.import_batch_id=j.import_batch_id WHERE j.job_id=:job"
                    ),
                    {"job": exhausted_job_id},
                )
                .mappings()
                .one()
            )
        expected = {
            "job_status": "FAILED",
            "error_code": "MAX_ATTEMPTS_EXCEEDED",
            "batch_status": "FAILED",
        }
        if dict(exhausted_state) != expected:
            raise RuntimeError(f"unexpected exhausted state: {exhausted_state}")
        print(
            "initial_import_state_consistency=PASS "
            "concurrent_reprocess=ONE_JOB max_attempt_batch=FAILED"
        )
    finally:
        _delete_batch(engine, exhausted_batch_id)
        _delete_batch(engine, concurrent_batch_id)
        print("initial_import_state_cleanup=PASS")


if __name__ == "__main__":
    main()
