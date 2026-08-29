from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.infrastructure.formal_artifact_files import (
    FormalArtifactFileCleaner,
    ManagedJobPathPolicy,
)
from app.infrastructure.sql_formal_artifact_cleanup import (
    SqlFormalArtifactCleanupService,
)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.calls: list[str] = []

    def execute(self, statement, parameters=None):
        self.calls.append(str(statement))
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


def test_formal_cleanup_dry_run_is_default_and_has_no_database_or_file_mutation(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "work").absolute()
    attempt = root / "81" / "attempt-1"
    attempt.mkdir(parents=True)
    artifact = attempt / "latest.xlsx"
    artifact.write_bytes(b"latest")
    connection = _Connection(
        [
            _Result([{"job_id": 81}]),
            _Result(
                [
                    {
                        "processing_artifact_id": 3,
                        "artifact_role": "EXPORT",
                        "storage_uri": str(artifact),
                        "file_size": len(b"latest"),
                        "sha256": "a" * 64,
                        "physical_status": "PRESENT",
                    }
                ]
            ),
        ]
    )
    engine = _Engine(connection)
    service = SqlFormalArtifactCleanupService(
        engine,  # type: ignore[arg-type]
        FormalArtifactFileCleaner(ManagedJobPathPolicy(root)),
    )

    results = service.run_due(limit=1)

    assert results[0].cleanup_status == "DRY_RUN"
    assert artifact.is_file()
    assert engine.begin_calls == 0
    assert all("UPDATE " not in sql.upper() for sql in connection.calls)
    candidate_sql = connection.calls[0]
    assert "'INITIAL_IMPORT','EXPORT_LATEST','REPROCESS_UPDATE'" in candidate_sql
    assert "QUICK_PAT" not in candidate_sql
    assert "test." not in candidate_sql
    assert "source_file" not in candidate_sql
