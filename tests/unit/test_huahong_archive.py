from __future__ import annotations

import io
import zipfile
from pathlib import Path

import py7zr
import pytest

from app.cleaners.huahong_archive import (
    ArchiveLimits,
    HuaHongArchiveError,
    prepare_huahong_input,
)
from tests.unit.test_huahong_dcp import source_text


def _zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_zip_is_extracted_to_temporary_root_and_cleaned(tmp_path: Path) -> None:
    archive = tmp_path / "NCEPRODUCT_FA00-0001.zip"
    _zip(archive, {"lot/FA00-0001-000A-260820@203_001.TXT": source_text()})

    with prepare_huahong_input(archive) as prepared:
        extraction_root = prepared.root
        assert prepared.txt_files[0].read_text(encoding="utf-8") == source_text()
        assert extraction_root.exists()

    assert not extraction_root.exists()


def test_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    _zip(archive, {"../escape.TXT": source_text()})
    with pytest.raises(HuaHongArchiveError, match="unsafe archive member path"):
        with prepare_huahong_input(archive):
            pass


def test_zip_rejects_encrypted_flag(tmp_path: Path) -> None:
    archive = tmp_path / "encrypted.zip"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as output:
        output.writestr("data.TXT", source_text())
    raw = bytearray(payload.getvalue())
    # Set the encryption flag in both the local and central directory headers.
    for signature in (b"PK\x03\x04", b"PK\x01\x02"):
        offset = raw.index(signature)
        flag_offset = offset + (6 if signature == b"PK\x03\x04" else 8)
        raw[flag_offset : flag_offset + 2] = (1).to_bytes(2, "little")
    archive.write_bytes(raw)
    with pytest.raises(HuaHongArchiveError, match="encrypted ZIP"):
        with prepare_huahong_input(archive):
            pass


def test_archive_limits_are_enforced(tmp_path: Path) -> None:
    archive = tmp_path / "large.zip"
    _zip(archive, {"data.TXT": source_text()})
    with pytest.raises(HuaHongArchiveError, match="allowed size"):
        with prepare_huahong_input(archive, limits=ArchiveLimits(max_member_bytes=10)):
            pass


def test_archive_without_txt_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    _zip(archive, {"readme.md": "not data"})
    with pytest.raises(HuaHongArchiveError, match="no HuaHong TXT"):
        with prepare_huahong_input(archive):
            pass


def test_7z_is_supported(tmp_path: Path) -> None:
    source = tmp_path / "FA00-0001-000A-260820@203_001.TXT"
    source.write_text(source_text(), encoding="utf-8")
    archive = tmp_path / "NCEPRODUCT_FA00-0001.7z"
    with py7zr.SevenZipFile(archive, "w") as output:
        output.write(source, arcname=f"lot/{source.name}")

    with prepare_huahong_input(archive) as prepared:
        assert len(prepared.txt_files) == 1
        assert prepared.txt_files[0].name == source.name
