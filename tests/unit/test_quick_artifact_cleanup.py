from __future__ import annotations

from pathlib import Path

import pytest
from app.infrastructure.quick_artifact_cleanup import (
    QuickArtifactFileCleaner,
    UnsafeCleanupTarget,
)


def test_cleanup_deletes_only_exact_job_directory(tmp_path: Path) -> None:
    work_root = tmp_path / "workspace"
    job_root = work_root / "42" / "attempt-1"
    job_root.mkdir(parents=True)
    report = job_root / "PAT.xlsx"
    report.write_bytes(b"pat-result")
    sibling = work_root / "keep.txt"
    sibling.write_text("keep", encoding="utf-8")

    cleaner = QuickArtifactFileCleaner(work_root)
    preview = cleaner.cleanup_job(42, (str(report),), dry_run=True)
    assert preview.discovered_file_count == 1
    assert preview.discovered_bytes == len(b"pat-result")
    assert report.is_file()

    result = cleaner.cleanup_job(42, (str(report),))
    assert result.physical_status == "DELETED"
    assert not (work_root / "42").exists()
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_cleanup_blocks_artifact_path_outside_job_root(tmp_path: Path) -> None:
    work_root = tmp_path / "workspace"
    (work_root / "42").mkdir(parents=True)
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"outside")

    with pytest.raises(UnsafeCleanupTarget):
        QuickArtifactFileCleaner(work_root).cleanup_job(42, (str(outside),))
    assert outside.is_file()
    assert (work_root / "42").is_dir()


def test_cleanup_treats_absent_job_root_as_already_missing(tmp_path: Path) -> None:
    result = QuickArtifactFileCleaner(tmp_path / "workspace").cleanup_job(
        7, (str(tmp_path / "workspace" / "7" / "PAT.xlsx"),)
    )
    assert result.physical_status == "MISSING"
    assert result.removed_job_root is False
