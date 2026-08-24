from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import CreateJobRequest, JobType, TriggerType
from app.domain.stage_data import StoredUpload
from app.infrastructure.existing_cleaner_results import (
    summarize_existing_cleaner_result,
)
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner

router = APIRouter()
ALLOWED_SUFFIXES = {
    "CP": {".zip", ".7z", ".txt"},
    "FT": {".xlsx"},
}
BUSINESS_DOMAINS = {"engineering": "ENGINEERING", "production": "PRODUCTION"}
LIST_TEST_STAGES = {
    "cp": "CP",
    "ft": "FT",
    "wat": "WAT",
    "wft": "WFT",
    "slt": "SLT",
    "qa": "QA",
    "ort": "ORT",
    "other": "OTHER",
}
UPLOAD_TEST_STAGES = {"cp": "CP", "ft": "FT"}
STAGE_FACTORIES = {
    "CP": {"huahong", "hh", "华虹"},
    "FT": {"riyuexin", "ase", "日月新"},
}
CP_FACTORIES = {"huahong", "hh", "华虹"}


def service(request: Request):
    instance = getattr(request.app.state, "stage_data_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "数据服务尚未连接数据库", 503)
    return instance


def cleaner_registry(request: Request):
    instance = getattr(request.app.state, "cleaner_registry", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED", "Cleaner Registry 尚未连接数据库", 503
        )
    return instance


def job_service(request: Request):
    return request.app.state.job_service


def _normalize_business_domain(value: str) -> str:
    domain = BUSINESS_DOMAINS.get(value.strip().lower())
    if domain is None:
        raise DomainError(
            "BUSINESS_DOMAIN_UNSUPPORTED", f"不支持的业务分类：{value}", 404
        )
    return domain


def _normalize_list_stage(value: str) -> str:
    stage = LIST_TEST_STAGES.get(value.strip().lower())
    if stage is None:
        raise DomainError("TEST_STAGE_UNSUPPORTED", f"不支持的测试阶段：{value}", 404)
    return stage


def _save_uploads(
    business_domain: str, test_stage: str, files: list[UploadFile]
) -> tuple[StoredUpload, ...]:
    if not files:
        raise DomainError("STAGE_UPLOAD_EMPTY", "请选择需要上传的源文件", 422)
    allowed = ALLOWED_SUFFIXES[test_stage]
    target = (
        Path(os.getenv("TMS_UPLOAD_ROOT", r"F:\CP-FT数据分析\data\raw"))
        / business_domain.lower()
        / test_stage.lower()
        / uuid4().hex
    )
    target.mkdir(parents=True, exist_ok=False)
    stored: list[StoredUpload] = []
    for uploaded in files:
        original = Path(uploaded.filename or "").name
        suffix = Path(original).suffix.lower()
        if not original or suffix not in allowed:
            raise DomainError(
                "FILE_TYPE_UNSUPPORTED",
                f"不支持的{test_stage}源文件：{original or '未命名文件'}",
                422,
            )
        destination = target / original
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            while chunk := uploaded.file.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
        stored.append(
            StoredUpload(original, destination.resolve(), size, digest.hexdigest())
        )
    return tuple(stored)


def _run_cleaner_and_summarize(
    stage: str, factory: str, input_paths: list[Path], output_root: Path
) -> dict:
    result = ExistingCleanerRunner().run(
        test_stage=stage, factory=factory, inputs=input_paths, output_root=output_root
    )
    return summarize_existing_cleaner_result(result)


@router.post(
    "/{business_domain}/{test_stage}/uploads", status_code=status.HTTP_201_CREATED
)
def upload_stage_data(
    request: Request,
    business_domain: str,
    test_stage: str,
    files: list[UploadFile] = File(...),
    factory_code: str = Form(""),
    remark: str | None = Form(None),
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    domain = _normalize_business_domain(business_domain)
    stage = UPLOAD_TEST_STAGES.get(test_stage.strip().lower())
    if stage is None:
        raise DomainError(
            "STAGE_UPLOAD_UNSUPPORTED",
            f"{test_stage.upper()}数据上传将在后续版本开放，当前支持CP/FT数据",
            422,
        )
    factory_aliases = {
        "华虹": "huahong",
        "hh": "huahong",
        "日月新": "riyuexin",
        "ase": "riyuexin",
    }
    factory = factory_aliases.get(
        factory_code.strip().lower(), factory_code.strip().lower()
    )
    if factory not in STAGE_FACTORIES[stage]:
        raise DomainError(
            "FACTORY_UNSUPPORTED",
            f"当前{stage}首版支持{'华虹' if stage == 'CP' else '日月新'}源数据",
            422,
        )
    stored = _save_uploads(domain, stage, files)
    batch_id = service(request).register_upload(
        principal, domain, stage, factory, stored, remark.strip() if remark else None
    )
    registry_factory = {"huahong": "HUAHONG", "riyuexin": "RIYUEXIN"}[factory]
    release = cleaner_registry(request).latest_released(stage, registry_factory)
    job = job_service(request).create(
        CreateJobRequest(
            import_batch_id=batch_id,
            cleaner_release_id=release.cleaner_release_id,
            job_type=JobType.INITIAL_IMPORT,
            trigger_type=TriggerType.AUTO,
            requested_by=principal.login_name,
            requested_by_user_id=principal.user_id,
            reason="上传后由 Route A Worker 调用已发布 Cleaner",
            idempotency_key=f"initial-import:{batch_id}",
        )
    )
    service(request).mark_queued(batch_id)
    return {
        "import_batch_id": batch_id,
        "job_id": job.job_id,
        "status": "QUEUED",
        "business_domain": domain,
        "test_stage": stage,
        "cleaner_release": {
            "cleaner_release_id": release.cleaner_release_id,
            "cleaner_code": release.cleaner_code,
            "cleaner_version": release.cleaner_version,
        },
        "uploader": {
            "user_id": principal.user_id,
            "login_name": principal.login_name,
            "display_name": principal.display_name,
        },
    }


@router.get("/{business_domain}/{test_stage}/uploads")
def list_stage_uploads(
    request: Request,
    business_domain: str,
    test_stage: str,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> list[dict]:
    domain = _normalize_business_domain(business_domain)
    stage = _normalize_list_stage(test_stage)
    return [
        asdict(item) for item in service(request).list_uploads(principal, domain, stage)
    ]


@router.get("/{business_domain}/{test_stage}/results")
def list_stage_results(
    request: Request,
    business_domain: str,
    test_stage: str,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> list[dict]:
    domain = _normalize_business_domain(business_domain)
    stage = _normalize_list_stage(test_stage)
    return [
        asdict(item) for item in service(request).list_results(principal, domain, stage)
    ]


@router.get(
    "/{business_domain}/{test_stage}/uploads/{batch_id}/files/{receipt_id}/download"
)
def download_upload_file(
    request: Request,
    business_domain: str,
    test_stage: str,
    batch_id: int,
    receipt_id: int,
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> FileResponse:
    domain = _normalize_business_domain(business_domain)
    stage = _normalize_list_stage(test_stage)
    info = service(request).get_batch_info(principal, domain, stage, batch_id)
    if info is None:
        raise DomainError("BATCH_NOT_FOUND", "批次不存在或无权访问", 404)
    match = [item for item in info.files if item.receipt_id == receipt_id]
    if not match:
        raise DomainError("UPLOAD_FILE_NOT_FOUND", "源文件不存在或无权访问", 404)
    path = Path(match[0].storage_uri)
    if not path.is_file():
        raise DomainError("UPLOAD_FILE_MISSING", "源文件已不在存储位置", 404)
    return FileResponse(path, filename=match[0].original_file_name)


@router.post("/{business_domain}/{test_stage}/uploads/{batch_id}/reprocess")
def reprocess_batch(
    request: Request,
    business_domain: str,
    test_stage: str,
    batch_id: int,
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    domain = _normalize_business_domain(business_domain)
    stage = UPLOAD_TEST_STAGES.get(test_stage.strip().lower())
    if stage is None:
        raise DomainError(
            "STAGE_UPLOAD_UNSUPPORTED",
            f"{test_stage.upper()}数据暂不支持重新处理，当前支持CP/FT数据",
            422,
        )
    info = service(request).get_batch_info(principal, domain, stage, batch_id)
    if info is None or not info.files:
        raise DomainError("BATCH_NOT_FOUND", "批次不存在或无权访问", 404)
    factory = info.factory_code.strip().lower()
    job_id = service(request).mark_processing(batch_id, principal)
    output_root = (
        Path(os.getenv("TMS_WORK_ROOT", r"F:\CP-FT数据分析\data\work"))
        / "legacy-reprocess"
        / str(batch_id)
        / str(job_id)
    )
    try:
        input_paths = (
            [Path(item.storage_uri) for item in info.files]
            if stage == "CP"
            else [Path(info.files[0].storage_uri).parent]
        )
        summary = _run_cleaner_and_summarize(stage, factory, input_paths, output_root)
        service(request).archive_previous_results(batch_id)
        service(request).record_result(batch_id, job_id, summary)
    except Exception as exc:
        service(request).mark_failed(batch_id, job_id, str(exc))
        raise DomainError("CLEANING_FAILED", f"重新处理失败：{exc}", 422) from exc
    return {
        "import_batch_id": batch_id,
        "status": "PROCESSED",
        "business_domain": domain,
        "test_stage": stage,
        "result": summary,
    }
