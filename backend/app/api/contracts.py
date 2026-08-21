from __future__ import annotations

from fastapi import APIRouter

from app.domain.cleaner_adapters import ExistingCleanerAdapterRegistry

from app.domain.governance import CleanerReleaseDraft, FormatProfileDraft


router = APIRouter()


@router.get("/cleaner-adapters")
def cleaner_adapters() -> list[dict[str, object]]:
    return ExistingCleanerAdapterRegistry().as_dicts()


@router.post("/format-profiles/validate")
def validate_format_profile(payload: FormatProfileDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}


@router.post("/cleaner-releases/validate")
def validate_cleaner_release(payload: CleanerReleaseDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}
