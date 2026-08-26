from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.workers.route_a_worker import DatabaseJobWorker


def _claimed_job() -> Job:
    now = datetime.now(UTC)
    return Job(
        job_id=41,
        source_file_id=None,
        import_batch_id=7,
        analysis_session_id=None,
        cleaner_release_id=9,
        job_type=JobType.INITIAL_IMPORT,
        trigger_type=TriggerType.AUTO,
        requested_by="tester",
        requested_by_user_id=1,
        reason=None,
        status=JobStatus.RUNNING,
        requested_at_utc=now,
        started_at_utc=now,
        idempotency_key="initial-import:7",
        not_before_utc=now,
        lease_token="11111111-1111-1111-1111-111111111111",
        lease_owner="worker-1",
        lease_expires_at_utc=now + timedelta(minutes=1),
        heartbeat_at_utc=now,
        attempt_count=1,
        max_attempts=3,
    )


class FakeQueue:
    def __init__(self, job: Job | None) -> None:
        self.job = job
        self.finished: tuple[JobStatus, str | None] | None = None

    def claim_next(self, worker_id, lease_for, accepted_job_types):
        assert accepted_job_types == (JobType.INITIAL_IMPORT,)
        job, self.job = self.job, None
        return job

    def heartbeat(self, job_id, lease_token, lease_for):
        raise AssertionError("fast unit handler should finish before heartbeat")

    def finish_leased(
        self,
        job_id,
        lease_token,
        target_status,
        *,
        error_code=None,
        error_message=None,
    ):
        self.finished = (target_status, error_code)
        return replace(
            _claimed_job(),
            status=target_status,
            error_code=error_code,
            error_message=error_message,
            lease_token=None,
            lease_owner=None,
            lease_expires_at_utc=None,
        )


def test_worker_dispatches_claimed_job_and_finishes_success() -> None:
    queue = FakeQueue(_claimed_job())
    handled: list[int] = []
    worker = DatabaseJobWorker(
        queue,
        {JobType.INITIAL_IMPORT: lambda job: handled.append(job.job_id)},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    result = worker.run_once()
    assert handled == [41]
    assert result is not None and result.status == JobStatus.SUCCESS
    assert queue.finished == (JobStatus.SUCCESS, None)


def test_worker_records_handler_failure_as_terminal_job() -> None:
    queue = FakeQueue(_claimed_job())

    def fail(_job):
        raise RuntimeError("synthetic Cleaner failure")

    worker = DatabaseJobWorker(
        queue,
        {JobType.INITIAL_IMPORT: fail},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    result = worker.run_once()
    assert result is not None and result.status == JobStatus.FAILED
    assert queue.finished == (JobStatus.FAILED, "WORKER_EXECUTION_FAILED")


def test_worker_returns_none_when_queue_is_empty() -> None:
    worker = DatabaseJobWorker(
        FakeQueue(None),
        {},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    assert worker.run_once() is None
