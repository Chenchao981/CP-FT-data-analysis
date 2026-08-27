from __future__ import annotations

from pathlib import Path

import pytest

from scripts.g0.bootstrap_existing_cleaner_releases import _sha256, _snapshot_package


def test_cleaner_release_snapshot_is_content_addressed_and_reusable(
    tmp_path: Path,
) -> None:
    package = tmp_path / "source" / "cleaner.pyz"
    package.parent.mkdir()
    package.write_bytes(b"release-one")
    snapshot_root = tmp_path / "artifacts" / "cleaner_releases"
    first_checksum = _sha256(package)

    first = _snapshot_package(package, first_checksum, snapshot_root)
    repeated = _snapshot_package(package, first_checksum, snapshot_root)

    assert first == snapshot_root / first_checksum / package.name
    assert repeated == first
    assert first.read_bytes() == b"release-one"

    package.write_bytes(b"release-two")
    second_checksum = _sha256(package)
    second = _snapshot_package(package, second_checksum, snapshot_root)

    assert second != first
    assert second == snapshot_root / second_checksum / package.name
    assert first.read_bytes() == b"release-one"
    assert second.read_bytes() == b"release-two"


def test_cleaner_release_snapshot_rejects_corrupted_existing_content(
    tmp_path: Path,
) -> None:
    package = tmp_path / "cleaner.pyz"
    package.write_bytes(b"approved-release")
    checksum = _sha256(package)
    snapshot_root = tmp_path / "snapshots"
    corrupted = snapshot_root / checksum / package.name
    corrupted.parent.mkdir(parents=True)
    corrupted.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="snapshot checksum mismatch"):
        _snapshot_package(package, checksum, snapshot_root)

    assert corrupted.read_bytes() == b"tampered"
