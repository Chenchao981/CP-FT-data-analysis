from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import (
    CreateJobRequest,
    JobService,
    JobType,
    TransitionJobRequest,
)

router = APIRouter()


def service(request: Request) -> JobService:
    return request.app.state.job_service


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(
    payload: CreateJobRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    if payload.job_type == JobType.INITIAL_IMPORT:
        raise DomainError(
            "INITIAL_IMPORT_ENTRYPOINT_REQUIRED",
            "INITIAL_IMPORT 只能由上传、重新处理或Lot补录恢复入口创建",
            422,
        )
    trusted = payload.model_copy(
        update={
            "requested_by": principal.login_name,
            "requested_by_user_id": principal.user_id,
        }
    )
    instance = service(request)
    if hasattr(instance, "create_for_principal"):
        return asdict(instance.create_for_principal(trusted, principal))
    return asdict(instance.create(trusted))


@router.get("/{job_id}")
def get_job(
    job_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    instance = service(request)
    if hasattr(instance, "get_for_principal"):
        return asdict(instance.get_for_principal(job_id, principal))
    return asdict(instance.get(job_id))


@router.post("/{job_id}/transitions")
def transition_job(
    job_id: int,
    payload: TransitionJobRequest,
    request: Request,
    principal: Principal = Depends(require_permission("TASK_RETRY")),
) -> dict:
    instance = service(request)
    if hasattr(instance, "get_for_principal"):
        current = instance.get_for_principal(job_id, principal)
    else:
        current = instance.get(job_id)
    if current.job_type == JobType.INITIAL_IMPORT:
        raise DomainError(
            "INITIAL_IMPORT_TRANSITION_RESERVED",
            "INITIAL_IMPORT 状态只能由持有租约的 Worker 迁移",
            422,
        )
    if hasattr(instance, "transition_for_principal"):
        return asdict(instance.transition_for_principal(job_id, payload, principal))
    return asdict(instance.transition(job_id, payload))
