from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import DatasetService
from app.domain.spatial_analysis import SpatialAnalysisRequest
from app.infrastructure.sql_spatial_analysis_service import SqlSpatialAnalysisService

router = APIRouter()


def _services(request: Request) -> tuple[SqlSpatialAnalysisService, DatasetService]:
    spatial = getattr(request.app.state, "spatial_analysis_service", None)
    datasets = getattr(request.app.state, "dataset_service", None)
    if spatial is None or datasets is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "spatial analysis requires TMS_DATABASE_URL",
            503,
        )
    return spatial, datasets


@router.post("")
def analyze_spatial(
    payload: SpatialAnalysisRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    service, datasets = _services(request)
    for reference in payload.datasets:
        datasets.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(service.analyze(payload))
