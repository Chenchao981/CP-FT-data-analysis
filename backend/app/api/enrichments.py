from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import require_permission
from app.domain.auth import Principal

from app.core.errors import DomainError
from app.domain.enrichments import (
    STAGE_FIELD_CATALOG,
    CreateFieldEnrichmentRequest,
    EnrichmentStage,
    FieldEnrichmentService,
)


router = APIRouter()


def service(request: Request) -> FieldEnrichmentService:
    instance = getattr(request.app.state, "field_enrichment_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "manual field enrichment requires TMS_DATABASE_URL",
            503,
        )
    return instance


@router.post("", status_code=status.HTTP_201_CREATED)
def create_enrichment(payload: CreateFieldEnrichmentRequest, request: Request, principal: Principal = Depends(require_permission("TASK_CREATE"))) -> dict:
    owned_payload = payload.model_copy(update={"entered_by": principal.user_id})
    return asdict(service(request).create(owned_payload))


@router.get("/batches/{import_batch_id}")
def list_enrichments(import_batch_id: int, request: Request, _principal: Principal = Depends(require_permission("DATASET_READ"))) -> list[dict]:
    return [asdict(item) for item in service(request).list_current(import_batch_id)]


@router.get("/fields/{test_stage}")
def field_catalog(test_stage: EnrichmentStage, _principal: Principal = Depends(require_permission("TASK_CREATE"))) -> list[dict[str, object]]:
    return [dict(item) for item in STAGE_FIELD_CATALOG[test_stage]]
