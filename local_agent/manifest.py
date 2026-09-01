from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .errors import ManifestError
from .models import LocalManifest, ManifestFile


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction())


def build_local_manifest(
    source_path: str | Path,
    *,
    allowed_suffixes: tuple[str, ...],
    max_files: int,
) -> LocalManifest:
    raw_source = Path(source_path).expanduser().absolute()
    if _is_link_or_junction(raw_source):
        raise ManifestError(
            "LOCAL_SOURCE_LINK_UNSUPPORTED", "所选目录不能是符号链接或联接点"
        )
    source = raw_source.resolve()
    if not source.is_dir():
        raise ManifestError("LOCAL_SOURCE_NOT_FOUND", "所选本机目录不存在", 404)
    suffixes = {item.lower() for item in allowed_suffixes}
    files: list[ManifestFile] = []
    total_bytes = 0
    seen: set[str] = set()
    try:
        for current_name, directory_names, file_names in os.walk(
            source, topdown=True, followlinks=False
        ):
            current = Path(current_name)
            for directory_name in tuple(directory_names):
                child = current / directory_name
                if _is_link_or_junction(child):
                    raise ManifestError(
                        "LOCAL_SOURCE_LINK_UNSUPPORTED",
                        "所选目录内不能包含符号链接或联接点",
                    )
            for file_name in file_names:
                candidate = current / file_name
                if candidate.suffix.lower() not in suffixes:
                    continue
                if _is_link_or_junction(candidate):
                    raise ManifestError(
                        "LOCAL_SOURCE_LINK_UNSUPPORTED",
                        "所选目录内不能包含符号链接文件",
                    )
                relative = candidate.relative_to(source).as_posix()
                normalized = relative.casefold()
                if normalized in seen:
                    raise ManifestError(
                        "LOCAL_SOURCE_DUPLICATE_PATH",
                        "所选目录包含大小写不唯一的相对文件路径",
                    )
                seen.add(normalized)
                if len(files) >= max_files:
                    raise ManifestError(
                        "LOCAL_SOURCE_FILE_LIMIT_EXCEEDED",
                        f"所选目录文件数超过上限 {max_files}",
                    )
                stat = candidate.stat()
                files.append(ManifestFile(relative, stat.st_size, stat.st_mtime_ns))
                total_bytes += stat.st_size
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(
            "LOCAL_SOURCE_UNREADABLE", "所选本机目录无法完整读取"
        ) from exc
    files.sort(key=lambda item: item.relative_path.casefold())
    if not files:
        raise ManifestError(
            "LOCAL_SOURCE_EMPTY", "所选目录没有符合当前工具合同的源文件"
        )
    payload = {
        "mode": "LOCAL_PATH_SIZE_MTIME_V1",
        "source_label": source.name,
        "files": [
            {
                "relative_path": item.relative_path,
                "size_bytes": item.size_bytes,
                "mtime_ns": item.mtime_ns,
            }
            for item in files
        ],
        "file_count": len(files),
        "total_bytes": total_bytes,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return LocalManifest(
        source.name,
        tuple(files),
        total_bytes,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def manifest_json(manifest: LocalManifest) -> str:
    return json.dumps(
        manifest.canonical_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
