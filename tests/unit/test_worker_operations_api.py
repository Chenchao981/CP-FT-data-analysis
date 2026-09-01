from __future__ import annotations

from datetime import timedelta

from app.api.dependencies import current_principal
from app.api.worker_operations import router
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler
from app.domain.auth import Principal
from app.domain.worker_operations import (
    WorkerControlState,
    WorkerFleetHealth,
    WorkerHealth,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


class StubWorkerOperationsService:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def list_health(
        self, *, stale_after: timedelta = timedelta(seconds=90)
    ) -> WorkerFleetHealth:
        return WorkerFleetHealth(
            observed_at_utc="2026-08-29T02:00:00.000Z",
            stale_after_seconds=int(stale_after.total_seconds()),
            active_worker_count=1,
            ready_worker_count=1,
            draining_worker_count=0,
            stale_worker_count=0,
            failed_worker_count=0,
            last_heartbeat_at_utc="2026-08-29T01:59:59.000Z",
            queued_job_count=3,
            oldest_queued_seconds=42,
            alert_codes=(),
            workers=(
                WorkerHealth(
                    worker_id="route-a-0123456789abcdef",
                    worker_kind="ROUTE_A",
                    state="READY",
                    desired_state="RUN",
                    started_at_utc="2026-08-29T01:00:00.000Z",
                    last_seen_at_utc="2026-08-29T01:59:59.000Z",
                    stopped_at_utc=None,
                    database_name="TMS_G0_DEV",
                    schema_revision="sql2014_0016",
                    is_stale=False,
                ),
            ),
        )

    def request_drain(self, worker_id: str) -> WorkerControlState:
        self.actions.append(("DRAIN", worker_id))
        return self._control(worker_id, "READY", "DRAIN")

    def resume(self, worker_id: str) -> WorkerControlState:
        self.actions.append(("RUN", worker_id))
        return self._control(worker_id, "DRAINING", "RUN")

    @staticmethod
    def _control(
        worker_id: str, state: str, desired_state: str
    ) -> WorkerControlState:
        return WorkerControlState(
            worker_id=worker_id,
            worker_kind="ROUTE_A",
            state=state,
            desired_state=desired_state,
            last_seen_at_utc="2026-08-29T01:59:59.000Z",
        )


def _principal(
    *, admin: bool, audit: bool = True, system_operate: bool = False
) -> Principal:
    permissions = {"AUDIT_READ"} if audit else {"DATASET_READ"}
    if system_operate:
        permissions.add("SYSTEM_OPERATE")
    return Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        roles=("SYSTEM_ADMIN",) if admin else ("OPERATOR",),
        permissions=frozenset(permissions),
    )


def _client(
    operations: StubWorkerOperationsService | None,
    principal: Principal,
) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.state.worker_operations_service = operations
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_worker_health_requires_audit_and_does_not_expose_host_identity() -> None:
    response = _client(
        StubWorkerOperationsService(), _principal(admin=False)
    ).get("/api/v1/operations/workers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_worker_count"] == 1
    assert payload["queued_job_count"] == 3
    assert payload["oldest_queued_seconds"] == 42
    assert payload["workers"][0]["schema_revision"] == "sql2014_0016"
    assert "host" not in str(payload).lower()
    assert "path" not in str(payload).lower()

    denied = _client(
        StubWorkerOperationsService(), _principal(admin=False, audit=False)
    ).get("/api/v1/operations/workers")
    assert denied.status_code == 403


def test_worker_control_uses_explicit_system_operate_permission_not_role_name() -> None:
    operations = StubWorkerOperationsService()
    worker_id = "route-a-0123456789abcdef"

    denied = _client(operations, _principal(admin=False)).post(
        f"/api/v1/operations/workers/{worker_id}/drain"
    )
    assert denied.status_code == 403
    assert operations.actions == []

    role_only = _client(operations, _principal(admin=True)).post(
        f"/api/v1/operations/workers/{worker_id}/drain"
    )
    assert role_only.status_code == 403
    assert operations.actions == []

    client = _client(
        operations, _principal(admin=False, system_operate=True)
    )
    drained = client.post(f"/api/v1/operations/workers/{worker_id}/drain")
    resumed = client.post(f"/api/v1/operations/workers/{worker_id}/resume")

    assert drained.status_code == 200
    assert drained.json()["desired_state"] == "DRAIN"
    assert resumed.status_code == 200
    assert resumed.json()["desired_state"] == "RUN"
    assert operations.actions == [("DRAIN", worker_id), ("RUN", worker_id)]


def test_worker_health_fails_closed_without_service_registration() -> None:
    response = _client(None, _principal(admin=True)).get(
        "/api/v1/operations/workers"
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"
