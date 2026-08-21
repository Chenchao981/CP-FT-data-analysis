from __future__ import annotations

from datetime import datetime

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
        "reason": None,
        "status": "RUNNING",
        "requested_at_utc": datetime(2026, 8, 20, 1, 2, 3),
        "started_at_utc": datetime(2026, 8, 20, 1, 2, 4),
        "finished_at_utc": None,
        "error_code": None,
        "error_message": None,
    }
    job = _to_job(row)
    assert job.status == JobStatus.RUNNING
    assert job.requested_at_utc.utcoffset().total_seconds() == 0


def test_sql_server_build_comparison_input() -> None:
    assert version_tuple("12.0.6024.0") == (12, 0, 6024, 0)
