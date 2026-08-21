from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.domain.jobs import (
    CreateJobRequest,
    InMemoryJobService,
    JobStatus,
    TransitionJobRequest,
)


def create_request() -> CreateJobRequest:
    return CreateJobRequest(
        import_batch_id=10,
        cleaner_release_id=3,
        job_type="PARSE",
        trigger_type="MANUAL",
        requested_by="tester",
    )


def test_job_happy_path() -> None:
    service = InMemoryJobService()
    job = service.create(create_request())
    assert job.status == JobStatus.QUEUED

    running = service.transition(
        job.job_id, TransitionJobRequest(target_status=JobStatus.RUNNING)
    )
    assert running.started_at_utc is not None

    finished = service.transition(
        job.job_id, TransitionJobRequest(target_status=JobStatus.SUCCESS)
    )
    assert finished.finished_at_utc is not None


def test_terminal_job_cannot_transition() -> None:
    service = InMemoryJobService()
    job = service.create(create_request())
    service.transition(job.job_id, TransitionJobRequest(target_status="CANCELLED"))
    with pytest.raises(DomainError) as captured:
        service.transition(job.job_id, TransitionJobRequest(target_status="RUNNING"))
    assert captured.value.code == "INVALID_JOB_TRANSITION"


def test_job_requires_exactly_one_input_identity() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        CreateJobRequest(
            source_file_id=1,
            import_batch_id=2,
            cleaner_release_id=3,
            requested_by="tester",
        )


def test_parse_job_requires_cleaner_release() -> None:
    with pytest.raises(ValidationError, match="cleaner_release_id"):
        CreateJobRequest(import_batch_id=2, requested_by="tester")


def test_failed_transition_requires_error_code() -> None:
    with pytest.raises(ValidationError, match="error_code"):
        TransitionJobRequest(target_status="FAILED")
