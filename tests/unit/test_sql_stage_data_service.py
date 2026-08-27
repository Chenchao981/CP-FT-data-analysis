from __future__ import annotations

from contextlib import contextmanager

import pytest
from app.core.errors import DomainError
from app.infrastructure.sql_stage_data_service import SqlStageDataService


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Connection:
    def __init__(self, rowcounts: tuple[int, ...]) -> None:
        self._rowcounts = iter(rowcounts)
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return _Result(next(self._rowcounts))


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def test_worker_only_moves_a_queued_batch_to_processing() -> None:
    connection = _Connection((0,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.worker_mark_processing(7)

    assert exc_info.value.code == "BATCH_STATE_CONFLICT"
    assert "status='QUEUED'" in connection.statements[0]


def test_legacy_mark_queued_cannot_regress_terminal_batches() -> None:
    connection = _Connection((1,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    service.mark_queued(7)

    assert "status='RECEIVED'" in connection.statements[0]
    assert "PROCESSED" not in connection.statements[0]
    assert "FAILED" not in connection.statements[0]


def test_worker_failure_only_changes_active_batch_states() -> None:
    connection = _Connection((1,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    service.mark_failed(7, 41, "validation failed", finish_job=False)

    assert "status IN('QUEUED','PROCESSING')" in connection.statements[0]
