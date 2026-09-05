from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import DomainError
from app.domain.cleaner_adapters import ExistingCleanerAdapterRegistry
from app.domain.cleaner_capabilities import FORMAL_CLEANER_CONTRACTS, capability_catalog
from app.domain.governance import CleanerReleaseDraft, FormatProfileDraft

router = APIRouter()


@router.get("/cleaner-adapters")
def cleaner_adapters() -> list[dict[str, object]]:
    return ExistingCleanerAdapterRegistry().as_dicts()


@router.get("/cleaner-capabilities")
def cleaner_capabilities() -> list[dict[str, object]]:
    """Expose the factory -> format-method -> release-contract hierarchy."""

    return capability_catalog()


@router.get("/cleaner-capability-status")
def cleaner_capability_status(request: Request) -> list[dict[str, object]]:
    """Show exact formal selections; do not expose server paths or execution config."""
    registry = getattr(request.app.state, "cleaner_registry", None)
    result = capability_catalog()
    for item in result:
        item["release"] = None
        item["release_status"] = "PERSONAL_CONTRACT"
        if "FORMAL_IMPORT" not in item["use_scopes"]:
            continue
        contract = FORMAL_CLEANER_CONTRACTS[(item["test_stage"], item["factory_code"])]
        if registry is None:
            item["release_status"] = "NOT_CONFIGURED"
            continue
        try:
            release = registry.latest_released_for_contract(
                test_stage=item["test_stage"],
                factory_code=item["factory_code"],
                **contract,
            )
        except DomainError as exc:
            item["release_status"] = exc.code
        else:
            item["release_status"] = "REGISTERED"
            item["release"] = {
                "version": release.cleaner_version,
                "sha256": release.code_checksum,
                "release_id": release.cleaner_release_id,
            }
    return result


@router.post("/format-profiles/validate")
def validate_format_profile(payload: FormatProfileDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}


@router.post("/cleaner-releases/validate")
def validate_cleaner_release(payload: CleanerReleaseDraft) -> dict:
    return {"valid": True, "normalized": payload.model_dump(mode="json")}
