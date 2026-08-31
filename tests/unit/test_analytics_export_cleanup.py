from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.infrastructure.analytics_export_cleanup import AnalyticsExportFileCleaner
from app.infrastructure.analytics_export_files import (
    AnalyticsExportPathPolicy,
    UnsafeAnalyticsExportPath,
)
from app.infrastructure.sql_analytics_export_cleanup import (
    SqlAnalyticsExportCleanupService,
)


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
        rowcount: int = 0,
    ) -> None:
        self.rows = rows or []
        self.scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, dict(parameters or {})))
        return next(self.results)


class _Engine:
    def __init__(self, results: list[_Result]) -> None:
        self.connection = _Connection(results)
        self.begin_calls = 0

    def connect(self):
        return nullcontext(self.connection)

    def begin(self):
        self.begin_calls += 1
        return nullcontext(self.connection)


def _artifact_row(path: Path, *, expires_at: datetime) -> dict[str, Any]:
    return {
        "export_artifact_id": 71,
        "file_name": path.name,
        "mime_type": "text/csv; charset=utf-8",
        "storage_uri": str(path),
        "file_size": path.stat().st_size,
        "sha256": "a" * 64,
        "expires_at_utc": expires_at.replace(tzinfo=None),
        "physical_status": "PRESENT",
        "deletion_attempt_count": 0,
        "deletion_attempted_at_utc": None,
    }


def test_analytics_export_file_cleanup_is_exact_and_dry_run_by_default(
    tmp_path: Path,
) -> None:
    policy = AnalyticsExportPathPolicy((tmp_path / "exports").absolute())
    root = policy.prepare_job_root(17)
    registered = root / "analytics-export-17-attempt-2.csv"
    residual = root / "analytics-export-17-attempt-1.csv"
    registered.write_bytes(b"winner")
    residual.write_bytes(b"stale")
    cleaner = AnalyticsExportFileCleaner(policy)

    preview = cleaner.cleanup_job(17, (str(registered),), dry_run=True)

    assert preview.physical_status == "PRESENT"
    assert preview.discovered_file_count == 2
    assert preview.discovered_bytes == len(b"winnerstale")
    assert registered.is_file() and residual.is_file()

    deleted = cleaner.cleanup_job(17, (str(registered),))
    assert deleted.physical_status == "DELETED"
    assert not root.exists()


def test_analytics_export_file_cleanup_blocks_escape_directory_and_link(
    tmp_path: Path,
) -> None:
    policy = AnalyticsExportPathPolicy((tmp_path / "exports").absolute())
    root = policy.prepare_job_root(18)
    artifact = root / "analytics-export-18-attempt-1.csv"
    artifact.write_bytes(b"content")
    cleaner = AnalyticsExportFileCleaner(policy)
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"outside")

    with pytest.raises(UnsafeAnalyticsExportPath):
        cleaner.cleanup_job(18, (str(outside),), dry_run=True)

    unexpected = root / "unexpected"
    unexpected.mkdir()
    with pytest.raises(UnsafeAnalyticsExportPath):
        cleaner.cleanup_job(18, (str(artifact),), dry_run=True)
    unexpected.rmdir()

    linked = root / "linked.csv"
    try:
        os.symlink(outside, linked)
    except (OSError, NotImplementedError):
        pytest.skip("the current Windows account cannot create a test symlink")
    with pytest.raises(UnsafeAnalyticsExportPath):
        cleaner.cleanup_job(18, (str(artifact),), dry_run=True)


def test_sql_cleanup_dry_run_has_no_database_or_file_mutation(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    policy = AnalyticsExportPathPolicy((tmp_path / "exports").absolute())
    root = policy.prepare_job_root(19)
    artifact = root / "analytics-export-19-attempt-1.csv"
    artifact.write_bytes(b"content")
    engine = _Engine(
        [
            _Result(rows=[{"export_job_id": 19}]),
            _Result(
                rows=[_artifact_row(artifact, expires_at=now - timedelta(hours=1))]
            ),
        ]
    )

    results = SqlAnalyticsExportCleanupService(
        engine,  # type: ignore[arg-type]
        AnalyticsExportFileCleaner(policy),
    ).run_due(now=now)

    assert results[0].cleanup_status == "DRY_RUN"
    assert artifact.is_file()
    assert engine.begin_calls == 0
    assert all("UPDATE " not in sql.upper() for sql, _ in engine.connection.calls)
    candidate_sql = engine.connection.calls[0][0]
    assert "j.status='SUCCESS'" in candidate_sql
    assert "physical_status='BLOCKED'" in candidate_sql


def test_sql_cleanup_execute_deletes_exact_root_expires_job_and_audits(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    policy = AnalyticsExportPathPolicy((tmp_path / "exports").absolute())
    root = policy.prepare_job_root(20)
    artifact = root / "analytics-export-20-attempt-2.csv"
    artifact.write_bytes(b"content")
    row = _artifact_row(artifact, expires_at=now - timedelta(hours=1))
    engine = _Engine(
        [
            _Result(rows=[{"export_job_id": 20}]),
            _Result(scalar="SUCCESS"),
            _Result(rows=[row]),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(),
        ]
    )

    results = SqlAnalyticsExportCleanupService(
        engine,  # type: ignore[arg-type]
        AnalyticsExportFileCleaner(policy),
    ).run_due(now=now, dry_run=False)

    assert results[0].cleanup_status == "CLEANED"
    assert results[0].physical_status == "DELETED"
    assert not root.exists()
    sql = [statement for statement, _ in engine.connection.calls]
    assert any("physical_status='DELETING'" in statement for statement in sql)
    assert any("SET status='EXPIRED'" in statement for statement in sql)
    assert any("ANALYTICS_EXPORT_TTL_CLEANUP" in statement for statement in sql)
    audit_parameters = next(
        parameters
        for statement, parameters in engine.connection.calls
        if statement.startswith("INSERT governance.audit_log")
    )
    assert str(policy.export_root) not in audit_parameters["before_json"]
    assert '"sha256":"' + "a" * 64 in audit_parameters["before_json"]


def test_cleanup_execute_blocks_escaped_registered_path_without_deleting(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    policy = AnalyticsExportPathPolicy((tmp_path / "exports").absolute())
    root = policy.prepare_job_root(21)
    safe = root / "analytics-export-21-attempt-1.csv"
    safe.write_bytes(b"safe")
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"outside")
    row = _artifact_row(safe, expires_at=now - timedelta(hours=1))
    row["storage_uri"] = str(outside)
    engine = _Engine(
        [
            _Result(rows=[{"export_job_id": 21}]),
            _Result(scalar="SUCCESS"),
            _Result(rows=[row]),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(),
        ]
    )

    result = SqlAnalyticsExportCleanupService(
        engine,  # type: ignore[arg-type]
        AnalyticsExportFileCleaner(policy),
    ).run_due(now=now, dry_run=False)[0]

    assert result.cleanup_status == "BLOCKED"
    assert result.physical_status == "BLOCKED"
    assert root.is_dir() and safe.is_file() and outside.is_file()
    assert not any(
        "SET status='EXPIRED'" in statement for statement, _ in engine.connection.calls
    )
