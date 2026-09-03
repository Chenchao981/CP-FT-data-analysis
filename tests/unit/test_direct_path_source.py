from __future__ import annotations

from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.infrastructure.direct_path_source import (
    browse_direct_path,
    build_direct_path_manifest,
)


def _write(path: Path, content: bytes = b"raw") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_riyuexin_manifest_matches_typed_raw_directories(tmp_path: Path) -> None:
    source = tmp_path / "PRODUCT_RX_PAT"
    for raw_type in ("DC", "DVDS", "Rg"):
        _write(source / raw_type / f"{raw_type}.xlsx")
    _write(source / "historical_PAT.xlsx", b"derived-report")
    _write(source / "nested" / "unrelated.xlsx")

    _, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=(".xlsx",),
        path_policy="RIYUEXIN_RAW_DIRECTORY_V1",
    )

    assert manifest.file_count == 3
    assert {item.relative_path for item in manifest.files} == {
        "DC/DC.xlsx",
        "DVDS/DVDS.xlsx",
        "Rg/Rg.xlsx",
    }


def test_riyuexin_single_type_manifest_uses_only_direct_files(tmp_path: Path) -> None:
    source = tmp_path / "DC"
    _write(source / "one.xlsx")
    _write(source / "nested" / "two.xlsx")

    _, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=(".xlsx",),
        path_policy="RIYUEXIN_RAW_DIRECTORY_V1",
    )

    assert [item.relative_path for item in manifest.files] == ["one.xlsx"]


def test_riyueguang_manifest_uses_only_dc_dvds_rg_xlsx(tmp_path: Path) -> None:
    source = tmp_path / "RIYUEGUANG"
    for raw_type in ("DC", "DVDS", "RG"):
        _write(source / raw_type / f"{raw_type}.xlsx")
    _write(source / "HTDC" / "high-temp.xlsx")
    _write(source / "TF" / "switching.xlsx")

    _, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=(".xlsx",),
        path_policy="RIYUEGUANG_RAW_DIRECTORY_V1",
    )

    assert {item.relative_path for item in manifest.files} == {
        "DC/DC.xlsx",
        "DVDS/DVDS.xlsx",
        "RG/RG.xlsx",
    }


def test_dianji_manifest_excludes_prior_output_and_pat_runs(tmp_path: Path) -> None:
    source = tmp_path / "dianji"
    _write(source / "raw.xls")
    _write(source / "nested" / "raw.csv")
    _write(source / "output" / "cleaned.xlsx")
    _write(source / "PAT_001" / "PAT_001.xlsx")

    _, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=(".xls", ".xlsx", ".csv"),
        path_policy="DIANJI_RAW_DIRECTORY_V1",
    )

    assert {item.relative_path for item in manifest.files} == {
        "raw.xls",
        "nested/raw.csv",
    }


def test_direct_path_manifest_accepts_one_supported_source_file(tmp_path: Path) -> None:
    source = tmp_path / "one.csv"
    _write(source, b"value\n1\n")

    resolved, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=(".csv",),
    )

    assert resolved == source.resolve()
    assert manifest.source_label == "one.csv"
    assert manifest.file_count == 1
    assert manifest.files[0].relative_path == "one.csv"


def test_direct_path_browser_lists_folders_source_files_and_archives(tmp_path: Path) -> None:
    (tmp_path / "lot-a").mkdir()
    _write(tmp_path / "wafer.xlsx", b"xlsx")
    _write(tmp_path / "lot.zip", b"zip")
    _write(tmp_path / "ignored.txt", b"txt")

    listing = browse_direct_path(
        tmp_path,
        allowed_suffixes=(".xlsx", ".zip"),
    )

    assert listing["path"] == str(tmp_path.resolve())
    assert listing["parent_path"] == str(tmp_path.parent.resolve())
    assert [item["name"] for item in listing["items"]] == [
        "lot-a",
        "lot.zip",
        "wafer.xlsx",
    ]
    assert listing["items"][1]["is_archive"] is True


def test_direct_path_browser_marks_identity_dependent_file_as_folder_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wafer.txt"
    _write(source, b"raw")

    listing = browse_direct_path(
        tmp_path,
        allowed_suffixes=(".txt", ".zip"),
        selectable_file_suffixes=(".zip",),
    )

    assert listing["items"][0]["selectable"] is False
    assert listing["items"][0]["selection_hint"] == "请选择该源文件所在的文件夹"


def test_direct_path_manifest_rejects_identity_dependent_single_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "wafer.txt"
    _write(source, b"raw")

    with pytest.raises(DomainError) as captured:
        build_direct_path_manifest(
            source,
            allowed_suffixes=(".txt", ".zip"),
            allowed_single_file_suffixes=(".zip",),
        )

    assert captured.value.code == "DIRECT_PATH_FILE_REQUIRES_DIRECTORY"


def test_archive_is_selectable_without_becoming_a_directory_member(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "one.csv"
    archive = tmp_path / "raw.zip"
    _write(raw, b"raw")
    _write(archive, b"zip")

    _, directory_manifest = build_direct_path_manifest(
        tmp_path,
        allowed_suffixes=(".csv",),
        allowed_single_file_suffixes=(".csv", ".zip", ".7z"),
    )
    _, archive_manifest = build_direct_path_manifest(
        archive,
        allowed_suffixes=(".csv",),
        allowed_single_file_suffixes=(".csv", ".zip", ".7z"),
    )

    assert [item.relative_path for item in directory_manifest.files] == ["one.csv"]
    assert [item.relative_path for item in archive_manifest.files] == ["raw.zip"]
