from __future__ import annotations

from fastapi import APIRouter

from app.domain.cleaner_adapters import ExistingCleanerAdapterRegistry
from app.domain.cleaner_capabilities import capability_catalog
from app.domain.governance import CleanerReleaseDraft, FormatProfileDraft

router = APIRouter()


@router.get("/cleaner-adapters")
def cleaner_adapters() -> list[dict[str, object]]:
    return ExistingCleanerAdapterRegistry().as_dicts()


@router.get("/cleaner-capabilities")
def cleaner_capabilities() -> list[dict[str, object]]:
    """Expose the factory -> format-method -> release-contract hierarchy."""

    return capability_catalog()


@router.post("/format-profiles/validate")
def validate_format_profile(payload: FormatProfileDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}


@router.post("/cleaner-releases/validate")
def validate_cleaner_release(payload: CleanerReleaseDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}
