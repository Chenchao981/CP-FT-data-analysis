from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.domain.lifecycle import (
    LifecycleInputFile,
    LifecycleWorkerContext,
    TemporaryArtifactInput,
)
from app.infrastructure.existing_cleaner_runner import (
    CleanerArtifact,
    ExistingCleanerRunResult,
)
from app.infrastructure.formal_artifact_files import ManagedJobPathPolicy
from app.workers.lifecycle_worker import (
    ExportLatestHandler,
    LogicalArchiveHandler,
)
from app.workers.route_a_worker import DatabaseJobWorker


def _job(job_type: JobType) -> Job:
    now = datetime.now(UTC)
    return Job(
        job_id=81,
        source_file_id=None,
        import_batch_id=17,
        analysis_session_id=None,
        cleaner_release_id=(None if job_type == JobType.DELETE_TASK else 9),
        job_type=job_type,
        trigger_type=TriggerType.API,
        requested_by="owner",
        requested_by_user_id=7,
        reason="explicit lifecycle request",
        status=JobStatus.RUNNING,
        requested_at_utc=now,
        started_at_utc=now,
        lease_token="11111111-1111-1111-1111-111111111111",
        lease_owner="worker-a",
        lease_expires_at_utc=now + timedelta(minutes=5),
        attempt_count=2,
        max_attempts=3,
    )


def _context(action: str, input_path: Path) -> LifecycleWorkerContext:
    return LifecycleWorkerContext(
        job_id=81,
        action_type=action,
        dataset_id=5,
        dataset_version_id=6,
        import_batch_id=17,
        test_stage="FT",
        factory_code="RIYUEXIN",
        requested_by_user_id=7,
        request_reason=None,
        files=(
            LifecycleInputFile(
                import_batch_file_id=3,
                original_file_name="lot.xlsx",
                storage_uri=str(input_path),
                expected_sha256="a" * 64,
                lot_id_override="LOT-1",
            ),
        ),
    )


def _release() -> CleanerRelease:
    return CleanerRelease(
        cleaner_release_id=9,
        format_profile_id=2,
        test_stage="FT",
        factory_code="RIYUEXIN",
        format_code="FT_XLSX_SCATTER_V1",
        profile_version="1",
        cleaner_code="riyuexin",
        cleaner_version="1",
        code_checksum="b" * 64,
        artifact_uri="cleaner.pyz",
        runtime_uri="python.exe",
        entrypoint="clean",
        adapter_code="RIYUEXIN_FT_PYZ",
        input_contract_version="1",
        output_contract_version="1",
        execution_config_json=None,
        timeout_seconds=30,
        max_output_bytes=1024,
    )


def test_export_handler_reruns_cleaner_into_job_attempt_and_only_records_temp_artifacts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        def get_released(self, release_id: int):
            assert release_id == 9
            return _release()

    class Lifecycle:
        def __init__(self) -> None:
            self.recorded: tuple[TemporaryArtifactInput, ...] | None = None

        def worker_context(self, job_id, lease_token, action_type):
            assert action_type == "EXPORT_LATEST"
            return _context(action_type, source)

        def record_export_artifacts(
            self, job_id, lease_token, artifacts, expires_at_utc
        ):
            assert job_id == 81
            assert expires_at_utc > datetime.now(UTC)
            self.recorded = artifacts
            return ()

    class Runner:
        def __init__(self) -> None:
            self.output_root: Path | None = None

        def run_release(self, **kwargs):
            self.output_root = Path(kwargs["output_root"])
            self.output_root.mkdir(parents=True)
            output = self.output_root / "latest.xlsx"
            payload = b"latest-export"
            output.write_bytes(payload)
            assert kwargs["lot_overrides"] == {"lot.xlsx": "LOT-1"}
            assert kwargs["expected_sha256"] == ("a" * 64,)
            return ExistingCleanerRunResult(
                test_stage="FT",
                factory="RIYUEXIN",
                output_root=str(self.output_root),
                artifacts=(
                    CleanerArtifact(
                        "EXPORT",
                        str(output),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    ),
                ),
                stdout_tail="ok",
            )

    lifecycle = Lifecycle()
    runner = Runner()
    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        lifecycle,  # type: ignore[arg-type]
        policy,
        runner=runner,  # type: ignore[arg-type]
    )

    completed = handler(_job(JobType.EXPORT_LATEST))
    assert completed.status == JobStatus.SUCCESS
    assert completed.lease_token is None
    assert runner.output_root == policy.work_root / "81" / "attempt-2"
    assert lifecycle.recorded is not None
    assert lifecycle.recorded[0].role == "EXPORT"
    assert lifecycle.recorded[0].path.endswith("latest.xlsx")


def test_logical_archive_handler_returns_atomic_terminal_job() -> None:
    class Lifecycle:
        called = False

        def archive_dataset_leased(self, job_id, lease_token):
            assert job_id == 81
            assert lease_token
            self.called = True

    lifecycle = Lifecycle()
    result = LogicalArchiveHandler(lifecycle)(_job(JobType.DELETE_TASK))  # type: ignore[arg-type]

    assert lifecycle.called is True
    assert result.status == JobStatus.SUCCESS
    assert result.lease_token is None


def test_export_handler_removes_physical_output_when_finalize_access_is_revoked(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        @staticmethod
        def get_released(_release_id: int):
            return _release()

    class Lifecycle:
        @staticmethod
        def worker_context(_job_id, _lease_token, action_type):
            return _context(action_type, source)

        @staticmethod
        def record_export_artifacts(*_args, **_kwargs):
            raise DomainError(
                "LIFECYCLE_EXPORT_ACCESS_REVOKED",
                "requester grant was revoked while rendering",
                409,
            )

    class Runner:
        @staticmethod
        def run_release(**kwargs):
            output_root = Path(kwargs["output_root"])
            output_root.mkdir(parents=True)
            output = output_root / "latest.xlsx"
            payload = b"latest-export"
            output.write_bytes(payload)
            return ExistingCleanerRunResult(
                test_stage="FT",
                factory="RIYUEXIN",
                output_root=str(output_root),
                artifacts=(
                    CleanerArtifact(
                        "EXPORT",
                        str(output),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    ),
                ),
                stdout_tail="ok",
            )

    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        Lifecycle(),  # type: ignore[arg-type]
        policy,
        runner=Runner(),  # type: ignore[arg-type]
    )

    with pytest.raises(DomainError) as denied:
        handler(_job(JobType.EXPORT_LATEST))

    assert denied.value.code == "LIFECYCLE_EXPORT_ACCESS_REVOKED"
    assert not policy.job_root(81).exists()


def test_cleanup_io_failure_is_logged_and_database_worker_marks_job_failed(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        @staticmethod
        def get_released(_release_id: int):
            return _release()

    class Lifecycle:
        @staticmethod
        def worker_context(_job_id, _lease_token, action_type):
            return _context(action_type, source)

        @staticmethod
        def record_export_artifacts(*_args, **_kwargs):
            raise DomainError(
                "LIFECYCLE_EXPORT_ACCESS_REVOKED",
                "requester was disabled while rendering",
                409,
            )

    class Runner:
        @staticmethod
        def run_release(**kwargs):
            output_root = Path(kwargs["output_root"])
            output_root.mkdir(parents=True)
            output = output_root / "latest.xlsx"
            payload = b"latest-export"
            output.write_bytes(payload)
            return ExistingCleanerRunResult(
                test_stage="FT",
                factory="RIYUEXIN",
                output_root=str(output_root),
                artifacts=(
                    CleanerArtifact(
                        "EXPORT",
                        str(output),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    ),
                ),
                stdout_tail="ok",
            )

    class Queue:
        terminal_status = None
        error_code = None

        @staticmethod
        def claim_next(_worker_id, _lease_for, _accepted_job_types):
            return _job(JobType.EXPORT_LATEST)

        @staticmethod
        def heartbeat(*_args):
            return None

        def finish_leased(
            self,
            _job_id,
            _lease_token,
            target_status,
            *,
            error_code,
            error_message,
        ):
            self.terminal_status = target_status
            self.error_code = error_code
            assert "disabled" in error_message

    def cleanup_fails(*_args, **_kwargs):
        raise OSError("synthetic managed-directory cleanup failure")

    monkeypatch.setattr(
        "app.workers.lifecycle_worker.FormalArtifactFileCleaner.cleanup_attempt",
        cleanup_fails,
    )
    caplog.set_level("ERROR")
    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        Lifecycle(),  # type: ignore[arg-type]
        policy,
        runner=Runner(),  # type: ignore[arg-type]
    )
    queue = Queue()

    DatabaseJobWorker(
        queue,  # type: ignore[arg-type]
        {JobType.EXPORT_LATEST: handler},
        worker_id="worker-a",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    ).run_once()

    assert queue.terminal_status == JobStatus.FAILED
    assert queue.error_code == "WORKER_EXECUTION_FAILED"
    assert policy.job_root(81).exists()
    assert any(
        record.message.startswith("failed to clean rejected Lifecycle Export attempt")
        and record.job_id == 81
        and record.attempt_count == 2
        for record in caplog.records
    )


def test_export_handler_rechecks_disabled_requester_before_cleaner_execution(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        @staticmethod
        def get_released(_release_id: int):
            return _release()

    class Lifecycle:
        checks = 0

        def worker_context(self, _job_id, _lease_token, action_type):
            self.checks += 1
            if self.checks == 2:
                raise DomainError(
                    "LIFECYCLE_EXPORT_ACCESS_REVOKED",
                    "requester account is disabled",
                    409,
                )
            return _context(action_type, source)

        @staticmethod
        def record_export_artifacts(*_args, **_kwargs):
            raise AssertionError("disabled requester must not finalize an export")

    class Runner:
        @staticmethod
        def run_release(**_kwargs):
            raise AssertionError("disabled requester must not start the Cleaner")

    lifecycle = Lifecycle()
    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        lifecycle,  # type: ignore[arg-type]
        policy,
        runner=Runner(),  # type: ignore[arg-type]
    )

    with pytest.raises(DomainError) as denied:
        handler(_job(JobType.EXPORT_LATEST))

    assert denied.value.code == "LIFECYCLE_EXPORT_ACCESS_REVOKED"
    assert lifecycle.checks == 2
    assert not policy.job_root(81).exists()


def test_export_handler_removes_runner_half_product_after_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        @staticmethod
        def get_released(_release_id: int):
            return _release()

    class Lifecycle:
        @staticmethod
        def worker_context(_job_id, _lease_token, action_type):
            return _context(action_type, source)

        @staticmethod
        def record_export_artifacts(*_args, **_kwargs):
            raise AssertionError("failed Cleaner output must not be registered")

    class Runner:
        @staticmethod
        def run_release(**kwargs):
            output_root = Path(kwargs["output_root"])
            output_root.mkdir(parents=True)
            (output_root / "half-product.xlsx").write_bytes(b"partial")
            raise RuntimeError("synthetic Cleaner failure")

    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        Lifecycle(),  # type: ignore[arg-type]
        policy,
        runner=Runner(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="synthetic Cleaner failure"):
        handler(_job(JobType.EXPORT_LATEST))

    assert not policy.job_root(81).exists()


def test_archive_handler_atomic_success_is_not_finished_a_second_time() -> None:
    class Lifecycle:
        def archive_dataset_leased(self, job_id, lease_token):
            assert job_id == 81

    class Queue:
        def __init__(self) -> None:
            self.claimed = False

        def claim_next(self, worker_id, lease_for, accepted_job_types):
            assert accepted_job_types == (JobType.DELETE_TASK,)
            if self.claimed:
                return None
            self.claimed = True
            return _job(JobType.DELETE_TASK)

        def heartbeat(self, *args):
            raise AssertionError("fast handler must stop before heartbeat")

        def finish_leased(self, *args, **kwargs):
            raise AssertionError("atomic archive handler must not finish twice")

    result = DatabaseJobWorker(
        Queue(),  # type: ignore[arg-type]
        {JobType.DELETE_TASK: LogicalArchiveHandler(Lifecycle())},  # type: ignore[arg-type]
        worker_id="worker-a",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    ).run_once()

    assert result is not None
    assert result.status == JobStatus.SUCCESS


def test_export_handler_atomic_success_is_not_finished_a_second_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    class Registry:
        def get_released(self, release_id: int):
            return _release()

    class Lifecycle:
        def worker_context(self, job_id, lease_token, action_type):
            return _context(action_type, source)

        def record_export_artifacts(self, *args, **kwargs):
            return ()

    class Runner:
        def run_release(self, **kwargs):
            output_root = Path(kwargs["output_root"])
            output_root.mkdir(parents=True)
            output = output_root / "latest.xlsx"
            payload = b"latest-export"
            output.write_bytes(payload)
            return ExistingCleanerRunResult(
                test_stage="FT",
                factory="RIYUEXIN",
                output_root=str(output_root),
                artifacts=(
                    CleanerArtifact(
                        "EXPORT",
                        str(output),
                        len(payload),
                        hashlib.sha256(payload).hexdigest(),
                    ),
                ),
                stdout_tail="ok",
            )

    class Queue:
        def claim_next(self, worker_id, lease_for, accepted_job_types):
            assert accepted_job_types == (JobType.EXPORT_LATEST,)
            return _job(JobType.EXPORT_LATEST)

        def heartbeat(self, *args):
            raise AssertionError("fast handler must stop before heartbeat")

        def finish_leased(self, *args, **kwargs):
            raise AssertionError("atomic export handler must not finish twice")

    policy = ManagedJobPathPolicy((tmp_path / "work").absolute())
    handler = ExportLatestHandler(
        Registry(),  # type: ignore[arg-type]
        Lifecycle(),  # type: ignore[arg-type]
        policy,
        runner=Runner(),  # type: ignore[arg-type]
    )
    result = DatabaseJobWorker(
        Queue(),  # type: ignore[arg-type]
        {JobType.EXPORT_LATEST: handler},
        worker_id="worker-a",
        lease_for=timedelta(seconds=2),
        heartbeat_every=timedelta(seconds=1),
    ).run_once()

    assert result is not None
    assert result.status == JobStatus.SUCCESS
