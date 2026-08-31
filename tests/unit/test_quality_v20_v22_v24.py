from __future__ import annotations

import pytest
from app.core.errors import DomainError
from app.domain.analysis_rules import CreateAnalysisRuleVersionRequest
from app.domain.quality_evaluation import QualityEvaluationRequest
from app.infrastructure.quality_evaluation_kernels import (
    OrderedKernelValue,
    pass_fail_distribution,
    spc_i_mr,
    syl_grouped_limit,
)
from app.infrastructure.sql_quality_evaluation_service import (
    SqlQualityEvaluationService,
)
from pydantic import ValidationError


def _request(analysis: str, **overrides: object) -> QualityEvaluationRequest:
    payload: dict[str, object] = {
        "datasets": [{"dataset_id": 11, "version_no": 2}],
        "filters": {},
        "parameters": [],
        "analysis": analysis,
        "rule": {"rule_code": "FT_QUALITY", "version_code": "V1"},
        "group_by": "LOT",
    }
    payload.update(overrides)
    return QualityEvaluationRequest.model_validate(payload)


def _parameters(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "missing_value_policy": "EXCLUDE_AND_COUNT",
        "retest_policy": "EACH_ATTEMPT",
        "outlier_policy": "MARK_ONLY",
        "minimum_sample_size": 2,
        "subgroup_dimension": "LOT",
    }
    value.update(overrides)
    return value


def _version(algorithm: str, parameters: dict[str, object]) -> dict[str, object]:
    return {
        "version_code": "V1",
        "implementation_version": "analytics-1.3",
        "algorithm_code": algorithm,
        "parameters": parameters,
        "applicability": {
            "test_stages": ["FT"],
            "supplier_ids": [],
            "product_ids": [],
            "parameter_patterns": [],
        },
        "algorithm_sha256": "a" * 64,
        "golden_manifest_sha256": "b" * 64,
    }


def test_syl_and_pass_fail_requests_are_explicit_and_do_not_infer_population() -> None:
    syl = _request("SYL_GROUPED_LIMIT")
    assert syl.parameters == []
    with pytest.raises(ValidationError, match="cannot infer"):
        _request("SYL_GROUPED_LIMIT", group_by="CONDITION")

    distribution = _request("PASS_FAIL_DISTRIBUTION", parameters=["VTH"])
    assert distribution.parameters == ["VTH"]
    with pytest.raises(ValidationError, match="exactly one parameter"):
        _request("PASS_FAIL_DISTRIBUTION", parameters=[])


def test_syl_rule_requires_sample_sigma_and_explicit_rounding() -> None:
    missing_rounding = _version(
        "SYL_GROUPED_LIMIT_V1",
        _parameters(lower_multiplier=3.0, sigma_definition="SAMPLE"),
    )
    with pytest.raises(ValidationError, match="explicit rounding policy"):
        CreateAnalysisRuleVersionRequest.model_validate(missing_rounding)

    no_rounding = CreateAnalysisRuleVersionRequest.model_validate(
        _version(
            "SYL_GROUPED_LIMIT_V1",
            _parameters(
                lower_multiplier=3.0,
                sigma_definition="SAMPLE",
                limit_rounding_policy="NONE",
            ),
        )
    )
    assert no_rounding.expected_rule_type.value == "SYL"

    with pytest.raises(ValidationError, match="requires limit_rounding_step"):
        CreateAnalysisRuleVersionRequest.model_validate(
            _version(
                "SYL_GROUPED_LIMIT_V1",
                _parameters(
                    lower_multiplier=3.0,
                    sigma_definition="SAMPLE",
                    limit_rounding_policy="FLOOR_TO_STEP",
                ),
            )
        )


def test_syl_kernel_uses_ddof_one_and_only_declared_rounding() -> None:
    unrounded = syl_grouped_limit(
        {"LOT-A": 0.91, "LOT-B": 0.82, "LOT-C": 0.77},
        lower_multiplier=3.0,
        rounding_policy="NONE",
        rounding_step=None,
    )
    assert unrounded.mean_yield == pytest.approx(0.8333333333333334)
    assert unrounded.sample_stddev == pytest.approx(0.07094598884597589)
    assert unrounded.lower_limit == unrounded.raw_lower_limit

    rounded = syl_grouped_limit(
        {"LOT-A": 0.91, "LOT-B": 0.82, "LOT-C": 0.77},
        lower_multiplier=3.0,
        rounding_policy="FLOOR_TO_STEP",
        rounding_step=0.01,
    )
    assert rounded.raw_lower_limit == pytest.approx(unrounded.raw_lower_limit)
    assert rounded.lower_limit == 0.62
    assert rounded.lower_limit <= rounded.raw_lower_limit


def test_pass_fail_distribution_has_shared_server_bins_and_complete_evidence() -> None:
    result = pass_fail_distribution(
        [(0.0, "UNIT:1"), (10.0, "UNIT:2")],
        [(2.0, "UNIT:3"), (10.0, "UNIT:4")],
        bin_count=5,
    )
    assert result.pass_mean == 5.0
    assert result.fail_mean == 6.0
    assert result.minimum == 0.0
    assert result.maximum == 10.0
    assert len(result.bins) == 5
    assert sum(item.pass_count for item in result.bins) == 2
    assert sum(item.fail_count for item in result.bins) == 2
    assert {
        key
        for item in result.bins
        for key in (*item.pass_drilldown_keys, *item.fail_drilldown_keys)
    } == {"UNIT:1", "UNIT:2", "UNIT:3", "UNIT:4"}


def test_pass_fail_rule_requires_server_bin_contract() -> None:
    with pytest.raises(ValidationError, match="histogram_bin_count"):
        CreateAnalysisRuleVersionRequest.model_validate(
            _version("PASS_FAIL_DISTRIBUTION_V1", _parameters())
        )
    approved_shape = CreateAnalysisRuleVersionRequest.model_validate(
        _version(
            "PASS_FAIL_DISTRIBUTION_V1",
            _parameters(histogram_bin_count=20),
        )
    )
    assert approved_shape.expected_rule_type.value == "PASS_FAIL_DISTRIBUTION"


def test_spc_run_rules_require_explicit_none_or_complete_basic_contract() -> None:
    base = _parameters(sigma_definition="POOLED_WITHIN")
    with pytest.raises(ValidationError, match="explicit spc_run_rule_mode"):
        CreateAnalysisRuleVersionRequest.model_validate(_version("SPC_I_MR_V1", base))
    none_rule = CreateAnalysisRuleVersionRequest.model_validate(
        _version("SPC_I_MR_V1", {**base, "spc_run_rule_mode": "NONE"})
    )
    assert none_rule.parameters.spc_run_rule_mode.value == "NONE"
    with pytest.raises(ValidationError, match="all versioned thresholds"):
        CreateAnalysisRuleVersionRequest.model_validate(
            _version(
                "SPC_I_MR_V1",
                {**base, "spc_run_rule_mode": "BASIC", "spc_same_side_run_length": 4},
            )
        )


def test_spc_kernel_executes_basic_rules_but_none_never_inferrs_them() -> None:
    values = [
        OrderedKernelValue(index, float(index), f"UNIT:{index}")
        for index in range(1, 9)
    ]
    none_result = spc_i_mr(values, run_rule_mode="NONE")
    assert all(
        "SAME_SIDE" not in hit and "MONOTONIC" not in hit
        for point in none_result.points
        for hit in point.rule_hits
    )
    basic = spc_i_mr(
        values,
        run_rule_mode="BASIC",
        consecutive_beyond_count=2,
        consecutive_beyond_sigma=0.1,
        same_side_run_length=4,
        monotonic_run_length=4,
    )
    hits = {hit for point in basic.points for hit in point.rule_hits}
    assert "4_POINTS_SAME_SIDE" in hits
    assert "4_POINT_MONOTONIC_RUN" in hits
    assert "2_CONSECUTIVE_BEYOND_0.1_SIGMA_SAME_SIDE" in hits


class _RejectingRuleService:
    def __init__(self) -> None:
        self.expected_algorithms: list[str] = []

    def approved_rule_parameters(self, **kwargs):
        self.expected_algorithms.append(str(kwargs["expected_algorithm_code"]))
        raise DomainError("ANALYSIS_RULE_NOT_APPROVED", "not approved", 409)


@pytest.mark.parametrize(
    ("analysis", "expected", "overrides"),
    [
        ("SYL_GROUPED_LIMIT", "SYL_GROUPED_LIMIT_V1", {}),
        (
            "PASS_FAIL_DISTRIBUTION",
            "PASS_FAIL_DISTRIBUTION_V1",
            {"parameters": ["VTH"]},
        ),
    ],
)
def test_new_quality_algorithms_remain_closed_without_exact_approval(
    analysis: str, expected: str, overrides: dict[str, object]
) -> None:
    rules = _RejectingRuleService()
    service = SqlQualityEvaluationService.__new__(SqlQualityEvaluationService)
    service._rules = rules
    request = _request(analysis, **overrides)
    with pytest.raises(DomainError) as caught:
        service._resolve_rule(request, "FT", {(1, 2)})
    assert caught.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert rules.expected_algorithms == [expected]
