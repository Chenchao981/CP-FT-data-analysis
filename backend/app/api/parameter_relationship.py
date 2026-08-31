from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import DatasetService
from app.domain.parameter_relationship import (
    ParameterRelationshipRequest,
    ParameterRelationshipService,
)

router = APIRouter()


def _services(
    request: Request,
) -> tuple[ParameterRelationshipService, DatasetService]:
    relationship_service = getattr(
        request.app.state, "parameter_relationship_service", None
    )
    dataset_service = getattr(request.app.state, "dataset_service", None)
    if relationship_service is None or dataset_service is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "parameter relationship analysis requires TMS_DATABASE_URL",
            503,
        )
    return relationship_service, dataset_service


@router.post("/parameter-relationship")
def parameter_relationship(
    payload: ParameterRelationshipRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),  # noqa: B008
) -> dict:
    relationship_service, dataset_service = _services(request)
    for reference in payload.datasets:
        dataset_service.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(relationship_service.relationship(payload))
