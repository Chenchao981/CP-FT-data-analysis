from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import CreateJobRequest, JobType, TriggerType
from app.domain.quick_analysis import (
    CreateQuickPatRequest,
    NewQuickAnalysisSession,
    QuickAnalysisStatus,
)

router = APIRouter(prefix="/quick-analysis")


def quick_service(request: Request):
    return request.app.state.quick_analysis_service


def source_catalog(request: Request):
    return request.app.state.source_catalog


def cleaner_registry(request: Request):
    instance = getattr(request.app.state, "cleaner_registry", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED", "Cleaner Registry 尚未连接数据库", 503
        )
    return instance


def job_service(request: Request):
    return request.app.state.job_service


def capacity_policy(request: Request):
    return request.app.state.quick_capacity_policy


@router.get("/source-roots")
def list_source_roots(
    request: Request,
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> tuple[dict[str, object], ...]:
    return source_catalog(request).list_roots(purpose="QUICK_ANALYSIS")


@router.get("/source-roots/{root_code}/directories")
def list_source_directories(
    root_code: str,
    request: Request,
    relative_path: str = Query(default=".", max_length=1000),
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    catalog = source_catalog(request)
    catalog.require_scope(
        root_code,
        purpose="QUICK_ANALYSIS",
        test_stage="FT",
        factory_code="JIEQUN",
    )
    current, parent, directories = catalog.browse(root_code, relative_path)
    return {
        "root_code": root_code.strip().upper(),
        "current_relative_path": current,
        "parent_relative_path": parent,
        "directories": [asdict(item) for item in directories],
    }


@router.get("/source-roots/{root_code}/manifest-preview")
def preview_source_manifest(
    root_code: str,
    request: Request,
    relative_path: str = Query(default=".", max_length=1000),
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    catalog = source_catalog(request)
    root = catalog.require_scope(
        root_code,
        purpose="QUICK_ANALYSIS",
        test_stage="FT",
        factory_code="JIEQUN",
    )
    manifest = catalog.build_manifest(root.code, relative_path)
    return {
        "root_code": root.code,
        "relative_path": manifest.selected_relative_path,
        "mode": manifest.mode,
        "recursive": True,
        "file_count": manifest.file_count,
        "total_bytes": manifest.total_bytes,
        "sha": manifest.sha256,
        "allowed_suffixes": list(root.allowed_suffixes),
        "tool_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
    }


@router.post("/pat", status_code=status.HTTP_201_CREATED)
def create_quick_pat(
    payload: CreateQuickPatRequest,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict:
    catalog = source_catalog(request)
    root = catalog.require_scope(
        payload.source_root_code,
        purpose="QUICK_ANALYSIS",
        test_stage="FT",
        factory_code="JIEQUN",
    )
    manifest = catalog.build_manifest(root.code, payload.source_relative_path)
    if not manifest.matches_confirmation(
        mode=payload.source_manifest_mode,
        sha256=payload.source_manifest_sha256,
    ):
        raise DomainError(
            "QUICK_SOURCE_CHANGED",
            "源目录与确认时的文件清单不一致，请重新预览后再提交",
            409,
        )
    capacity = capacity_policy(request)
    reserved_bytes = capacity.reservation_for(manifest.total_bytes)
    capacity.ensure_filesystem_capacity(reserved_bytes)
    release = cleaner_registry(request).latest_released("FT", "JIEQUN")
    ttl_hours = int(os.getenv("TMS_QUICK_RESULT_TTL_HOURS", "168"))
    if ttl_hours < 1 or ttl_hours > 8760:
        raise RuntimeError("TMS_QUICK_RESULT_TTL_HOURS must be between 1 and 8760")
    session = quick_service(request).create(
        principal,
        NewQuickAnalysisSession(
            analysis_type="QUICK_PAT",
            test_stage="FT",
            factory_code="JIEQUN",
            source_root_code=root.code,
            source_relative_path=manifest.selected_relative_path,
            source_manifest_mode=manifest.mode,
            source_manifest_json=manifest.as_json(),
            source_manifest_sha256=manifest.sha256,
            source_file_count=manifest.file_count,
            source_total_bytes=manifest.total_bytes,
            retention_mode="RESULT_ONLY",
            cleaner_release_id=release.cleaner_release_id,
            expires_at_utc=datetime.now(UTC) + timedelta(hours=ttl_hours),
            reserved_bytes=reserved_bytes,
        ),
    )
    try:
        job = job_service(request).create(
            CreateJobRequest(
                analysis_session_id=session.analysis_session_id,
                cleaner_release_id=release.cleaner_release_id,
                job_type=JobType.QUICK_PAT,
                trigger_type=TriggerType.MANUAL,
                requested_by=principal.login_name,
                requested_by_user_id=principal.user_id,
                reason="受控服务器目录直接执行杰群低内存 PAT，不写入 Canonical",
                idempotency_key=f"quick-pat:{session.analysis_session_id}",
            )
        )
        quick_service(request).attach_job(session.analysis_session_id, job.job_id)
    except Exception as exc:
        quick_service(request).mark_failed(
            session.analysis_session_id, "QUEUE_CREATE_FAILED", str(exc)
        )
        raise
    created = quick_service(request).get_for_principal(
        session.analysis_session_id, principal
    )
    return asdict(created)


@router.get("/sessions")
def list_quick_sessions(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: QuickAnalysisStatus | None = Query(  # noqa: B008
        default=None, alias="status"
    ),
    from_utc: datetime | None = Query(default=None),  # noqa: B008
    to_utc: datetime | None = Query(default=None),  # noqa: B008
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict:
    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise DomainError(
            "QUICK_TIME_RANGE_INVALID", "开始时间必须早于结束时间", 422
        )
    result = quick_service(request).list_page_for_principal(
        principal,
        page=page,
        page_size=page_size,
        status=status_filter,
        from_utc=from_utc,
        to_utc=to_utc,
    )
    return asdict(result)


@router.get("/sessions/{analysis_session_id}")
def get_quick_session(
    analysis_session_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict:
    return asdict(
        quick_service(request).get_for_principal(analysis_session_id, principal)
    )


@router.get("/sessions/{analysis_session_id}/download")
def download_quick_pat(
    analysis_session_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> FileResponse:
    artifact = quick_service(request).result_artifact(analysis_session_id, principal)
    path = Path(artifact.path).resolve()
    work_root = Path(
        os.getenv("TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace")
    ).resolve()
    try:
        contained = os.path.commonpath(
            (os.path.normcase(str(work_root)), os.path.normcase(str(path)))
        ) == os.path.normcase(str(work_root))
    except ValueError:
        contained = False
    if not contained or path.suffix.lower() != ".xlsx":
        raise DomainError("QUICK_RESULT_PATH_INVALID", "PAT 结果存储路径无效", 409)
    if not path.is_file():
        raise DomainError("QUICK_RESULT_MISSING", "PAT 结果已不在存储位置", 404)
    return FileResponse(path, filename=path.name)
