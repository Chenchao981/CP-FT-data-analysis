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
from app.infrastructure.sql_operations_service import SqlOperationsService


class _RollbackEngine:
    """Bind service transactions to one caller-owned rollback transaction."""

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
        description="Verify A5 logical archive against TMS_G0_DEV and roll it back"
    )
    parser.add_argument("--dataset-id", type=int)
    return parser.parse_args()


def _fact_counts(connection: Connection) -> tuple[int, int, int, int]:
    statements = (
        "SELECT COUNT_BIG(*) FROM test.test_run",
        "SELECT COUNT_BIG(*) FROM test.unit_result",
        "SELECT COUNT_BIG(*) FROM test.measurement",
        (
            "SELECT COUNT_BIG(*) FROM dataset.dataset_version "
            "WHERE status='PUBLISHED' AND is_current=1"
        ),
    )
    return tuple(
        int(connection.execute(text(statement)).scalar_one())
        for statement in statements
    )  # type: ignore[return-value]


def _current_view_counts(
    connection: Connection, dataset_version_id: int
) -> tuple[int, int, int, int]:
    statements = (
        (
            "SELECT COUNT_BIG(*) FROM analytics.v_current_dataset_version "
            "WHERE dataset_version_id=:version"
        ),
        (
            "SELECT COUNT_BIG(*) FROM analytics.v_current_test_run "
            "WHERE dataset_version_id=:version"
        ),
        (
            "SELECT COUNT_BIG(*) FROM analytics.v_current_unit_result "
            "WHERE dataset_version_id=:version"
        ),
        (
            "SELECT COUNT_BIG(*) FROM analytics.v_current_measurement "
            "WHERE dataset_version_id=:version"
        ),
    )
    return tuple(
        int(
            connection.execute(
                text(statement), {"version": dataset_version_id}
            ).scalar_one()
        )
        for statement in statements
    )  # type: ignore[return-value]


def _target(connection: Connection, dataset_id: int | None):
    row = (
        connection.execute(
            text(
                "SELECT TOP (1) d.dataset_id,d.owner_user_id,dv.dataset_version_id,"
                "dv.version_no "
                "FROM dataset.dataset d "
                "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                "WHERE d.lifecycle_status='ACTIVE' AND dv.status='PUBLISHED' "
                "AND dv.is_current=1 AND (:dataset IS NULL OR d.dataset_id=:dataset) "
                "ORDER BY d.dataset_id DESC"
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("No matching Active Current Dataset is available")
    return row


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
        raise RuntimeError(
            "An active SYSTEM_ADMIN is required for this controlled test"
        )
    return Principal(
        user_id=int(row["user_id"]),
        login_name=str(row["login_name"]),
        display_name=str(row["display_name"]),
        roles=("SYSTEM_ADMIN",),
        permissions=frozenset({"EXPORT_DATA", "TASK_CREATE"}),
    )


def main() -> None:
    args = _parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    if identity["database"] != "TMS_G0_DEV":
        raise RuntimeError("This rollback verification is restricted to TMS_G0_DEV")
    if identity["schema_revision"] != "sql2014_0029":
        raise RuntimeError("sql2014_0029 is required")

    engine = get_engine()
    work_root = Path(os.getenv("TMS_WORK_ROOT", str(ROOT / "data" / "work"))).absolute()
    archive_key = f"g2-archive-rollback-{uuid4().hex}"
    lease_token = str(uuid4())

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            target = _target(connection, args.dataset_id)
            dataset_id = int(target["dataset_id"])
            version_id = int(target["dataset_version_id"])
            version_no = int(target["version_no"])
            principal = _admin(connection)
            before_facts = _fact_counts(connection)
            before_views = _current_view_counts(connection, version_id)
            before_summaries = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.processing_result_summary "
                        "WHERE dataset_id=:dataset AND dataset_version_no=:version_no "
                        "AND status='PROCESSED'"
                    ),
                    {"dataset": dataset_id, "version_no": version_no},
                ).scalar_one()
            )
            if before_views[0] != 1 or any(value < 1 for value in before_views):
                raise RuntimeError(
                    "Selected Dataset is not fully visible in Current views"
                )
            if before_summaries < 1:
                raise RuntimeError(
                    "Selected Dataset has no processed result projection"
                )

            bound = _RollbackEngine(connection)
            lifecycle = SqlLifecycleService(
                bound,  # type: ignore[arg-type]
                ManagedJobPathPolicy(work_root),
            )
            receipt = lifecycle.create_archive(
                dataset_id,
                "G2 rollback-only logical archive verification",
                archive_key,
                principal,
            )
            claimed = connection.execute(
                text(
                    "UPDATE ingestion.processing_job SET status='RUNNING',"
                    "started_at_utc=COALESCE(started_at_utc,SYSUTCDATETIME()),"
                    "lease_token=CONVERT(uniqueidentifier,:lease),"
                    "lease_owner='g2-a5-rollback',"
                    "lease_expires_at_utc=DATEADD(minute,5,SYSUTCDATETIME()),"
                    "heartbeat_at_utc=SYSUTCDATETIME() "
                    "WHERE job_id=:job AND status='QUEUED'"
                ),
                {"job": receipt.job_id, "lease": lease_token},
            )
            if claimed.rowcount != 1:
                raise RuntimeError("Archive Job could not be claimed")
            lifecycle.archive_dataset_leased(receipt.job_id, lease_token)

            archived = connection.execute(
                text(
                    "SELECT d.lifecycle_status,dv.status,dv.is_current,j.status "
                    "FROM dataset.dataset d "
                    "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                    "JOIN ingestion.processing_job j ON j.job_id=:job "
                    "WHERE d.dataset_id=:dataset AND dv.dataset_version_id=:version"
                ),
                {
                    "job": receipt.job_id,
                    "dataset": dataset_id,
                    "version": version_id,
                },
            ).one()
            if tuple(archived) != ("ARCHIVED", "ARCHIVED", False, "SUCCESS"):
                raise RuntimeError(
                    f"Unexpected logical archive state: {tuple(archived)}"
                )
            run_drift = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset_version_run dvr "
                        "JOIN ingestion.processing_run r "
                        "ON r.processing_run_id=dvr.processing_run_id "
                        "WHERE dvr.dataset_version_id=:version "
                        "AND (r.status<>'SUPERSEDED' OR r.is_current<>0)"
                    ),
                    {"version": version_id},
                ).scalar_one()
            )
            if run_drift != 0:
                raise RuntimeError(
                    "Archived Processing Run did not leave Current state"
                )
            if _current_view_counts(connection, version_id) != (0, 0, 0, 0):
                raise RuntimeError("Logical archive remained visible in Current views")
            if _fact_counts(connection)[:3] != before_facts[:3]:
                raise RuntimeError("Logical archive modified Canonical test facts")
            archived_summaries = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.processing_result_summary "
                        "WHERE dataset_id=:dataset AND dataset_version_no=:version_no "
                        "AND status='ARCHIVED'"
                    ),
                    {"dataset": dataset_id, "version_no": version_no},
                ).scalar_one()
            )
            if archived_summaries != before_summaries:
                raise RuntimeError("Result projection did not follow logical archive")
            operations = SqlOperationsService(  # type: ignore[arg-type]
                bound,
                environment="development",
            ).consistency_summary()
            if (
                operations.issue_counts.batch_job_intent != 0
                or operations.issue_counts.dataset_current != 0
            ):
                raise RuntimeError("Logical archive was misreported as an anomaly")
            print(
                "archive_transaction=PASS "
                f"dataset_id={dataset_id} version_id={version_id} "
                f"current_before={before_views} current_after={(0, 0, 0, 0)} "
                f"facts={before_facts[:3]} summaries={archived_summaries} "
                f"operations={operations.overall_state}"
            )
        finally:
            transaction.rollback()

    with engine.connect() as connection:
        after_facts = _fact_counts(connection)
        after_views = _current_view_counts(connection, version_id)
        leaked_job = int(
            connection.execute(
                text(
                    "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
                    "WHERE idempotency_key LIKE 'a5:delete_task:%' "
                    "AND reason='G2 rollback-only logical archive verification'"
                )
            ).scalar_one()
        )
        restored_summaries = int(
            connection.execute(
                text(
                    "SELECT COUNT_BIG(*) FROM ingestion.processing_result_summary "
                    "WHERE dataset_id=:dataset AND dataset_version_no=:version_no "
                    "AND status='PROCESSED'"
                ),
                {"dataset": dataset_id, "version_no": version_no},
            ).scalar_one()
        )
    if (
        after_facts != before_facts
        or after_views != before_views
        or leaked_job
        or restored_summaries != before_summaries
    ):
        raise RuntimeError("Rollback did not restore the pre-test database state")
    print("archive_rollback=PASS database_state_restored=true")


if __name__ == "__main__":
    main()
