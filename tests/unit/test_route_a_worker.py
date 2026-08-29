from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.domain.stage_data import BatchFileInfo, WorkerBatchInfo
from app.infrastructure.cp_csv_triplet_writer import (
    CP_MULTI_LOT_SPEC_BINDING_REQUIRED,
    CpMultiLotSpecBindingRequired,
)
from app.infrastructure.existing_cleaner_runner import CleanerInputRequired
from app.workers.route_a_worker import DatabaseJobWorker, RouteAInitialImportHandler


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
        self.paused: tuple[str, tuple[str, ...]] | None = None

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

    def pause_leased_for_input(
        self, job_id, lease_token, *, field_code, files, message
    ):
        assert job_id == 41
        assert lease_token == "11111111-1111-1111-1111-111111111111"
        assert message
        self.paused = (field_code, files)
        return replace(
            _claimed_job(),
            status=JobStatus.NEEDS_INPUT,
            error_code="LOT_ID_REQUIRED",
            error_message=message,
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


def test_worker_preserves_multi_lot_spec_binding_error_code() -> None:
    queue = FakeQueue(_claimed_job())

    def fail_closed(_job):
        raise CpMultiLotSpecBindingRequired(
            f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: Lots L1, L2"
        )

    worker = DatabaseJobWorker(
        queue,
        {JobType.INITIAL_IMPORT: fail_closed},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    result = worker.run_once()
    assert result is not None and result.status == JobStatus.FAILED
    assert queue.finished == (
        JobStatus.FAILED,
        CP_MULTI_LOT_SPEC_BINDING_REQUIRED,
    )


def test_worker_pauses_for_typed_lot_input_without_marking_failed() -> None:
    queue = FakeQueue(_claimed_job())

    def needs_input(_job):
        raise CleanerInputRequired(
            field_code="LOT_ID",
            files=("missing-lot.xlsx",),
            message="请确认批次号",
        )

    worker = DatabaseJobWorker(
        queue,
        {JobType.INITIAL_IMPORT: needs_input},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    result = worker.run_once()
    assert result is not None and result.status == JobStatus.NEEDS_INPUT
    assert queue.paused == ("LOT_ID", ("missing-lot.xlsx",))
    assert queue.finished is None


def test_worker_returns_none_when_queue_is_empty() -> None:
    worker = DatabaseJobWorker(
        FakeQueue(None),
        {},
        worker_id="worker-1",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    )
    assert worker.run_once() is None


def test_initial_import_validation_failure_marks_batch_failed_before_processing(
    tmp_path: Path,
) -> None:
    class StageData:
        def __init__(self) -> None:
            self.failed = None

        def worker_batch_info(self, batch_id):
            assert batch_id == 7
            return WorkerBatchInfo(
                7,
                "PRODUCTION",
                "FT",
                "riyuexin",
                (
                    BatchFileInfo(
                        17,
                        "missing-lot.xlsx",
                        str(tmp_path / "missing-lot.xlsx"),
                        71,
                        "0" * 64,
                    ),
                ),
            )

        def worker_mark_processing(self, batch_id):
            raise AssertionError("factory mismatch must fail before PROCESSING")

        def mark_failed(self, batch_id, job_id, message, *, finish_job):
            self.failed = (batch_id, job_id, message, finish_job)

    class Registry:
        def get_released(self, release_id):
            assert release_id == 9
            return CleanerRelease(
                cleaner_release_id=9,
                format_profile_id=7,
                test_stage="FT",
                factory_code="RIYUEGUANG",
                format_code="FT_DC",
                profile_version="v1",
                cleaner_code="RIYUEGUANG_FT",
                cleaner_version="v1",
                code_checksum="0" * 64,
                artifact_uri="cleaner.pyz",
                runtime_uri="python.exe",
                entrypoint="tms-adapter",
                adapter_code="FT_XLSX_SCATTER_V1",
                input_contract_version="v1",
                output_contract_version="v1",
                execution_config_json=None,
                timeout_seconds=60,
                max_output_bytes=1_000_000,
            )

    stage_data = StageData()
    handler = RouteAInitialImportHandler(
        Registry(),  # type: ignore[arg-type]
        stage_data,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        work_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Cleaner Release does not match"):
        handler(_claimed_job())

    assert stage_data.failed is not None
    assert stage_data.failed[:2] == (7, 41)
    assert stage_data.failed[3] is False


def test_initial_import_missing_release_marks_linked_batch_failed(tmp_path: Path) -> None:
    class StageData:
        def __init__(self) -> None:
            self.failed = None

        def mark_failed(self, batch_id, job_id, message, *, finish_job):
            self.failed = (batch_id, job_id, message, finish_job)

    stage_data = StageData()
    handler = RouteAInitialImportHandler(
        object(),  # type: ignore[arg-type]
        stage_data,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        work_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="requires cleaner_release_id"):
        handler(replace(_claimed_job(), cleaner_release_id=None))

    assert stage_data.failed == (
        7,
        41,
        "INITIAL_IMPORT requires cleaner_release_id",
        False,
    )
