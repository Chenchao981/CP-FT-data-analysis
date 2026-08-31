from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.analytics_export_files import (
    AnalyticsExportPathPolicy,
    UnsafeAnalyticsExportPath,
)


@dataclass(frozen=True, slots=True)
class AnalyticsExportCleanupFileOutcome:
    physical_status: str
    discovered_file_count: int
    discovered_bytes: int
    removed_job_root: bool


class AnalyticsExportFileCleaner:
    """Delete one exact export Job root after fail-closed direct-child checks."""

    def __init__(self, policy: AnalyticsExportPathPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> AnalyticsExportPathPolicy:
        return self._policy

    def cleanup_job(
        self,
        export_job_id: int,
        artifact_paths: tuple[str, ...],
        *,
        dry_run: bool = False,
    ) -> AnalyticsExportCleanupFileOutcome:
        root = self._policy.job_root(export_job_id)
        for artifact_path in artifact_paths:
            self._policy.require_artifact(
                export_job_id,
                artifact_path,
                must_exist=False,
            )
        if not os.path.lexists(root):
            return AnalyticsExportCleanupFileOutcome("MISSING", 0, 0, False)
        self._policy._reject_existing_links(root)
        if not root.is_dir() or self._policy._is_link_or_reparse(root):
            raise UnsafeAnalyticsExportPath(
                "managed analytics export Job root is unsafe"
            )

        entries: list[Path] = []
        discovered_bytes = 0
        with os.scandir(root) as iterator:
            for entry in iterator:
                path = root / entry.name
                if self._policy._is_link_or_reparse(path):
                    raise UnsafeAnalyticsExportPath(
                        "managed analytics export Job root contains a link "
                        "or reparse point"
                    )
                if not entry.is_file(follow_symlinks=False):
                    raise UnsafeAnalyticsExportPath(
                        "managed analytics export Job root contains a non-file entry"
                    )
                self._policy.require_artifact(
                    export_job_id,
                    path,
                    must_exist=True,
                )
                discovered_bytes += entry.stat(follow_symlinks=False).st_size
                entries.append(path)

        if not dry_run:
            for path in entries:
                self._policy.require_artifact(
                    export_job_id,
                    path,
                    must_exist=True,
                ).unlink()
            root.rmdir()
        return AnalyticsExportCleanupFileOutcome(
            "PRESENT" if dry_run else "DELETED",
            len(entries),
            discovered_bytes,
            not dry_run,
        )
