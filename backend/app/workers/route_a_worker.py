from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.cleaner_registry import CleanerRegistry
from app.domain.jobs import Job, JobStatus, JobType, WorkerJobQueue
from app.domain.quick_analysis import QuickAnalysisService
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.existing_cleaner_results import (
    summarize_existing_cleaner_result,
)
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.quick_pat_runner import QuickPatRunner
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_stage_data_service import SqlStageDataService

logger = logging.getLogger(__name__)
JobHandler = Callable[[Job], None]


class QuickPatHandler:
    def __init__(
        self,
        registry: CleanerRegistry,
        quick_analysis: QuickAnalysisService,
        source_catalog: SourceCatalog,
        *,
        runner: QuickPatRunner | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._quick_analysis = quick_analysis
        self._source_catalog = source_catalog
        self._runner = runner or QuickPatRunner()
        self._work_root = Path(
            work_root
            or os.getenv(
                "TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace"
            )
        )

    def __call__(self, job: Job) -> None:
        if job.analysis_session_id is None or job.cleaner_release_id is None:
            raise RuntimeError(
                "QUICK_PAT requires analysis_session_id and cleaner_release_id"
            )
        session = self._quick_analysis.worker_session_info(job.analysis_session_id)
        self._quick_analysis.mark_running(session.analysis_session_id)
        try:
            if datetime.now(UTC) >= session.expires_at_utc:
                raise RuntimeError("Quick PAT session expired before execution")
            if session.analysis_type != "QUICK_PAT":
                raise RuntimeError(
                    f"unsupported Quick Analysis type: {session.analysis_type}"
                )
            if session.cleaner_release_id != job.cleaner_release_id:
                raise RuntimeError("Quick PAT job and session Release do not match")
            release = self._registry.get_released(job.cleaner_release_id)
            if (
                release.test_stage != session.test_stage
                or release.factory_code != session.factory_code
            ):
                raise RuntimeError(
                    "PAT Release does not match Quick Analysis session: "
                    f"{release.test_stage}/{release.factory_code} != "
                    f"{session.test_stage}/{session.factory_code}"
                )
            source = self._source_catalog.resolve_directory(
                session.source_root_code, session.source_relative_path
            )
            current_manifest = self._source_catalog.build_manifest(
                session.source_root_code, session.source_relative_path
            )
            if (
                current_manifest.mode != session.source_manifest_mode
                or current_manifest.sha256 != session.source_manifest_sha256
                or current_manifest.as_json() != session.source_manifest_json
            ):
                raise RuntimeError(
                    "Source directory changed after the Quick PAT session was queued"
                )
            output_root = (
                self._work_root
                / str(job.job_id)
                / f"attempt-{job.attempt_count}"
            )
            result = self._runner.run_release(
                release=release,
                input_directory=source,
                output_root=output_root,
                source_manifest_json=session.source_manifest_json,
                source_manifest_sha256=session.source_manifest_sha256,
            )
            completed_manifest = self._source_catalog.build_manifest(
                session.source_root_code, session.source_relative_path
            )
            if (
                completed_manifest.sha256 != session.source_manifest_sha256
                or completed_manifest.as_json() != session.source_manifest_json
            ):
                raise RuntimeError(
                    "Source directory changed while Quick PAT was running"
                )
            self._quick_analysis.record_success(
                session.analysis_session_id,
                job.job_id,
                parameter_count=result.parameter_count,
                record_count=result.record_count,
                summary=result.summary,
                artifacts=result.artifacts,
            )
        except Exception as exc:
            self._quick_analysis.mark_failed(
                session.analysis_session_id, "QUICK_PAT_FAILED", str(exc)
            )
            raise


class RouteAInitialImportHandler:
    def __init__(
        self,
        registry: SqlCleanerRegistry,
        stage_data: SqlStageDataService,
        cp_writer: CpCsvTripletWriter,
        ft_writer: FtXlsxScatterWriter | None = None,
        runner: ExistingCleanerRunner | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._stage_data = stage_data
        self._cp_writer = cp_writer
        self._ft_writer = ft_writer
        self._runner = runner or ExistingCleanerRunner()
        self._work_root = Path(
            work_root or os.getenv("TMS_WORK_ROOT", r"F:\CP-FT数据分析\data\work")
        )

    def __call__(self, job: Job) -> None:
        if job.import_batch_id is None or job.cleaner_release_id is None:
            raise RuntimeError(
                "INITIAL_IMPORT requires import_batch_id and cleaner_release_id"
            )
        batch = self._stage_data.worker_batch_info(job.import_batch_id)
        if not batch.files:
            raise RuntimeError(f"upload task {job.import_batch_id} has no input files")
        release = self._registry.get_released(job.cleaner_release_id)
        expected_factory = batch.factory_code.strip().upper()
        aliases = {
            "HH": "HUAHONG",
            "华虹": "HUAHONG",
            "JT": "JETECH",
            "捷特": "JETECH",
            "立昂微": "LION",
            "国宇": "GUOYU",
            "国宇FRD": "GUOYU",
            "ASE": "RIYUEXIN",
            "日月新": "RIYUEXIN",
        }
        expected_factory = aliases.get(expected_factory, expected_factory)
        if (
            release.test_stage != batch.test_stage
            or release.factory_code != expected_factory
        ):
            raise RuntimeError(
                "Cleaner Release does not match upload task: "
                f"{release.test_stage}/{release.factory_code} != "
                f"{batch.test_stage}/{expected_factory}"
            )
        inputs = [Path(item.storage_uri) for item in batch.files]
        if batch.test_stage == "FT":
            inputs = [inputs[0].parent]
        output_root = self._work_root / str(job.job_id) / f"attempt-{job.attempt_count}"
        self._stage_data.worker_mark_processing(job.import_batch_id)
        try:
            result = self._runner.run_release(
                release=release,
                inputs=inputs,
                output_root=output_root,
            )
            summary = summarize_existing_cleaner_result(result)
            expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24)
            self._stage_data.record_artifacts(job.job_id, result.artifacts, expires)
            if batch.test_stage == "CP":
                canonical = self._cp_writer.write(
                    job_id=job.job_id,
                    import_batch_id=job.import_batch_id,
                    artifacts=result.artifacts,
                )
                summary.update(
                    {
                        "dataset_id": canonical.dataset_id,
                        "dataset_version_no": canonical.dataset_version_no,
                    }
                )
            elif batch.test_stage == "FT":
                if self._ft_writer is None:
                    raise RuntimeError("FT Canonical Writer is not configured")
                canonical = self._ft_writer.write(
                    job_id=job.job_id,
                    import_batch_id=job.import_batch_id,
                    artifacts=result.artifacts,
                )
                summary.update(
                    {
                        "dataset_id": canonical.dataset_id,
                        "dataset_version_no": canonical.dataset_version_no,
                    }
                )
            self._stage_data.archive_previous_results(job.import_batch_id)
            self._stage_data.record_result(
                job.import_batch_id,
                job.job_id,
                summary,
                finish_job=False,
            )
        except Exception as exc:
            self._stage_data.mark_failed(
                job.import_batch_id,
                job.job_id,
                str(exc),
                finish_job=False,
            )
            raise


class DatabaseJobWorker:
    def __init__(
        self,
        queue: WorkerJobQueue,
        handlers: dict[JobType, JobHandler],
        *,
        worker_id: str,
        lease_for: timedelta = timedelta(minutes=5),
        heartbeat_every: timedelta = timedelta(minutes=1),
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if heartbeat_every >= lease_for:
            raise ValueError("heartbeat interval must be shorter than the lease")
        self._queue = queue
        self._handlers = handlers
        self._worker_id = worker_id.strip()
        self._lease_for = lease_for
        self._heartbeat_every = heartbeat_every

    def run_once(self) -> Job | None:
        if not self._handlers:
            return None
        job = self._queue.claim_next(
            self._worker_id,
            self._lease_for,
            tuple(self._handlers),
        )
        if job is None:
            return None
        if not job.lease_token:
            raise RuntimeError(f"claimed job {job.job_id} has no lease token")
        stop = threading.Event()
        heartbeat_error: list[Exception] = []

        def keep_alive() -> None:
            while not stop.wait(self._heartbeat_every.total_seconds()):
                try:
                    self._queue.heartbeat(
                        job.job_id, job.lease_token or "", self._lease_for
                    )
                except Exception as exc:  # noqa: BLE001 - terminal write enforces lease
                    heartbeat_error.append(exc)
                    stop.set()

        thread = threading.Thread(
            target=keep_alive,
            name=f"tms-job-heartbeat-{job.job_id}",
            daemon=True,
        )
        thread.start()
        try:
            try:
                handler = self._handlers[job.job_type]
            except KeyError as exc:
                raise RuntimeError(f"no Worker handler for {job.job_type}") from exc
            handler(job)
            if heartbeat_error:
                raise RuntimeError(f"Worker heartbeat failed: {heartbeat_error[-1]}")
            return self._queue.finish_leased(
                job.job_id,
                job.lease_token,
                JobStatus.SUCCESS,
            )
        except Exception as exc:
            logger.exception("Route A job failed", extra={"job_id": job.job_id})
            return self._queue.finish_leased(
                job.job_id,
                job.lease_token,
                JobStatus.FAILED,
                error_code="WORKER_EXECUTION_FAILED",
                error_message=str(exc)[-2000:],
            )
        finally:
            stop.set()
            thread.join(timeout=2)
