from __future__ import annotations

from pathlib import Path

import pytest

from scripts.g0.bootstrap_existing_cleaner_releases import (
    _definitions,
    _parse_args,
    _sha256,
    _snapshot_package,
)


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


def test_bootstrap_requires_explicit_factory_selection() -> None:
    with pytest.raises(SystemExit):
        _parse_args([])

    selected = _parse_args(["--factory", "DIANJI"])
    assert selected.factory == ["DIANJI"]
    assert selected.all is False

    repeated = _parse_args(
        ["--factory", "RIYUEXIN", "--factory", "DIANJI"]
    )
    assert repeated.factory == ["RIYUEXIN", "DIANJI"]

    with pytest.raises(SystemExit):
        _parse_args(["--all", "--factory", "DIANJI"])


def test_bootstrap_defines_dianji_as_an_independent_powertech_release() -> None:
    dianji = next(item for item in _definitions() if item.factory == "DIANJI")

    assert dianji.stage == "FT"
    assert dianji.format_code == "DIANJI_POWERTECH_DYNAMIC_EXISTING"
    assert dianji.cleaner_code == "DIANJI_FT_POWERTECH_EXISTING"
    assert dianji.adapter_code == "DIANJI_FT_PYZ"
    assert dianji.input_contract == "DIANJI_POWERTECH_DIRECTORY_V1"
    assert dianji.output_contract == "DIANJI_FT_SCATTER_V1"
    assert dianji.cleaner_version == "v2.19.0"


def test_bootstrap_pins_quick_pat_execution_limits() -> None:
    jiequn = next(
        item
        for item in _definitions()
        if item.cleaner_code == "JIEQUN_FT_QUICK_PAT_EXISTING"
    )

    assert jiequn.timeout_seconds == 7200
    assert jiequn.max_output_bytes == 64 * 1024 * 1024

    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "g0"
        / "bootstrap_existing_cleaner_releases.py"
    ).read_text(encoding="utf-8-sig")
    assert '"timeout_seconds": values["timeout_seconds"]' in script
    assert '"max_output_bytes": values["max_output_bytes"]' in script
    assert "Published Cleaner Release is immutable" in script
    assert "UPDATE ingestion.cleaner_release SET" not in script
