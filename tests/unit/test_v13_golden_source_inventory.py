from __future__ import annotations

import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.g0 import inventory_v13_golden_sources as inventory


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _rows_by_code(rows: list[dict[str, object]]) -> dict[str, int]:
    return {str(row["code"]): int(row["file_count"]) for row in rows}


def test_inventory_aggregates_without_emitting_paths_or_source_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "controlled_sources"
    _write(
        root / "CP数据" / "CP_VENDOR" / "raw" / "sample_cp.txt",
        b"Program name\tSYNTHETIC_ONLY\nLot Number\tSYNTHETIC_ONLY\n" + b"x" * 2048,
    )
    _write(
        root / "FT数据" / "FT_VENDOR" / "raw" / "sample_ft.xls",
        b"PowerTECH Test System\tSYNTHETIC_ONLY\nN/A\t9999\n" + b"x" * 2048,
    )
    _write(
        root / "FT数据" / "FT_VENDOR" / "raw" / "sample_ft.csv",
        b"STS8203 Station,SYNTHETIC_ONLY\n" + b"x" * 2048,
    )
    _write(
        root / "FT数据" / "FT_VENDOR" / "output" / "derived.xlsx",
        b"PK\x03\x04" + b"x" * 2048,
    )
    _write(
        root / "FT数据" / "FT_VENDOR" / "PaT2验证" / "derived.xlsx",
        b"PK\x03\x04" + b"x" * 2048,
    )
    _write(
        root / "FT数据" / "FT_VENDOR" / "raw" / "~$lock.xlsx",
        b"x" * 165,
    )
    _write(root / "unrelated" / "notes.docx", b"PK\x03\x04" + b"x" * 2048)
    before = _snapshot(root)

    result = inventory.inventory_source_root(root)

    assert result["status"] == "PASS"
    assert result["totals"]["file_count"] == 7
    assert result["totals"]["total_bytes"] == sum(size for size, _ in before.values())
    assert result["totals"]["inventory_candidate_file_count"] == 3
    assert result["root"]["absolute_path_emitted"] is False
    assert result["read_policy"] == {
        "explicit_root_required": True,
        "write_source_tree": False,
        "follow_links_or_reparse_points": False,
        "extract_archives": False,
        "content_hash_files": False,
        "header_bytes_read_per_file": inventory.DEFAULT_HEADER_BYTES,
        "emit_individual_paths_or_values": False,
    }
    signatures = _rows_by_code(result["header_signatures"])
    assert signatures == {
        "HUAHONG_DCP_HEADER": 1,
        "POWERTECH_TEXT": 1,
        "STS8203_CSV": 1,
    }
    exclusions = _rows_by_code(result["exclusions"])
    assert exclusions == {
        "AUXILIARY_DOCUMENT": 1,
        "DERIVED_OR_REPORT_DIRECTORY": 2,
        "OFFICE_LOCK_FILE": 1,
        "TINY_FILE": 1,
        "UNCLASSIFIED_STAGE": 1,
    }
    groups = {(row["stage"], row["vendor_directory"]): row for row in result["groups"]}
    assert groups[("CP", "CP_VENDOR")]["file_count"] == 1
    assert groups[("FT", "FT_VENDOR")]["file_count"] == 5
    assert groups[("UNCLASSIFIED", "UNCLASSIFIED")]["file_count"] == 1
    serialized = json.dumps(result, ensure_ascii=False)
    assert "sample_cp.txt" not in serialized
    assert "sample_ft.xls" not in serialized
    assert "SYNTHETIC_ONLY" not in serialized
    assert str(root) not in serialized
    assert _snapshot(root) == before


def test_inventory_digest_is_deterministic_and_changes_with_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sources"
    source = root / "CP" / "CP_VENDOR" / "raw" / "sample.txt"
    _write(source, b"Program name\nLot Number\n")

    first = inventory.inventory_source_root(root)
    second = inventory.inventory_source_root(root)
    assert first["inventory_manifest"] == second["inventory_manifest"]

    source.write_bytes(source.read_bytes() + b"x")
    changed = inventory.inventory_source_root(root)
    assert (
        changed["inventory_manifest"]["sha256"] != first["inventory_manifest"]["sha256"]
    )
    assert changed["inventory_manifest"]["is_source_content_sha256"] is False


def test_inventory_does_not_extract_archives(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    archive = root / "CP数据" / "CP_VENDOR" / "raw" / "source.zip"
    archive.parent.mkdir(parents=True)
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as handle:
        handle.writestr("inside/private_measurements.txt", "SYNTHETIC_PRIVATE_VALUE")

    result = inventory.inventory_source_root(root)

    assert result["totals"]["file_count"] == 1
    assert result["read_policy"]["extract_archives"] is False
    assert "ARCHIVES_NOT_EXTRACTED_OR_CONTENT_VALIDATED" in result["warnings"]
    serialized = json.dumps(result)
    assert "private_measurements" not in serialized
    assert "SYNTHETIC_PRIVATE_VALUE" not in serialized


def test_inventory_skips_directory_links_without_following(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "outside"
    _write(outside / "must_not_be_seen.csv", b"STS8203 Station\n")
    link = root / "linked_source"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    result = inventory.inventory_source_root(root)

    assert result["totals"]["file_count"] == 0
    assert result["traversal"]["reparse_entries_skipped"] == 1
    assert result["traversal"]["symlink_entries_skipped"] == 1


def test_cli_requires_explicit_root_and_emits_safe_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as missing:
        inventory._parser().parse_args([])
    assert missing.value.code == 2

    root = tmp_path / "sources"
    _write(root / "FT数据" / "FT_VENDOR" / "raw" / "sample.csv", b"Item\nSerial\n")
    assert inventory.main(["--root", os.fspath(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["root"]["name"] == "sources"
    assert payload["root"]["absolute_path_emitted"] is False


@pytest.mark.parametrize("header_bytes", (-1, inventory.MAX_HEADER_BYTES + 1))
def test_inventory_rejects_unsafe_header_scan_bounds(
    tmp_path: Path, header_bytes: int
) -> None:
    with pytest.raises(inventory.InventoryError) as error:
        inventory.inventory_source_root(tmp_path, header_bytes=header_bytes)
    assert error.value.code == "HEADER_BYTES_INVALID"
