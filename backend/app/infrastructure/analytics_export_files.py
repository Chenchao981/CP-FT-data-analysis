from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class UnsafeAnalyticsExportPath(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnalyticsExportFileIdentity:
    path: Path
    file_name: str
    file_size: int
    sha256: str


class AnalyticsExportPathPolicy:
    """Confine artifacts to one direct TMS_ANALYTICS_EXPORT_ROOT/<job_id> child."""

    _FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")

    def __init__(self, export_root: str | Path) -> None:
        raw_root = Path(export_root)
        if not raw_root.is_absolute():
            raise ValueError("TMS_ANALYTICS_EXPORT_ROOT must be an absolute path")
        self._export_root = Path(os.path.abspath(os.fspath(raw_root)))
        self._reject_existing_links(self._export_root)

    @property
    def export_root(self) -> Path:
        return self._export_root

    def job_root(self, export_job_id: int) -> Path:
        if export_job_id < 1:
            raise UnsafeAnalyticsExportPath("export job id must be positive")
        candidate = self._export_root / str(export_job_id)
        self._require_contained(self._export_root, candidate)
        if candidate.parent != self._export_root or candidate.name != str(
            export_job_id
        ):
            raise UnsafeAnalyticsExportPath("job root must be an exact managed child")
        return candidate

    def prepare_job_root(self, export_job_id: int) -> Path:
        self._export_root.mkdir(parents=True, exist_ok=True)
        self._reject_existing_links(self._export_root)
        root = self.job_root(export_job_id)
        if os.path.lexists(root):
            if self._is_link_or_reparse(root) or not root.is_dir():
                raise UnsafeAnalyticsExportPath("managed export Job root is unsafe")
        else:
            root.mkdir()
        self._reject_existing_links(root)
        return root

    def artifact_path(self, export_job_id: int, file_name: str) -> Path:
        if not self._FILE_NAME.fullmatch(file_name) or file_name in {".", ".."}:
            raise UnsafeAnalyticsExportPath("artifact file name is not allowed")
        root = self.job_root(export_job_id)
        candidate = root / file_name
        self._require_contained(root, candidate)
        if candidate.parent != root:
            raise UnsafeAnalyticsExportPath("artifact must be a direct Job child")
        self._reject_existing_links(candidate)
        return candidate

    def remove_empty_job_root(self, export_job_id: int) -> bool:
        """Remove only an empty, exact managed Job directory after render failure."""

        root = self.job_root(export_job_id)
        if not os.path.lexists(root):
            return False
        self._reject_existing_links(root)
        if not root.is_dir() or self._is_link_or_reparse(root):
            raise UnsafeAnalyticsExportPath("managed export Job root is unsafe")
        try:
            root.rmdir()
        except OSError:
            if root.is_dir() and any(root.iterdir()):
                return False
            raise
        return True

    def remove_artifact(self, export_job_id: int, raw_path: str | Path) -> bool:
        """Remove only one exact direct-child Artifact owned by a fenced attempt."""

        path = self.require_artifact(
            export_job_id,
            raw_path,
            must_exist=False,
        )
        if not os.path.lexists(path):
            return False
        self._reject_existing_links(path)
        if self._is_link_or_reparse(path) or not path.is_file():
            raise UnsafeAnalyticsExportPath("managed export Artifact is unsafe")
        path.unlink()
        self.remove_empty_job_root(export_job_id)
        return True

    def remove_attempt_files(self, export_job_id: int, file_name: str) -> int:
        """Best-effort cleanup for one fenced target and its atomic temp files.

        Other attempt targets are preserved.  All matching direct children are
        inspected before deletion so a link/reparse attack fails closed.
        """

        target = self.artifact_path(export_job_id, file_name)
        root = target.parent
        if not os.path.lexists(root):
            return 0
        self._reject_existing_links(root)
        if not root.is_dir() or self._is_link_or_reparse(root):
            raise UnsafeAnalyticsExportPath("managed export Job root is unsafe")
        temporary_name = re.compile(
            rf"^\.{re.escape(file_name)}\.[0-9a-f]{{32}}\.tmp$"
        )
        candidates: list[Path] = []
        with os.scandir(root) as iterator:
            for entry in iterator:
                if (
                    entry.name != file_name
                    and temporary_name.fullmatch(entry.name) is None
                ):
                    continue
                path = root / entry.name
                if self._is_link_or_reparse(path) or not entry.is_file(
                    follow_symlinks=False
                ):
                    raise UnsafeAnalyticsExportPath(
                        "managed export attempt contains an unsafe entry"
                    )
                candidates.append(path)

        first_error: OSError | None = None
        removed = 0
        for path in candidates:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        try:
            self.remove_empty_job_root(export_job_id)
        except OSError as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error
        return removed

    @contextmanager
    def atomic_binary_writer(
        self, export_job_id: int, file_name: str
    ) -> Iterator[tuple[BinaryIO, Path]]:
        target = self.artifact_path(export_job_id, file_name)
        root = target.parent
        if not root.is_dir() or self._is_link_or_reparse(root):
            raise UnsafeAnalyticsExportPath("managed export Job root is unsafe")
        if os.path.lexists(target):
            raise UnsafeAnalyticsExportPath("artifact target already exists")
        temporary = root / f".{file_name}.{uuid.uuid4().hex}.tmp"
        self._require_contained(root, temporary)
        try:
            with open(temporary, "xb") as stream:
                yield stream, temporary
                stream.flush()
                os.fsync(stream.fileno())
            self._reject_existing_links(temporary)
            if self._is_link_or_reparse(temporary):
                raise UnsafeAnalyticsExportPath("temporary artifact became a link")
            if os.path.lexists(target):
                raise UnsafeAnalyticsExportPath(
                    "artifact target appeared during generation"
                )
            os.replace(temporary, target)
            self._reject_existing_links(target)
            if self._is_link_or_reparse(target):
                raise UnsafeAnalyticsExportPath("artifact became a link")
        finally:
            if os.path.lexists(temporary):
                temporary.unlink()

    def require_artifact(
        self,
        export_job_id: int,
        raw_path: str | Path,
        *,
        must_exist: bool,
    ) -> Path:
        raw = Path(raw_path)
        if not raw.is_absolute():
            raise UnsafeAnalyticsExportPath("artifact path must be absolute")
        candidate = Path(os.path.abspath(os.fspath(raw)))
        root = self.job_root(export_job_id)
        self._require_contained(root, candidate)
        if candidate.parent != root:
            raise UnsafeAnalyticsExportPath("artifact must be a direct Job child")
        if not self._FILE_NAME.fullmatch(candidate.name):
            raise UnsafeAnalyticsExportPath("artifact file name is not allowed")
        self._reject_existing_links(candidate)
        if must_exist:
            if not candidate.is_file():
                raise FileNotFoundError(str(candidate))
            if self._is_link_or_reparse(candidate):
                raise UnsafeAnalyticsExportPath("artifact is a link or reparse point")
        return candidate

    def identify(
        self, export_job_id: int, raw_path: str | Path
    ) -> AnalyticsExportFileIdentity:
        path = self.require_artifact(export_job_id, raw_path, must_exist=True)
        digest = hashlib.sha256()
        file_size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                file_size += len(chunk)
        return AnalyticsExportFileIdentity(
            path=path,
            file_name=path.name,
            file_size=file_size,
            sha256=digest.hexdigest(),
        )

    def _reject_existing_links(self, candidate: Path) -> None:
        existing: list[Path] = []
        current = candidate
        while True:
            if os.path.lexists(current):
                existing.append(current)
            if current.parent == current:
                break
            current = current.parent
        for item in reversed(existing):
            if self._is_link_or_reparse(item):
                raise UnsafeAnalyticsExportPath(
                    "managed export path ancestry contains a link or reparse point"
                )

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return False
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def _require_contained(root: Path, candidate: Path) -> None:
        normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
        normalized_candidate = os.path.normcase(os.path.abspath(os.fspath(candidate)))
        try:
            common = os.path.commonpath((normalized_root, normalized_candidate))
        except ValueError as exc:
            raise UnsafeAnalyticsExportPath(
                "artifact path is on another filesystem"
            ) from exc
        if common != normalized_root:
            raise UnsafeAnalyticsExportPath(
                "artifact path escapes its managed Job root"
            )
