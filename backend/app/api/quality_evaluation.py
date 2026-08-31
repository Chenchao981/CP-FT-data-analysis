from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import DatasetService
from app.domain.quality_evaluation import QualityEvaluationRequest
from app.infrastructure.sql_quality_evaluation_service import (
    SqlQualityEvaluationService,
)

router = APIRouter()


def _services(
    request: Request,
) -> tuple[SqlQualityEvaluationService, DatasetService]:
    quality = getattr(request.app.state, "quality_evaluation_service", None)
    datasets = getattr(request.app.state, "dataset_service", None)
    if quality is None or datasets is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "formal quality evaluation requires TMS_DATABASE_URL",
            503,
        )
    return quality, datasets


@router.post("")
def quality_evaluation(
    payload: QualityEvaluationRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    quality, datasets = _services(request)
    for reference in payload.datasets:
        datasets.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(quality.analyze(payload))
