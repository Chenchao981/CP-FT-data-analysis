from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.errors import DomainError
from app.domain.cleaner_registry import CleanerRegistry
from app.domain.jobs import Job, JobStatus, JobType
from app.domain.lifecycle import LifecycleService, TemporaryArtifactInput
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner
from app.infrastructure.formal_artifact_files import (
    FormalArtifactFileCleaner,
    ManagedJobPathPolicy,
    UnsafeFormalArtifactPath,
)

logger = logging.getLogger(__name__)


class ExportLatestHandler:
    """Rerun a released Cleaner without writing Canonical or Dataset state."""

    def __init__(
        self,
        registry: CleanerRegistry,
        lifecycle: LifecycleService,
        path_policy: ManagedJobPathPolicy,
        *,
        runner: ExistingCleanerRunner | None = None,
        artifact_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if artifact_ttl <= timedelta(0) or artifact_ttl > timedelta(days=30):
            raise ValueError("artifact_ttl must be between 0 and 30 days")
        self._registry = registry
        self._lifecycle = lifecycle
        self._path_policy = path_policy
        self._runner = runner or ExistingCleanerRunner()
        self._artifact_ttl = artifact_ttl

    def __call__(self, job: Job) -> Job:
        if job.job_type != JobType.EXPORT_LATEST:
            raise RuntimeError("ExportLatestHandler received another Job type")
        if job.import_batch_id is None or job.cleaner_release_id is None:
            raise RuntimeError("EXPORT_LATEST requires input batch and Cleaner Release")
        if not job.lease_token:
            raise RuntimeError("EXPORT_LATEST requires an active Worker lease")
        context = self._lifecycle.worker_context(
            job.job_id, job.lease_token, JobType.EXPORT_LATEST.value
        )
        if context.import_batch_id != job.import_batch_id:
            raise DomainError(
                "LIFECYCLE_JOB_SCOPE_MISMATCH",
                "Export Job 与 Lifecycle 目标 Input Batch 不一致",
                409,
            )
        release = self._registry.get_released(job.cleaner_release_id)
        if (
            release.test_stage != context.test_stage
            or release.factory_code.strip().upper()
            != context.factory_code.strip().upper()
        ):
            raise DomainError(
                "CLEANER_RELEASE_SCOPE_MISMATCH",
                "Export Cleaner Release 与 Current Dataset 来源不一致",
                409,
            )
        output_root = (
            self._path_policy.job_root(job.job_id)
            / f"attempt-{job.attempt_count}"
        )
        result = None
        completed = False
        try:
            # Keep this as a short transaction immediately before handing source
            # paths to the released Cleaner.  Revocation after this boundary may
            # allow an already-started trusted process to finish computing, but
            # record_export_artifacts rechecks again before any success delivery.
            execution_context = self._lifecycle.worker_context(
                job.job_id, job.lease_token, JobType.EXPORT_LATEST.value
            )
            if (
                execution_context.dataset_id != context.dataset_id
                or execution_context.dataset_version_id != context.dataset_version_id
                or execution_context.import_batch_id != context.import_batch_id
                or execution_context.requested_by_user_id
                != context.requested_by_user_id
            ):
                raise DomainError(
                    "LIFECYCLE_JOB_SCOPE_MISMATCH",
                    "Export Job authorization context changed before Cleaner execution",
                    409,
                )
            context = execution_context
            inputs = [Path(item.storage_uri) for item in context.files]
            lot_overrides = {
                Path(item.original_file_name).name: item.lot_id_override
                for item in context.files
                if item.lot_id_override
            }
            result = self._runner.run_release(
                release=release,
                inputs=inputs,
                output_root=output_root,
                lot_overrides=lot_overrides if context.test_stage == "FT" else None,
                expected_sha256=tuple(
                    item.expected_sha256 for item in context.files
                ),
            )
            self._lifecycle.record_export_artifacts(
                job.job_id,
                job.lease_token,
                tuple(
                    TemporaryArtifactInput(
                        role=item.role,
                        path=item.path,
                        size_bytes=item.size_bytes,
                        sha256=item.sha256,
                    )
                    for item in result.artifacts
                ),
                datetime.now(UTC) + self._artifact_ttl,
            )
            completed = True
        finally:
            if not completed:
                artifact_paths = (
                    tuple(str(item.path) for item in result.artifacts)
                    if result is not None
                    else ()
                )
                try:
                    FormalArtifactFileCleaner(self._path_policy).cleanup_attempt(
                        job.job_id,
                        job.attempt_count,
                        artifact_paths,
                        dry_run=False,
                    )
                except (OSError, UnsafeFormalArtifactPath):
                    logger.exception(
                        "failed to clean rejected Lifecycle Export attempt; "
                        "formal orphan cleanup must retry after terminal retention",
                        extra={
                            "job_id": job.job_id,
                            "attempt_count": job.attempt_count,
                        },
                    )
        return replace(
            job,
            status=JobStatus.SUCCESS,
            finished_at_utc=datetime.now(UTC),
            lease_token=None,
            lease_owner=None,
            lease_expires_at_utc=None,
            heartbeat_at_utc=None,
        )


class LogicalArchiveHandler:
    def __init__(self, lifecycle: LifecycleService) -> None:
        self._lifecycle = lifecycle

    def __call__(self, job: Job) -> Job:
        if job.job_type != JobType.DELETE_TASK or not job.lease_token:
            raise RuntimeError("DELETE_TASK requires an active Worker lease")
        self._lifecycle.archive_dataset_leased(job.job_id, job.lease_token)
        return replace(
            job,
            status=JobStatus.SUCCESS,
            finished_at_utc=datetime.now(UTC),
            lease_token=None,
            lease_owner=None,
            lease_expires_at_utc=None,
            heartbeat_at_utc=None,
        )
