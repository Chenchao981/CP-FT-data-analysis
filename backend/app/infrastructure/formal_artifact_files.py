from __future__ import annotations

import mimetypes
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


class UnsafeFormalArtifactPath(RuntimeError):
    pass


class OversizedFormalOrphanRoot(UnsafeFormalArtifactPath):
    pass


@dataclass(frozen=True, slots=True)
class FormalCleanupFileOutcome:
    physical_status: str
    discovered_file_count: int
    discovered_bytes: int
    removed_job_root: bool


@dataclass(frozen=True, slots=True)
class FormalOrphanCandidate:
    directory_name: str
    job_id: int | None
    issue_code: str | None = None


@dataclass(frozen=True, slots=True)
class FormalOrphanRootOutcome:
    physical_status: str
    discovered_file_count: int
    discovered_entry_count: int
    discovered_bytes: int
    removed_job_root: bool


class ManagedJobPathPolicy:
    """Lexically confine artifacts to TMS_WORK_ROOT/<job>/attempt-n.

    Paths are checked without resolving through a link first. Every existing
    component is then inspected with lstat so symlinks and Windows reparse
    points fail closed.
    """

    _ATTEMPT = re.compile(r"^attempt-[1-9][0-9]*$")

    def __init__(self, work_root: str | Path) -> None:
        raw_root = Path(work_root)
        if not raw_root.is_absolute():
            raise ValueError("TMS_WORK_ROOT must be an absolute path")
        self._work_root = Path(os.path.abspath(os.fspath(raw_root)))
        for component in reversed((self._work_root, *self._work_root.parents)):
            if component.exists() and self._is_link_or_reparse(component):
                raise UnsafeFormalArtifactPath(
                    "managed work root ancestry contains a link or reparse point"
                )

    @property
    def work_root(self) -> Path:
        return self._work_root

    def job_root(self, job_id: int) -> Path:
        if job_id < 1:
            raise UnsafeFormalArtifactPath("formal artifact job id must be positive")
        candidate = self._work_root / str(job_id)
        self._require_contained(self._work_root, candidate)
        if candidate.parent != self._work_root or candidate.name != str(job_id):
            raise UnsafeFormalArtifactPath("job root must be an exact managed child")
        return candidate

    def attempt_root(self, job_id: int, attempt_count: int) -> Path:
        if attempt_count < 1:
            raise UnsafeFormalArtifactPath("formal artifact attempt must be positive")
        job_root = self.job_root(job_id)
        candidate = job_root / f"attempt-{attempt_count}"
        self._require_contained(job_root, candidate)
        if candidate.parent != job_root or not self._ATTEMPT.fullmatch(candidate.name):
            raise UnsafeFormalArtifactPath(
                "attempt root must be an exact managed Job child"
            )
        self._reject_links_in_existing_path(job_root, candidate)
        return candidate

    def require_artifact(
        self,
        job_id: int,
        raw_path: str | Path,
        *,
        must_exist: bool,
    ) -> Path:
        candidate_raw = Path(raw_path)
        if not candidate_raw.is_absolute():
            raise UnsafeFormalArtifactPath("artifact path must be absolute")
        candidate = Path(os.path.abspath(os.fspath(candidate_raw)))
        job_root = self.job_root(job_id)
        self._require_contained(job_root, candidate)
        relative = candidate.relative_to(job_root)
        if len(relative.parts) < 2 or not self._ATTEMPT.fullmatch(relative.parts[0]):
            raise UnsafeFormalArtifactPath(
                "artifact must be inside the Job attempt-n directory"
            )
        self._reject_links_in_existing_path(job_root, candidate)
        if must_exist:
            if not candidate.exists() or not candidate.is_file():
                raise FileNotFoundError(str(candidate))
            if self._is_link_or_reparse(candidate):
                raise UnsafeFormalArtifactPath("artifact is a link or reparse point")
        return candidate

    def media_type(self, path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def _reject_links_in_existing_path(self, root: Path, candidate: Path) -> None:
        current = root
        if current.exists() and self._is_link_or_reparse(current):
            raise UnsafeFormalArtifactPath("managed work root is a link or reparse point")
        for part in candidate.relative_to(root).parts:
            current = current / part
            if not os.path.lexists(current):
                continue
            if self._is_link_or_reparse(current):
                raise UnsafeFormalArtifactPath(
                    "managed artifact path contains a link or reparse point"
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
        normalized_candidate = os.path.normcase(
            os.path.abspath(os.fspath(candidate))
        )
        try:
            common = os.path.commonpath((normalized_root, normalized_candidate))
        except ValueError as exc:
            raise UnsafeFormalArtifactPath(
                "artifact path is on another filesystem"
            ) from exc
        if common != normalized_root:
            raise UnsafeFormalArtifactPath("artifact path escapes its managed Job root")


class FormalArtifactFileCleaner:
    """Remove one exact formal Job root after every registered path is verified."""

    def __init__(self, policy: ManagedJobPathPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> ManagedJobPathPolicy:
        return self._policy

    def cleanup_job(
        self,
        job_id: int,
        artifact_paths: tuple[str, ...],
        *,
        dry_run: bool = False,
    ) -> FormalCleanupFileOutcome:
        job_root = self._policy.job_root(job_id)
        for artifact_path in artifact_paths:
            self._policy.require_artifact(
                job_id,
                artifact_path,
                must_exist=False,
            )
        if not job_root.exists():
            return FormalCleanupFileOutcome("MISSING", 0, 0, False)
        if not job_root.is_dir() or self._policy._is_link_or_reparse(job_root):
            raise UnsafeFormalArtifactPath("managed Job root is not a safe directory")

        discovered_count = 0
        discovered_bytes = 0
        for directory, directory_names, file_names in os.walk(
            job_root, followlinks=False
        ):
            directory_path = Path(directory)
            if self._policy._is_link_or_reparse(directory_path):
                raise UnsafeFormalArtifactPath(
                    "managed Job directory contains a link or reparse point"
                )
            for name in (*directory_names, *file_names):
                entry = directory_path / name
                if self._policy._is_link_or_reparse(entry):
                    raise UnsafeFormalArtifactPath(
                        "managed Job directory contains a link or reparse point"
                    )
            for name in file_names:
                entry = directory_path / name
                discovered_count += 1
                discovered_bytes += os.lstat(entry).st_size

        if not dry_run:
            shutil.rmtree(job_root)
        return FormalCleanupFileOutcome(
            "PRESENT" if dry_run else "DELETED",
            discovered_count,
            discovered_bytes,
            not dry_run,
        )

    def cleanup_attempt(
        self,
        job_id: int,
        attempt_count: int,
        artifact_paths: tuple[str, ...],
        *,
        dry_run: bool = False,
    ) -> FormalCleanupFileOutcome:
        """Remove only one fenced attempt, preserving any newer attempt."""

        job_root = self._policy.job_root(job_id)
        attempt_root = self._policy.attempt_root(job_id, attempt_count)
        for artifact_path in artifact_paths:
            path = self._policy.require_artifact(
                job_id,
                artifact_path,
                must_exist=False,
            )
            try:
                relative = path.relative_to(attempt_root)
            except ValueError as exc:
                raise UnsafeFormalArtifactPath(
                    "artifact does not belong to the fenced attempt"
                ) from exc
            if not relative.parts:
                raise UnsafeFormalArtifactPath(
                    "artifact path must be below the fenced attempt root"
                )
        if not attempt_root.exists():
            removed_job_root = False
            if not dry_run and job_root.exists():
                if not job_root.is_dir() or self._policy._is_link_or_reparse(
                    job_root
                ):
                    raise UnsafeFormalArtifactPath(
                        "managed Job root is not a safe directory"
                    )
                try:
                    job_root.rmdir()
                    removed_job_root = True
                except OSError:
                    if not job_root.is_dir() or not any(job_root.iterdir()):
                        raise
            return FormalCleanupFileOutcome(
                "MISSING", 0, 0, removed_job_root
            )
        if not attempt_root.is_dir() or self._policy._is_link_or_reparse(
            attempt_root
        ):
            raise UnsafeFormalArtifactPath(
                "managed attempt root is not a safe directory"
            )

        discovered_count = 0
        discovered_bytes = 0
        for directory, directory_names, file_names in os.walk(
            attempt_root, followlinks=False
        ):
            directory_path = Path(directory)
            if self._policy._is_link_or_reparse(directory_path):
                raise UnsafeFormalArtifactPath(
                    "managed attempt directory contains a link or reparse point"
                )
            for name in (*directory_names, *file_names):
                entry = directory_path / name
                if self._policy._is_link_or_reparse(entry):
                    raise UnsafeFormalArtifactPath(
                        "managed attempt directory contains a link or reparse point"
                    )
            for name in file_names:
                entry = directory_path / name
                discovered_count += 1
                discovered_bytes += os.lstat(entry).st_size

        removed_job_root = False
        if not dry_run:
            shutil.rmtree(attempt_root)
            try:
                job_root.rmdir()
                removed_job_root = True
            except OSError:
                if not job_root.is_dir() or not any(job_root.iterdir()):
                    raise
        return FormalCleanupFileOutcome(
            "PRESENT" if dry_run else "DELETED",
            discovered_count,
            discovered_bytes,
            removed_job_root,
        )


class FormalOrphanRootCleaner:
    """Inspect and remove only exact numeric TMS_WORK_ROOT/<job_id> roots."""

    _NUMERIC = re.compile(r"^[0-9]+$")
    _MAX_JOB_ID = 9_223_372_036_854_775_807

    def __init__(
        self,
        policy: ManagedJobPathPolicy,
        *,
        max_entries: int = 100_000,
        max_bytes: int = 50 * 1024 * 1024 * 1024,
    ) -> None:
        if max_entries < 1 or max_entries > 10_000_000:
            raise ValueError("max_entries must be between 1 and 10000000")
        if max_bytes < 1 or max_bytes > 10 * 1024**4:
            raise ValueError("max_bytes must be between 1 and 10 TiB")
        self._policy = policy
        self._max_entries = max_entries
        self._max_bytes = max_bytes

    @property
    def policy(self) -> ManagedJobPathPolicy:
        return self._policy

    def candidates(self, *, limit: int) -> tuple[FormalOrphanCandidate, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        root = self._policy.work_root
        if not root.exists() or not root.is_dir():
            raise UnsafeFormalArtifactPath("managed work root is not a directory")
        if self._policy._is_link_or_reparse(root):
            raise UnsafeFormalArtifactPath("managed work root is a reparse point")
        candidates: list[FormalOrphanCandidate] = []
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if not self._NUMERIC.fullmatch(entry.name):
                    continue
                issue_code: str | None = None
                job_id: int | None = None
                try:
                    numeric = int(entry.name)
                except ValueError:
                    issue_code = "JOB_DIRECTORY_ID_INVALID"
                else:
                    if (
                        numeric < 1
                        or numeric > self._MAX_JOB_ID
                        or entry.name != str(numeric)
                    ):
                        issue_code = "JOB_DIRECTORY_ID_INVALID"
                    else:
                        job_id = numeric
                entry_path = root / entry.name
                if self._policy._is_link_or_reparse(entry_path):
                    issue_code = "JOB_DIRECTORY_REPARSE_BLOCKED"
                elif not entry.is_dir(follow_symlinks=False):
                    issue_code = "JOB_DIRECTORY_NOT_DIRECTORY"
                candidates.append(
                    FormalOrphanCandidate(entry.name, job_id, issue_code)
                )
                if len(candidates) >= limit:
                    break
        return tuple(candidates)

    def inspect_job(self, job_id: int) -> FormalOrphanRootOutcome:
        job_root = self._policy.job_root(job_id)
        if not os.path.lexists(job_root):
            return FormalOrphanRootOutcome("MISSING", 0, 0, 0, False)
        if not job_root.is_dir() or self._policy._is_link_or_reparse(job_root):
            raise UnsafeFormalArtifactPath("orphan Job root is not a safe directory")
        root_metadata = os.lstat(job_root)
        root_identity = (root_metadata.st_dev, root_metadata.st_ino)
        discovered_files = 0
        discovered_entries = 0
        discovered_bytes = 0
        pending = [job_root]
        while pending:
            directory = pending.pop()
            self._policy._require_contained(job_root, directory)
            if self._policy._is_link_or_reparse(directory):
                raise UnsafeFormalArtifactPath(
                    "orphan Job tree contains a reparse point"
                )
            directory_metadata = os.lstat(directory)
            # On Windows, ``st_dev`` is not a reliable same-volume identity for
            # files versus directories. Junctions and mounted-directory
            # boundaries carry the reparse attribute and are rejected above.
            # Preserve the device-boundary guard on POSIX, where it is stable.
            if os.name != "nt" and directory_metadata.st_dev != root_metadata.st_dev:
                raise UnsafeFormalArtifactPath(
                    "orphan Job tree crosses a filesystem boundary"
                )
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = directory / entry.name
                    self._policy._require_contained(job_root, entry_path)
                    metadata = entry.stat(follow_symlinks=False)
                    discovered_entries += 1
                    if discovered_entries > self._max_entries:
                        raise OversizedFormalOrphanRoot(
                            "orphan Job tree exceeds the entry limit"
                        )
                    if self._policy._is_link_or_reparse(entry_path):
                        raise UnsafeFormalArtifactPath(
                            "orphan Job tree contains a reparse point"
                        )
                    if os.name != "nt" and metadata.st_dev != root_metadata.st_dev:
                        raise UnsafeFormalArtifactPath(
                            "orphan Job tree crosses a filesystem boundary"
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(entry_path)
                    elif stat.S_ISREG(metadata.st_mode):
                        discovered_files += 1
                        discovered_bytes += metadata.st_size
                        if discovered_bytes > self._max_bytes:
                            raise OversizedFormalOrphanRoot(
                                "orphan Job tree exceeds the byte limit"
                            )
                    else:
                        raise UnsafeFormalArtifactPath(
                            "orphan Job tree contains an unsupported entry"
                        )
        final_metadata = os.lstat(job_root)
        if (final_metadata.st_dev, final_metadata.st_ino) != root_identity:
            raise UnsafeFormalArtifactPath("orphan Job root changed during inspection")
        return FormalOrphanRootOutcome(
            "PRESENT",
            discovered_files,
            discovered_entries,
            discovered_bytes,
            False,
        )

    def cleanup_job(
        self,
        job_id: int,
        *,
        dry_run: bool = True,
    ) -> FormalOrphanRootOutcome:
        outcome = self.inspect_job(job_id)
        if dry_run or outcome.physical_status == "MISSING":
            return outcome
        # Re-inspect immediately before removal to narrow the replacement race.
        confirmed = self.inspect_job(job_id)
        if (
            confirmed.discovered_entry_count != outcome.discovered_entry_count
            or confirmed.discovered_file_count != outcome.discovered_file_count
            or confirmed.discovered_bytes != outcome.discovered_bytes
        ):
            raise UnsafeFormalArtifactPath(
                "orphan Job root changed before deletion"
            )
        job_root = self._policy.job_root(job_id)
        shutil.rmtree(job_root)
        if os.path.lexists(job_root):
            raise OSError("orphan Job root still exists after deletion")
        return FormalOrphanRootOutcome(
            "DELETED",
            confirmed.discovered_file_count,
            confirmed.discovered_entry_count,
            confirmed.discovered_bytes,
            True,
        )
