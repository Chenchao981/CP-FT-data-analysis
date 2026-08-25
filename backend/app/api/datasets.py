from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetService,
    PublishDatasetVersionRequest,
)

router = APIRouter()


def service(request: Request) -> DatasetService:
    instance = getattr(request.app.state, "dataset_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "dataset operations require TMS_DATABASE_URL",
            503,
        )
    return instance


@router.post("", status_code=status.HTTP_201_CREATED)
def create_dataset(
    payload: CreateDatasetRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    owned = payload.model_copy(update={"owner_user_id": principal.user_id})
    return asdict(service(request).create_dataset(owned))


@router.get("")
def list_datasets(
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> list[dict]:
    return [asdict(item) for item in service(request).list_datasets(principal)]


@router.post("/{dataset_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    dataset_id: int,
    payload: CreateDatasetVersionRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    service(request).assert_dataset_access(dataset_id, principal, "WRITE")
    return asdict(service(request).create_version(dataset_id, payload))


@router.get("/{dataset_id}/versions/{version_no}/gate")
def evaluate_gate(
    dataset_id: int,
    version_no: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    service(request).assert_dataset_access(dataset_id, principal)
    return asdict(service(request).evaluate_gate(dataset_id, version_no))


@router.post("/{dataset_id}/versions/{version_no}/publish")
def publish_version(
    dataset_id: int,
    version_no: int,
    payload: PublishDatasetVersionRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_PUBLISH")),
) -> dict:
    service(request).assert_dataset_access(dataset_id, principal, "WRITE")
    return asdict(service(request).publish(dataset_id, version_no, payload))


@router.get("/{dataset_id}/versions/{version_no}/summary")
def result_summary(
    dataset_id: int,
    version_no: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    service(request).assert_dataset_access(dataset_id, principal)
    return asdict(service(request).get_summary(dataset_id, version_no))


@router.get("/{dataset_id}/versions/{version_no}/charts")
def chart_data(
    dataset_id: int,
    version_no: int,
    request: Request,
    lot_id: str | None = None,
    wafer_id: str | None = None,
    source_id: str | None = None,
    parameter: str | None = None,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    service(request).assert_dataset_access(dataset_id, principal)
    return asdict(
        service(request).get_chart_data(
            dataset_id, version_no, lot_id, wafer_id, source_id, parameter
        )
    )
