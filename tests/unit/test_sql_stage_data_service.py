from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.stage_data import StoredUpload
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


class _ScalarResult:
    def __init__(self, scalar=None) -> None:
        self.scalar = scalar

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class _RegisterConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.results = iter((101, None, 201, 301, None))

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return _ScalarResult(next(self.results))


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


def test_registration_does_not_claim_a_false_detected_factory_format(
    tmp_path: Path,
) -> None:
    connection = _RegisterConnection()
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"sample")
    principal = Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        department_code=None,
        roles=("OPERATOR",),
        permissions=frozenset({"TASK_CREATE"}),
    )

    batch_id = service.register_upload(
        principal,
        "PRODUCTION",
        "FT",
        "riyuexin",
        (StoredUpload("sample.xlsx", source, 6, "a" * 64),),
        None,
    )

    assert batch_id == 101
    import_file_sql = next(
        statement
        for statement, _parameters in connection.calls
        if "INSERT ingestion.import_batch_file" in statement
    )
    assert "NULL,NULL" in import_file_sql
    assert "HUAHONG_DCP" not in import_file_sql
