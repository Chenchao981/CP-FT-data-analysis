from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.errors import DomainError
from app.domain.cleaner_registry import CleanerRegistry
from app.domain.jobs import Job, JobStatus, JobType, WorkerJobQueue
from app.domain.quick_analysis import QuickAnalysisService
from app.infrastructure.cp_csv_triplet_writer import (
    CP_MULTI_LOT_SPEC_BINDING_REQUIRED,
    CpCsvTripletWriter,
    CpMultiLotSpecBindingRequired,
)
from app.infrastructure.direct_path_source import build_direct_path_manifest
from app.infrastructure.existing_cleaner_results import (
    summarize_existing_cleaner_result,
)
from app.infrastructure.existing_cleaner_runner import (
    CleanerInputRequired,
    ExistingCleanerRunner,
)
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.quick_pat_runner import QuickPatRunner
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_stage_data_service import SqlStageDataService

logger = logging.getLogger(__name__)
JobHandler = Callable[[Job], Job | None]


class RetryableInitialImportFinalizeError(RuntimeError):
    """A Finalizer infrastructure failure that must preserve a STAGED import."""


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
            or os.getenv("TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace")
        )

    def __call__(self, job: Job) -> None:
        if job.analysis_session_id is None or job.cleaner_release_id is None:
            raise RuntimeError(
                "QUICK_PAT requires analysis_session_id and cleaner_release_id"
            )
        session = self._quick_analysis.worker_session_info(job.analysis_session_id)
        try:
            self._quick_analysis.mark_running(session.analysis_session_id)
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
            is_direct_path = session.source_root_code == "LOCAL_AGENT"
            if is_direct_path:
                if (
                    session.access_scope != "PERSONAL"
                    or session.data_domain_id is not None
                    or session.data_domain_code is not None
                ):
                    raise DomainError(
                        "QUICK_DIRECT_PATH_BINDING_INVALID",
                        "本机目录快速分析必须属于发起人个人数据",
                        409,
                    )
                source, current_manifest = build_direct_path_manifest(
                    session.source_relative_path
                )
            else:
                root = self._source_catalog.require_scope(
                    session.source_root_code,
                    purpose="QUICK_ANALYSIS",
                    test_stage=session.test_stage,
                    factory_code=session.factory_code,
                )
                if (
                    session.access_scope != "DOMAIN"
                    or session.data_domain_id is None
                    or not session.data_domain_code
                    or (root.data_domain_code or "").strip().upper()
                    != session.data_domain_code.strip().upper()
                ):
                    raise DomainError(
                        "QUICK_SOURCE_DOMAIN_BINDING_CHANGED",
                        "Quick PAT 数据源的数据域绑定已变化，任务已停止",
                        409,
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
                self._work_root / str(job.job_id) / f"attempt-{job.attempt_count}"
            )
            result = self._runner.run_release(
                release=release,
                input_directory=source,
                output_root=output_root,
                source_manifest_json=session.source_manifest_json,
                source_manifest_sha256=session.source_manifest_sha256,
            )
            if is_direct_path:
                _completed_source, completed_manifest = build_direct_path_manifest(
                    session.source_relative_path
                )
                binding_changed = False
            else:
                completed_manifest = self._source_catalog.build_manifest(
                    session.source_root_code, session.source_relative_path
                )
                completed_root = self._source_catalog.require_scope(
                    session.source_root_code,
                    purpose="QUICK_ANALYSIS",
                    test_stage=session.test_stage,
                    factory_code=session.factory_code,
                )
                binding_changed = (
                    (completed_root.data_domain_code or "").strip().upper()
                    != session.data_domain_code.strip().upper()
                )
            if (
                completed_manifest.sha256 != session.source_manifest_sha256
                or completed_manifest.as_json() != session.source_manifest_json
                or binding_changed
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
        except DomainError as exc:
            self._quick_analysis.mark_failed(
                session.analysis_session_id, exc.code, exc.message
            )
            raise
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
        finalizer=None,
    ) -> None:
        self._registry = registry
        self._stage_data = stage_data
        self._cp_writer = cp_writer
        self._ft_writer = ft_writer
        self._runner = runner or ExistingCleanerRunner()
        self._finalizer = finalizer
        self._work_root = Path(
            work_root or os.getenv("TMS_WORK_ROOT", r"F:\CP-FT数据分析\data\work")
        )

    def __call__(self, job: Job) -> Job:
        if job.import_batch_id is None:
            raise RuntimeError("INITIAL_IMPORT requires import_batch_id")
        if job.cleaner_release_id is None:
            raise RuntimeError("INITIAL_IMPORT requires cleaner_release_id")
        if not job.lease_token:
            raise RuntimeError("INITIAL_IMPORT requires an active Worker lease")
        if job.finalize_protocol != "ATOMIC_V1":
            raise RuntimeError("INITIAL_IMPORT requires finalize_protocol=ATOMIC_V1")
        if self._finalizer is None:
            raise RuntimeError("INITIAL_IMPORT Atomic Finalizer is not configured")
        try:
            staged = self._finalizer.finalize_staged_initial_import_if_present(
                job_id=job.job_id,
                lease_token=job.lease_token,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise RetryableInitialImportFinalizeError(
                "STAGED Initial Import recovery probe failed; preserve the Job for retry"
            ) from exc
        if staged is not None:
            return staged
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
            "日月新": "RIYUEXIN",
            "ASE": "RIYUEGUANG",
            "日月光": "RIYUEGUANG",
            "电基": "DIANJI",
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
        lot_overrides = {
            Path(item.original_file_name).name: item.lot_id_override
            for item in batch.files
            if item.lot_id_override
        }
        expected_sha256 = tuple(item.expected_sha256 for item in batch.files)
        output_root = self._work_root / str(job.job_id) / f"attempt-{job.attempt_count}"
        self._stage_data.worker_mark_processing(
            job.import_batch_id, job.job_id, job.lease_token
        )
        result = self._runner.run_release(
            release=release,
            inputs=inputs,
            output_root=output_root,
            lot_overrides=lot_overrides if batch.test_stage == "FT" else None,
            expected_sha256=expected_sha256,
        )
        summary = summarize_existing_cleaner_result(result)
        expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24)
        self._stage_data.record_artifacts(
            job.job_id, job.lease_token, result.artifacts, expires
        )
        if batch.test_stage == "CP":
            canonical = self._cp_writer.write(
                job_id=job.job_id,
                import_batch_id=job.import_batch_id,
                lease_token=job.lease_token,
                artifacts=result.artifacts,
                finalize_summary=summary,
            )
        elif batch.test_stage == "FT":
            if self._ft_writer is None:
                raise RuntimeError("FT Canonical Writer is not configured")
            canonical = self._ft_writer.write(
                job_id=job.job_id,
                import_batch_id=job.import_batch_id,
                lease_token=job.lease_token,
                artifacts=result.artifacts,
                finalize_summary=summary,
            )
        else:
            raise RuntimeError(f"unsupported formal test stage: {batch.test_stage}")
        try:
            return self._finalizer.finalize_initial_import(
                job_id=job.job_id,
                lease_token=job.lease_token,
                processing_run_id=canonical.processing_run_id,
                dataset_version_id=canonical.dataset_version_id,
                summary=summary,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise RetryableInitialImportFinalizeError(
                "Initial Import Finalizer failed after STAGED write; preserve the Job for retry"
            ) from exc


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

        def finish_or_release(
            target_status: JobStatus,
            *,
            error_code: str | None = None,
            error_message: str | None = None,
        ) -> Job | None:
            try:
                return self._queue.finish_leased(
                    job.job_id,
                    job.lease_token or "",
                    target_status,
                    error_code=error_code,
                    error_message=error_message,
                )
            except DomainError as exc:
                if exc.code != "JOB_LEASE_LOST":
                    raise
                logger.warning(
                    "Worker terminal write skipped because its lease was lost",
                    extra={"job_id": job.job_id, "target_status": target_status.value},
                )
                return None

        try:
            try:
                handler = self._handlers[job.job_type]
            except KeyError as exc:
                raise RuntimeError(f"no Worker handler for {job.job_type}") from exc
            handled_job = handler(job)
            if handled_job is not None:
                if (
                    handled_job.job_id != job.job_id
                    or handled_job.status != JobStatus.SUCCESS
                ):
                    raise RuntimeError(
                        "Worker handler returned an invalid terminal Job result"
                    )
                stop.set()
                thread.join(timeout=2)
                return handled_job
            if heartbeat_error:
                raise RuntimeError(f"Worker heartbeat failed: {heartbeat_error[-1]}")
            return finish_or_release(JobStatus.SUCCESS)
        except CleanerInputRequired as exc:
            logger.info(
                "Route A job needs user input",
                extra={"job_id": job.job_id, "field_code": exc.field_code},
            )
            try:
                return self._queue.pause_leased_for_input(
                    job.job_id,
                    job.lease_token,
                    field_code=exc.field_code,
                    files=exc.files,
                    message=exc.message,
                )
            except DomainError as pause_error:
                if pause_error.code != "JOB_LEASE_LOST":
                    raise
                logger.warning(
                    "Worker input pause skipped because its lease was lost",
                    extra={"job_id": job.job_id},
                )
                return None
        except CpMultiLotSpecBindingRequired as exc:
            logger.warning(
                "Route A CP job rejected because per-Lot Spec binding is absent",
                extra={"job_id": job.job_id},
            )
            return finish_or_release(
                JobStatus.FAILED,
                error_code=CP_MULTI_LOT_SPEC_BINDING_REQUIRED,
                error_message=str(exc)[-2000:],
            )
        except RetryableInitialImportFinalizeError:
            logger.exception(
                "Atomic Initial Import Finalizer failed; leaving STAGED Job leased for retry",
                extra={"job_id": job.job_id},
            )
            return None
        except Exception as exc:
            logger.exception("Route A job failed", extra={"job_id": job.job_id})
            return finish_or_release(
                JobStatus.FAILED,
                error_code="WORKER_EXECUTION_FAILED",
                error_message=str(exc)[-2000:],
            )
        finally:
            stop.set()
            thread.join(timeout=2)
