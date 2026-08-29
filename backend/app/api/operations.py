from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.operations import OperationsService

router = APIRouter(prefix="/operations")


def service(request: Request) -> OperationsService:
    instance = getattr(request.app.state, "operations_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "系统运行摘要尚未连接数据库",
            503,
        )
    return instance


@router.get("/consistency")
def consistency_summary(
    request: Request,
    recent_failure_limit: int = Query(default=5, ge=1, le=20),
    _principal: Principal = Depends(require_permission("AUDIT_READ")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).consistency_summary(
            recent_failure_limit=recent_failure_limit
        )
    )
