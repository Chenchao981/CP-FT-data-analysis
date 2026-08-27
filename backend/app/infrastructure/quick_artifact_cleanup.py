from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class UnsafeCleanupTarget(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CleanupFileOutcome:
    physical_status: str
    discovered_file_count: int
    discovered_bytes: int
    removed_job_root: bool


class QuickArtifactFileCleaner:
    """Delete only an exact Quick Analysis job directory under the work root."""

    def __init__(self, work_root: str | Path) -> None:
        self._work_root = Path(work_root).resolve()

    @property
    def work_root(self) -> Path:
        return self._work_root

    def cleanup_job(
        self,
        job_id: int,
        artifact_paths: tuple[str, ...],
        *,
        dry_run: bool = False,
    ) -> CleanupFileOutcome:
        if job_id < 1:
            raise UnsafeCleanupTarget("Quick Analysis job id must be positive")
        job_root = (self._work_root / str(job_id)).resolve()
        if job_root.parent != self._work_root or job_root.name != str(job_id):
            raise UnsafeCleanupTarget("Quick Analysis job root is not an exact child")
        self._require_contained(self._work_root, job_root)
        for raw_path in artifact_paths:
            artifact = Path(raw_path).resolve()
            self._require_contained(job_root, artifact)

        if not job_root.exists():
            return CleanupFileOutcome("MISSING", 0, 0, False)
        if not job_root.is_dir() or job_root.is_symlink():
            raise UnsafeCleanupTarget("Quick Analysis job root is not a safe directory")

        discovered_file_count = 0
        discovered_bytes = 0
        for directory, names, files in os.walk(job_root, followlinks=False):
            directory_path = Path(directory)
            for name in (*names, *files):
                entry = directory_path / name
                if entry.is_symlink():
                    raise UnsafeCleanupTarget(
                        "Quick Analysis job directory contains a symbolic link"
                    )
            for name in files:
                entry = directory_path / name
                discovered_file_count += 1
                discovered_bytes += entry.stat().st_size

        if not dry_run:
            shutil.rmtree(job_root)
        return CleanupFileOutcome(
            "PRESENT" if dry_run else "DELETED",
            discovered_file_count,
            discovered_bytes,
            not dry_run,
        )

    @staticmethod
    def _require_contained(root: Path, candidate: Path) -> None:
        normalized_root = os.path.normcase(str(root.resolve()))
        normalized_candidate = os.path.normcase(str(candidate.resolve()))
        try:
            common = os.path.commonpath((normalized_root, normalized_candidate))
        except ValueError as exc:
            raise UnsafeCleanupTarget("cleanup target is on another filesystem") from exc
        if common != normalized_root:
            raise UnsafeCleanupTarget("cleanup target escapes the Quick Analysis job root")
