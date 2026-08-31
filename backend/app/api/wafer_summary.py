from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import DatasetService
from app.domain.wafer_summary import WaferSummaryRequest
from app.infrastructure.sql_wafer_summary_service import SqlWaferSummaryService

router = APIRouter()


def _services(request: Request) -> tuple[SqlWaferSummaryService, DatasetService]:
    summaries = getattr(request.app.state, "wafer_summary_service", None)
    datasets = getattr(request.app.state, "dataset_service", None)
    if summaries is None or datasets is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "wafer summary requires TMS_DATABASE_URL",
            503,
        )
    return summaries, datasets


@router.post("")
def wafer_summary(
    payload: WaferSummaryRequest,
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
    return asdict(service.summarize(payload))
