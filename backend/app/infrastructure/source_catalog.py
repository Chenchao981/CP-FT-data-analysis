from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

from app.core.errors import DomainError

MANIFEST_MODE = "PATH_SIZE_MTIME_V1"
_ROOT_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,127}$")


@dataclass(frozen=True, slots=True)
class SourceRoot:
    code: str
    name: str
    path: Path
    test_stage: str
    factory_code: str
    allowed_suffixes: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "name": self.name,
            "test_stage": self.test_stage,
            "factory_code": self.factory_code,
            "allowed_suffixes": list(self.allowed_suffixes),
            "available": self.path.is_dir(),
        }


@dataclass(frozen=True, slots=True)
class SourceDirectory:
    name: str
    relative_path: str
    direct_file_count: int
    direct_total_bytes: int


@dataclass(frozen=True, slots=True)
class SourceManifestFile:
    relative_path: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class SourceManifest:
    mode: str
    root_code: str
    selected_relative_path: str
    files: tuple[SourceManifestFile, ...]
    total_bytes: int
    sha256: str

    @property
    def file_count(self) -> int:
        return len(self.files)

    def as_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "root_code": self.root_code,
            "selected_relative_path": self.selected_relative_path,
            "files": [asdict(item) for item in self.files],
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }

    def as_json(self) -> str:
        return json.dumps(
            self.as_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


class SourceCatalog:
    """Resolve only administrator-configured server roots and their descendants."""

    def __init__(
        self,
        roots: tuple[SourceRoot, ...] = (),
        *,
        max_manifest_files: int = 100_000,
    ) -> None:
        if max_manifest_files < 1:
            raise ValueError("max_manifest_files must be positive")
        self._roots = {item.code: item for item in roots}
        if len(self._roots) != len(roots):
            raise ValueError("source root codes must be unique")
        self._max_manifest_files = max_manifest_files

    @classmethod
    def from_environment(cls) -> SourceCatalog:
        raw = os.getenv("TMS_SOURCE_ROOTS_JSON", "").strip()
        if not raw:
            return cls()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("TMS_SOURCE_ROOTS_JSON is not valid JSON") from exc
        if not isinstance(values, list):
            raise TypeError("TMS_SOURCE_ROOTS_JSON must be a JSON array")
        roots: list[SourceRoot] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise TypeError(f"source root #{index + 1} must be an object")
            try:
                code = str(value["code"]).strip().upper()
                name = str(value["name"]).strip()
                path = Path(str(value["path"]).strip()).expanduser().resolve()
                stage = str(value["test_stage"]).strip().upper()
                factory = str(value["factory_code"]).strip().upper()
                suffix_values = value["allowed_suffixes"]
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"source root #{index + 1} is missing a required field"
                ) from exc
            if not _ROOT_CODE.fullmatch(code) or not name:
                raise RuntimeError(f"source root #{index + 1} has invalid code or name")
            if stage != "FT" or factory != "JIEQUN":
                raise RuntimeError(
                    "P0 source roots must use test_stage=FT and factory_code=JIEQUN"
                )
            if not isinstance(suffix_values, list) or not suffix_values:
                raise RuntimeError(
                    f"source root {code} must define allowed_suffixes"
                )
            suffixes = tuple(
                dict.fromkeys(
                    (
                        item
                        if item.startswith(".")
                        else f".{item}"
                    ).lower()
                    for item in (str(raw_suffix).strip() for raw_suffix in suffix_values)
                    if item
                )
            )
            if suffixes != (".csv",):
                raise RuntimeError(
                    f"source root {code} must use the P0 .csv input contract"
                )
            roots.append(SourceRoot(code, name, path, stage, factory, suffixes))
        max_files = int(os.getenv("TMS_QUICK_MAX_SOURCE_FILES", "100000"))
        return cls(tuple(roots), max_manifest_files=max_files)

    def list_roots(self) -> tuple[dict[str, object], ...]:
        return tuple(item.public_dict() for item in self._roots.values())

    def get_root(self, root_code: str) -> SourceRoot:
        try:
            return self._roots[root_code.strip().upper()]
        except KeyError as exc:
            raise DomainError(
                "SOURCE_ROOT_NOT_FOUND", "数据源不存在或未获管理员授权", 404
            ) from exc

    def resolve_directory(self, root_code: str, relative_path: str | None) -> Path:
        root = self.get_root(root_code)
        if not root.path.is_dir():
            raise DomainError(
                "SOURCE_ROOT_UNAVAILABLE", f"数据源“{root.name}”当前不可用", 503
            )
        raw = (relative_path or ".").strip().replace("/", os.sep)
        windows = PureWindowsPath(raw)
        if windows.is_absolute() or windows.drive or windows.root:
            raise DomainError("SOURCE_PATH_INVALID", "只能选择数据源内的相对目录", 422)
        if any(part == ".." for part in windows.parts):
            raise DomainError("SOURCE_PATH_INVALID", "相对目录不能包含上级跳转", 422)
        candidate = (root.path / Path(raw)).resolve()
        self._require_contained(root.path, candidate)
        if not candidate.is_dir():
            raise DomainError("SOURCE_DIRECTORY_NOT_FOUND", "所选数据目录不存在", 404)
        return candidate

    def relative_path(self, root_code: str, directory: Path) -> str:
        root = self.get_root(root_code)
        resolved = directory.resolve()
        self._require_contained(root.path, resolved)
        relative = os.path.relpath(str(resolved), str(root.path.resolve()))
        return "." if relative == "." else Path(relative).as_posix()

    def browse(
        self, root_code: str, relative_path: str | None
    ) -> tuple[str, str | None, tuple[SourceDirectory, ...]]:
        root = self.get_root(root_code)
        current = self.resolve_directory(root_code, relative_path)
        current_relative = self.relative_path(root_code, current)
        parent_relative = None
        if current_relative != ".":
            parent_relative = self.relative_path(root_code, current.parent)
        directories: list[SourceDirectory] = []
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise DomainError(
                "SOURCE_DIRECTORY_UNREADABLE", "所选数据目录无法读取", 422
            ) from exc
        for child in children:
            if not child.is_dir():
                continue
            resolved = child.resolve()
            self._require_contained(root.path, resolved)
            direct_files = [
                item
                for item in resolved.iterdir()
                if item.is_file() and item.suffix.lower() in root.allowed_suffixes
            ]
            directories.append(
                SourceDirectory(
                    child.name,
                    self.relative_path(root_code, resolved),
                    len(direct_files),
                    sum(item.stat().st_size for item in direct_files),
                )
            )
        return current_relative, parent_relative, tuple(directories)

    def build_manifest(
        self, root_code: str, relative_path: str | None
    ) -> SourceManifest:
        root = self.get_root(root_code)
        selected = self.resolve_directory(root_code, relative_path)
        selected_relative = self.relative_path(root_code, selected)
        files: list[SourceManifestFile] = []
        total_bytes = 0
        for path in sorted(selected.rglob("*"), key=lambda item: str(item).casefold()):
            if not path.is_file() or path.suffix.lower() not in root.allowed_suffixes:
                continue
            resolved = path.resolve()
            self._require_contained(selected, resolved)
            if len(files) >= self._max_manifest_files:
                raise DomainError(
                    "SOURCE_FILE_LIMIT_EXCEEDED",
                    f"数据目录文件数超过上限 {self._max_manifest_files}",
                    422,
                )
            stat = resolved.stat()
            files.append(
                SourceManifestFile(
                    resolved.relative_to(selected).as_posix(),
                    stat.st_size,
                    stat.st_mtime_ns,
                )
            )
            total_bytes += stat.st_size
        if not files:
            raise DomainError(
                "SOURCE_DIRECTORY_EMPTY", "所选目录没有符合合同的 CSV 文件", 422
            )
        payload = {
            "mode": MANIFEST_MODE,
            "root_code": root.code,
            "selected_relative_path": selected_relative,
            "files": [asdict(item) for item in files],
            "file_count": len(files),
            "total_bytes": total_bytes,
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return SourceManifest(
            MANIFEST_MODE,
            root.code,
            selected_relative,
            tuple(files),
            total_bytes,
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _require_contained(root: Path, candidate: Path) -> None:
        normalized_root = os.path.normcase(str(root.resolve()))
        normalized_candidate = os.path.normcase(str(candidate.resolve()))
        try:
            common = os.path.commonpath((normalized_root, normalized_candidate))
        except ValueError as exc:
            raise DomainError(
                "SOURCE_PATH_ESCAPE", "所选目录超出已授权数据源", 422
            ) from exc
        if common != normalized_root:
            raise DomainError(
                "SOURCE_PATH_ESCAPE", "所选目录超出已授权数据源", 422
            )
