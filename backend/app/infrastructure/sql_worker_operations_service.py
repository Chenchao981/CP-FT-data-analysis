from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.worker_operations import (
    WorkerControlState,
    WorkerFleetHealth,
    WorkerHealth,
)

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HOST_FINGERPRINT = re.compile(r"^[0-9a-fA-F]{64}$")
_ACTIVE_STATES = frozenset({"READY", "DRAINING"})
_CONTROL_COLUMNS = (
    "worker_id,worker_kind,state,desired_state,last_seen_at_utc"
)


def _as_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise DomainError(
            "WORKER_REGISTRY_INVALID",
            "Worker 注册表包含无效时间",
            503,
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_utc(value: Any) -> str:
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_worker_id(worker_id: str) -> str:
    value = worker_id.strip()
    if not _WORKER_ID.fullmatch(value):
        raise DomainError(
            "WORKER_ID_INVALID",
            "Worker 标识仅允许字母、数字、点、下划线和连字符",
            422,
        )
    return value


def _safe_label(value: str, *, field: str, max_length: int = 128) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(marker in normalized for marker in ("/", "\\", "@", "\r", "\n", "\0"))
    ):
        raise DomainError(
            "WORKER_IDENTITY_INVALID",
            f"Worker {field} 标识无效",
            422,
        )
    return normalized


def _to_control(row: Mapping[str, Any]) -> WorkerControlState:
    return WorkerControlState(
        worker_id=str(row["worker_id"]),
        worker_kind=str(row["worker_kind"]),
        state=str(row["state"]),
        desired_state=str(row["desired_state"]),
        last_seen_at_utc=_iso_utc(row["last_seen_at_utc"]),
    )


class SqlWorkerOperationsService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(
        self,
        *,
        worker_id: str,
        worker_kind: str,
        database_name: str,
        schema_revision: str,
        host_fingerprint: str,
    ) -> WorkerControlState:
        worker = _safe_worker_id(worker_id)
        kind = worker_kind.strip().upper()
        if kind != "ROUTE_A":
            raise DomainError(
                "WORKER_KIND_INVALID",
                "当前注册表仅允许 ROUTE_A Worker",
                422,
            )
        database = _safe_label(database_name, field="database")
        revision = _safe_label(schema_revision, field="schema")
        fingerprint = host_fingerprint.strip().lower()
        if not _HOST_FINGERPRINT.fullmatch(fingerprint):
            raise DomainError(
                "WORKER_HOST_ID_INVALID",
                "Worker 主机指纹必须为 SHA-256",
                422,
            )
        try:
            with self._engine.begin() as connection:
                existing = (
                    connection.execute(
                        text(
                            "SELECT desired_state FROM ingestion.worker_instance "
                            "WITH (UPDLOCK,HOLDLOCK) WHERE worker_id=:worker_id"
                        ),
                        {"worker_id": worker},
                    )
                    .mappings()
                    .one_or_none()
                )
                desired_state = (
                    str(existing["desired_state"]) if existing is not None else "RUN"
                )
                state = "DRAINING" if desired_state == "DRAIN" else "READY"
                parameters = {
                    "worker_id": worker,
                    "worker_kind": kind,
                    "state": state,
                    "desired_state": desired_state,
                    "database_name": database,
                    "schema_revision": revision,
                    "host_fingerprint": fingerprint,
                }
                if existing is None:
                    statement = text(
                        "INSERT ingestion.worker_instance("
                        "worker_id,worker_kind,state,desired_state,started_at_utc,"
                        "last_seen_at_utc,stopped_at_utc,database_name,schema_revision,"
                        "host_fingerprint,control_updated_at_utc) "
                        f"OUTPUT {', '.join('INSERTED.' + item for item in _CONTROL_COLUMNS.split(','))} "
                        "VALUES(:worker_id,:worker_kind,:state,:desired_state,"
                        "SYSUTCDATETIME(),SYSUTCDATETIME(),NULL,"
                        ":database_name,:schema_revision,:host_fingerprint,"
                        "SYSUTCDATETIME())"
                    )
                else:
                    statement = text(
                        "UPDATE ingestion.worker_instance SET worker_kind=:worker_kind,"
                        "state=:state,started_at_utc=SYSUTCDATETIME(),"
                        "last_seen_at_utc=SYSUTCDATETIME(),"
                        "stopped_at_utc=NULL,database_name=:database_name,"
                        "schema_revision=:schema_revision,host_fingerprint=:host_fingerprint,"
                        "desired_state=:desired_state "
                        f"OUTPUT {', '.join('INSERTED.' + item for item in _CONTROL_COLUMNS.split(','))} "
                        "WHERE worker_id=:worker_id"
                    )
                row = connection.execute(statement, parameters).mappings().one()
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "WORKER_REGISTRATION_UNAVAILABLE",
                "Worker 注册暂时不可用",
                503,
            ) from exc
        return _to_control(row)

    def heartbeat(self, worker_id: str) -> WorkerControlState:
        worker = _safe_worker_id(worker_id)
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        text(
                            "UPDATE ingestion.worker_instance WITH (UPDLOCK,HOLDLOCK) "
                            "SET state=CASE WHEN desired_state='DRAIN' THEN 'DRAINING' "
                            "ELSE 'READY' END,last_seen_at_utc=SYSUTCDATETIME(),"
                            "stopped_at_utc=NULL "
                            f"OUTPUT {', '.join('INSERTED.' + item for item in _CONTROL_COLUMNS.split(','))} "
                            "WHERE worker_id=:worker_id AND state IN('READY','DRAINING')"
                        ),
                        {"worker_id": worker},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise DomainError(
                        "WORKER_REGISTRATION_LOST",
                        "Worker 注册不存在或已经停止",
                        409,
                    )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "WORKER_HEARTBEAT_UNAVAILABLE",
                "Worker 心跳暂时不可用",
                503,
            ) from exc
        return _to_control(row)

    def mark_stopped(
        self, worker_id: str, *, failed: bool = False
    ) -> WorkerControlState:
        worker = _safe_worker_id(worker_id)
        try:
            with self._engine.begin() as connection:
                current = (
                    connection.execute(
                        text(
                            f"SELECT {_CONTROL_COLUMNS},stopped_at_utc "
                            "FROM ingestion.worker_instance WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE worker_id=:worker_id"
                        ),
                        {"worker_id": worker},
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise DomainError(
                        "WORKER_NOT_FOUND",
                        "Worker 注册不存在",
                        404,
                    )
                if str(current["state"]) in {"STOPPED", "FAILED"}:
                    return _to_control(current)
                row = (
                    connection.execute(
                        text(
                            "UPDATE ingestion.worker_instance SET state=:state,"
                            "last_seen_at_utc=SYSUTCDATETIME(),"
                            "stopped_at_utc=SYSUTCDATETIME() "
                            f"OUTPUT {', '.join('INSERTED.' + item for item in _CONTROL_COLUMNS.split(','))} "
                            "WHERE worker_id=:worker_id"
                        ),
                        {
                            "worker_id": worker,
                            "state": "FAILED" if failed else "STOPPED",
                        },
                    )
                    .mappings()
                    .one()
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "WORKER_STOP_UNAVAILABLE",
                "Worker 停止状态暂时无法登记",
                503,
            ) from exc
        return _to_control(row)

    def request_drain(self, worker_id: str) -> WorkerControlState:
        return self._set_desired_state(worker_id, "DRAIN")

    def resume(self, worker_id: str) -> WorkerControlState:
        return self._set_desired_state(worker_id, "RUN")

    def _set_desired_state(
        self, worker_id: str, desired_state: str
    ) -> WorkerControlState:
        worker = _safe_worker_id(worker_id)
        try:
            with self._engine.begin() as connection:
                current = (
                    connection.execute(
                        text(
                            "SELECT state FROM ingestion.worker_instance "
                            "WITH (UPDLOCK,HOLDLOCK) WHERE worker_id=:worker_id"
                        ),
                        {"worker_id": worker},
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise DomainError(
                        "WORKER_NOT_FOUND",
                        "Worker 注册不存在",
                        404,
                    )
                row = (
                    connection.execute(
                        text(
                            "UPDATE ingestion.worker_instance SET desired_state=:desired_state,"
                            "control_updated_at_utc=SYSUTCDATETIME() "
                            f"OUTPUT {', '.join('INSERTED.' + item for item in _CONTROL_COLUMNS.split(','))} "
                            "WHERE worker_id=:worker_id"
                        ),
                        {
                            "worker_id": worker,
                            "desired_state": desired_state,
                        },
                    )
                    .mappings()
                    .one()
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "WORKER_CONTROL_UNAVAILABLE",
                "Worker 控制指令暂时无法写入",
                503,
            ) from exc
        return _to_control(row)

    def list_health(
        self, *, stale_after: timedelta = timedelta(seconds=90)
    ) -> WorkerFleetHealth:
        stale_after_seconds = int(stale_after.total_seconds())
        if stale_after_seconds < 5 or stale_after_seconds > 86400:
            raise DomainError(
                "WORKER_STALE_THRESHOLD_INVALID",
                "Worker 心跳过期阈值必须在 5 秒到 24 小时之间",
                422,
            )
        try:
            with self._engine.connect() as connection:
                observed_at = connection.execute(
                    text("SELECT CAST(SYSUTCDATETIME() AS datetime2(3))")
                ).scalar_one()
                rows = (
                    connection.execute(
                        text(
                            "SELECT worker_id,worker_kind,state,desired_state,"
                            "started_at_utc,last_seen_at_utc,stopped_at_utc,"
                            "database_name,schema_revision "
                            "FROM ingestion.worker_instance "
                            "ORDER BY worker_kind,worker_id"
                        )
                    )
                    .mappings()
                    .all()
                )
                queue = (
                    connection.execute(
                        text(
                            "SELECT COUNT_BIG(*) AS queued_job_count,"
                            "CASE WHEN COUNT_BIG(*)=0 THEN NULL ELSE "
                            "DATEDIFF(SECOND,MIN(requested_at_utc),SYSUTCDATETIME()) END "
                            "AS oldest_queued_seconds "
                            "FROM ingestion.processing_job WHERE status='QUEUED' "
                            "AND job_type IN('INITIAL_IMPORT','QUICK_PAT') "
                            "AND not_before_utc<=SYSUTCDATETIME()"
                        )
                    )
                    .mappings()
                    .one()
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "WORKER_HEALTH_UNAVAILABLE",
                "Worker 运行状态暂时不可用",
                503,
            ) from exc

        observed = _as_utc(observed_at)
        stale_before = observed - stale_after
        workers = tuple(
            WorkerHealth(
                worker_id=str(row["worker_id"]),
                worker_kind=str(row["worker_kind"]),
                state=str(row["state"]),
                desired_state=str(row["desired_state"]),
                started_at_utc=_iso_utc(row["started_at_utc"]),
                last_seen_at_utc=_iso_utc(row["last_seen_at_utc"]),
                stopped_at_utc=(
                    _iso_utc(row["stopped_at_utc"])
                    if row["stopped_at_utc"] is not None
                    else None
                ),
                database_name=str(row["database_name"]),
                schema_revision=str(row["schema_revision"]),
                is_stale=(
                    str(row["state"]) in _ACTIVE_STATES
                    and _as_utc(row["last_seen_at_utc"]) < stale_before
                ),
            )
            for row in rows
        )
        active = tuple(
            item
            for item in workers
            if item.state in _ACTIVE_STATES and not item.is_stale
        )
        ready_count = sum(item.state == "READY" for item in active)
        draining_count = sum(item.state == "DRAINING" for item in active)
        stale_count = sum(item.is_stale for item in workers)
        failed_count = sum(item.state == "FAILED" for item in workers)
        queued_count = int(queue["queued_job_count"])
        alert_codes: list[str] = []
        if stale_count:
            alert_codes.append("WORKER_HEARTBEAT_STALE")
        if failed_count:
            alert_codes.append("WORKER_FAILED")
        if not active:
            alert_codes.append("NO_ACTIVE_WORKER")
        if queued_count and not ready_count:
            alert_codes.append("QUEUE_BACKLOG_WITHOUT_READY_WORKER")
        if any(item.desired_state == "DRAIN" for item in workers):
            alert_codes.append("WORKER_DRAIN_REQUESTED")
        last_heartbeat = max(
            (_as_utc(row["last_seen_at_utc"]) for row in rows),
            default=None,
        )
        oldest_queued = queue["oldest_queued_seconds"]
        return WorkerFleetHealth(
            observed_at_utc=_iso_utc(observed),
            stale_after_seconds=stale_after_seconds,
            active_worker_count=len(active),
            ready_worker_count=ready_count,
            draining_worker_count=draining_count,
            stale_worker_count=stale_count,
            failed_worker_count=failed_count,
            last_heartbeat_at_utc=(
                _iso_utc(last_heartbeat) if last_heartbeat is not None else None
            ),
            queued_job_count=queued_count,
            oldest_queued_seconds=(
                int(oldest_queued) if oldest_queued is not None else None
            ),
            alert_codes=tuple(alert_codes),
            workers=workers,
        )
