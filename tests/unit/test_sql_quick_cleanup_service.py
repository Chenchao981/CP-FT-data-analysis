from __future__ import annotations

from pathlib import Path

from app.infrastructure.quick_artifact_cleanup import QuickArtifactFileCleaner
from app.infrastructure.sql_quick_cleanup_service import SqlQuickCleanupService


class _Rows:
    def mappings(self):
        return self

    def all(self) -> list:
        return []


class _Connection:
    def __init__(self) -> None:
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, _parameters):
        self.sql = str(statement)
        return _Rows()


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self):
        return self.connection


def test_due_cleanup_excludes_successful_result_history(tmp_path: Path) -> None:
    engine = _Engine()
    service = SqlQuickCleanupService(  # type: ignore[arg-type]
        engine,
        QuickArtifactFileCleaner(tmp_path / "workspace"),
    )

    assert service.run_due() == ()
    assert "s.status IN('FAILED','CANCELLED')" in engine.connection.sql
    assert "s.status IN('SUCCESS','FAILED','CANCELLED')" not in engine.connection.sql
