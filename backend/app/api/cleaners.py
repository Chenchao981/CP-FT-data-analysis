from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.dependencies import require_permission
from app.domain.auth import Principal

from app.cleaners.huahong_dcp import (
    MAX_FILE_BYTES,
    HuaHongDcpParser,
    HuaHongFormatError,
)
from app.core.errors import DomainError


router = APIRouter()


@router.post("/huahong/inspect")
async def inspect_huahong_file(
    file: UploadFile = File(...),
    _principal: Principal = Depends(require_permission("TASK_CREATE")),
) -> dict:
    content = await file.read(MAX_FILE_BYTES + 1)
    try:
        parsed = HuaHongDcpParser().parse_bytes(
            content,
            source_name=file.filename or "upload.TXT",
            include_units=True,
        )
    except HuaHongFormatError as exc:
        raise DomainError(
            code="HUAHONG_FORMAT_INVALID",
            message=str(exc),
            status_code=422,
        ) from exc
    return {
        "profile_code": HuaHongDcpParser.profile_code,
        "profile_version": HuaHongDcpParser.profile_version,
        "source_file": {
            "name": parsed.source_name,
            "sha256": parsed.source_sha256,
        },
        "identity": {
            "business_lot_id": parsed.business_lot_id,
            "lot_number": parsed.lot_number,
            "wafer_number": parsed.wafer_number,
            "program_name": parsed.program_name,
        },
        "schema": {
            "schema_id": parsed.schema_id,
            "parameter_count": len(parsed.parameters),
            "parameters": parsed.parameters,
        },
        "quality": {
            "status": "PASS",
            "row_count": parsed.row_count,
            "pass_bin": HuaHongDcpParser.pass_bin,
            "pass_count": parsed.pass_count,
            "yield_rate": parsed.yield_rate,
            "bin_counts": parsed.bin_counts,
        },
    }
