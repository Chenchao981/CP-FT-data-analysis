from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.worker_operations import WorkerOperationsService

router = APIRouter(prefix="/operations/workers")
require_worker_control = require_permission("SYSTEM_OPERATE")


def service(request: Request) -> WorkerOperationsService:
    instance = getattr(request.app.state, "worker_operations_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "Worker 运维服务尚未连接数据库",
            503,
        )
    return instance


@router.get("")
def worker_health(
    request: Request,
    stale_after_seconds: int = Query(default=90, ge=5, le=86400),
    _principal: Principal = Depends(require_permission("AUDIT_READ")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).list_health(
            stale_after=timedelta(seconds=stale_after_seconds)
        )
    )


@router.post("/{worker_id}/drain")
def request_worker_drain(
    worker_id: str,
    request: Request,
    _principal: Principal = Depends(require_worker_control),  # noqa: B008
) -> dict:
    return asdict(service(request).request_drain(worker_id))


@router.post("/{worker_id}/resume")
def resume_worker(
    worker_id: str,
    request: Request,
    _principal: Principal = Depends(require_worker_control),  # noqa: B008
) -> dict:
    return asdict(service(request).resume(worker_id))
