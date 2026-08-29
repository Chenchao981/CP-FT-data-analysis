from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest
from app.domain.worker_operations import WorkerControlState

from scripts.run_route_a_worker import (
    WorkerRegistrationMonitor,
    default_worker_id,
    is_stop_requested,
    remove_ready_file,
    run_worker_loop,
    validate_database_identity,
    worker_host_fingerprint,
    write_ready_file,
)


def test_stop_file_requests_drain_only_after_it_exists(tmp_path: Path) -> None:
    stop_file = tmp_path / "worker.stop"

    assert is_stop_requested(None) is False
    assert is_stop_requested(stop_file) is False

    stop_file.write_text("stop", encoding="ascii")

    assert is_stop_requested(stop_file) is True


def test_ready_file_is_atomic_and_removed_on_shutdown(tmp_path: Path, monkeypatch) -> None:
    ready_file = tmp_path / "worker.ready.json"
    monkeypatch.setattr("scripts.run_route_a_worker.os.getpid", lambda: 4321)

    write_ready_file(
        ready_file,
        "local-test-worker",
        {
            "database": "TMS_G0_DEV",
            "schema_revision": "sql2014_0014",
            "database_server": "LOCALHOST\\SQLEXPRESS",
        },
    )

    payload = ready_file.read_text(encoding="utf-8")
    assert '"status": "READY"' in payload
    assert '"pid": 4321' in payload
    assert '"worker_id": "local-test-worker"' in payload
    assert '"database": "TMS_G0_DEV"' in payload
    assert '"schema_revision": "sql2014_0014"' in payload
    assert '"database_server": "LOCALHOST\\\\SQLEXPRESS"' in payload
    assert list(tmp_path.glob("*.tmp")) == []

    remove_ready_file(ready_file)

    assert ready_file.exists() is False


def test_worker_database_identity_fails_closed_before_queue_processing() -> None:
    metadata = {
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0014",
        "database_server": "SQL-B",
    }

    with pytest.raises(RuntimeError, match="Worker database identity rejected"):
        validate_database_identity(
            metadata,
            expected_database="TMS_G0_DEV",
            expected_schema_revision="sql2014_0014",
            expected_database_server="SQL-A",
        )


def test_default_worker_identity_uses_a_host_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_route_a_worker.socket.gethostname",
        lambda: "Sensitive-Host-01",
    )

    fingerprint = worker_host_fingerprint()
    worker_id = default_worker_id()

    assert len(fingerprint) == 64
    assert worker_id == f"route-a-{fingerprint[:16]}"
    assert "Sensitive-Host-01" not in worker_id


def test_database_drain_stops_new_claims_after_current_job() -> None:
    class Operations:
        def __init__(self) -> None:
            self.calls = 0

        def heartbeat(self, worker_id: str) -> WorkerControlState:
            self.calls += 1
            desired = "RUN" if self.calls == 1 else "DRAIN"
            state = "READY" if desired == "RUN" else "DRAINING"
            return WorkerControlState(
                worker_id=worker_id,
                worker_kind="ROUTE_A",
                state=state,
                desired_state=desired,
                last_seen_at_utc="2026-08-29T02:00:00.000Z",
            )

    class Worker:
        def __init__(self) -> None:
            self.claims = 0

        def run_once(self):
            self.claims += 1

    initial = WorkerControlState(
        worker_id="route-a-test",
        worker_kind="ROUTE_A",
        state="READY",
        desired_state="RUN",
        last_seen_at_utc="2026-08-29T02:00:00.000Z",
    )
    operations = Operations()
    worker = Worker()
    monitor = WorkerRegistrationMonitor(
        operations,  # type: ignore[arg-type]
        "route-a-test",
        heartbeat_every=60,
        initial=initial,
    )

    outcome = run_worker_loop(
        worker,  # type: ignore[arg-type]
        monitor,
        once=False,
        poll_seconds=0.1,
        stop_file=None,
        logger=logging.getLogger("test-worker-drain"),
        sleep=lambda _seconds: None,
    )

    assert outcome == "DATABASE_DRAIN"
    assert worker.claims == 1
    assert operations.calls == 2


def test_local_stop_file_remains_a_graceful_no_claim_control(tmp_path: Path) -> None:
    class Operations:
        def heartbeat(self, worker_id: str):
            raise AssertionError("local stop must be checked before heartbeat or claim")

    class Worker:
        def run_once(self):
            raise AssertionError("local stop must not claim work")

    stop_file = tmp_path / "worker.stop"
    stop_file.write_text("stop", encoding="ascii")
    initial = WorkerControlState(
        worker_id="route-a-test",
        worker_kind="ROUTE_A",
        state="READY",
        desired_state="RUN",
        last_seen_at_utc="2026-08-29T02:00:00.000Z",
    )
    monitor = WorkerRegistrationMonitor(
        Operations(),  # type: ignore[arg-type]
        "route-a-test",
        heartbeat_every=60,
        initial=initial,
    )

    outcome = run_worker_loop(
        Worker(),  # type: ignore[arg-type]
        monitor,
        once=False,
        poll_seconds=0.1,
        stop_file=stop_file,
        logger=logging.getLogger("test-worker-stop"),
    )

    assert outcome == "LOCAL_STOP"


def test_registry_heartbeat_runs_while_the_main_worker_may_be_busy() -> None:
    heartbeat_seen = threading.Event()

    class Operations:
        def heartbeat(self, worker_id: str) -> WorkerControlState:
            heartbeat_seen.set()
            return WorkerControlState(
                worker_id=worker_id,
                worker_kind="ROUTE_A",
                state="READY",
                desired_state="RUN",
                last_seen_at_utc="2026-08-29T02:00:00.000Z",
            )

    initial = WorkerControlState(
        worker_id="route-a-test",
        worker_kind="ROUTE_A",
        state="READY",
        desired_state="RUN",
        last_seen_at_utc="2026-08-29T02:00:00.000Z",
    )
    monitor = WorkerRegistrationMonitor(
        Operations(),  # type: ignore[arg-type]
        "route-a-test",
        heartbeat_every=0.01,
        initial=initial,
    )

    monitor.start()
    try:
        assert heartbeat_seen.wait(timeout=0.5)
    finally:
        monitor.stop()


def test_entrypoint_validates_identity_before_registering_or_claiming() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "scripts" / "run_route_a_worker.py"
    ).read_text(encoding="utf-8-sig")

    validate_at = source.index("validate_database_identity(", source.index("def main"))
    register_at = source.index("operations.register(", validate_at)
    ready_at = source.index("write_ready_file(", register_at)
    loop_at = source.index("run_worker_loop(", ready_at)

    assert validate_at < register_at < ready_at < loop_at
