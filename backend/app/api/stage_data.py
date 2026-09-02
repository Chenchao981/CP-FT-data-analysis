from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import require_permission
from app.api.m2_filters import build_page_filters
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.data_domains import DataDomainRecord
from app.domain.jobs import CreateJobRequest, JobType, TriggerType
from app.domain.m2_queries import M2QueryService
from app.domain.stage_data import FormalSourceManifestPreview, StoredUpload
from app.infrastructure.source_catalog import SourceCatalog, SourceManifest, SourceRoot

router = APIRouter()
FACTORY_ALLOWED_SUFFIXES = {
    "huahong": {".zip", ".7z", ".txt"},
    "jetech": {".zip", ".xls", ".xlsx"},
    "lion": {".zip", ".xls", ".xlsx"},
    "riyuexin": {".xlsx"},
    "riyueguang": {".xlsx"},
    "dianji": {".xls", ".xlsx"},
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
    "CP": {"huahong", "jetech", "lion"},
    "FT": {"riyuexin", "riyueguang", "dianji"},
}
CP_FACTORIES = STAGE_FACTORIES["CP"]
FACTORY_ALIASES = {
    "华虹": "huahong",
    "hh": "huahong",
    "jt": "jetech",
    "捷特": "jetech",
    "立昂微": "lion",
    "日月新": "riyuexin",
    "日月光": "riyueguang",
    "ase": "riyueguang",
    "电基": "dianji",
}
REGISTRY_FACTORY_CODES = {
    "huahong": "HUAHONG",
    "jetech": "JETECH",
    "lion": "LION",
    "riyuexin": "RIYUEXIN",
    "riyueguang": "RIYUEGUANG",
    "dianji": "DIANJI",
}
UPLOAD_PAGE_STATUSES = frozenset(
    {
        "RECEIVED",
        "QUEUED",
        "PROCESSING",
        "NEEDS_INPUT",
        "PROCESSED",
        "FAILED",
        "CANCELLED",
    }
)
RESULT_PAGE_STATUSES = frozenset({"PROCESSED", "FAILED", "ARCHIVED"})


def service(request: Request):
    instance = getattr(request.app.state, "stage_data_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "数据服务尚未连接数据库", 503)
    return instance


def m2_query_service(request: Request) -> M2QueryService:
    instance = getattr(request.app.state, "m2_query_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "数据查询服务尚未连接数据库", 503)
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


def source_catalog(request: Request):
    return request.app.state.source_catalog


def data_domain_service(request: Request):
    instance = getattr(request.app.state, "data_domain_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "数据源授权服务尚未连接数据库",
            503,
        )
    return instance


def _hidden_formal_source_root() -> DomainError:
    return DomainError(
        "SOURCE_ROOT_NOT_FOUND",
        "数据源不存在或当前账户无权访问",
        404,
    )


def _formal_domain_binding(
    root: SourceRoot,
    grants: tuple[DataDomainRecord, ...],
) -> DataDomainRecord | None:
    code = (root.data_domain_code or "").strip().upper()
    for grant in grants:
        if grant.domain_code.strip().upper() != code:
            continue
        if grant.test_stage.strip().upper() != root.test_stage:
            continue
        factory = (grant.factory_code or "").strip().upper()
        if factory and factory != root.factory_code:
            continue
        return grant
    return None


def _authorized_formal_roots(
    request: Request,
    principal: Principal,
    *,
    business_domain: str,
    test_stage: str,
    factory_code: str,
) -> tuple[dict[str, object], ...]:
    catalog = source_catalog(request)
    grants = data_domain_service(request).list_for_principal(principal)
    visible: list[dict[str, object]] = []
    for public in catalog.list_roots(
        purpose="FORMAL_IMPORT",
        business_domain=business_domain,
        test_stage=test_stage,
        factory_code=factory_code,
    ):
        root = catalog.get_root(str(public["code"]))
        if _formal_domain_binding(root, grants) is not None:
            visible.append(public)
    return tuple(visible)


def _require_authorized_formal_root(
    request: Request,
    principal: Principal,
    *,
    root_code: str,
    business_domain: str,
    test_stage: str,
    factory_code: str,
) -> tuple[SourceRoot, DataDomainRecord]:
    catalog = source_catalog(request)
    grants = data_domain_service(request).list_for_principal(principal)
    try:
        root = catalog.require_scope(
            root_code,
            purpose="FORMAL_IMPORT",
            business_domain=business_domain,
            test_stage=test_stage,
            factory_code=factory_code,
        )
    except DomainError as exc:
        if exc.code in {"SOURCE_ROOT_NOT_FOUND", "SOURCE_ROOT_SCOPE_MISMATCH"}:
            raise _hidden_formal_source_root() from None
        raise
    binding = _formal_domain_binding(root, grants)
    if binding is None:
        raise _hidden_formal_source_root()
    return root, binding


def _queue_initial_import(
    request: Request,
    principal: Principal,
    payload: CreateJobRequest,
    *,
    allowed_batch_statuses: tuple[str, ...],
):
    queue = job_service(request)
    atomic_create = getattr(queue, "create_initial_import_for_batch", None)
    if callable(atomic_create):
        return atomic_create(
            payload,
            principal,
            allowed_batch_statuses=allowed_batch_statuses,
        )
    job = queue.create(payload)
    service(request).mark_queued(payload.import_batch_id)
    return job


def _normalize_business_domain(value: str) -> str:
    domain = BUSINESS_DOMAINS.get(value.strip().lower())
    if domain is None:
        raise DomainError(
            "BUSINESS_DOMAIN_UNSUPPORTED", f"不支持的业务分类：{value}", 404
        )
    return domain


def _normalize_list_business_domain(value: str) -> str:
    if value.strip().lower() == "all":
        return "ALL"
    return _normalize_business_domain(value)


def _normalize_list_stage(value: str) -> str:
    stage = LIST_TEST_STAGES.get(value.strip().lower())
    if stage is None:
        raise DomainError("TEST_STAGE_UNSUPPORTED", f"不支持的测试阶段：{value}", 404)
    return stage


def _normalize_page_stage(value: str) -> str:
    stage = UPLOAD_TEST_STAGES.get(value.strip().lower())
    if stage is None:
        raise DomainError(
            "TEST_STAGE_UNSUPPORTED",
            f"分页查询仅支持 CP/FT，收到：{value}",
            404,
        )
    return stage


def _normalize_factory(value: str, stage: str) -> str:
    normalized = value.strip().lower()
    factory = FACTORY_ALIASES.get(normalized, normalized)
    if factory not in STAGE_FACTORIES[stage]:
        raise DomainError(
            "FACTORY_UNSUPPORTED",
            f"当前{stage}不支持该厂家源数据",
            422,
        )
    return factory


def _save_uploads(
    business_domain: str,
    test_stage: str,
    files: list[UploadFile],
    allowed_suffixes: set[str],
) -> tuple[StoredUpload, ...]:
    if not files:
        raise DomainError("STAGE_UPLOAD_EMPTY", "请选择需要上传的源文件", 422)
    validated: list[tuple[UploadFile, str]] = []
    seen_names: set[str] = set()
    for uploaded in files:
        original = Path(uploaded.filename or "").name
        suffix = Path(original).suffix.lower()
        if not original or suffix not in allowed_suffixes:
            raise DomainError(
                "FILE_TYPE_UNSUPPORTED",
                f"不支持的{test_stage}源文件：{original or '未命名文件'}",
                422,
            )
        normalized_name = original.casefold()
        if normalized_name in seen_names:
            raise DomainError(
                "DUPLICATE_UPLOAD_FILE_NAME",
                f"同一批次不允许重复文件名（不区分大小写）：{original}",
                422,
            )
        seen_names.add(normalized_name)
        validated.append((uploaded, original))

    target = _upload_root() / business_domain.lower() / test_stage.lower() / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    stored: list[StoredUpload] = []
    try:
        for uploaded, original in validated:
            destination = target / original
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                while chunk := uploaded.file.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
            stored.append(
                StoredUpload(original, destination.resolve(), size, digest.hexdigest())
            )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return tuple(stored)


def _positive_limit(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


def _upload_root() -> Path:
    raw = os.getenv("TMS_UPLOAD_ROOT", r"F:\CP-FT数据分析\data\raw").strip()
    if not raw:
        raise RuntimeError("TMS_UPLOAD_ROOT must be a non-empty absolute path")
    unresolved = Path(raw).expanduser()
    if not unresolved.is_absolute():
        raise RuntimeError("TMS_UPLOAD_ROOT must be a non-empty absolute path")
    return unresolved.resolve()


def _managed_upload_path(value: str) -> Path:
    path = Path(value).resolve()
    upload_root = _upload_root()
    try:
        contained = os.path.commonpath(
            (os.path.normcase(str(upload_root)), os.path.normcase(str(path)))
        ) == os.path.normcase(str(upload_root))
    except ValueError:
        contained = False
    if not contained:
        raise DomainError(
            "UPLOAD_FILE_STORAGE_UNMANAGED",
            "该历史源文件不在 TMS 受管原始区，已禁止通过页面下载",
            409,
        )
    return path


def _cleanup_unregistered_uploads(
    stored: tuple[StoredUpload, ...], business_domain: str, test_stage: str
) -> None:
    """Remove only the fresh request snapshot when DB registration rolls back."""

    if not stored:
        return
    scope_root = (
        _upload_root() / business_domain.lower() / test_stage.lower()
    ).resolve()
    targets: set[Path] = set()
    for item in stored:
        path = item.path.resolve()
        try:
            relative = path.relative_to(scope_root)
        except ValueError:
            continue
        if not relative.parts:
            continue
        target = (scope_root / relative.parts[0]).resolve()
        if target.parent == scope_root:
            targets.add(target)
    for target in targets:
        shutil.rmtree(target, ignore_errors=True)


def _snapshot_catalog_directory(
    request: Request,
    principal: Principal,
    *,
    business_domain: str,
    test_stage: str,
    factory: str,
    root_code: str,
    relative_path: str,
    expected_manifest_mode: str,
    expected_manifest_sha256: str,
) -> tuple[tuple[StoredUpload, ...], int]:
    catalog, root, manifest, recursive, data_domain_id = _formal_source_manifest(
        request,
        principal,
        business_domain=business_domain,
        test_stage=test_stage,
        factory=factory,
        root_code=root_code,
        relative_path=relative_path,
    )
    if not manifest.matches_confirmation(
        mode=expected_manifest_mode, sha256=expected_manifest_sha256
    ):
        raise DomainError(
            "FORMAL_SOURCE_CHANGED",
            "源目录与提交前确认的清单不一致，请重新预览后再提交",
            409,
        )

    selected = catalog.resolve_directory(root.code, manifest.selected_relative_path)
    upload_root = _upload_root()
    catalog.assert_storage_separate(upload_root)
    upload_root.mkdir(parents=True, exist_ok=True)
    available = shutil.disk_usage(upload_root).free
    reserve = _positive_limit("TMS_FORMAL_MIN_FREE_BYTES", 1024**3)
    if available - manifest.total_bytes < reserve:
        raise DomainError(
            "FORMAL_SOURCE_DISK_CAPACITY_INSUFFICIENT",
            "正式入库快照空间不足，请联系管理员清理或扩容",
            507,
        )
    target = upload_root / business_domain.lower() / test_stage.lower() / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    selected_directory_name = selected.name.strip()
    if not selected_directory_name:
        shutil.rmtree(target, ignore_errors=True)
        raise DomainError(
            "FORMAL_SOURCE_IDENTITY_UNAVAILABLE",
            "所选目录缺少可保留的目录身份，请选择数据源内的业务目录",
            422,
        )
    snapshot_root = target / selected_directory_name
    stored: list[StoredUpload] = []
    try:
        for entry in manifest.files:
            source = selected / Path(entry.relative_path)
            # Existing CP Cleaners intentionally use the source parent directory as
            # business identity. Keep the selected directory leaf inside the managed
            # snapshot instead of exposing or reusing the physical source path.
            destination = snapshot_root / Path(entry.relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            copied_bytes = 0
            with source.open("rb") as input_stream, destination.open("xb") as output:
                while chunk := input_stream.read(1024 * 1024):
                    copied_bytes += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
            if copied_bytes != entry.size_bytes:
                raise DomainError(
                    "FORMAL_SOURCE_CHANGED",
                    f"源文件在建立快照时发生变化：{entry.relative_path}",
                    409,
                )
            metadata: dict[str, object] = {
                "purpose": "FORMAL_IMPORT",
                "source_root_code": root.code,
                "data_domain_code": root.data_domain_code,
                "source_relative_path": manifest.selected_relative_path,
                "source_file_relative_path": entry.relative_path,
                "source_manifest_mode": manifest.mode,
                "source_manifest_sha256": manifest.sha256,
                "source_file_count": manifest.file_count,
                "source_total_bytes": manifest.total_bytes,
                "snapshot_selected_directory_name": selected_directory_name,
                "snapshot_copy": True,
            }
            stored.append(
                StoredUpload(
                    entry.relative_path,
                    destination.resolve(),
                    copied_bytes,
                    digest.hexdigest(),
                    metadata,
                )
            )
        completed = catalog.build_manifest(
            root.code, relative_path, recursive=recursive
        )
        if (
            completed.sha256 != manifest.sha256
            or completed.as_json() != manifest.as_json()
        ):
            raise DomainError(
                "FORMAL_SOURCE_CHANGED",
                "源目录在建立正式入库快照期间发生变化，请重新选择后再提交",
                409,
            )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return tuple(stored), data_domain_id


def _formal_source_manifest(
    request: Request,
    principal: Principal,
    *,
    business_domain: str,
    test_stage: str,
    factory: str,
    root_code: str,
    relative_path: str,
) -> tuple[SourceCatalog, SourceRoot, SourceManifest, bool, int]:
    """Build the one authoritative formal-import manifest used by preview and submit."""

    catalog = source_catalog(request)
    registry_factory = REGISTRY_FACTORY_CODES[factory]
    root, binding = _require_authorized_formal_root(
        request,
        principal,
        root_code=root_code,
        business_domain=business_domain,
        test_stage=test_stage,
        factory_code=registry_factory,
    )
    allowed_suffixes = FACTORY_ALLOWED_SUFFIXES[factory]
    if not set(root.allowed_suffixes).issubset(allowed_suffixes):
        raise DomainError(
            "SOURCE_ROOT_CONTRACT_INVALID",
            "受控数据源配置包含当前厂家不允许的文件类型",
            503,
        )
    recursive = test_stage == "CP"
    manifest = catalog.build_manifest(root.code, relative_path, recursive=recursive)
    max_files = _positive_limit("TMS_FORMAL_MAX_SOURCE_FILES", 10_000)
    max_bytes = _positive_limit("TMS_FORMAL_MAX_SOURCE_BYTES", 50 * 1024**3)
    if manifest.file_count > max_files:
        raise DomainError(
            "FORMAL_SOURCE_FILE_LIMIT_EXCEEDED",
            f"正式入库源文件数超过上限 {max_files}",
            422,
        )
    if manifest.total_bytes > max_bytes:
        raise DomainError(
            "FORMAL_SOURCE_SIZE_LIMIT_EXCEEDED",
            f"正式入库源数据大小超过上限 {max_bytes} 字节",
            422,
        )
    return catalog, root, manifest, recursive, binding.data_domain_id


@router.get("/{business_domain}/{test_stage}/source-roots")
def list_formal_source_roots(
    request: Request,
    business_domain: str,
    test_stage: str,
    factory_code: str = Query(min_length=1, max_length=128),
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> tuple[dict[str, object], ...]:
    domain = _normalize_business_domain(business_domain)
    stage = UPLOAD_TEST_STAGES.get(test_stage.strip().lower())
    if stage is None:
        raise DomainError(
            "STAGE_UPLOAD_UNSUPPORTED",
            f"{test_stage.upper()}暂不支持受控数据源正式入库",
            422,
        )
    factory = _normalize_factory(factory_code, stage)
    return _authorized_formal_roots(
        request,
        principal,
        business_domain=domain,
        test_stage=stage,
        factory_code=REGISTRY_FACTORY_CODES[factory],
    )


@router.get("/{business_domain}/{test_stage}/source-roots/{root_code}/directories")
def list_formal_source_directories(
    root_code: str,
    request: Request,
    business_domain: str,
    test_stage: str,
    factory_code: str = Query(min_length=1, max_length=128),
    relative_path: str = Query(default=".", max_length=1000),
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict[str, object]:
    domain = _normalize_business_domain(business_domain)
    stage = UPLOAD_TEST_STAGES.get(test_stage.strip().lower())
    if stage is None:
        raise DomainError(
            "STAGE_UPLOAD_UNSUPPORTED",
            f"{test_stage.upper()}暂不支持受控数据源正式入库",
            422,
        )
    factory = _normalize_factory(factory_code, stage)
    catalog = source_catalog(request)
    root, _binding = _require_authorized_formal_root(
        request,
        principal,
        root_code=root_code,
        business_domain=domain,
        test_stage=stage,
        factory_code=REGISTRY_FACTORY_CODES[factory],
    )
    current, parent, directories = catalog.browse(root.code, relative_path)
    return {
        "root_code": root_code.strip().upper(),
        "current_relative_path": current,
        "parent_relative_path": parent,
        "directories": [asdict(item) for item in directories],
    }


@router.get("/{business_domain}/{test_stage}/source-roots/{root_code}/manifest-preview")
def preview_formal_source_manifest(
    root_code: str,
    request: Request,
    business_domain: str,
    test_stage: str,
    factory_code: str = Query(min_length=1, max_length=128),
    relative_path: str = Query(default=".", max_length=1000),
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict[str, object]:
    domain = _normalize_business_domain(business_domain)
    stage = UPLOAD_TEST_STAGES.get(test_stage.strip().lower())
    if stage is None:
        raise DomainError(
            "STAGE_UPLOAD_UNSUPPORTED",
            f"{test_stage.upper()}暂不支持受控数据源正式入库",
            422,
        )
    factory = _normalize_factory(factory_code, stage)
    _, root, manifest, recursive, _data_domain_id = _formal_source_manifest(
        request,
        principal,
        business_domain=domain,
        test_stage=stage,
        factory=factory,
        root_code=root_code,
        relative_path=relative_path,
    )
    preview = FormalSourceManifestPreview(
        root_code=root.code,
        relative_path=manifest.selected_relative_path,
        mode=manifest.mode,
        recursive=recursive,
        file_count=manifest.file_count,
        total_bytes=manifest.total_bytes,
        sha=manifest.sha256,
        allowed_suffixes=root.allowed_suffixes,
    )
    return asdict(preview)


@router.post(
    "/{business_domain}/{test_stage}/uploads", status_code=status.HTTP_201_CREATED
)
def upload_stage_data(
    request: Request,
    business_domain: str,
    test_stage: str,
    files: list[UploadFile] | None = File(None),
    factory_code: str = Form(""),
    source_root_code: str | None = Form(None),
    source_relative_path: str | None = Form(None),
    source_manifest_mode: str | None = Form(None),
    source_manifest_sha256: str | None = Form(None),
    source_path: str | None = Form(None),
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
    factory = _normalize_factory(factory_code, stage)
    if source_path and source_path.strip():
        raise DomainError(
            "SOURCE_PATH_UNSUPPORTED",
            "任意服务器绝对路径已关闭，请选择管理员授权的数据源和相对目录",
            422,
        )
    selected_root = (source_root_code or "").strip()
    selected_relative = (source_relative_path or ".").strip() or "."
    source_data_domain_id: int | None = None
    if selected_root:
        if files:
            raise DomainError(
                "SOURCE_INPUT_CONFLICT", "源文件上传和受控数据源只能选择一种", 422
            )
        confirmed_mode = (source_manifest_mode or "").strip()
        confirmed_sha256 = (source_manifest_sha256 or "").strip()
        if not confirmed_mode or not confirmed_sha256:
            raise DomainError(
                "FORMAL_SOURCE_MANIFEST_REQUIRED",
                "受控数据源提交前必须先预览并确认 Manifest",
                422,
            )
        stored, source_data_domain_id = _snapshot_catalog_directory(
            request,
            principal,
            business_domain=domain,
            test_stage=stage,
            factory=factory,
            root_code=selected_root,
            relative_path=selected_relative,
            expected_manifest_mode=confirmed_mode,
            expected_manifest_sha256=confirmed_sha256,
        )
    else:
        if source_manifest_mode or source_manifest_sha256:
            raise DomainError(
                "SOURCE_ROOT_REQUIRED",
                "Manifest 确认必须与受控数据源一起提交",
                422,
            )
        if source_relative_path and source_relative_path.strip() not in {"", "."}:
            raise DomainError(
                "SOURCE_ROOT_REQUIRED",
                "选择受控目录时必须同时选择数据源",
                422,
            )
        stored = _save_uploads(
            domain, stage, files or [], FACTORY_ALLOWED_SUFFIXES[factory]
        )
    try:
        batch_id = service(request).register_upload(
            principal,
            domain,
            stage,
            factory,
            stored,
            remark.strip() if remark else None,
            data_domain_id=source_data_domain_id,
        )
    except Exception:
        _cleanup_unregistered_uploads(stored, domain, stage)
        raise
    registry_factory = REGISTRY_FACTORY_CODES[factory]
    release = cleaner_registry(request).latest_released(stage, registry_factory)
    job = _queue_initial_import(
        request,
        principal,
        CreateJobRequest(
            import_batch_id=batch_id,
            cleaner_release_id=release.cleaner_release_id,
            job_type=JobType.INITIAL_IMPORT,
            trigger_type=TriggerType.AUTO,
            requested_by=principal.login_name,
            requested_by_user_id=principal.user_id,
            reason="上传后由 Route A Worker 调用已发布 Cleaner",
            idempotency_key=f"initial-import:{batch_id}",
        ),
        allowed_batch_statuses=("RECEIVED",),
    )
    return {
        "import_batch_id": batch_id,
        "job_id": job.job_id,
        "status": "QUEUED",
        "input_mode": "SOURCE_CATALOG" if selected_root else "WEB_UPLOAD",
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


@router.get("/{business_domain}/{test_stage}/uploads/page")
def list_stage_uploads_page(
    request: Request,
    business_domain: str,
    test_stage: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    factory_code: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    product_name: str | None = Query(default=None, max_length=200),
    lot_id: str | None = Query(default=None, max_length=128),
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    domain = _normalize_list_business_domain(business_domain)
    stage = _normalize_page_stage(test_stage)
    filters = build_page_filters(
        page=page,
        page_size=page_size,
        factory_code=factory_code,
        status=status_filter,
        product_name=product_name,
        lot_id=lot_id,
        from_utc=from_utc,
        to_utc=to_utc,
        allowed_statuses=UPLOAD_PAGE_STATUSES,
    )
    return asdict(
        m2_query_service(request).list_uploads_page(principal, domain, stage, filters)
    )


@router.get("/{business_domain}/{test_stage}/results/page")
def list_stage_results_page(
    request: Request,
    business_domain: str,
    test_stage: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    factory_code: str | None = Query(default=None, max_length=64),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    product_name: str | None = Query(default=None, max_length=200),
    lot_id: str | None = Query(default=None, max_length=128),
    from_utc: datetime | None = Query(default=None),
    to_utc: datetime | None = Query(default=None),
    principal: Principal = Depends(require_permission("DATASET_READ")),
) -> dict:
    domain = _normalize_list_business_domain(business_domain)
    stage = _normalize_page_stage(test_stage)
    filters = build_page_filters(
        page=page,
        page_size=page_size,
        factory_code=factory_code,
        status=status_filter,
        product_name=product_name,
        lot_id=lot_id,
        from_utc=from_utc,
        to_utc=to_utc,
        allowed_statuses=RESULT_PAGE_STATUSES,
    )
    return asdict(
        m2_query_service(request).list_results_page(principal, domain, stage, filters)
    )


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
    path = _managed_upload_path(match[0].storage_uri)
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
    batch_status = info.status.strip().upper()
    if batch_status == "NEEDS_INPUT":
        raise DomainError(
            "LOT_INPUT_RESOLUTION_REQUIRED",
            "该批次正在等待Lot补录，请使用专用补录入口保存并恢复任务",
            409,
        )
    if batch_status in {"QUEUED", "PROCESSING"}:
        raise DomainError(
            "BATCH_ALREADY_ACTIVE",
            "该批次已有排队中或处理中的任务，不能重复提交",
            409,
        )
    if batch_status not in {"PROCESSED", "FAILED"}:
        raise DomainError(
            "BATCH_REPROCESS_NOT_ALLOWED",
            f"当前批次状态不能重新处理：{batch_status}",
            409,
        )
    factory_aliases = {
        "华虹": "HUAHONG",
        "hh": "HUAHONG",
        "jt": "JETECH",
        "捷特": "JETECH",
        "立昂微": "LION",
        "日月新": "RIYUEXIN",
        "日月光": "RIYUEGUANG",
        "ase": "RIYUEGUANG",
        "电基": "DIANJI",
    }
    factory = factory_aliases.get(
        info.factory_code.strip().lower(), info.factory_code.strip().upper()
    )
    if factory not in {
        "HUAHONG",
        "JETECH",
        "LION",
        "RIYUEXIN",
        "RIYUEGUANG",
        "DIANJI",
    }:
        raise DomainError(
            "CAPABILITY_NOT_FORMAL_IMPORT",
            "该历史批次属于定制能力，不能从通用正式入库入口重新处理",
            422,
        )
    release = cleaner_registry(request).latest_released(stage, factory)
    job = _queue_initial_import(
        request,
        principal,
        CreateJobRequest(
            import_batch_id=batch_id,
            cleaner_release_id=release.cleaner_release_id,
            job_type=JobType.INITIAL_IMPORT,
            trigger_type=TriggerType.MANUAL,
            requested_by=principal.login_name,
            requested_by_user_id=principal.user_id,
            reason="用户重新处理：由 Route A Worker 重跑并发布新数据版本",
            idempotency_key=f"reprocess:{batch_id}:{uuid4().hex}",
        ),
        allowed_batch_statuses=("PROCESSED", "FAILED"),
    )
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
    }
