from __future__ import annotations

from pathlib import Path

import pytest
from app.infrastructure.formal_artifact_files import (
    FormalArtifactFileCleaner,
    ManagedJobPathPolicy,
    UnsafeFormalArtifactPath,
)


def test_formal_cleanup_defaults_to_preview_and_deletes_only_exact_job_root(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "formal"
    attempt = work_root / "42" / "attempt-1"
    attempt.mkdir(parents=True)
    artifact = attempt / "latest.xlsx"
    artifact.write_bytes(b"latest-data")
    sibling = work_root / "do-not-delete.txt"
    sibling.write_text("retained", encoding="utf-8")
    cleaner = FormalArtifactFileCleaner(ManagedJobPathPolicy(work_root.absolute()))

    preview = cleaner.cleanup_job(42, (str(artifact),), dry_run=True)

    assert preview.physical_status == "PRESENT"
    assert preview.discovered_file_count == 1
    assert artifact.is_file()
    removed = cleaner.cleanup_job(42, (str(artifact),))
    assert removed.physical_status == "DELETED"
    assert not (work_root / "42").exists()
    assert sibling.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize(
    "relative",
    ["../source/upload.xlsx", "42/not-an-attempt/export.xlsx"],
)
def test_formal_cleanup_rejects_escape_and_non_attempt_paths(
    tmp_path: Path, relative: str
) -> None:
    work_root = (tmp_path / "formal").absolute()
    (work_root / "42" / "attempt-1").mkdir(parents=True)
    candidate = work_root / relative

    with pytest.raises(UnsafeFormalArtifactPath):
        FormalArtifactFileCleaner(ManagedJobPathPolicy(work_root)).cleanup_job(
            42, (str(candidate),), dry_run=True
        )


def test_formal_cleanup_rejects_windows_reparse_or_symlink_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_root = (tmp_path / "formal").absolute()
    attempt = work_root / "42" / "attempt-1"
    attempt.mkdir(parents=True)
    artifact = attempt / "export.xlsx"
    artifact.write_bytes(b"payload")
    policy = ManagedJobPathPolicy(work_root)
    original = policy._is_link_or_reparse
    monkeypatch.setattr(
        policy,
        "_is_link_or_reparse",
        lambda path: Path(path) == attempt or original(Path(path)),
    )

    with pytest.raises(UnsafeFormalArtifactPath):
        policy.require_artifact(42, artifact, must_exist=True)
    assert artifact.is_file()


def test_formal_cleanup_script_requires_explicit_delete_switch() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_formal_artifact_cleanup.py"
    ).read_text(encoding="utf-8-sig")

    assert '"--delete"' in source
    assert "dry_run = not args.delete" in source
    assert "TMS_WORK_ROOT" in source
    assert "TMS_QUICK_WORK_ROOT" not in source
