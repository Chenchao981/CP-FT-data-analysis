from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetComparisonRequest,
    DatasetParameterAnalysisRequest,
    DatasetService,
    PublishDatasetVersionRequest,
)

router = APIRouter()


def _detail_filter_values(
    values: list[str] | None, *, field: str, maximum: int
) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in (values or ()))
    if len(normalized) > maximum:
        raise DomainError(
            "ANALYSIS_FILTER_LIMIT_EXCEEDED",
            f"{field} exceeds the maximum of {maximum} values",
            422,
        )
    if any(not value or len(value) > 200 for value in normalized) or len(
        normalized
    ) != len(set(normalized)):
        raise DomainError(
            "ANALYSIS_FILTER_INVALID",
            f"{field} contains an empty, oversized, or duplicate value",
            422,
        )
    return normalized


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


@router.post("/compare")
def compare_datasets(
    payload: DatasetComparisonRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    instance = service(request)
    for reference in payload.datasets:
        instance.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(instance.compare(payload))


@router.post("/parameter-analysis")
def parameter_analysis(
    payload: DatasetParameterAnalysisRequest,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    instance = service(request)
    for reference in payload.datasets:
        instance.assert_dataset_access(
            reference.dataset_id,
            principal,
            version_no=reference.version_no,
        )
    return asdict(instance.analyze_parameters(payload))


@router.post("/{dataset_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    dataset_id: int,
    payload: CreateDatasetVersionRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    return asdict(service(request).create_version(dataset_id, payload, principal))


@router.get("/{dataset_id}/versions/{version_no}/gate")
def evaluate_gate(
    dataset_id: int,
    version_no: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    instance = service(request)
    instance.assert_dataset_access(dataset_id, principal, version_no=version_no)
    return asdict(instance.evaluate_gate(dataset_id, version_no, principal))


@router.post("/{dataset_id}/versions/{version_no}/publish")
def publish_version(
    dataset_id: int,
    version_no: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_PUBLISH")),
) -> dict:
    service(request).assert_dataset_access(
        dataset_id, principal, "WRITE", version_no=version_no
    )
    attributed = PublishDatasetVersionRequest(published_by=principal.user_id)
    return asdict(service(request).publish(dataset_id, version_no, attributed))


@router.get("/{dataset_id}/versions/{version_no}/summary")
def result_summary(
    dataset_id: int,
    version_no: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    instance = service(request)
    instance.assert_dataset_access(dataset_id, principal, version_no=version_no)
    return asdict(instance.get_summary(dataset_id, version_no, principal))


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
    service(request).assert_dataset_access(dataset_id, principal, version_no=version_no)
    return asdict(
        service(request).get_chart_data(
            dataset_id, version_no, lot_id, wafer_id, source_id, parameter
        )
    )


@router.get("/{dataset_id}/versions/{version_no}/details")
def detail_page(
    dataset_id: int,
    version_no: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    lot_id: list[str] | None = Query(default=None),
    wafer_id: list[str] | None = Query(default=None),
    bin_code: list[str] | None = Query(default=None),
    parameter: list[str] | None = Query(default=None),
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    lot_ids = _detail_filter_values(lot_id, field="lot_id", maximum=50)
    wafer_ids = _detail_filter_values(wafer_id, field="wafer_id", maximum=100)
    bin_codes = _detail_filter_values(bin_code, field="bin_code", maximum=50)
    parameters = _detail_filter_values(parameter, field="parameter", maximum=20)
    instance = service(request)
    instance.assert_dataset_access(dataset_id, principal, version_no=version_no)
    return asdict(
        instance.get_detail_page(
            dataset_id,
            version_no,
            page=page,
            page_size=page_size,
            lot_ids=lot_ids,
            wafer_ids=wafer_ids,
            bin_codes=bin_codes,
            parameters=parameters,
        )
    )
