from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkerControlState:
    worker_id: str
    worker_kind: str
    state: str
    desired_state: str
    last_seen_at_utc: str


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    worker_id: str
    worker_kind: str
    state: str
    desired_state: str
    started_at_utc: str
    last_seen_at_utc: str
    stopped_at_utc: str | None
    database_name: str
    schema_revision: str
    is_stale: bool


@dataclass(frozen=True, slots=True)
class WorkerFleetHealth:
    observed_at_utc: str
    stale_after_seconds: int
    active_worker_count: int
    ready_worker_count: int
    draining_worker_count: int
    stale_worker_count: int
    failed_worker_count: int
    last_heartbeat_at_utc: str | None
    queued_job_count: int
    oldest_queued_seconds: int | None
    alert_codes: tuple[str, ...]
    workers: tuple[WorkerHealth, ...]


class WorkerOperationsService(Protocol):
    def register(
        self,
        *,
        worker_id: str,
        worker_kind: str,
        database_name: str,
        schema_revision: str,
        host_fingerprint: str,
    ) -> WorkerControlState: ...

    def heartbeat(self, worker_id: str) -> WorkerControlState: ...

    def mark_stopped(
        self, worker_id: str, *, failed: bool = False
    ) -> WorkerControlState: ...

    def list_health(
        self, *, stale_after: timedelta = timedelta(seconds=90)
    ) -> WorkerFleetHealth: ...

    def request_drain(self, worker_id: str) -> WorkerControlState: ...

    def resume(self, worker_id: str) -> WorkerControlState: ...
