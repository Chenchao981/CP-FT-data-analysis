from __future__ import annotations

from datetime import UTC, datetime

from app.domain.jobs import JobStatus
from app.infrastructure.sql_job_service import _to_job

from scripts.g0.revalidate_sqlserver_enterprise import version_tuple


def test_database_job_row_maps_to_domain() -> None:
    row = {
        "job_id": 1,
        "source_file_id": None,
        "import_batch_id": 7,
        "cleaner_release_id": 2,
        "job_type": "PARSE",
        "trigger_type": "MANUAL",
        "requested_by": "tester",
        "requested_by_user_id": 1,
        "reason": None,
        "status": "RUNNING",
        "requested_at_utc": datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC),
        "started_at_utc": datetime(2026, 8, 20, 1, 2, 4, tzinfo=UTC),
        "finished_at_utc": None,
        "error_code": None,
        "error_message": None,
        "idempotency_key": "test-job-0001",
        "not_before_utc": datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC),
        "lease_token": "11111111-1111-1111-1111-111111111111",
        "lease_owner": "worker-1",
        "lease_expires_at_utc": datetime(2026, 8, 20, 1, 7, 3, tzinfo=UTC),
        "heartbeat_at_utc": datetime(2026, 8, 20, 1, 2, 4, tzinfo=UTC),
        "attempt_count": 1,
        "max_attempts": 3,
    }
    job = _to_job(row)
    assert job.status == JobStatus.RUNNING
    assert job.requested_at_utc.utcoffset().total_seconds() == 0


def test_sql_server_build_comparison_input() -> None:
    assert version_tuple("12.0.6024.0") == (12, 0, 6024, 0)
