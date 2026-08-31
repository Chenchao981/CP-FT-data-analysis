from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsDetailRequest,
    AnalyticsDrilldownRequest,
    AnalyticsOverviewRequest,
)
from app.domain.analytics_risk import AnalyticsInstantRiskRequest
from app.domain.auth import Principal
from app.domain.datasets import DatasetService
from app.infrastructure.analytics_instant_risk_service import (
    AnalyticsInstantRiskService,
)
from app.infrastructure.sql_analytics_service import SqlAnalyticsService

router = APIRouter()


@router.get("/features")
def features(
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    del principal
    flags = getattr(request.app.state, "analytics_feature_flags", {})
    groups = []
    for code in ("OVERVIEW", "DETAIL", "PARAMETER", "SPATIAL", "QUALITY", "DELIVERY"):
        enabled = flags.get(code, True) is not False
        groups.append(
            {
                "code": code,
                "enabled": enabled,
                "reason_code": None if enabled else "ANALYSIS_FEATURE_DISABLED",
                "message": (
                    None
                    if enabled
                    else f"{code} analytics is disabled by the release kill switch"
                ),
            }
        )
    return {"contract_version": "ANALYTICS_FEATURE_FLAGS_V1", "groups": groups}


def _services(request: Request) -> tuple[SqlAnalyticsService, DatasetService]:
    analytics = getattr(request.app.state, "analytics_service", None)
    datasets = getattr(request.app.state, "dataset_service", None)
    if analytics is None or datasets is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "analytics operations require TMS_DATABASE_URL",
            503,
        )
    return analytics, datasets


def _authorize(
    request: Request,
    principal: Principal,
    references,
) -> SqlAnalyticsService:
    analytics, datasets = _services(request)
    for reference in references:
        datasets.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return analytics


def _risk_service(request: Request) -> AnalyticsInstantRiskService:
    service = getattr(request.app.state, "analytics_instant_risk_service", None)
    if service is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "instant risk evaluation requires TMS_DATABASE_URL",
            503,
        )
    return service


@router.post("/overview")
def overview(
    payload: AnalyticsOverviewRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(_authorize(request, principal, payload.datasets).overview(payload))


@router.post("/instant-risk")
def instant_risk(
    payload: AnalyticsInstantRiskRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    _authorize(request, principal, payload.datasets)
    return asdict(_risk_service(request).evaluate(payload))


@router.post("/context")
def shell_context(
    payload: AnalyticsOverviewRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(
        _authorize(request, principal, payload.datasets).shell_context(payload)
    )


@router.post("/detail")
def detail(
    payload: AnalyticsDetailRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(_authorize(request, principal, payload.datasets).detail(payload))


@router.post("/drilldown")
def drilldown(
    payload: AnalyticsDrilldownRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    return asdict(_authorize(request, principal, payload.datasets).drilldown(payload))
