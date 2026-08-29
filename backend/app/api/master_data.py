from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import current_principal, require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.master_data import (
    ApproveProductCrosswalkRequest,
    MasterDataService,
    RejectProductCrosswalkRequest,
)

router = APIRouter(prefix="/master-data")


def service(request: Request) -> MasterDataService:
    instance = getattr(request.app.state, "master_data_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "主数据映射服务尚未连接数据库",
            503,
        )
    return instance


def require_crosswalk_read(
    principal: Principal = Depends(current_principal),  # noqa: B008
) -> Principal:
    if not (
        principal.can("RULE_GOVERN") or principal.can("MANAGEMENT_READ")
    ):
        raise DomainError(
            "PERMISSION_DENIED",
            "缺少主数据映射读取权限",
            403,
        )
    return principal


@router.get("/product-crosswalks")
def list_product_crosswalks(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(
        default=None, pattern=r"^(PENDING|APPROVED|REJECTED|RETIRED)$"
    ),
    supplier_code: str | None = Query(default=None, min_length=1, max_length=64),
    test_stage: str | None = Query(default=None, pattern=r"^(CP|FT)$"),
    raw_product_code: str | None = Query(
        default=None, min_length=1, max_length=200
    ),
    _principal: Principal = Depends(require_crosswalk_read),  # noqa: B008
) -> dict:
    return asdict(
        service(request).list_product_crosswalks(
            page=page,
            page_size=page_size,
            status=status,
            supplier_code=_clean(supplier_code, upper=True),
            test_stage=test_stage,
            raw_product_code=_clean(raw_product_code),
        )
    )


@router.post("/product-crosswalks/{crosswalk_id}/approve")
def approve_product_crosswalk(
    crosswalk_id: int,
    payload: ApproveProductCrosswalkRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).approve_product_crosswalk(
            crosswalk_id, payload, principal
        )
    )


@router.post("/product-crosswalks/{crosswalk_id}/reject")
def reject_product_crosswalk(
    crosswalk_id: int,
    payload: RejectProductCrosswalkRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).reject_product_crosswalk(
            crosswalk_id, payload, principal
        )
    )


def _clean(value: str | None, *, upper: bool = False) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    return normalized.upper() if upper else normalized
