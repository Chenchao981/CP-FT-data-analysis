from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.production_data import StoredUpload
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner


router = APIRouter()
ALLOWED_CP_SUFFIXES = {".zip", ".7z", ".txt"}


def service(request: Request):
    instance = getattr(request.app.state, "production_data_service", None)
    if instance is None:
        raise DomainError("DATABASE_NOT_CONFIGURED", "量产数据服务尚未连接数据库", 503)
    return instance


def _save_uploads(files: list[UploadFile]) -> tuple[StoredUpload, ...]:
    if not files:
        raise DomainError("CP_UPLOAD_EMPTY", "请选择需要上传的CP源文件", 422)
    target = Path(os.getenv("TMS_UPLOAD_ROOT", r"F:\CP-FT数据分析\data\raw")) / "production" / "cp" / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    stored: list[StoredUpload] = []
    for uploaded in files:
        original = Path(uploaded.filename or "").name
        suffix = Path(original).suffix.lower()
        if not original or suffix not in ALLOWED_CP_SUFFIXES:
            raise DomainError("CP_FILE_TYPE_UNSUPPORTED", f"不支持的CP文件：{original or '未命名文件'}", 422)
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
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }


@router.post("/cp/uploads", status_code=status.HTTP_201_CREATED)
def upload_cp_data(
    request: Request,
    files: list[UploadFile] = File(...),
    factory_code: str = Form("huahong"),
    remark: str | None = Form(None),
    principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    factory = factory_code.strip().lower()
    if factory not in {"huahong", "hh", "华虹"}:
        raise DomainError("CP_FACTORY_UNSUPPORTED", "当前量产CP首版支持华虹源数据", 422)
    stored = _save_uploads(files)
    batch_id = service(request).register_upload(principal, "huahong", stored, remark.strip() if remark else None)
    job_id = service(request).mark_processing(batch_id, principal)
    output_root = Path(os.getenv("TMS_PROCESSED_ROOT", r"F:\CP-FT数据分析\data\processed")) / "production" / "cp" / str(batch_id)
    try:
        result = ExistingCleanerRunner().run(test_stage="CP", factory="huahong", inputs=[item.path for item in stored], output_root=output_root)
        summary = _read_cp_summary(result)
        service(request).record_cp_result(batch_id, job_id, summary)
    except Exception as exc:
        service(request).mark_failed(batch_id, job_id, str(exc))
        raise DomainError("CP_CLEANING_FAILED", f"CP文件已入库，但清洗失败：{exc}", 422) from exc
    return {"import_batch_id": batch_id, "status": "PROCESSED", "uploader": {"user_id": principal.user_id, "login_name": principal.login_name, "display_name": principal.display_name}, "result": summary}


@router.get("/cp/uploads")
def list_cp_uploads(request: Request, principal: Principal = Depends(require_permission("DATASET_READ"))) -> list[dict]:
    return [asdict(item) for item in service(request).list_uploads(principal)]


@router.get("/cp/results")
def list_cp_results(request: Request, principal: Principal = Depends(require_permission("DATASET_READ"))) -> list[dict]:
    return [asdict(item) for item in service(request).list_results(principal)]
