from __future__ import annotations

import math

import pytest
from app.domain.formal_pat_contract import (
    FORMAL_PAT_ADAPTER_CONTRACT_VERSION,
    FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
    FORMAL_PAT_ALGORITHM_CODE,
    FORMAL_PAT_SOURCE_COMMIT,
    FORMAL_PAT_SOURCE_SHA256,
)
from app.infrastructure.formal_pat_adapter import (
    calculate_formal_pat,
    source_engine_sha256,
)


def test_formal_pat_adapter_contract_is_explicit_and_versioned() -> None:
    assert FORMAL_PAT_ALGORITHM_CODE == "PAT_SHARED_IQR_1_35_V1"
    assert FORMAL_PAT_ADAPTER_CONTRACT_VERSION == "FORMAL_PAT_SHARED_ENGINE_ADAPTER_V1"
    assert FORMAL_PAT_SOURCE_COMMIT == "ebf9c7a05b8a10987941c8dacd7e4b1295ae58c1"
    assert FORMAL_PAT_SOURCE_SHA256 == (
        "b853dd935a2adf75190f25bb664b1e2c27f1c09bb89e2c91b5245799aa9f183a"
    )
    assert len(FORMAL_PAT_ADAPTER_MANIFEST_SHA256) == 64
    assert source_engine_sha256(b"contract-probe") == (
        "ca86394eb0be044c1afa5bae1bca27f4d028c2e1206aca6b40588c773ec5d4b1"
    )


def test_formal_pat_matches_shared_engine_golden_and_traces_outlier() -> None:
    result = calculate_formal_pat(
        list(range(10)) + [100], lower_multiplier=6.0, upper_multiplier=6.0
    )
    assert result.q1 == 2.5
    assert result.median == 5.0
    assert result.q3 == 7.5
    assert result.iqr == 5.0
    assert result.robust_sigma == 3.703704
    assert result.lower_limit == -17.222222
    assert result.upper_limit == 27.222222
    assert result.outlier_indexes == (10,)


def test_formal_pat_constant_boundary_and_invalid_inputs_fail_closed() -> None:
    constant = calculate_formal_pat(
        [5.0] * 30, lower_multiplier=6.0, upper_multiplier=6.0
    )
    assert constant.iqr == 0.0
    assert constant.robust_sigma == 0.0
    assert constant.lower_limit == constant.upper_limit == 5.0
    assert constant.outlier_indexes == ()

    with pytest.raises(ValueError, match="requires lower_multiplier=6"):
        calculate_formal_pat([1.0, 2.0], lower_multiplier=3.0, upper_multiplier=3.0)
    with pytest.raises(ValueError, match="finite"):
        calculate_formal_pat(
            [1.0, math.nan], lower_multiplier=6.0, upper_multiplier=6.0
        )
    with pytest.raises(ValueError, match="non-empty"):
        calculate_formal_pat([], lower_multiplier=6.0, upper_multiplier=6.0)
