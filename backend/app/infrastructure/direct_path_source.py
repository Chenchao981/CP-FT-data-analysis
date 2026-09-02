from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.errors import DomainError

DIRECT_PATH_MANIFEST_MODE = "LOCAL_PATH_SIZE_MTIME_V1"


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


def build_direct_path_manifest(
    source_path: str | Path,
    *,
    allowed_suffixes: tuple[str, ...] = (".csv",),
    max_files: int | None = None,
) -> tuple[Path, DirectPathManifest]:
    """Preview a directory visible to the current TMS backend host."""

    raw_source = Path(source_path).expanduser().absolute()
    if _is_link_or_junction(raw_source):
        raise DomainError(
            "DIRECT_PATH_LINK_UNSUPPORTED",
            "所选目录不能是符号链接或联接点",
            422,
        )
    source = raw_source.resolve()
    if not source.is_dir():
        raise DomainError("DIRECT_PATH_NOT_FOUND", "所选目录不存在", 404)
    suffixes = {item.lower() for item in allowed_suffixes}
    file_limit = max_files or int(os.getenv("TMS_QUICK_MAX_SOURCE_FILES", "100000"))
    files: list[DirectPathFile] = []
    seen: set[str] = set()
    total_bytes = 0
    try:
        for current_name, directory_names, file_names in os.walk(
            source, topdown=True, followlinks=False
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
                if _is_link_or_junction(candidate):
                    raise DomainError(
                        "DIRECT_PATH_LINK_UNSUPPORTED",
                        "所选目录内不能包含符号链接文件",
                        422,
                    )
                relative = candidate.relative_to(source).as_posix()
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
            "DIRECT_PATH_EMPTY", "所选目录没有符合当前工具要求的 CSV 文件", 422
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


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction())
