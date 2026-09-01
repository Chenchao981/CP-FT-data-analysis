from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import current_principal, require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.data_domains import (
    CreateDataDomainGrantRequest,
    CreateDataDomainRequest,
    DataDomainService,
    UpdateDataDomainRequest,
)

router = APIRouter()
require_data_domain_admin = require_permission("DATA_DOMAIN_ADMIN")
data_domain_admin_dependency = Depends(require_data_domain_admin)


def service(request: Request) -> DataDomainService:
    instance = getattr(request.app.state, "data_domain_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED", "数据域授权服务尚未连接数据库", 503
        )
    return instance


@router.get("/data-domains")
def list_my_data_domains(
    request: Request,
    principal: Principal = Depends(current_principal),  # noqa: B008
) -> list[dict]:
    return [asdict(item) for item in service(request).list_for_principal(principal)]


@router.get("/admin/data-domains")
def list_admin_data_domains(
    request: Request,
    _principal: Principal = data_domain_admin_dependency,
) -> list[dict]:
    return [asdict(item) for item in service(request).list_admin()]


@router.get("/admin/data-domains/grantable-users")
def list_grantable_users(
    request: Request,
    _principal: Principal = data_domain_admin_dependency,
) -> list[dict]:
    return [asdict(item) for item in service(request).list_grantable_users()]


@router.post(
    "/admin/data-domains",
    status_code=status.HTTP_201_CREATED,
)
def create_data_domain(
    payload: CreateDataDomainRequest,
    request: Request,
    principal: Principal = data_domain_admin_dependency,
) -> dict:
    return asdict(service(request).create(payload, principal))


@router.put("/admin/data-domains/{data_domain_id}")
def update_data_domain(
    data_domain_id: int,
    payload: UpdateDataDomainRequest,
    request: Request,
    principal: Principal = data_domain_admin_dependency,
) -> dict:
    return asdict(service(request).update(data_domain_id, payload, principal))


@router.post(
    "/admin/data-domains/{data_domain_id}/grants",
    status_code=status.HTTP_201_CREATED,
)
def grant_data_domain(
    data_domain_id: int,
    payload: CreateDataDomainGrantRequest,
    request: Request,
    principal: Principal = data_domain_admin_dependency,
) -> dict:
    return asdict(service(request).grant(data_domain_id, payload, principal))


@router.delete(
    "/admin/data-domains/{data_domain_id}/grants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_data_domain(
    data_domain_id: int,
    user_id: int,
    request: Request,
    principal: Principal = data_domain_admin_dependency,
) -> Response:
    service(request).revoke(data_domain_id, user_id, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
