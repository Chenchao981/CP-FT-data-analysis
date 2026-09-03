from __future__ import annotations

import hashlib
import json
import os
import re
import string
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.errors import DomainError

DIRECT_PATH_MANIFEST_MODE = "LOCAL_PATH_SIZE_MTIME_V1"
DIRECT_PATH_MANIFEST_POLICIES = frozenset(
    {
        "ALL_MATCHING_SUFFIXES_V1",
        "RIYUEXIN_RAW_DIRECTORY_V1",
        "RIYUEGUANG_RAW_DIRECTORY_V1",
        "DIANJI_RAW_DIRECTORY_V1",
    }
)
_FT_TYPED_RAW_TYPES = frozenset({"DC", "DVDS", "RG"})
_ARCHIVE_SUFFIXES = frozenset({".zip", ".7z"})


@dataclass(frozen=True, slots=True)
class DirectPathFile:
    relative_path: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class DirectPathManifest:
    source_label: str
    files: tuple[DirectPathFile, ...]
    total_bytes: int
    sha256: str

    @property
    def mode(self) -> str:
        return DIRECT_PATH_MANIFEST_MODE

    @property
    def file_count(self) -> int:
        return len(self.files)

    def as_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "source_label": self.source_label,
            "files": [asdict(item) for item in self.files],
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }

    def as_json(self) -> str:
        return json.dumps(
            self.as_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def matches_confirmation(self, *, mode: str, sha256: str) -> bool:
        return self.mode == mode and self.sha256 == sha256.lower()


@dataclass(frozen=True, slots=True)
class DirectPathBrowseItem:
    name: str
    path: str
    kind: str
    size_bytes: int | None
    suffix: str | None
    is_archive: bool
    selectable: bool
    selection_hint: str | None


def browse_direct_path(
    source_path: str | Path | None,
    *,
    allowed_suffixes: tuple[str, ...],
    selectable_file_suffixes: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """List one local directory for the web path picker without uploading data."""

    suffixes = {item.lower() for item in allowed_suffixes}
    selectable_suffixes = {
        item.lower() for item in (selectable_file_suffixes or allowed_suffixes)
    }
    raw_text = str(source_path or "").strip().strip('"')
    if not raw_text:
        roots = _available_roots()
        return {
            "path": None,
            "parent_path": None,
            "items": [
                asdict(
                    DirectPathBrowseItem(
                        name=str(root),
                        path=str(root),
                        kind="DIRECTORY",
                        size_bytes=None,
                        suffix=None,
                        is_archive=False,
                        selectable=True,
                        selection_hint=None,
                    )
                )
                for root in roots
            ],
            "allowed_suffixes": sorted(suffixes),
            "truncated": False,
        }

    raw_source = Path(raw_text).expanduser().absolute()
    if _is_link_or_junction(raw_source):
        raise DomainError(
            "DIRECT_PATH_LINK_UNSUPPORTED",
            "所选路径不能是符号链接或联接点",
            422,
        )
    source = raw_source.resolve()
    if source.is_file():
        source = source.parent
    if not source.is_dir():
        raise DomainError("DIRECT_PATH_NOT_FOUND", "所选路径不存在", 404)

    limit = int(os.getenv("TMS_QUICK_BROWSE_MAX_ITEMS", "2000"))
    items: list[DirectPathBrowseItem] = []
    truncated = False
    try:
        children = sorted(
            source.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
        for child in children:
            if _is_link_or_junction(child):
                continue
            if child.is_dir():
                item = DirectPathBrowseItem(
                    child.name,
                    str(child.resolve()),
                    "DIRECTORY",
                    None,
                    None,
                    False,
                    True,
                    None,
                )
            elif child.is_file() and child.suffix.lower() in suffixes:
                suffix = child.suffix.lower()
                selectable = suffix in selectable_suffixes
                item = DirectPathBrowseItem(
                    child.name,
                    str(child.resolve()),
                    "FILE",
                    child.stat().st_size,
                    suffix,
                    suffix in _ARCHIVE_SUFFIXES,
                    selectable,
                    None if selectable else "请选择该源文件所在的文件夹",
                )
            else:
                continue
            if len(items) >= limit:
                truncated = True
                break
            items.append(item)
    except OSError as exc:
        raise DomainError(
            "DIRECT_PATH_UNREADABLE", "所选目录无法读取", 422
        ) from exc

    parent = source.parent
    return {
        "path": str(source),
        "parent_path": str(parent) if parent != source else None,
        "items": [asdict(item) for item in items],
        "allowed_suffixes": sorted(suffixes),
        "truncated": truncated,
    }


def build_direct_path_manifest(
    source_path: str | Path,
    *,
    allowed_suffixes: tuple[str, ...] = (".csv",),
    allowed_single_file_suffixes: tuple[str, ...] | None = None,
    max_files: int | None = None,
    path_policy: str = "ALL_MATCHING_SUFFIXES_V1",
) -> tuple[Path, DirectPathManifest]:
    """Preview one file or directory visible to the current TMS backend host."""

    raw_source = Path(source_path).expanduser().absolute()
    if _is_link_or_junction(raw_source):
        raise DomainError(
            "DIRECT_PATH_LINK_UNSUPPORTED",
            "所选目录不能是符号链接或联接点",
            422,
        )
    source = raw_source.resolve()
    suffixes = {item.lower() for item in allowed_suffixes}
    single_file_suffixes = {
        item.lower() for item in (allowed_single_file_suffixes or allowed_suffixes)
    }
    normalized_policy = path_policy.strip().upper()
    if normalized_policy not in DIRECT_PATH_MANIFEST_POLICIES:
        raise ValueError(f"unsupported direct-path Manifest policy: {path_policy}")
    file_limit = max_files or int(os.getenv("TMS_QUICK_MAX_SOURCE_FILES", "100000"))
    if not source.exists() or not (source.is_dir() or source.is_file()):
        raise DomainError("DIRECT_PATH_NOT_FOUND", "所选路径不存在", 404)
    files: list[DirectPathFile] = []
    seen: set[str] = set()
    total_bytes = 0
    try:
        if source.is_file():
            suffix = source.suffix.lower()
            if suffix not in suffixes:
                raise DomainError(
                    "DIRECT_PATH_FILE_UNSUPPORTED",
                    "所选文件不符合当前工具支持的格式",
                    422,
                )
            if suffix not in single_file_suffixes:
                raise DomainError(
                    "DIRECT_PATH_FILE_REQUIRES_DIRECTORY",
                    "当前格式需要保留产品/批次目录信息，请选择该文件所在的文件夹",
                    422,
                )
            stat = source.stat()
            files.append(DirectPathFile(source.name, stat.st_size, stat.st_mtime_ns))
            total_bytes = stat.st_size
        typed_raw_directories = (
            {
                child.name.upper()
                for child in source.iterdir()
                if child.is_dir() and child.name.upper() in _FT_TYPED_RAW_TYPES
            }
            if source.is_dir()
            and normalized_policy
            in {"RIYUEXIN_RAW_DIRECTORY_V1", "RIYUEGUANG_RAW_DIRECTORY_V1"}
            and source.name.upper() not in _FT_TYPED_RAW_TYPES
            else set()
        )
        for current_name, directory_names, file_names in (
            os.walk(source, topdown=True, followlinks=False)
            if source.is_dir()
            else ()
        ):
            current = Path(current_name)
            for directory_name in tuple(directory_names):
                if _is_link_or_junction(current / directory_name):
                    raise DomainError(
                        "DIRECT_PATH_LINK_UNSUPPORTED",
                        "所选目录内不能包含符号链接或联接点",
                        422,
                    )
            for file_name in file_names:
                candidate = current / file_name
                if candidate.suffix.lower() not in suffixes:
                    continue
                relative_path = candidate.relative_to(source)
                if not _matches_path_policy(
                    source,
                    relative_path,
                    normalized_policy,
                    typed_raw_directories,
                ):
                    continue
                if _is_link_or_junction(candidate):
                    raise DomainError(
                        "DIRECT_PATH_LINK_UNSUPPORTED",
                        "所选目录内不能包含符号链接文件",
                        422,
                    )
                relative = relative_path.as_posix()
                normalized = relative.casefold()
                if normalized in seen:
                    raise DomainError(
                        "DIRECT_PATH_DUPLICATE",
                        "所选目录包含大小写不唯一的相对文件路径",
                        422,
                    )
                if len(files) >= file_limit:
                    raise DomainError(
                        "DIRECT_PATH_FILE_LIMIT",
                        f"所选目录文件数超过上限 {file_limit}",
                        422,
                    )
                seen.add(normalized)
                stat = candidate.stat()
                files.append(DirectPathFile(relative, stat.st_size, stat.st_mtime_ns))
                total_bytes += stat.st_size
    except DomainError:
        raise
    except OSError as exc:
        raise DomainError(
            "DIRECT_PATH_UNREADABLE", "所选目录无法完整读取", 422
        ) from exc
    files.sort(key=lambda item: item.relative_path.casefold())
    if not files:
        raise DomainError(
            "DIRECT_PATH_EMPTY", "所选目录没有符合当前工具要求的源文件", 422
        )
    payload = {
        "mode": DIRECT_PATH_MANIFEST_MODE,
        "source_label": source.name,
        "files": [asdict(item) for item in files],
        "file_count": len(files),
        "total_bytes": total_bytes,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return source, DirectPathManifest(
        source.name,
        tuple(files),
        total_bytes,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _available_roots() -> tuple[Path, ...]:
    if os.name == "nt":
        return tuple(
            root
            for letter in string.ascii_uppercase
            if (root := Path(f"{letter}:\\")).is_dir()
        )
    return (Path("/"),)


def _matches_path_policy(
    source: Path,
    relative_path: Path,
    policy: str,
    typed_raw_directories: set[str],
) -> bool:
    if policy == "ALL_MATCHING_SUFFIXES_V1":
        return True
    parent_parts = tuple(part.upper() for part in relative_path.parts[:-1])
    if policy == "DIANJI_RAW_DIRECTORY_V1":
        return "OUTPUT" not in parent_parts and not any(
            re.fullmatch(r"PAT_\d+", part) for part in parent_parts
        )
    if source.name.upper() in _FT_TYPED_RAW_TYPES:
        return len(relative_path.parts) == 1
    if typed_raw_directories:
        return (
            len(relative_path.parts) == 2
            and relative_path.parts[0].upper() in typed_raw_directories
        )
    return len(relative_path.parts) == 1


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction())
