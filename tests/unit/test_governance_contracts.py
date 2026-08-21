from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.domain.governance import (
    CleanerReleaseDraft,
    FileRoleContract,
    FormatProfileDraft,
    GovernedStatus,
    transition_governed_status,
)


def valid_profile_payload() -> dict:
    return {
        "supplier_id": 1,
        "test_stage": "CP",
        "format_code": "HH_DCP_V1",
        "profile_version": "1.0.0",
        "signature": {
            "schema_version": "1.0",
            "match_mode": "ALL",
            "ambiguity_policy": "BLOCK",
            "rules": [
                {
                    "kind": "FILE_EXTENSION",
                    "operator": "EQUALS",
                    "value": ".dcp",
                },
                {
                    "kind": "HEADER_TOKEN",
                    "operator": "CONTAINS",
                    "value": "Lot_ID",
                },
            ],
        },
        "file_role_contract": {
            "schema_version": "1.0",
            "roles": [
                {"role": "DETAIL", "required": True, "min_files": 1, "max_files": 100},
                {"role": "SPEC", "required": False, "min_files": 0, "max_files": 10},
            ],
        },
    }


def test_format_profile_contract_accepts_strict_payload() -> None:
    profile = FormatProfileDraft.model_validate(valid_profile_payload())
    assert profile.format_code == "HH_DCP_V1"
    assert profile.signature.ambiguity_policy == "BLOCK"


def test_file_roles_must_be_unique() -> None:
    payload = valid_profile_payload()["file_role_contract"]
    payload["roles"].append(
        {"role": "DETAIL", "required": False, "min_files": 0, "max_files": 1}
    )
    with pytest.raises(ValidationError, match="file roles must be unique"):
        FileRoleContract.model_validate(payload)


def test_unknown_contract_fields_fail_closed() -> None:
    payload = valid_profile_payload()
    payload["guess_unknown_format"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FormatProfileDraft.model_validate(payload)


def test_cleaner_checksum_is_normalized() -> None:
    cleaner = CleanerReleaseDraft(
        format_profile_id=1,
        cleaner_code="HH_DCP_CLEANER",
        cleaner_version="1.0.0",
        code_checksum="A" * 64,
    )
    assert cleaner.code_checksum == "a" * 64


def test_governed_status_only_moves_forward() -> None:
    assert (
        transition_governed_status(GovernedStatus.DRAFT, GovernedStatus.RELEASED)
        == GovernedStatus.RELEASED
    )
    with pytest.raises(DomainError) as captured:
        transition_governed_status(GovernedStatus.RELEASED, GovernedStatus.DRAFT)
    assert captured.value.code == "INVALID_GOVERNANCE_TRANSITION"
