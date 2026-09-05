from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Connection, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.auth import Principal
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.formal_artifact_files import ManagedJobPathPolicy
from app.infrastructure.sql_lifecycle_service import SqlLifecycleService
from app.infrastructure.sql_m2_query_service import SqlM2QueryService


class _RollbackEngine:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        yield self._connection

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        yield self._connection


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify A5 reprocess queue contract in TMS_G0_DEV and roll it back"
    )
    parser.add_argument("--dataset-id", type=int)
    return parser.parse_args()


def _facts(connection: Connection) -> tuple[int, int, int, int]:
    return tuple(
        int(connection.execute(text(statement)).scalar_one())
        for statement in (
            "SELECT COUNT_BIG(*) FROM test.test_run",
            "SELECT COUNT_BIG(*) FROM test.unit_result",
            "SELECT COUNT_BIG(*) FROM test.measurement",
            (
                "SELECT COUNT_BIG(*) FROM dataset.dataset_version "
                "WHERE status='PUBLISHED' AND is_current=1"
            ),
        )
    )  # type: ignore[return-value]


def _admin(connection: Connection) -> Principal:
    row = (
        connection.execute(
            text(
                "SELECT TOP (1) u.user_id,u.login_name,u.display_name "
                "FROM iam.app_user u "
                "JOIN iam.user_role ur ON ur.user_id=u.user_id "
                "JOIN iam.role r ON r.role_id=ur.role_id "
                "WHERE u.status='ACTIVE' AND r.active=1 "
                "AND r.role_code='SYSTEM_ADMIN' ORDER BY u.user_id"
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("An active SYSTEM_ADMIN is required")
    return Principal(
        user_id=int(row["user_id"]),
        login_name=str(row["login_name"]),
        display_name=str(row["display_name"]),
        roles=("SYSTEM_ADMIN",),
        permissions=frozenset({"EXPORT_DATA", "TASK_CREATE"}),
    )


def _target(connection: Connection, dataset_id: int | None):
    row = (
        connection.execute(
            text(
                "SELECT TOP (1) d.dataset_id,dv.dataset_version_id,"
                "dv.input_batch_id,b.status AS batch_status "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "JOIN ingestion.import_batch b "
                "ON b.import_batch_id=dv.input_batch_id "
                "WHERE d.lifecycle_status='ACTIVE' AND dv.status='PUBLISHED' "
                "AND dv.is_current=1 AND b.status IN('PROCESSED','FAILED') "
                "AND (:dataset IS NULL OR d.dataset_id=:dataset) "
                "ORDER BY d.dataset_id DESC"
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("No reprocessable Current Dataset is available")
    return row


def main() -> None:
    args = _parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    if identity["database"] != "TMS_G0_DEV":
        raise RuntimeError("This rollback verification is restricted to TMS_G0_DEV")
    if identity["schema_revision"] != "sql2014_0028":
        raise RuntimeError("sql2014_0028 is required")

    engine = get_engine()
    work_root = Path(os.getenv("TMS_WORK_ROOT", str(ROOT / "data" / "work"))).absolute()
    reason = "G2 rollback-only explicit reprocess verification"
    external_key = f"g2-reprocess-rollback-{uuid4().hex}"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            target = _target(connection, args.dataset_id)
            dataset_id = int(target["dataset_id"])
            batch_id = int(target["input_batch_id"])
            original_batch_status = str(target["batch_status"])
            baseline = _facts(connection)
            bound_engine = _RollbackEngine(connection)
            lifecycle = SqlLifecycleService(
                bound_engine,  # type: ignore[arg-type]
                ManagedJobPathPolicy(work_root),
            )
            principal = _admin(connection)
            receipt = lifecycle.create_reprocess(
                dataset_id,
                reason,
                external_key,
                principal,
            )
            replay = lifecycle.create_reprocess(
                dataset_id,
                reason,
                external_key,
                principal,
            )
            if replay.created or replay.job_id != receipt.job_id:
                raise RuntimeError("Reprocess idempotency replay created another Job")
            row = connection.execute(
                text(
                    "SELECT j.job_type,j.status,j.finalize_protocol,j.parent_job_id,"
                    "j.import_batch_id,j.cleaner_release_id,t.action_type,b.status "
                    "FROM ingestion.processing_job j "
                    "JOIN ingestion.lifecycle_job_target t ON t.job_id=j.job_id "
                    "JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=j.import_batch_id "
                    "WHERE j.job_id=:job"
                ),
                {"job": receipt.job_id},
            ).one()
            if tuple(row[:3]) != ("INITIAL_IMPORT", "QUEUED", "ATOMIC_V1"):
                raise RuntimeError(f"Unexpected reprocess Job contract: {tuple(row)}")
            if (
                row.parent_job_id is None
                or int(row.import_batch_id) != batch_id
                or row.cleaner_release_id is None
                or row.action_type != "REPROCESS_UPDATE"
                or row.status != "QUEUED"
            ):
                raise RuntimeError(f"Incomplete reprocess lineage: {tuple(row)}")
            cleaner_contract = connection.execute(
                text(
                    "SELECT original.format_profile_id AS original_profile_id,"
                    "selected.format_profile_id AS selected_profile_id,"
                    "original.input_contract_version AS original_input_contract,"
                    "selected.input_contract_version AS selected_input_contract "
                    "FROM ingestion.processing_job parent_job "
                    "JOIN ingestion.cleaner_release original "
                    "ON original.cleaner_release_id=parent_job.cleaner_release_id "
                    "JOIN ingestion.cleaner_release selected "
                    "ON selected.cleaner_release_id=:release "
                    "WHERE parent_job.job_id=:parent"
                ),
                {
                    "release": receipt.cleaner_release_id,
                    "parent": receipt.parent_job_id,
                },
            ).one()
            if (
                int(cleaner_contract.original_profile_id)
                != int(cleaner_contract.selected_profile_id)
                or cleaner_contract.original_input_contract
                != cleaner_contract.selected_input_contract
            ):
                raise RuntimeError(
                    "Reprocess selected an incompatible Cleaner contract"
                )
            if _facts(connection) != baseline:
                raise RuntimeError(
                    "Queueing reprocess changed Canonical facts or Current"
                )
            details = SqlM2QueryService(  # type: ignore[arg-type]
                bound_engine
            ).get_job_details(principal, receipt.job_id)
            if details.job.lifecycle_action_type != "REPROCESS_UPDATE":
                raise RuntimeError("Job detail lost the explicit reprocess action")
            print(
                "reprocess_transaction=PASS "
                f"dataset_id={dataset_id} job_id={receipt.job_id} "
                f"parent_job_id={receipt.parent_job_id} batch_id={batch_id} "
                f"release_id={receipt.cleaner_release_id} "
                f"format_profile_id={cleaner_contract.selected_profile_id} "
                f"lifecycle_action={details.job.lifecycle_action_type} "
                f"facts={baseline[:3]}"
            )
        finally:
            transaction.rollback()

    with engine.connect() as connection:
        restored_status = connection.execute(
            text(
                "SELECT status FROM ingestion.import_batch WHERE import_batch_id=:batch"
            ),
            {"batch": batch_id},
        ).scalar_one()
        leaked = int(
            connection.execute(
                text(
                    "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
                    "WHERE reason=:reason"
                ),
                {"reason": reason},
            ).scalar_one()
        )
        after = _facts(connection)
    if restored_status != original_batch_status or leaked or after != baseline:
        raise RuntimeError("Rollback did not restore the pre-test database state")
    print("reprocess_rollback=PASS database_state_restored=true")


if __name__ == "__main__":
    main()
