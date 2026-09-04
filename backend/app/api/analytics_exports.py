from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.analytics_exports import (
    AnalyticsExportService,
    CancelAnalyticsExportRequest,
    CreateAnalyticsExportRequest,
)
from app.domain.auth import Principal
from app.domain.datasets import DatasetService

router = APIRouter(prefix="/exports")


def _services(request: Request) -> tuple[AnalyticsExportService, DatasetService]:
    exports = getattr(request.app.state, "analytics_export_service", None)
    datasets = getattr(request.app.state, "dataset_service", None)
    if exports is None or datasets is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "analytics export operations require TMS_DATABASE_URL/sql2014_0026",
            503,
        )
    return exports, datasets


def require_export_access(
    principal: Principal = Depends(require_permission("EXPORT_DATA")),  # noqa: B008
) -> Principal:
    if not principal.can("DATASET_READ"):
        raise DomainError(
            "PERMISSION_DENIED",
            "analytics exports require DATASET_READ and EXPORT_DATA",
            403,
        )
    return principal


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_export(
    payload: CreateAnalyticsExportRequest,
    request: Request,
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> dict:
    exports, datasets = _services(request)
    for reference in payload.datasets:
        datasets.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(exports.create(payload, principal))


@router.get("")
def list_exports(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> dict:
    exports, _datasets = _services(request)
    return asdict(exports.list_page(principal, page=page, page_size=page_size))


@router.get("/{export_job_id}")
def get_export(
    export_job_id: int,
    request: Request,
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> dict:
    exports, _datasets = _services(request)
    return asdict(exports.get(export_job_id, principal))


@router.get("/{export_job_id}/download-metadata")
def get_export_download_metadata(
    export_job_id: int,
    request: Request,
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> dict:
    exports, _datasets = _services(request)
    return asdict(exports.download_metadata(export_job_id, principal))


@router.get("/{export_job_id}/artifacts/{export_artifact_id}/download")
def download_export_artifact(
    export_job_id: int,
    export_artifact_id: int,
    request: Request,
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> FileResponse:
    exports, _datasets = _services(request)
    target = exports.resolve_download(export_job_id, export_artifact_id, principal)
    return FileResponse(
        path=target.path,
        filename=target.file_name,
        media_type=target.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{export_job_id}/cancel")
def cancel_export(
    export_job_id: int,
    payload: CancelAnalyticsExportRequest,
    request: Request,
    principal: Principal = Depends(require_export_access),  # noqa: B008
) -> dict:
    exports, _datasets = _services(request)
    return asdict(exports.cancel(export_job_id, payload, principal))
