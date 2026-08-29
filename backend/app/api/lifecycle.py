from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from app.api.dependencies import current_principal, require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.lifecycle import (
    ArchiveDatasetRequest,
    ExportLatestRequest,
    LifecycleJobReceipt,
    LifecycleService,
    ReprocessUpdateRequest,
)

router = APIRouter(prefix="/lifecycle")


def service(request: Request) -> LifecycleService:
    instance = getattr(request.app.state, "lifecycle_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED", "Lifecycle 服务尚未连接数据库", 503
        )
    return instance


def _receipt(value: LifecycleJobReceipt, external_key: str) -> dict:
    payload = asdict(value)
    payload["idempotency_key"] = external_key
    payload["idempotent_replay"] = not value.created
    return payload


@router.post("/exports", status_code=202)
def create_latest_export(
    body: ExportLatestRequest,
    request: Request,
    principal: Principal = Depends(require_permission("EXPORT_DATA")),  # noqa: B008
) -> dict:
    receipt = service(request).create_export(
        body.dataset_id, body.idempotency_key, principal
    )
    return _receipt(receipt, body.idempotency_key)


@router.get("/exports/{job_id}/artifacts/{artifact_id}/download")
def download_export_artifact(
    job_id: int,
    artifact_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("EXPORT_DATA")),  # noqa: B008
) -> FileResponse:
    artifact = service(request).artifact_download(
        job_id, artifact_id, principal
    )
    return FileResponse(
        path=artifact.path,
        filename=artifact.file_name,
        media_type=artifact.media_type,
    )


@router.get("/exports/{job_id}")
def get_export_status(
    job_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("EXPORT_DATA")),  # noqa: B008
) -> dict:
    value = service(request).export_status(job_id, principal)
    payload = asdict(value)
    now = datetime.now(UTC)
    payload["artifacts"] = [
        {
            "artifact_id": artifact.processing_artifact_id,
            "role": artifact.artifact_role,
            "file_name": artifact.file_name,
            "size_bytes": artifact.file_size,
            "sha256": artifact.sha256,
            "physical_status": artifact.physical_status,
            "expires_at_utc": artifact.expires_at_utc,
            "download_url": (
                f"/api/v1/lifecycle/exports/{job_id}/artifacts/"
                f"{artifact.processing_artifact_id}/download"
                if artifact.physical_status == "PRESENT"
                and artifact.expires_at_utc > now
                else None
            ),
        }
        for artifact in value.artifacts
    ]
    return payload


@router.post("/datasets/{dataset_id}/archive", status_code=202)
def archive_dataset(
    dataset_id: int,
    body: ArchiveDatasetRequest,
    request: Request,
    principal: Principal = Depends(current_principal),  # noqa: B008
) -> dict:
    receipt = service(request).create_archive(
        dataset_id,
        body.reason,
        body.idempotency_key,
        principal,
    )
    return _receipt(receipt, body.idempotency_key)


@router.post("/datasets/{dataset_id}/reprocess", status_code=202)
def reprocess_dataset(
    dataset_id: int,
    body: ReprocessUpdateRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_CREATE")),  # noqa: B008
) -> dict:
    receipt = service(request).create_reprocess(
        dataset_id,
        body.reason,
        body.idempotency_key,
        principal,
    )
    return _receipt(receipt, body.idempotency_key)
