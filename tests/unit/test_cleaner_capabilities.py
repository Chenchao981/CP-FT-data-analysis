from __future__ import annotations

import json

import pytest
from app.domain.cleaner_capabilities import (
    capability_allowed_suffixes,
    cleaner_capability,
    validate_capability_contract,
)


def test_lion_capability_binds_two_internal_format_methods() -> None:
    capability = cleaner_capability(" lion_cp_pyz ")

    assert capability is not None
    assert capability.capability_code == "LION_CP_STANDARD_CLEAN"
    assert capability.use_scopes == ("FORMAL_IMPORT", "PERSONAL_ANALYSIS")
    assert capability.format_method_codes == (
        "LION_V1_DYNAMIC_EXCEL",
        "LION_V2_PROFILED_OLE_XLS",
    )
    assert capability_allowed_suffixes("LION_CP_PYZ") == frozenset(
        {".xls", ".xlsx"}
    )


def test_dianji_formal_and_personal_capabilities_have_different_method_sets() -> None:
    formal = cleaner_capability("DIANJI_FT_PYZ")
    personal = cleaner_capability("DIANJI_FT_QUICK_PAT_PYZ")

    assert formal is not None
    assert personal is not None
    assert formal.format_method_codes == (
        "DIANJI_POWERTECH_TEXT_XLS",
        "DIANJI_POWERTECH_NATIVE_XLSX",
    )
    assert personal.format_method_codes == (
        "DIANJI_POWERTECH_TEXT_XLS",
        "DIANJI_POWERTECH_NATIVE_XLSX",
        "DIANJI_STS8203_CSV",
        "DIANJI_TF_CSV",
    )
    assert capability_allowed_suffixes("DIANJI_FT_PYZ") == frozenset(
        {".xls", ".xlsx"}
    )
    assert capability_allowed_suffixes("DIANJI_FT_QUICK_PAT_PYZ") == frozenset(
        {".xls", ".xlsx", ".csv"}
    )


def test_known_adapter_release_contract_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="modular capability contract"):
        validate_capability_contract(
            adapter_code="DIANJI_FT_PYZ",
            test_stage="FT",
            factory_code="DIANJI",
            cleaner_code="DIANJI_FT_POWERTECH_EXISTING",
            input_contract_version="UNAPPROVED_INPUT",
            output_contract_version="DIANJI_FT_SCATTER_V1",
        )


def test_declared_format_method_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="format methods"):
        validate_capability_contract(
            adapter_code="LION_CP_PYZ",
            test_stage="CP",
            factory_code="LION",
            cleaner_code="LION_CP_EXISTING",
            input_contract_version="CP_EXCEL_OR_ZIP_V1",
            output_contract_version="CP_STANDARD_CSV_TRIPLET_V1",
            execution_config_json=json.dumps(
                {
                    "capability_code": "LION_CP_STANDARD_CLEAN",
                    "format_method_codes": ["LION_V1_DYNAMIC_EXCEL"],
                }
            ),
        )


def test_riyuexin_adapter_is_now_validated_by_capability_registry() -> None:
    assert (
        validate_capability_contract(
            adapter_code="RIYUEXIN_FT_PYZ",
            test_stage="FT",
            factory_code="RIYUEXIN",
            cleaner_code="RIYUEXIN_FT_EXISTING",
            input_contract_version="FT_DIRECTORY_XLSX_V1",
            output_contract_version="FT_XLSX_SCATTER_V1",
        )
        is not None
    )


@pytest.mark.parametrize("stage,factory", [("CP", "HUAHONG"), ("CP", "JETECH"), ("CP", "LION"), ("FT", "RIYUEXIN"), ("FT", "RIYUEGUANG"), ("FT", "DIANJI")])
def test_every_formal_entry_has_a_matching_capability(stage, factory):
    from app.domain.cleaner_capabilities import FORMAL_CLEANER_CONTRACTS
    contract = FORMAL_CLEANER_CONTRACTS[(stage, factory)]
    capability = validate_capability_contract(test_stage=stage, factory_code=factory, **{k: v for k, v in contract.items() if k != "format_code"})
    assert capability is not None
    with pytest.raises(ValueError, match="contract"):
        validate_capability_contract(test_stage=stage, factory_code=factory, **{k: ("PERSONAL_PAT" if k == "cleaner_code" else v) for k, v in contract.items() if k != "format_code"})
