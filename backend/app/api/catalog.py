from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_permission
from app.api.m2_filters import build_page_filters
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.m2_queries import M2QueryService

router = APIRouter()
CURRENT_DATASET_STATUSES = frozenset({"PUBLISHED"})


def service(request: Request) -> M2QueryService:
    instance = getattr(request.app.state, "m2_query_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "数据查询服务尚未连接数据库", 503)
    return instance


@router.get("/datasets/current")
def list_current_datasets(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    business_domain: str | None = Query(default=None, max_length=16),
    test_stage: str | None = Query(default=None, max_length=16),
    factory_code: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    product_name: str | None = Query(default=None, max_length=200),
    lot_id: str | None = Query(default=None, max_length=128),
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    filters = build_page_filters(
        page=page,
        page_size=page_size,
        business_domain=business_domain,
        test_stage=test_stage,
        factory_code=factory_code,
        status=status_filter,
        product_name=product_name,
        lot_id=lot_id,
        from_utc=from_utc,
        to_utc=to_utc,
        allowed_statuses=CURRENT_DATASET_STATUSES,
    )
    return asdict(service(request).list_current_datasets(principal, filters))
