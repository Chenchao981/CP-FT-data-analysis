from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.infrastructure.formal_artifact_files import (
    FormalOrphanRootCleaner,
    ManagedJobPathPolicy,
    OversizedFormalOrphanRoot,
    UnsafeFormalArtifactPath,
)
from app.infrastructure.sql_formal_orphan_cleanup import (
    SqlFormalOrphanCleanupService,
)


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return next(self.results)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.begin_calls = 0

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        self.begin_calls += 1
        yield self.connection


def _eligible_state(now: datetime, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "job_id": 81,
        "job_type": "EXPORT_LATEST",
        "status": "FAILED",
        "finished_at_utc": now - timedelta(days=8),
        "lease_token": None,
        "lease_owner": None,
        "lease_expires_at_utc": None,
        "permanent_artifact_count": 0,
        "active_temporary_artifact_count": 0,
    }
    state.update(overrides)
    return state


def _job_tree(tmp_path: Path, job_id: int = 81) -> tuple[Path, Path]:
    root = (tmp_path / "work").absolute()
    artifact = root / str(job_id) / "attempt-1" / "partial.xlsx"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"cleaner-crashed-before-registration")
    return root, artifact


def test_orphan_candidates_are_only_exact_numeric_job_children(tmp_path: Path) -> None:
    root = (tmp_path / "work").absolute()
    (root / "81").mkdir(parents=True)
    (root / "0082").mkdir()
    (root / "attempt-1").mkdir()
    (root / "83.txt").write_text("not a directory", encoding="utf-8")
    cleaner = FormalOrphanRootCleaner(ManagedJobPathPolicy(root))

    candidates = cleaner.candidates(limit=10)

    assert [(item.directory_name, item.job_id, item.issue_code) for item in candidates] == [
        ("0082", None, "JOB_DIRECTORY_ID_INVALID"),
        ("81", 81, None),
    ]


def test_orphan_inspection_blocks_oversized_root(tmp_path: Path) -> None:
    root = (tmp_path / "work").absolute()
    job = root / "81"
    job.mkdir(parents=True)
    (job / "one.bin").write_bytes(b"1")
    (job / "two.bin").write_bytes(b"2")
    cleaner = FormalOrphanRootCleaner(
        ManagedJobPathPolicy(root),
        max_entries=1,
    )

    with pytest.raises(OversizedFormalOrphanRoot, match="entry limit"):
        cleaner.inspect_job(81)
    assert job.is_dir()


def test_orphan_inspection_blocks_nested_reparse_attack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, artifact = _job_tree(tmp_path)
    attempt = artifact.parent
    policy = ManagedJobPathPolicy(root)
    original = policy._is_link_or_reparse
    monkeypatch.setattr(
        policy,
        "_is_link_or_reparse",
        lambda path: Path(path) == attempt or original(Path(path)),
    )
    cleaner = FormalOrphanRootCleaner(policy)

    with pytest.raises(UnsafeFormalArtifactPath, match="reparse"):
        cleaner.inspect_job(81)
    assert artifact.is_file()


def test_oversized_orphan_is_retained_and_audited(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root, artifact = _job_tree(tmp_path)
    (artifact.parent / "second.bin").write_bytes(b"second")
    connection = _Connection([_Result(_eligible_state(now)), _Result()])
    service = SqlFormalOrphanCleanupService(
        _Engine(connection),  # type: ignore[arg-type]
        FormalOrphanRootCleaner(ManagedJobPathPolicy(root), max_entries=1),
    )

    result = service.run(now=now, retention=timedelta(days=7), dry_run=False)[0]

    assert result.cleanup_status == "BLOCKED"
    assert result.reason_code == "ORPHAN_ROOT_OVERSIZED"
    assert artifact.is_file()
    assert sum(
        "FORMAL_ORPHAN_ROOT_SWEEP" in sql for sql, _ in connection.calls
    ) == 1


def test_reparse_attack_is_retained_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    root, artifact = _job_tree(tmp_path)
    policy = ManagedJobPathPolicy(root)
    original = policy._is_link_or_reparse
    monkeypatch.setattr(
        policy,
        "_is_link_or_reparse",
        lambda path: Path(path) == artifact.parent or original(Path(path)),
    )
    connection = _Connection([_Result(_eligible_state(now)), _Result()])
    service = SqlFormalOrphanCleanupService(
        _Engine(connection),  # type: ignore[arg-type]
        FormalOrphanRootCleaner(policy),
    )

    result = service.run(now=now, retention=timedelta(days=7), dry_run=False)[0]

    assert result.cleanup_status == "BLOCKED"
    assert result.reason_code == "ORPHAN_ROOT_PATH_UNSAFE"
    assert artifact.is_file()
    assert sum(
        "FORMAL_ORPHAN_ROOT_SWEEP" in sql for sql, _ in connection.calls
    ) == 1


def test_pre_registration_crash_orphan_dry_run_is_audited(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root, artifact = _job_tree(tmp_path)
    connection = _Connection([_Result(_eligible_state(now)), _Result()])
    engine = _Engine(connection)
    service = SqlFormalOrphanCleanupService(
        engine,  # type: ignore[arg-type]
        FormalOrphanRootCleaner(ManagedJobPathPolicy(root)),
    )

    result = service.run(now=now, retention=timedelta(days=7))[0]

    assert result.cleanup_status == "DRY_RUN"
    assert result.reason_code == "ELIGIBLE_ORPHAN_ROOT"
    assert artifact.is_file()
    assert engine.begin_calls == 1
    assert sum(
        "FORMAL_ORPHAN_ROOT_SWEEP" in sql for sql, _ in connection.calls
    ) == 1


def test_pre_registration_crash_orphan_delete_is_rechecked_and_audited(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    root, _artifact = _job_tree(tmp_path)
    connection = _Connection(
        [
            _Result(_eligible_state(now)),
            _Result(),
            _Result(_eligible_state(now)),
            _Result(),
        ]
    )
    engine = _Engine(connection)
    service = SqlFormalOrphanCleanupService(
        engine,  # type: ignore[arg-type]
        FormalOrphanRootCleaner(ManagedJobPathPolicy(root)),
    )

    result = service.run(
        now=now,
        retention=timedelta(days=7),
        dry_run=False,
    )[0]

    assert result.cleanup_status == "DELETED"
    assert not (root / "81").exists()
    assert engine.begin_calls == 2
    assert sum(
        "FORMAL_ORPHAN_ROOT_SWEEP" in sql for sql, _ in connection.calls
    ) == 2
    locked_query = next(
        sql for sql, _ in connection.calls if "WITH (UPDLOCK,HOLDLOCK)" in sql
    )
    assert locked_query.count("WITH (UPDLOCK,HOLDLOCK)") == 3


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"status": "RUNNING"}, "JOB_NOT_TERMINAL"),
        (
            {
                "lease_token": "11111111-1111-1111-1111-111111111111",
                "lease_owner": "worker-1",
                "lease_expires_at_utc": datetime.now(UTC) + timedelta(minutes=5),
            },
            "JOB_LEASE_ACTIVE",
        ),
        ({"permanent_artifact_count": 1}, "PERMANENT_ARTIFACT_PRESENT"),
        (
            {"active_temporary_artifact_count": 1},
            "REGISTERED_TEMPORARY_ARTIFACT_ACTIVE",
        ),
    ],
)
def test_orphan_sweep_never_removes_ineligible_job_root(
    tmp_path: Path,
    overrides: dict[str, Any],
    reason_code: str,
) -> None:
    now = datetime.now(UTC)
    root, artifact = _job_tree(tmp_path)
    connection = _Connection([_Result(_eligible_state(now, **overrides)), _Result()])
    service = SqlFormalOrphanCleanupService(
        _Engine(connection),  # type: ignore[arg-type]
        FormalOrphanRootCleaner(ManagedJobPathPolicy(root)),
    )

    result = service.run(
        now=now,
        retention=timedelta(days=7),
        dry_run=False,
    )[0]

    assert result.cleanup_status == "INELIGIBLE"
    assert result.reason_code == reason_code
    assert artifact.is_file()
    assert sum(
        "FORMAL_ORPHAN_ROOT_SWEEP" in sql for sql, _ in connection.calls
    ) == 1


def test_orphan_sql_contract_checks_terminal_lease_and_artifacts(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    root, _artifact = _job_tree(tmp_path)
    connection = _Connection([_Result(_eligible_state(now)), _Result()])
    service = SqlFormalOrphanCleanupService(
        _Engine(connection),  # type: ignore[arg-type]
        FormalOrphanRootCleaner(ManagedJobPathPolicy(root)),
    )

    service.run(now=now, retention=timedelta(days=7))

    query = connection.calls[0][0]
    assert "lease_token" in query
    assert "lease_expires_at_utc" in query
    assert "temporary_flag=0" in query
    assert "physical_status NOT IN('DELETED','MISSING')" in query
