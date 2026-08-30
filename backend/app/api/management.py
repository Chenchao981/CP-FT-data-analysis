from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.management import ManagementService

router = APIRouter(prefix="/management")


def service(request: Request) -> ManagementService:
    instance = getattr(request.app.state, "management_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "管理质量摘要尚未连接数据库",
            503,
        )
    return instance


@router.get("/quality-summary")
def quality_summary(
    request: Request,
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    business_domain: str | None = Query(
        default=None, pattern=r"^(ENGINEERING|PRODUCTION)$"
    ),
    test_stage: str | None = Query(default=None, pattern=r"^(CP|FT)$"),
    factory_code: str | None = Query(default=None, min_length=1, max_length=64),
    product_name: str | None = Query(default=None, min_length=1, max_length=200),
    lot_id: str | None = Query(default=None, min_length=1, max_length=128),
    recent_limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_permission("MANAGEMENT_READ")),  # noqa: B008
) -> dict:
    upper = _as_utc(to_utc or datetime.now(UTC))
    lower = _as_utc(from_utc or (upper - timedelta(days=30)))
    if lower >= upper:
        raise DomainError(
            "QUALITY_TIME_RANGE_INVALID",
            "质量汇总开始时间必须早于结束时间",
            422,
        )
    if upper - lower > timedelta(days=366 * 5):
        raise DomainError(
            "QUALITY_TIME_RANGE_TOO_WIDE",
            "单次质量汇总时间范围不能超过五年",
            422,
        )
    return asdict(
        service(request).quality_summary(
            principal=principal,
            from_utc=lower,
            to_utc=upper,
            business_domain=business_domain,
            test_stage=test_stage,
            factory_code=_clean(factory_code),
            product_name=_clean(product_name),
            lot_id=_clean(lot_id),
            recent_limit=recent_limit,
        )
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None
