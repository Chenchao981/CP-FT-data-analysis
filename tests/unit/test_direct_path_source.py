from __future__ import annotations

from pathlib import Path

from app.infrastructure.direct_path_source import build_direct_path_manifest


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
