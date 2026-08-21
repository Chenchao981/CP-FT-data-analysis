from __future__ import annotations

import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

import py7zr


class HuaHongArchiveError(ValueError):
    """Raised when a HuaHong archive cannot be handled safely."""


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int = 10_000
    max_member_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_zip_ratio: float = 200.0


@dataclass(frozen=True, slots=True)
class PreparedHuaHongInput:
    root: Path
    txt_files: tuple[Path, ...]
    container_name: str


def _safe_member_name(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise HuaHongArchiveError(f"unsafe archive member path: {raw_name!r}")
    return path


def _validate_counts(sizes: list[int], limits: ArchiveLimits) -> None:
    if len(sizes) > limits.max_members:
        raise HuaHongArchiveError("archive contains too many members")
    if any(size < 0 or size > limits.max_member_bytes for size in sizes):
        raise HuaHongArchiveError("archive member exceeds the allowed size")
    if sum(sizes) > limits.max_total_bytes:
        raise HuaHongArchiveError("archive expands beyond the allowed total size")


def _ensure_unique(names: list[PurePosixPath]) -> None:
    keys = [str(name).casefold() for name in names]
    if len(keys) != len(set(keys)):
        raise HuaHongArchiveError("archive contains duplicate member paths")


def _ensure_extracted_paths(root: Path, names: list[PurePosixPath]) -> tuple[Path, ...]:
    resolved_root = root.resolve()
    extracted: list[Path] = []
    for name in names:
        target = (root / Path(*name.parts)).resolve()
        if target == resolved_root or resolved_root not in target.parents:
            raise HuaHongArchiveError(f"archive member escaped extraction root: {name}")
        if not target.is_file() or target.is_symlink():
            raise HuaHongArchiveError(f"archive member was not extracted as a regular file: {name}")
        extracted.append(target)
    return tuple(extracted)


def _extract_zip(source: Path, target: Path, limits: ArchiveLimits) -> tuple[Path, ...]:
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if any(info.flag_bits & 0x1 for info in infos):
                raise HuaHongArchiveError("encrypted ZIP archives are not allowed")
            all_names = [_safe_member_name(info.filename) for info in infos]
            _ensure_unique(all_names)
            _validate_counts([info.file_size for info in infos], limits)
            regular = [info for info in infos if not info.is_dir()]
            names = [_safe_member_name(info.filename) for info in regular]
            for info in regular:
                mode = info.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise HuaHongArchiveError("symbolic links are not allowed in ZIP archives")
                if info.file_size and info.compress_size == 0:
                    raise HuaHongArchiveError("ZIP member has an invalid compression size")
                if info.compress_size and info.file_size / info.compress_size > limits.max_zip_ratio:
                    raise HuaHongArchiveError("ZIP member exceeds the allowed compression ratio")
            txt_pairs = [
                (info, name)
                for info, name in zip(regular, names, strict=True)
                if name.suffix.lower() == ".txt"
            ]
            if not txt_pairs:
                raise HuaHongArchiveError("archive contains no HuaHong TXT data files")
            for info, name in txt_pairs:
                destination = target / Path(*name.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_stream, destination.open("xb") as output:
                    while block := source_stream.read(1024 * 1024):
                        output.write(block)
            return _ensure_extracted_paths(target, [name for _, name in txt_pairs])
    except HuaHongArchiveError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise HuaHongArchiveError("ZIP archive is damaged or unreadable") from exc


def _extract_7z(source: Path, target: Path, limits: ArchiveLimits) -> tuple[Path, ...]:
    try:
        with py7zr.SevenZipFile(source, mode="r") as archive:
            if archive.needs_password():
                raise HuaHongArchiveError("encrypted 7z archives are not allowed")
            infos = archive.list()
            if any(info.is_symlink for info in infos):
                raise HuaHongArchiveError("symbolic links are not allowed in 7z archives")
            all_names = [_safe_member_name(info.filename) for info in infos]
            _ensure_unique(all_names)
            _validate_counts([int(info.uncompressed or 0) for info in infos], limits)
            regular = [info for info in infos if info.is_file]
            names = [_safe_member_name(info.filename) for info in regular]
            txt_names = [name for name in names if name.suffix.lower() == ".txt"]
            if not txt_names:
                raise HuaHongArchiveError("archive contains no HuaHong TXT data files")
            archive.extract(path=target, targets=[str(name) for name in txt_names])
            return _ensure_extracted_paths(target, txt_names)
    except HuaHongArchiveError:
        raise
    except Exception as exc:
        # py7zr exposes several backend-specific corruption exceptions.
        raise HuaHongArchiveError("7z archive is damaged or unreadable") from exc


@contextmanager
def prepare_huahong_input(
    source: str | Path, *, limits: ArchiveLimits | None = None
) -> Iterator[PreparedHuaHongInput]:
    """Materialize a TXT/ZIP/7z input and always remove temporary extraction data."""

    input_path = Path(source)
    if not input_path.is_file():
        raise HuaHongArchiveError("HuaHong input must be an existing file")
    active_limits = limits or ArchiveLimits()
    suffix = input_path.suffix.lower()
    if suffix == ".txt":
        yield PreparedHuaHongInput(input_path.parent, (input_path,), input_path.name)
        return
    if suffix not in {".zip", ".7z"}:
        raise HuaHongArchiveError("HuaHong input must be TXT, ZIP, or 7z")

    with tempfile.TemporaryDirectory(prefix="tms-huahong-") as temporary:
        root = Path(temporary)
        files = (
            _extract_zip(input_path, root, active_limits)
            if suffix == ".zip"
            else _extract_7z(input_path, root, active_limits)
        )
        yield PreparedHuaHongInput(root, tuple(sorted(files)), input_path.name)
