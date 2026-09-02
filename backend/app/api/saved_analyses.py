from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    CreateSavedAnalysisRequest,
    CreateSavedAnalysisRevisionRequest,
    DeleteSavedAnalysisRequest,
    SavedAnalysisService,
)

router = APIRouter(prefix="/saved-analyses")


def service(request: Request) -> SavedAnalysisService:
    instance = getattr(request.app.state, "saved_analysis_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "Saved Analysis operations require TMS_DATABASE_URL/sql2014_0025",
            503,
        )
    return instance


def require_saved_analysis_write(
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> Principal:
    if not principal.can("DATASET_READ"):
        raise DomainError(
            "PERMISSION_DENIED",
            "Saved Analysis writes require DATASET_READ and ANALYSIS_RUN",
            403,
        )
    return principal


@router.post("", status_code=status.HTTP_201_CREATED)
def create_saved_analysis(
    payload: CreateSavedAnalysisRequest,
    request: Request,
    principal: Principal = Depends(require_saved_analysis_write),  # noqa: B008
) -> dict:
    return asdict(service(request).create(payload, principal))


@router.get("")
def list_saved_analyses(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_deleted: bool = Query(default=False),
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).list_page(
            principal,
            page=page,
            page_size=page_size,
            include_deleted=include_deleted,
        )
    )


@router.get("/{saved_analysis_id}")
def get_saved_analysis(
    saved_analysis_id: int,
    request: Request,
    revision_no: int | None = Query(default=None, ge=1),
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(
        service(request).get(
            saved_analysis_id,
            principal,
            revision_no=revision_no,
        )
    )


@router.post("/{saved_analysis_id}/revisions", status_code=status.HTTP_201_CREATED)
def create_saved_analysis_revision(
    saved_analysis_id: int,
    payload: CreateSavedAnalysisRevisionRequest,
    request: Request,
    principal: Principal = Depends(require_saved_analysis_write),  # noqa: B008
) -> dict:
    return asdict(
        service(request).create_revision(saved_analysis_id, payload, principal)
    )


@router.delete("/{saved_analysis_id}")
def delete_saved_analysis(
    saved_analysis_id: int,
    payload: DeleteSavedAnalysisRequest,
    request: Request,
    principal: Principal = Depends(require_saved_analysis_write),  # noqa: B008
) -> dict:
    return asdict(service(request).delete(saved_analysis_id, payload, principal))
