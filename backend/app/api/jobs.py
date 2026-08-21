from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import require_permission
from app.domain.auth import Principal

from app.domain.jobs import (
    CreateJobRequest,
    JobService,
    TransitionJobRequest,
)


router = APIRouter()


def service(request: Request) -> JobService:
    return request.app.state.job_service


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(payload: CreateJobRequest, request: Request, _principal: Principal = Depends(require_permission("TASK_CREATE"))) -> dict:
    return asdict(service(request).create(payload))


@router.get("/{job_id}")
def get_job(job_id: int, request: Request, _principal: Principal = Depends(require_permission("DATASET_READ"))) -> dict:
    return asdict(service(request).get(job_id))


@router.post("/{job_id}/transitions")
def transition_job(
    job_id: int, payload: TransitionJobRequest, request: Request,
    _principal: Principal = Depends(require_permission("TASK_RETRY")),
) -> dict:
    return asdict(service(request).transition(job_id, payload))
