from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.stage_data import StoredUpload
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner


router = APIRouter()
ALLOWED_SUFFIXES = {
    "CP": {".zip", ".7z", ".txt"},
    "FT": {".xlsx"},
}
BUSINESS_DOMAINS = {"engineering": "ENGINEERING", "production": "PRODUCTION"}
LIST_TEST_STAGES = {"cp": "CP", "ft": "FT", "wat": "WAT", "wft": "WFT", "slt": "SLT", "qa": "QA", "ort": "ORT", "other": "OTHER"}
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


def _normalize_business_domain(value: str) -> str:
    domain = BUSINESS_DOMAINS.get(value.strip().lower())
    if domain is None:
        raise DomainError("BUSINESS_DOMAIN_UNSUPPORTED", f"不支持的业务分类：{value}", 404)
    return domain


def _normalize_list_stage(value: str) -> str:
    stage = LIST_TEST_STAGES.get(value.strip().lower())
    if stage is None:
        raise DomainError("TEST_STAGE_UNSUPPORTED", f"不支持的测试阶段：{value}", 404)
    return stage


def _save_uploads(business_domain: str, test_stage: str, files: list[UploadFile]) -> tuple[StoredUpload, ...]:
    if not files:
        raise DomainError("STAGE_UPLOAD_EMPTY", "请选择需要上传的源文件", 422)
    allowed = ALLOWED_SUFFIXES[test_stage]
    target = Path(os.getenv("TMS_UPLOAD_ROOT", r"F:\CP-FT数据分析\data\raw")) / business_domain.lower() / test_stage.lower() / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    stored: list[StoredUpload] = []
    for uploaded in files:
        original = Path(uploaded.filename or "").name
        suffix = Path(original).suffix.lower()
        if not original or suffix not in allowed:
            raise DomainError("FILE_TYPE_UNSUPPORTED", f"不支持的{test_stage}源文件：{original or '未命名文件'}", 422)
        destination = target / original
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            while chunk := uploaded.file.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
        stored.append(StoredUpload(original, destination.resolve(), size, digest.hexdigest()))
    return tuple(stored)


def _read_cp_summary(run_result) -> dict:
    yield_files = [Path(item.path) for item in run_result.artifacts if item.role == "yield"]
    cleaned_files = [Path(item.path) for item in run_result.artifacts if item.role == "cleaned"]
    if not yield_files or not cleaned_files:
        raise RuntimeError("现有CP清洗程序没有生成cleaned/yield标准文件")
    lots: list[str] = []
    products: list[str] = []
    wafers: set[tuple[str, str]] = set()
    units = passes = 0
    for path in yield_files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                lot = (row.get("Lot_ID") or "").strip()
                product = (row.get("Product_Name") or "").strip()
                wafer = (row.get("Wafer_ID") or "").strip()
                if lot.upper() == "ALL" or wafer.upper() == "ALL":
                    continue
                if lot and lot not in lots:
                    lots.append(lot)
                if product and product not in products:
                    products.append(product)
                if lot or wafer:
                    wafers.add((lot, wafer))
                units += int(float(row.get("Total") or 0))
                passes += int(float(row.get("Pass") or 0))
    with cleaned_files[0].open("r", encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), [])
    base = {"Lot_ID", "Wafer_ID", "Seq", "Bin", "X", "Y"}
    return {
        "data_name": "、".join(lots) or cleaned_files[0].stem,
        "product_name": "、".join(products) or None,
        "lot_id": "、".join(lots) or None,
        "wafer_count": len(wafers),
        "factory_code": run_result.factory,
        "output_uri": run_result.output_root,
        "test_item_count": sum(1 for name in header if name not in base),
        "unit_count": units,
        "pass_count": passes,
        "yield_rate": passes / units if units else None,
        "data_type": "CP",
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }


def _read_ft_summary(run_result) -> dict:
    manifest_files = [Path(item.path) for item in run_result.artifacts if item.role == "scatter_manifest"]
    spec_files = [Path(item.path) for item in run_result.artifacts if item.role == "scatter_spec"]
    cleaned_files = [Path(item.path) for item in run_result.artifacts if item.role == "cleaned"]
    if not manifest_files or not cleaned_files:
        raise RuntimeError("现有FT清洗程序没有生成cleaned/scatter标准文件")
    lots: list[str] = []
    products: list[str] = []
    parameters: list[str] = []
    unit_count = 0
    for path in manifest_files:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for lot in manifest.get("lots") or []:
            if lot and lot not in lots:
                lots.append(lot)
        for parameter in manifest.get("parameters") or []:
            if parameter and parameter not in parameters:
                parameters.append(parameter)
        unit_count += int(manifest.get("row_count") or 0)
    for path in spec_files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                source_file = (row.get("Source_File") or "").strip()
                product = source_file.split("_")[1] if "_" in source_file else ""
                if product and product not in products:
                    products.append(product)
    return {
        "data_name": "、".join(lots) or cleaned_files[0].stem,
        "product_name": "、".join(products) or None,
        "lot_id": "、".join(lots) or None,
        "wafer_count": None,
        "factory_code": run_result.factory,
        "output_uri": run_result.output_root,
        "test_item_count": len(parameters),
        "unit_count": unit_count,
        "pass_count": None,
        "yield_rate": None,
        "data_type": "FT",
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }


def _run_cleaner_and_summarize(stage: str, factory: str, input_paths: list[Path], output_root: Path) -> dict:
    result = ExistingCleanerRunner().run(test_stage=stage, factory=factory, inputs=input_paths, output_root=output_root)
    return _read_cp_summary(result) if stage == "CP" else _read_ft_summary(result)


@router.post("/{business_domain}/{test_stage}/uploads", status_code=status.HTTP_201_CREATED)
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
        raise DomainError("STAGE_UPLOAD_UNSUPPORTED", f"{test_stage.upper()}数据上传将在后续版本开放，当前支持CP/FT数据", 422)
    factory_aliases = {"华虹": "huahong", "hh": "huahong", "日月新": "riyuexin"}
    factory = factory_aliases.get(factory_code.strip().lower(), factory_code.strip().lower())
    if factory not in STAGE_FACTORIES[stage]:
        raise DomainError("FACTORY_UNSUPPORTED", f"当前{stage}首版支持{'华虹' if stage == 'CP' else '日月新'}源数据", 422)
    stored = _save_uploads(domain, stage, files)
    batch_id = service(request).register_upload(principal, domain, stage, factory, stored, remark.strip() if remark else None)
    job_id = service(request).mark_processing(batch_id, principal)
    output_root = Path(os.getenv("TMS_PROCESSED_ROOT", r"F:\CP-FT数据分析\data\processed")) / domain.lower() / stage.lower() / str(batch_id)
    try:
        input_paths = [item.path for item in stored] if stage == "CP" else [stored[0].path.parent]
        summary = _run_cleaner_and_summarize(stage, factory, input_paths, output_root)
        service(request).record_result(batch_id, job_id, summary)
    except Exception as exc:
        service(request).mark_failed(batch_id, job_id, str(exc))
        raise DomainError("CLEANING_FAILED", f"文件已入库，但清洗失败：{exc}", 422) from exc
    return {"import_batch_id": batch_id, "status": "PROCESSED", "business_domain": domain, "test_stage": stage, "uploader": {"user_id": principal.user_id, "login_name": principal.login_name, "display_name": principal.display_name}, "result": summary}


@router.get("/{business_domain}/{test_stage}/uploads")
def list_stage_uploads(request: Request, business_domain: str, test_stage: str, principal: Principal = Depends(require_permission("DATASET_READ"))) -> list[dict]:
    domain = _normalize_business_domain(business_domain)
    stage = _normalize_list_stage(test_stage)
    return [asdict(item) for item in service(request).list_uploads(principal, domain, stage)]


@router.get("/{business_domain}/{test_stage}/results")
def list_stage_results(request: Request, business_domain: str, test_stage: str, principal: Principal = Depends(require_permission("DATASET_READ"))) -> list[dict]:
    domain = _normalize_business_domain(business_domain)
    stage = _normalize_list_stage(test_stage)
    return [asdict(item) for item in service(request).list_results(principal, domain, stage)]


@router.get("/{business_domain}/{test_stage}/uploads/{batch_id}/files/{receipt_id}/download")
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
        raise DomainError("STAGE_UPLOAD_UNSUPPORTED", f"{test_stage.upper()}数据暂不支持重新处理，当前支持CP/FT数据", 422)
    info = service(request).get_batch_info(principal, domain, stage, batch_id)
    if info is None or not info.files:
        raise DomainError("BATCH_NOT_FOUND", "批次不存在或无权访问", 404)
    factory = info.factory_code.strip().lower()
    job_id = service(request).mark_processing(batch_id, principal)
    output_root = Path(os.getenv("TMS_PROCESSED_ROOT", r"F:\CP-FT数据分析\data\processed")) / domain.lower() / stage.lower() / str(batch_id)
    shutil.rmtree(output_root, ignore_errors=True)
    try:
        input_paths = [Path(item.storage_uri) for item in info.files] if stage == "CP" else [Path(info.files[0].storage_uri).parent]
        summary = _run_cleaner_and_summarize(stage, factory, input_paths, output_root)
        service(request).archive_previous_results(batch_id)
        service(request).record_result(batch_id, job_id, summary)
    except Exception as exc:
        service(request).mark_failed(batch_id, job_id, str(exc))
        raise DomainError("CLEANING_FAILED", f"重新处理失败：{exc}", 422) from exc
    return {"import_batch_id": batch_id, "status": "PROCESSED", "business_domain": domain, "test_stage": stage, "result": summary}
