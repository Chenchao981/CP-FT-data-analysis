from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.input_requests import (
    ProcessingInputRequestService,
    ResolveLotInputRequests,
)

router = APIRouter()
BUSINESS_DOMAINS = {"engineering": "ENGINEERING", "production": "PRODUCTION"}
TEST_STAGES = {"cp": "CP", "ft": "FT"}


def service(request: Request) -> ProcessingInputRequestService:
    instance = getattr(request.app.state, "processing_input_request_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "Lot补录服务尚未连接数据库",
            503,
        )
    return instance


def _route_identity(business_domain: str, test_stage: str) -> tuple[str, str]:
    domain = BUSINESS_DOMAINS.get(business_domain.strip().lower())
    stage = TEST_STAGES.get(test_stage.strip().lower())
    if domain is None:
        raise DomainError(
            "BUSINESS_DOMAIN_UNSUPPORTED", f"不支持的业务分类：{business_domain}", 404
        )
    if stage is None:
        raise DomainError(
            "TEST_STAGE_UNSUPPORTED", f"不支持的测试阶段：{test_stage}", 404
        )
    return domain, stage


@router.get(
    "/{business_domain}/{test_stage}/uploads/{batch_id}/input-requests"
)
def list_input_requests(
    request: Request,
    business_domain: str,
    test_stage: str,
    batch_id: int,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    domain, stage = _route_identity(business_domain, test_stage)
    return asdict(
        service(request).list_open(principal, domain, stage, batch_id)
    )


@router.post(
    "/{business_domain}/{test_stage}/uploads/{batch_id}/input-requests/resolve"
)
def resolve_input_requests(
    payload: ResolveLotInputRequests,
    request: Request,
    business_domain: str,
    test_stage: str,
    batch_id: int,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    domain, stage = _route_identity(business_domain, test_stage)
    return asdict(
        service(request).resolve(principal, domain, stage, batch_id, payload)
    )
