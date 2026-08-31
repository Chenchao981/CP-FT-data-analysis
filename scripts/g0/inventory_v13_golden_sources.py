from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "TMS_GOLDEN_SOURCE_INVENTORY_V1"
DEFAULT_HEADER_BYTES = 16 * 1024
MAX_HEADER_BYTES = 64 * 1024
LARGE_FILE_BYTES = 100 * 1024 * 1024

_STAGE_NAMES = {
    "cp": "CP",
    "cp数据": "CP",
    "ft": "FT",
    "ft数据": "FT",
}
_ARCHIVE_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".gz"})
_AUXILIARY_SUFFIXES = frozenset({".bmp", ".doc", ".docx", ".pdf"})
_DERIVED_SEGMENTS = frozenset(
    {
        "output",
        "pat2验证",
        "日月光数据示例",
        "立昂微-管芯数",
        "电基-良率报告",
        "电基元数据和整理好的数据-示例",
        "良率数据分析",
        "输出_统计报告",
    }
)
_HEADER_SIGNATURES = (
    ("HUAHONG_DCP_HEADER", (b"program name", b"lot number")),
    ("POWERTECH_TEXT", (b"powertech test system",)),
    ("STS8203_CSV", (b"sts8203 station",)),
    ("JIEQUN_DTA_CSV", (b"dta file name", b"item", b"serial")),
    ("DP1205_TF_CSV", (b"dp1205",)),
)


class InventoryError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class _FileRecord:
    relative_path: str
    stage: str
    vendor: str
    suffix: str
    size: int
    magic: str
    signatures: tuple[str, ...]
    exclusions: tuple[str, ...]


@dataclass(slots=True)
class _TraversalEvidence:
    directory_count: int = 0
    reparse_entries_skipped: int = 0
    symlink_entries_skipped: int = 0
    hidden_entry_count: int = 0
    max_depth: int = 0
    max_path_length: int = 0
    scan_errors: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.scan_errors is None:
            self.scan_errors = Counter()


def _has_reparse_attribute(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    marker = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & marker)


def _is_hidden(name: str, metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    marker = int(getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2))
    return name.startswith(".") or bool(attributes & marker)


def _stage_and_vendor(parts: tuple[str, ...]) -> tuple[str, str]:
    for index, part in enumerate(parts):
        stage = _STAGE_NAMES.get(part.casefold())
        if stage is not None:
            vendor = parts[index + 1] if index + 1 < len(parts) else "UNCLASSIFIED"
            return stage, vendor
    return "UNCLASSIFIED", "UNCLASSIFIED"


def _magic(header: bytes) -> str:
    if not header:
        return "EMPTY_OR_NOT_READ"
    if header.startswith(bytes.fromhex("D0CF11E0")):
        return "OLE_COMPOUND"
    if header.startswith(bytes.fromhex("504B0304")):
        return "ZIP_CONTAINER"
    if header.startswith(bytes.fromhex("EFBBBF")):
        return "UTF8_BOM_TEXT"
    if header.startswith(bytes.fromhex("FFFE")):
        return "UTF16LE_TEXT"
    if header.startswith(bytes.fromhex("FEFF")):
        return "UTF16BE_TEXT"
    return "TEXT_OR_OTHER"


def _header_signature_codes(header: bytes) -> tuple[str, ...]:
    lowered = header.lower()
    return tuple(
        code
        for code, required_tokens in _HEADER_SIGNATURES
        if all(token in lowered for token in required_tokens)
    )


def _exclusion_reasons(
    parts: tuple[str, ...], *, suffix: str, size: int
) -> tuple[str, ...]:
    reasons: set[str] = set()
    name = parts[-1]
    if name.startswith("~$"):
        reasons.add("OFFICE_LOCK_FILE")
    if size <= 1024:
        reasons.add("TINY_FILE")
    if suffix in _AUXILIARY_SUFFIXES:
        reasons.add("AUXILIARY_DOCUMENT")
    if any(part.casefold() in _DERIVED_SEGMENTS for part in parts[:-1]):
        reasons.add("DERIVED_OR_REPORT_DIRECTORY")
    if not suffix:
        reasons.add("MISSING_EXTENSION")
    stage, _ = _stage_and_vendor(parts)
    if stage == "UNCLASSIFIED":
        reasons.add("UNCLASSIFIED_STAGE")
    return tuple(sorted(reasons))


def _read_header(path: Path, header_bytes: int) -> bytes:
    if header_bytes == 0:
        return b""
    try:
        with path.open("rb") as handle:
            return handle.read(header_bytes)
    except OSError as exc:
        raise InventoryError(
            "HEADER_READ_FAILED", "one or more source headers could not be read"
        ) from exc


def _scan(
    root: Path, *, header_bytes: int
) -> tuple[list[_FileRecord], _TraversalEvidence]:
    evidence = _TraversalEvidence()
    records: list[_FileRecord] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        evidence.directory_count += 1
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator, key=lambda item: (item.name.casefold(), item.name)
                )
        except OSError:
            assert evidence.scan_errors is not None
            evidence.scan_errors["DIRECTORY_READ_FAILED"] += 1
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                assert evidence.scan_errors is not None
                evidence.scan_errors["ENTRY_STAT_FAILED"] += 1
                continue
            relative = path.relative_to(root)
            parts = tuple(relative.parts)
            evidence.max_depth = max(evidence.max_depth, len(parts))
            evidence.max_path_length = max(evidence.max_path_length, len(str(path)))
            if _is_hidden(entry.name, metadata):
                evidence.hidden_entry_count += 1
            is_symlink = entry.is_symlink()
            if is_symlink or _has_reparse_attribute(metadata):
                evidence.reparse_entries_skipped += 1
                if is_symlink:
                    evidence.symlink_entries_skipped += 1
                continue
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                assert evidence.scan_errors is not None
                evidence.scan_errors["NON_REGULAR_ENTRY_SKIPPED"] += 1
                continue
            suffix = path.suffix.casefold()
            try:
                header = _read_header(path, header_bytes)
            except InventoryError as exc:
                assert evidence.scan_errors is not None
                evidence.scan_errors[exc.code] += 1
                header = b""
            stage, vendor = _stage_and_vendor(parts)
            records.append(
                _FileRecord(
                    relative_path=relative.as_posix(),
                    stage=stage,
                    vendor=vendor,
                    suffix=suffix or "<no_ext>",
                    size=int(metadata.st_size),
                    magic=_magic(header),
                    signatures=_header_signature_codes(header),
                    exclusions=_exclusion_reasons(
                        parts, suffix=suffix, size=int(metadata.st_size)
                    ),
                )
            )
    return sorted(records, key=lambda item: item.relative_path.casefold()), evidence


def _counter_rows(counter: Counter[str]) -> list[dict[str, int | str]]:
    return [
        {"code": code, "file_count": int(count)}
        for code, count in sorted(counter.items())
    ]


def _inventory_digest(records: Sequence[_FileRecord]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(record.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record.size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def inventory_source_root(
    raw_root: str | os.PathLike[str], *, header_bytes: int = DEFAULT_HEADER_BYTES
) -> dict[str, Any]:
    if header_bytes < 0 or header_bytes > MAX_HEADER_BYTES:
        raise InventoryError(
            "HEADER_BYTES_INVALID",
            f"header-bytes must be between 0 and {MAX_HEADER_BYTES}",
        )
    root = Path(os.path.abspath(os.fspath(raw_root)))
    try:
        root_metadata = root.lstat()
    except FileNotFoundError as exc:
        raise InventoryError(
            "ROOT_NOT_FOUND", "the explicit source root was not found"
        ) from exc
    except OSError as exc:
        raise InventoryError(
            "ROOT_STAT_FAILED", "the explicit source root could not be inspected"
        ) from exc
    if root.is_symlink() or _has_reparse_attribute(root_metadata):
        raise InventoryError(
            "ROOT_REPARSE_POINT",
            "the explicit source root may not be a link or reparse point",
        )
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise InventoryError(
            "ROOT_NOT_DIRECTORY", "the explicit source root is not a directory"
        )

    records, traversal = _scan(root, header_bytes=header_bytes)
    extension_counts: Counter[str] = Counter()
    extension_bytes: Counter[str] = Counter()
    magic_counts: Counter[str] = Counter()
    signature_counts: Counter[str] = Counter()
    exclusion_counts: Counter[str] = Counter()
    stage_vendor: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "file_count": 0,
            "total_bytes": 0,
            "inventory_candidate_file_count": 0,
            "excluded_file_count": 0,
            "extensions": Counter(),
            "header_signatures": Counter(),
        }
    )
    for record in records:
        extension_counts[record.suffix] += 1
        extension_bytes[record.suffix] += record.size
        magic_counts[record.magic] += 1
        signature_counts.update(record.signatures)
        exclusion_counts.update(record.exclusions)
        aggregate = stage_vendor[(record.stage, record.vendor)]
        aggregate["file_count"] += 1
        aggregate["total_bytes"] += record.size
        aggregate["extensions"][record.suffix] += 1
        aggregate["header_signatures"].update(record.signatures)
        if record.exclusions:
            aggregate["excluded_file_count"] += 1
        else:
            aggregate["inventory_candidate_file_count"] += 1

    groups: list[dict[str, Any]] = []
    for (stage, vendor), aggregate in sorted(stage_vendor.items()):
        groups.append(
            {
                "stage": stage,
                "vendor_directory": vendor,
                "file_count": int(aggregate["file_count"]),
                "total_bytes": int(aggregate["total_bytes"]),
                "inventory_candidate_file_count": int(
                    aggregate["inventory_candidate_file_count"]
                ),
                "excluded_file_count": int(aggregate["excluded_file_count"]),
                "extensions": _counter_rows(aggregate["extensions"]),
                "header_signatures": _counter_rows(aggregate["header_signatures"]),
            }
        )

    scan_errors = traversal.scan_errors or Counter()
    total_bytes = sum(record.size for record in records)
    excluded_files = sum(bool(record.exclusions) for record in records)
    warnings: list[str] = []
    if exclusion_counts:
        warnings.append("INVENTORY_EXCLUSIONS_PRESENT")
    if any(record.suffix in _ARCHIVE_SUFFIXES for record in records):
        warnings.append("ARCHIVES_NOT_EXTRACTED_OR_CONTENT_VALIDATED")
    if traversal.reparse_entries_skipped:
        warnings.append("REPARSE_ENTRIES_SKIPPED")
    if scan_errors:
        warnings.append("SCAN_PARTIAL")
    if any(record.stage == "UNCLASSIFIED" for record in records):
        warnings.append("UNCLASSIFIED_STAGE_PRESENT")

    normalized_root = os.path.normcase(os.path.abspath(root))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "PARTIAL" if scan_errors else "PASS",
        "root": {
            "name": root.name,
            "path_sha256": hashlib.sha256(normalized_root.encode("utf-8")).hexdigest(),
            "absolute_path_emitted": False,
        },
        "read_policy": {
            "explicit_root_required": True,
            "write_source_tree": False,
            "follow_links_or_reparse_points": False,
            "extract_archives": False,
            "content_hash_files": False,
            "header_bytes_read_per_file": header_bytes,
            "emit_individual_paths_or_values": False,
        },
        "totals": {
            "directory_count": traversal.directory_count,
            "file_count": len(records),
            "total_bytes": total_bytes,
            "inventory_candidate_file_count": len(records) - excluded_files,
            "excluded_file_count": excluded_files,
            "large_file_threshold_bytes": LARGE_FILE_BYTES,
            "large_file_count": sum(
                record.size >= LARGE_FILE_BYTES for record in records
            ),
            "largest_file_bytes": max((record.size for record in records), default=0),
            "max_depth": traversal.max_depth,
            "max_path_length": traversal.max_path_length,
        },
        "groups": groups,
        "extensions": [
            {
                "extension": extension,
                "file_count": int(extension_counts[extension]),
                "total_bytes": int(extension_bytes[extension]),
            }
            for extension in sorted(extension_counts)
        ],
        "magic": _counter_rows(magic_counts),
        "header_signatures": _counter_rows(signature_counts),
        "exclusions": _counter_rows(exclusion_counts),
        "traversal": {
            "reparse_entries_skipped": traversal.reparse_entries_skipped,
            "symlink_entries_skipped": traversal.symlink_entries_skipped,
            "hidden_entry_count": traversal.hidden_entry_count,
            "scan_error_count": int(sum(scan_errors.values())),
            "scan_errors": _counter_rows(scan_errors),
        },
        "inventory_manifest": {
            "digest_scope": "RELATIVE_PATH_SIZE_V1",
            "sha256": _inventory_digest(records),
            "is_source_content_sha256": False,
        },
        "warnings": warnings,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only, aggregate inventory for v1.3 Golden source candidates; "
            "does not extract archives, follow links, or emit raw values"
        )
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Explicit CP/FT source root; no default source location is assumed",
    )
    parser.add_argument(
        "--header-bytes",
        type=int,
        default=DEFAULT_HEADER_BYTES,
        help=f"Bytes read from each file for magic/signature aggregation (0-{MAX_HEADER_BYTES})",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = inventory_source_root(args.root, header_bytes=args.header_bytes)
    except InventoryError as exc:
        result = {
            "contract_version": CONTRACT_VERSION,
            "status": "FAIL",
            "error": {"code": exc.code, "message": exc.safe_message},
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
