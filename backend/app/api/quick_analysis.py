from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.data_domains import DataDomainRecord
from app.domain.jobs import (
    CreateJobRequest,
    JobStatus,
    JobType,
    TransitionJobRequest,
    TriggerType,
)
from app.domain.quick_analysis import (
    DIRECT_PATH_TOOL_CONTRACTS,
    LOCAL_QUICK_PAT_ADAPTER_CODE,
    LOCAL_QUICK_PAT_INPUT_CONTRACT,
    LOCAL_QUICK_PAT_OUTPUT_CONTRACT,
    LOCAL_QUICK_PAT_TOOL_CODE,
    CreateDirectPathPatRequest,
    CreateDirectPathTaskRequest,
    CreateQuickPatRequest,
    DirectPathBrowseRequest,
    DirectPathPreviewRequest,
    LocalQuickPatResultReceipt,
    NewQuickAnalysisSession,
    QuickAnalysisStatus,
    TemporaryFtpPreviewRequest,
)
from app.infrastructure.direct_path_source import (
    browse_direct_path,
    build_direct_path_manifest,
)
from app.infrastructure.local_quick_result import (
    CommittedLocalQuickResult,
    LocalQuickResultStore,
    StagedLocalQuickResult,
    local_quick_pat_capability,
    validate_local_quick_pat_release,
)
from app.infrastructure.quick_result_export import QuickResultExportStore
from app.infrastructure.temporary_ftp_source import preview_ftp_directory

router = APIRouter(prefix="/quick-analysis")
logger = logging.getLogger(__name__)


def quick_service(request: Request):
    return request.app.state.quick_analysis_service


def source_catalog(request: Request):
    return request.app.state.source_catalog


def _data_domain_service(request: Request):
    instance = getattr(request.app.state, "data_domain_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "数据源授权服务尚未连接数据库",
            503,
        )
    return instance


def _hidden_source_root() -> DomainError:
    return DomainError(
        "SOURCE_ROOT_NOT_FOUND",
        "数据源不存在或当前账户无权访问",
        404,
    )


def _granted_data_domains_by_code(
    request: Request, principal: Principal
) -> dict[str, DataDomainRecord]:
    return {
        item.domain_code.strip().upper(): item
        for item in _data_domain_service(request).list_for_principal(principal)
    }


def _require_authorized_quick_root(
    request: Request, principal: Principal, root_code: str
):
    catalog = source_catalog(request)
    try:
        root = catalog.get_root(root_code)
    except DomainError as exc:
        if exc.code == "SOURCE_ROOT_NOT_FOUND":
            raise _hidden_source_root() from None
        raise
    expected_scope = ("QUICK_ANALYSIS", "FT", "JIEQUN")
    data_domain_code = (root.data_domain_code or "").strip().upper()
    if (
        root.purpose,
        root.test_stage,
        root.factory_code,
    ) != expected_scope or not data_domain_code:
        raise _hidden_source_root()
    domain = _granted_data_domains_by_code(request, principal).get(data_domain_code)
    if domain is None:
        raise _hidden_source_root()
    if domain.test_stage != root.test_stage or domain.factory_code not in {
        None,
        root.factory_code,
    }:
        raise _hidden_source_root()
    return root, domain


def cleaner_registry(request: Request):
    instance = getattr(request.app.state, "cleaner_registry", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED", "Cleaner Registry 尚未连接数据库", 503
        )
    return instance


def _local_quick_pat_release(request: Request):
    return cleaner_registry(request).latest_released_for_contract(
        test_stage="FT",
        factory_code="JIEQUN",
        format_code=LOCAL_QUICK_PAT_TOOL_CODE,
        cleaner_code=LOCAL_QUICK_PAT_TOOL_CODE,
        adapter_code=LOCAL_QUICK_PAT_ADAPTER_CODE,
        input_contract_version=LOCAL_QUICK_PAT_INPUT_CONTRACT,
        output_contract_version=LOCAL_QUICK_PAT_OUTPUT_CONTRACT,
    )


def _direct_path_tool(tool_code: str) -> dict[str, object]:
    return DIRECT_PATH_TOOL_CONTRACTS[tool_code]


def _direct_path_visible_suffixes(contract: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *tuple(contract["allowed_suffixes"]),
                *tuple(contract.get("single_file_suffixes", ())),
            )
        )
    )


def _direct_path_operation_contract(
    contract: dict[str, object], operation_code: str
) -> dict[str, object]:
    operation = operation_code.strip().upper()
    stage = str(contract["test_stage"])
    factory = str(contract["factory_code"])
    supported = {"PAT", "CLEAN"}
    if stage == "CP" or factory in {"RIYUEXIN", "JIEQUN", "DIANJI"}:
        supported.add("CHART")
    if factory in {"RIYUEXIN", "JIEQUN", "DIANJI"}:
        supported.add("SYL_SBL")
    if operation not in supported:
        raise DomainError(
            "QUICK_OPERATION_UNSUPPORTED",
            "当前厂家尚未提供所选个人分析功能",
            422,
        )
    if operation == "SYL_SBL":
        return {
            **contract,
            "allowed_suffixes": (".xls", ".xlsx"),
            "single_file_suffixes": (".xls", ".xlsx"),
            "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
            "tool_name": f"{contract['tool_name']} · SBL/SYL",
        }
    return contract


def _analysis_type(operation_code: str) -> str:
    return {
        "PAT": "QUICK_PAT",
        "CLEAN": "QUICK_CLEAN",
        "CHART": "QUICK_CHART",
        "SYL_SBL": "QUICK_SYL_SBL",
    }[operation_code.strip().upper()]


def _direct_path_release(request: Request, tool_code: str):
    contract = _direct_path_tool(tool_code)
    return cleaner_registry(request).latest_released_for_contract(
        test_stage=str(contract["test_stage"]),
        factory_code=str(contract["factory_code"]),
        format_code=str(contract["format_code"]),
        cleaner_code=str(contract["cleaner_code"]),
        adapter_code=str(contract["adapter_code"]),
        input_contract_version=str(contract["input_contract_version"]),
        output_contract_version=str(contract["output_contract_version"]),
    )


def job_service(request: Request):
    return request.app.state.job_service


def _create_quick_job(
    request: Request, payload: CreateJobRequest, principal: Principal
):
    service = job_service(request)
    if hasattr(service, "create_for_principal"):
        return service.create_for_principal(payload, principal)
    return service.create(payload)


def capacity_policy(request: Request):
    return request.app.state.quick_capacity_policy


def _quick_expiry() -> datetime:
    ttl_hours = int(os.getenv("TMS_QUICK_RESULT_TTL_HOURS", "168"))
    if ttl_hours < 1 or ttl_hours > 8760:
        raise RuntimeError("TMS_QUICK_RESULT_TTL_HOURS must be between 1 and 8760")
    return datetime.now(UTC) + timedelta(hours=ttl_hours)


def _parse_local_receipt(raw: str) -> LocalQuickPatResultReceipt:
    try:
        return LocalQuickPatResultReceipt.model_validate_json(raw)
    except ValidationError as exc:
        details = [
            {
                "field": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in exc.errors(include_url=False, include_input=False)
        ]
        raise DomainError(
            "LOCAL_RESULT_RECEIPT_INVALID",
            "Local Agent 结果回执不符合 TMS_LOCAL_RESULT_V1",
            422,
            details,
        ) from exc


def _fail_local_result(
    request: Request,
    analysis_session_id: int,
    job_id: int,
    error_code: str,
    error_message: str,
    *,
    cleanup_complete: bool,
) -> None:
    try:
        service = quick_service(request)
        if cleanup_complete:
            service.mark_failed_cleaned(analysis_session_id, error_code, error_message)
        else:
            service.mark_failed(analysis_session_id, error_code, error_message)
    except Exception:
        logger.exception(
            "failed to mark Local Agent quick session %s as failed",
            analysis_session_id,
        )
    try:
        job = job_service(request).get(job_id)
        if job.status == JobStatus.QUEUED:
            job_service(request).transition(
                job_id, TransitionJobRequest(target_status=JobStatus.RUNNING)
            )
            job = job_service(request).get(job_id)
        if job.status == JobStatus.RUNNING:
            job_service(request).transition(
                job_id,
                TransitionJobRequest(
                    target_status=JobStatus.FAILED,
                    error_code=error_code[:64],
                    error_message=error_message[-2000:],
                ),
            )
    except Exception:
        logger.exception("failed to mark Local Agent job %s as failed", job_id)


def _discard_local_result(
    store: LocalQuickResultStore,
    staged: StagedLocalQuickResult | None,
    committed: CommittedLocalQuickResult | None,
) -> bool:
    cleanup_complete = True
    if staged is not None and staged.stage_dir.exists():
        try:
            store.discard_staged(staged.stage_dir)
        except Exception:
            cleanup_complete = False
            logger.exception(
                "failed to discard Local Agent staging directory %s",
                staged.stage_dir,
            )
    if committed is not None and committed.job_root.exists():
        try:
            store.discard_committed(committed.job_root)
        except Exception:
            cleanup_complete = False
            logger.exception(
                "failed to discard Local Agent committed directory %s",
                committed.job_root,
            )
    return cleanup_complete


@router.post("/direct-path/preview")
def preview_direct_path(
    payload: DirectPathPreviewRequest,
    request: Request,
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    contract = _direct_path_operation_contract(
        _direct_path_tool(payload.tool_code), payload.operation_code
    )
    release = _direct_path_release(request, payload.tool_code)
    if payload.tool_code == LOCAL_QUICK_PAT_TOOL_CODE:
        validate_local_quick_pat_release(release)
    source, manifest = build_direct_path_manifest(
        payload.path,
        allowed_suffixes=tuple(contract["allowed_suffixes"]),
        allowed_single_file_suffixes=tuple(
            contract.get("single_file_suffixes", contract["allowed_suffixes"])
        ),
        path_policy=str(contract["manifest_policy"]),
    )
    if payload.operation_code == "SYL_SBL" and not source.is_file():
        raise DomainError(
            "QUICK_SYL_SBL_FILE_REQUIRED",
            "SBL/SYL 必须明确选择一个良率 Excel 文件",
            422,
        )
    return {
        "path": str(source),
        "source_label": manifest.source_label,
        "input_kind": "FILE" if source.is_file() else "DIRECTORY",
        "mode": manifest.mode,
        "recursive": True,
        "file_count": manifest.file_count,
        "total_bytes": manifest.total_bytes,
        "archive_count": sum(
            1
            for item in manifest.files
            if Path(item.relative_path).suffix.lower() in {".zip", ".7z"}
        ),
        "sample_files": [item.relative_path for item in manifest.files[:100]],
        "sample_truncated": manifest.file_count > 100,
        "sha": manifest.sha256,
        "allowed_suffixes": list(_direct_path_visible_suffixes(contract)),
        "tool_code": payload.tool_code,
        "tool_name": contract["tool_name"],
        "test_stage": contract["test_stage"],
        "factory_code": contract["factory_code"],
    }


@router.post("/direct-path/browse")
def browse_local_path(
    payload: DirectPathBrowseRequest,
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    contract = _direct_path_operation_contract(
        _direct_path_tool(payload.tool_code), payload.operation_code
    )
    return browse_direct_path(
        payload.path,
        allowed_suffixes=tuple(contract["allowed_suffixes"]),
        selectable_file_suffixes=tuple(
            contract.get("single_file_suffixes", contract["allowed_suffixes"])
        ),
    )


@router.post("/direct-path/pat", status_code=status.HTTP_201_CREATED)
def create_direct_path_pat(
    payload: CreateDirectPathPatRequest,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    payload.operation_code = "PAT"
    return _create_direct_path_task(payload, request, principal)


@router.post("/direct-path/tasks", status_code=status.HTTP_201_CREATED)
def create_direct_path_task(
    payload: CreateDirectPathTaskRequest,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    return _create_direct_path_task(payload, request, principal)


def _create_direct_path_task(
    payload: CreateDirectPathTaskRequest | CreateDirectPathPatRequest,
    request: Request,
    principal: Principal,
) -> dict[str, object]:
    contract = _direct_path_operation_contract(
        _direct_path_tool(payload.tool_code), payload.operation_code
    )
    source, manifest = build_direct_path_manifest(
        payload.path,
        allowed_suffixes=tuple(contract["allowed_suffixes"]),
        allowed_single_file_suffixes=tuple(
            contract.get("single_file_suffixes", contract["allowed_suffixes"])
        ),
        path_policy=str(contract["manifest_policy"]),
    )
    if payload.operation_code == "SYL_SBL" and not source.is_file():
        raise DomainError(
            "QUICK_SYL_SBL_FILE_REQUIRED",
            "SBL/SYL 必须明确选择一个良率 Excel 文件",
            422,
        )
    if not manifest.matches_confirmation(
        mode=payload.source_manifest_mode,
        sha256=payload.source_manifest_sha256,
    ):
        raise DomainError(
            "QUICK_SOURCE_CHANGED",
            "目录与预览时的文件范围不一致，请重新预览后再计算",
            409,
        )
    capacity = capacity_policy(request)
    reserved_bytes = capacity.reservation_for(manifest.total_bytes)
    capacity.ensure_filesystem_capacity(reserved_bytes)
    release = _direct_path_release(request, payload.tool_code)
    if payload.tool_code == LOCAL_QUICK_PAT_TOOL_CODE:
        validate_local_quick_pat_release(release)
    session = quick_service(request).create(
        principal,
        NewQuickAnalysisSession(
            analysis_type=_analysis_type(payload.operation_code),
            test_stage=str(contract["test_stage"]),
            factory_code=str(contract["factory_code"]),
            # LOCAL_AGENT is retained as the SQL compatibility value for personal
            # direct-path results. No Local Agent process participates in this flow.
            source_root_code="LOCAL_AGENT",
            source_relative_path=str(source),
            source_manifest_mode=manifest.mode,
            source_manifest_json=manifest.as_json(),
            source_manifest_sha256=manifest.sha256,
            source_file_count=manifest.file_count,
            source_total_bytes=manifest.total_bytes,
            retention_mode="RESULT_ONLY",
            cleaner_release_id=release.cleaner_release_id,
            expires_at_utc=_quick_expiry(),
            access_scope="PERSONAL",
            data_domain_id=None,
            reserved_bytes=reserved_bytes,
        ),
    )
    export_store = QuickResultExportStore(capacity.work_root)
    job = None
    try:
        if payload.output_directory:
            export_store.register(
                session.analysis_session_id, payload.output_directory
            )
        job = _create_quick_job(
            request,
            CreateJobRequest(
                analysis_session_id=session.analysis_session_id,
                cleaner_release_id=release.cleaner_release_id,
                job_type=JobType.QUICK_PAT,
                trigger_type=TriggerType.MANUAL,
                requested_by=principal.login_name,
                requested_by_user_id=principal.user_id,
                reason=(
                    "直接读取当前 TMS 主机可访问路径并调用既有 "
                    f"{contract['test_stage']} {payload.operation_code} 工具"
                ),
                idempotency_key=(
                    f"direct-path-{payload.operation_code.lower()}:"
                    f"{session.analysis_session_id}"
                ),
            ),
            principal,
        )
        quick_service(request).attach_job(session.analysis_session_id, job.job_id)
    except Exception as exc:
        export_store.discard(session.analysis_session_id)
        if job is None:
            quick_service(request).mark_failed_cleaned(
                session.analysis_session_id, "QUEUE_CREATE_FAILED", str(exc)
            )
        else:
            quick_service(request).mark_failed(
                session.analysis_session_id, "QUEUE_CREATE_FAILED", str(exc)
            )
        raise
    return asdict(
        quick_service(request).get_for_principal(
            session.analysis_session_id, principal
        )
    )


@router.post("/temporary-ftp/preview")
def preview_temporary_ftp(
    payload: TemporaryFtpPreviewRequest,
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    preview = preview_ftp_directory(
        protocol=payload.protocol,
        server=payload.server,
        port=payload.port,
        username=payload.username,
        password=payload.password,
        remote_path=payload.remote_path,
    )
    return {
        "protocol": preview.protocol,
        "server": preview.server,
        "port": preview.port,
        "remote_path": preview.remote_path,
        "mode": "FTP_PATH_SIZE_MTIME_V1",
        "recursive": True,
        "file_count": preview.file_count,
        "total_bytes": preview.total_bytes,
        "sha": preview.sha256,
        "allowed_suffixes": [".csv"],
        "sample_files": [item.relative_path for item in preview.files[:20]],
        "tool_code": LOCAL_QUICK_PAT_TOOL_CODE,
    }


@router.get("/source-roots")
def list_source_roots(
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> tuple[dict[str, object], ...]:
    roots = source_catalog(request).list_roots(purpose="QUICK_ANALYSIS")
    if not roots:
        return ()
    granted_domains = _granted_data_domains_by_code(request, principal)
    return tuple(
        {
            **root,
            "data_domain_id": granted_domains[
                str(root["data_domain_code"]).strip().upper()
            ].data_domain_id,
        }
        for root in roots
        if root["data_domain_code"] is not None
        and str(root["data_domain_code"]).strip().upper() in granted_domains
        and granted_domains[str(root["data_domain_code"]).strip().upper()].test_stage
        == root["test_stage"]
        and granted_domains[str(root["data_domain_code"]).strip().upper()].factory_code
        in {None, root["factory_code"]}
    )


@router.get("/source-roots/{root_code}/directories")
def list_source_directories(
    root_code: str,
    request: Request,
    relative_path: str = Query(default=".", max_length=1000),
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    catalog = source_catalog(request)
    _require_authorized_quick_root(request, principal, root_code)
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
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    catalog = source_catalog(request)
    root, _domain = _require_authorized_quick_root(request, principal, root_code)
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


@router.get("/local-capability")
def get_local_quick_capability(
    request: Request,
    _principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    release = _local_quick_pat_release(request)
    return local_quick_pat_capability(release)


@router.post("/local-results", status_code=status.HTTP_201_CREATED)
def receive_local_quick_result(
    request: Request,
    receipt_json: str = Form(..., min_length=2, max_length=20_000),
    result_file: UploadFile = File(...),  # noqa: B008
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict[str, object]:
    parsed = _parse_local_receipt(receipt_json)
    release = _local_quick_pat_release(request)
    validate_local_quick_pat_release(release)
    if parsed.release_sha256 != release.code_checksum.lower():
        raise DomainError(
            "LOCAL_RESULT_RELEASE_MISMATCH",
            "Local Agent 使用的 Cleaner SHA-256 不是服务器当前已发布版本",
            409,
        )
    capacity = capacity_policy(request)
    reserved_bytes = capacity.reservation_for_local_result(release.max_output_bytes)
    capacity.ensure_filesystem_capacity(reserved_bytes)
    source_manifest_json = json.dumps(
        {
            "contract_version": parsed.contract_version,
            "source_label": parsed.source_label,
            **parsed.manifest.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    session = quick_service(request).create(
        principal,
        NewQuickAnalysisSession(
            analysis_type=parsed.analysis_type,
            test_stage=parsed.test_stage,
            factory_code=parsed.factory_code,
            source_root_code="LOCAL_AGENT",
            source_relative_path=parsed.source_label,
            source_manifest_mode=parsed.manifest.mode,
            source_manifest_json=source_manifest_json,
            source_manifest_sha256=parsed.manifest.sha256,
            source_file_count=parsed.manifest.file_count,
            source_total_bytes=parsed.manifest.total_bytes,
            retention_mode="RESULT_ONLY",
            cleaner_release_id=release.cleaner_release_id,
            expires_at_utc=_quick_expiry(),
            access_scope="PERSONAL",
            data_domain_id=None,
            reserved_bytes=reserved_bytes,
        ),
    )
    job = None
    staged = None
    committed = None
    store = LocalQuickResultStore(capacity.work_root)
    try:
        job = _create_quick_job(
            request,
            CreateJobRequest(
                analysis_session_id=session.analysis_session_id,
                cleaner_release_id=release.cleaner_release_id,
                job_type=JobType.QUICK_PAT,
                trigger_type=TriggerType.API,
                requested_by=principal.login_name,
                requested_by_user_id=principal.user_id,
                reason="接收用户 Local Agent 生成的杰群 FT Quick PAT 结果",
                idempotency_key=f"local-result:{session.analysis_session_id}",
            ),
            principal,
        )
        quick_service(request).attach_job(session.analysis_session_id, job.job_id)
        job_service(request).transition(
            job.job_id, TransitionJobRequest(target_status=JobStatus.RUNNING)
        )
        quick_service(request).mark_running(session.analysis_session_id)
        staged = store.stage(
            result_file.file,
            upload_filename=result_file.filename,
            receipt=parsed,
            max_output_bytes=release.max_output_bytes,
        )
        committed = store.commit(
            staged,
            job_id=job.job_id,
            receipt=parsed,
            release=release,
        )
        staged = None
        quick_service(request).record_success(
            session.analysis_session_id,
            job.job_id,
            parameter_count=parsed.summary.parameter_count,
            record_count=parsed.summary.record_count,
            summary=committed.summary,
            artifacts=committed.artifacts,
        )
        job_service(request).transition(
            job.job_id, TransitionJobRequest(target_status=JobStatus.SUCCESS)
        )
    except Exception as exc:
        cleanup_complete = _discard_local_result(store, staged, committed)
        is_domain_error = isinstance(exc, DomainError)
        code = exc.code if is_domain_error else "LOCAL_RESULT_RECEIVE_FAILED"
        user_message = (
            exc.message
            if is_domain_error
            else "Local Agent PAT 结果接收失败，请联系系统管理员查看受控日志"
        )
        if not is_domain_error:
            logger.exception(
                "Local Agent result receive failed for session %s",
                session.analysis_session_id,
            )
        if job is not None:
            _fail_local_result(
                request,
                session.analysis_session_id,
                job.job_id,
                code,
                user_message,
                cleanup_complete=cleanup_complete,
            )
        else:
            try:
                quick_service(request).mark_failed_cleaned(
                    session.analysis_session_id,
                    "LOCAL_RESULT_JOB_CREATE_FAILED",
                    "Local Agent 结果任务创建失败，请联系系统管理员查看受控日志",
                )
            except Exception:
                logger.exception(
                    "failed to mark Local Agent quick session %s as cleaned",
                    session.analysis_session_id,
                )
        if is_domain_error:
            raise
        raise DomainError(
            "LOCAL_RESULT_RECEIVE_FAILED",
            "Local Agent PAT 结果接收失败",
            500,
        ) from exc
    completed = quick_service(request).get_for_principal(
        session.analysis_session_id, principal
    )
    return {
        **asdict(completed),
        "contract_version": parsed.contract_version,
        "result": parsed.result.model_dump(mode="json"),
        "artifacts": [
            {
                "role": artifact.role,
                "filename": Path(artifact.path).name,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
            for artifact in committed.artifacts
        ],
    }


@router.post("/pat", status_code=status.HTTP_201_CREATED)
def create_quick_pat(
    payload: CreateQuickPatRequest,
    request: Request,
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict:
    catalog = source_catalog(request)
    root, domain = _require_authorized_quick_root(
        request, principal, payload.source_root_code
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
    release = _local_quick_pat_release(request)
    validate_local_quick_pat_release(release)
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
            expires_at_utc=_quick_expiry(),
            access_scope="DOMAIN",
            data_domain_id=domain.data_domain_id,
            data_domain_code=domain.domain_code,
            reserved_bytes=reserved_bytes,
        ),
    )
    job = None
    try:
        job = _create_quick_job(
            request,
            CreateJobRequest(
                analysis_session_id=session.analysis_session_id,
                cleaner_release_id=release.cleaner_release_id,
                job_type=JobType.QUICK_PAT,
                trigger_type=TriggerType.MANUAL,
                requested_by=principal.login_name,
                requested_by_user_id=principal.user_id,
                reason="受控服务器目录直接执行杰群低内存 PAT，不写入 Canonical",
                idempotency_key=f"quick-pat:{session.analysis_session_id}",
            ),
            principal,
        )
        quick_service(request).attach_job(session.analysis_session_id, job.job_id)
    except Exception as exc:
        if job is None:
            quick_service(request).mark_failed_cleaned(
                session.analysis_session_id, "QUEUE_CREATE_FAILED", str(exc)
            )
        else:
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
    access_scope: Literal["PERSONAL", "DOMAIN"] | None = Query(default=None),
    principal: Principal = Depends(require_permission("ANALYSIS_RUN")),  # noqa: B008
) -> dict:
    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise DomainError("QUICK_TIME_RANGE_INVALID", "开始时间必须早于结束时间", 422)
    result = quick_service(request).list_page_for_principal(
        principal,
        page=page,
        page_size=page_size,
        status=status_filter,
        from_utc=from_utc,
        to_utc=to_utc,
        access_scope=access_scope,
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
    if not contained or path.suffix.lower() not in {".xlsx", ".zip", ".html"}:
        raise DomainError("QUICK_RESULT_PATH_INVALID", "分析结果存储路径无效", 409)
    if not path.is_file():
        raise DomainError("QUICK_RESULT_MISSING", "分析结果已不在存储位置", 404)
    if (
        path.stat().st_size != artifact.size_bytes
        or _sha256_file(path) != artifact.sha256
    ):
        raise DomainError(
            "QUICK_RESULT_INTEGRITY_MISMATCH",
            "分析结果文件与登记的大小或 SHA-256 不一致，已停止下载",
            409,
        )
    return FileResponse(path, filename=path.name)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
