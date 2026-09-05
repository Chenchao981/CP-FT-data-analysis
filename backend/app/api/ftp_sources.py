from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import current_principal, require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.ftp_sources import FtpSourceCreate, FtpSourceToggle
from app.infrastructure.ftp_credentials import read_ftp_credential
from app.infrastructure.ftp_storage import ftp_connection, read_mlsd

router = APIRouter()


def service(request: Request):
    instance = getattr(request.app.state, "ftp_source_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "FTP 数据源服务尚未连接数据库", 503)
    return instance


@router.get("")
def list_sources(request: Request, principal: Principal = Depends(current_principal)):
    return service(request).list(principal)


@router.get("/options")
def options(request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    return service(request).options(principal)


@router.post("", status_code=201)
def create_source(payload: FtpSourceCreate, request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    return service(request).create(principal, payload)


@router.patch("/{source_id}/state")
def toggle(source_id: int, payload: FtpSourceToggle, request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    return service(request).control(principal, source_id, active=payload.active)


@router.post("/{source_id}/scan", status_code=202)
def scan(source_id: int, request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    return service(request).control(principal, source_id, scan=True)


@router.post("/{source_id}/connection-check")
def connection_check(source_id: int, request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    config = service(request).config(source_id)
    with ftp_connection(config, read_ftp_credential) as ftp:
        read_mlsd(ftp, ".", limit=min(config.max_entries, 50000))
    return dict(status="SUCCESS", message="连接、登录、根目录与 MLSD 读取检查通过")


@router.get("/{source_id}/packages")
def packages(source_id: int, request: Request, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), principal: Principal = Depends(current_principal)):
    return service(request).packages(principal, source_id, page=page, page_size=page_size)


@router.post("/{source_id}/packages/{package_id}/retry", status_code=202)
def retry(source_id: int, package_id: int, request: Request, principal: Principal = Depends(require_permission("SOURCE_ADMIN"))):
    return service(request).retry_package(principal, source_id, package_id)
