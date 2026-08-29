from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core.errors import DomainError
from app.infrastructure.sql_worker_operations_service import (
    SqlWorkerOperationsService,
)


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]

    def one_or_none(self):
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self._results = iter(results)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return next(self._results)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection

    @contextmanager
    def connect(self):
        yield self.connection


def _control_row(
    *, state: str = "READY", desired_state: str = "RUN"
) -> dict[str, Any]:
    return {
        "worker_id": "route-a-0123456789abcdef",
        "worker_kind": "ROUTE_A",
        "state": state,
        "desired_state": desired_state,
        "last_seen_at_utc": datetime(2026, 8, 29, 1, 2, 3, tzinfo=UTC),
    }


def test_register_preserves_durable_drain_and_uses_transaction_lock() -> None:
    connection = _Connection(
        [
            _Result(rows=[{"desired_state": "DRAIN"}]),
            _Result(rows=[_control_row(state="DRAINING", desired_state="DRAIN")]),
        ]
    )
    service = SqlWorkerOperationsService(_Engine(connection))  # type: ignore[arg-type]

    registered = service.register(
        worker_id="route-a-0123456789abcdef",
        worker_kind="route_a",
        database_name="TMS_G0_DEV",
        schema_revision="sql2014_0016",
        host_fingerprint="a" * 64,
    )

    assert registered.state == "DRAINING"
    assert registered.desired_state == "DRAIN"
    assert "WITH (UPDLOCK,HOLDLOCK)" in connection.calls[0][0]
    update_sql, parameters = connection.calls[1]
    assert "started_at_utc=SYSUTCDATETIME()" in update_sql
    assert parameters is not None
    assert parameters["host_fingerprint"] == "a" * 64
    assert "host_name" not in parameters
    assert "account" not in parameters


def test_heartbeat_observes_drain_without_accepting_terminal_registration() -> None:
    connection = _Connection(
        [_Result(rows=[_control_row(state="DRAINING", desired_state="DRAIN")])]
    )
    service = SqlWorkerOperationsService(_Engine(connection))  # type: ignore[arg-type]

    control = service.heartbeat("route-a-0123456789abcdef")

    assert control.state == "DRAINING"
    sql = connection.calls[0][0]
    assert "WITH (UPDLOCK,HOLDLOCK)" in sql
    assert "state IN('READY','DRAINING')" in sql
    assert "desired_state='DRAIN'" in sql


def test_request_drain_and_resume_change_only_worker_control() -> None:
    drain_connection = _Connection(
        [
            _Result(rows=[{"state": "READY"}]),
            _Result(rows=[_control_row(state="READY", desired_state="DRAIN")]),
        ]
    )
    service = SqlWorkerOperationsService(  # type: ignore[arg-type]
        _Engine(drain_connection)
    )

    drained = service.request_drain("route-a-0123456789abcdef")

    assert drained.state == "READY"
    assert drained.desired_state == "DRAIN"
    assert "WITH (UPDLOCK,HOLDLOCK)" in drain_connection.calls[0][0]
    assert "state=:state" not in drain_connection.calls[1][0]
    assert all(
        "processing_job" not in sql and "import_batch" not in sql
        for sql, _parameters in drain_connection.calls
    )

    resume_connection = _Connection(
        [
            _Result(rows=[{"state": "DRAINING"}]),
            _Result(rows=[_control_row(state="DRAINING", desired_state="RUN")]),
        ]
    )
    resumed = SqlWorkerOperationsService(  # type: ignore[arg-type]
        _Engine(resume_connection)
    ).resume("route-a-0123456789abcdef")
    assert resumed.state == "DRAINING"
    assert resumed.desired_state == "RUN"


def test_mark_stopped_records_a_terminal_state() -> None:
    connection = _Connection(
        [
            _Result(rows=[{**_control_row(), "stopped_at_utc": None}]),
            _Result(rows=[_control_row(state="FAILED")]),
        ]
    )

    stopped = SqlWorkerOperationsService(  # type: ignore[arg-type]
        _Engine(connection)
    ).mark_stopped("route-a-0123456789abcdef", failed=True)

    assert stopped.state == "FAILED"
    assert connection.calls[1][1] is not None
    assert connection.calls[1][1]["state"] == "FAILED"


def test_list_health_reports_stale_backlog_and_never_returns_host_identity() -> None:
    observed = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
    connection = _Connection(
        [
            _Result(scalar=observed),
            _Result(
                rows=[
                    {
                        "worker_id": "route-a-healthy",
                        "worker_kind": "ROUTE_A",
                        "state": "READY",
                        "desired_state": "RUN",
                        "started_at_utc": observed - timedelta(hours=1),
                        "last_seen_at_utc": observed - timedelta(seconds=3),
                        "stopped_at_utc": None,
                        "database_name": "TMS_G0_DEV",
                        "schema_revision": "sql2014_0016",
                    },
                    {
                        "worker_id": "route-a-stale",
                        "worker_kind": "ROUTE_A",
                        "state": "DRAINING",
                        "desired_state": "DRAIN",
                        "started_at_utc": observed - timedelta(hours=2),
                        "last_seen_at_utc": observed - timedelta(minutes=5),
                        "stopped_at_utc": None,
                        "database_name": "TMS_G0_DEV",
                        "schema_revision": "sql2014_0016",
                    },
                ]
            ),
            _Result(rows=[{"queued_job_count": 4, "oldest_queued_seconds": 81}]),
        ]
    )

    health = SqlWorkerOperationsService(  # type: ignore[arg-type]
        _Engine(connection)
    ).list_health(stale_after=timedelta(seconds=90))

    assert health.active_worker_count == 1
    assert health.ready_worker_count == 1
    assert health.stale_worker_count == 1
    assert health.queued_job_count == 4
    assert health.oldest_queued_seconds == 81
    assert "WORKER_HEARTBEAT_STALE" in health.alert_codes
    assert "WORKER_DRAIN_REQUESTED" in health.alert_codes
    assert all(not hasattr(worker, "host_fingerprint") for worker in health.workers)
    health_sql = connection.calls[1][0]
    assert "host_fingerprint" not in health_sql
    queue_sql = connection.calls[2][0]
    assert "job_type IN('INITIAL_IMPORT','QUICK_PAT')" in queue_sql


@pytest.mark.parametrize("threshold", [timedelta(seconds=4), timedelta(days=2)])
def test_list_health_rejects_unbounded_stale_threshold(threshold: timedelta) -> None:
    with pytest.raises(DomainError) as exc_info:
        SqlWorkerOperationsService(  # type: ignore[arg-type]
            _Engine(_Connection([]))
        ).list_health(stale_after=threshold)

    assert exc_info.value.code == "WORKER_STALE_THRESHOLD_INVALID"


def test_worker_registry_rejects_path_or_account_shaped_identifiers() -> None:
    service = SqlWorkerOperationsService(_Engine(_Connection([])))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.register(
            worker_id=r"DOMAIN\\worker",
            worker_kind="ROUTE_A",
            database_name="TMS_G0_DEV",
            schema_revision="sql2014_0016",
            host_fingerprint="a" * 64,
        )

    assert exc_info.value.code == "WORKER_ID_INVALID"


def test_worker_health_database_failure_is_sanitized() -> None:
    class BrokenEngine:
        @contextmanager
        def connect(self):
            raise RuntimeError("server=secret;password=secret")
            yield

    with pytest.raises(DomainError) as exc_info:
        SqlWorkerOperationsService(BrokenEngine()).list_health()  # type: ignore[arg-type]

    assert exc_info.value.code == "WORKER_HEALTH_UNAVAILABLE"
    assert "secret" not in exc_info.value.message
