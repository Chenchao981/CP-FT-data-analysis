from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest
from app.api.quality_evaluation import router as quality_router
from app.core.errors import DomainError
from app.domain.analysis_rules import AnalysisRuleParameters
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsDatasetContext,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
)
from app.domain.quality_evaluation import (
    QualityCalculationCounts,
    QualityEvaluationRequest,
    QualityEvaluationResult,
    QualityRuleProvenance,
)
from app.infrastructure.quality_evaluation_kernels import (
    OrderedKernelValue,
    bin_cooccurrence,
    margin_oos,
    sbl_grouped_limit,
    spc_i_mr,
)
from app.infrastructure.sql_analytics_service import _hashes
from app.infrastructure.sql_quality_evaluation_service import (
    SqlQualityEvaluationService,
    _calculation_hash,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "datasets": [{"dataset_id": 11, "version_no": 2}],
        "filters": {"lot_ids": ["LOT-A"]},
        "parameters": ["VTH"],
        "analysis": "PAT_ROBUST_IQR",
        "rule": {"rule_code": "FT_PAT", "version_code": "V1"},
        "group_by": "LOT",
    }
    payload.update(overrides)
    return payload


def _rule_parameters(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "missing_value_policy": "EXCLUDE_AND_COUNT",
        "retest_policy": "EACH_ATTEMPT",
        "outlier_policy": "MARK_ONLY",
        "minimum_sample_size": 3,
        "subgroup_dimension": "LOT",
        "lower_multiplier": 6.0,
        "upper_multiplier": 6.0,
    }
    payload.update(overrides)
    return payload


def test_quality_request_requires_method_specific_explicit_inputs() -> None:
    with pytest.raises(ValidationError, match="exactly one parameter"):
        QualityEvaluationRequest.model_validate(_request(parameters=[]))
    with pytest.raises(ValidationError, match="explicit order"):
        QualityEvaluationRequest.model_validate(_request(analysis="SPC_I_MR"))
    with pytest.raises(ValidationError, match="Phase-I baseline"):
        QualityEvaluationRequest.model_validate(
            _request(analysis="SPC_I_MR", spc_order="UNIT_SEQUENCE")
        )
    with pytest.raises(ValidationError, match="explicit bin_type"):
        QualityEvaluationRequest.model_validate(
            _request(
                analysis="BIN_COOCCURRENCE",
                parameters=[],
                group_by="DATASET",
            )
        )
    request = QualityEvaluationRequest.model_validate(
        _request(
            analysis="SPC_I_MR",
            spc_order="UNIT_SEQUENCE",
            spc_phase="PHASE_I_BASELINE",
        )
    )
    assert request.spc_order.value == "UNIT_SEQUENCE"
    assert request.spc_phase.value == "PHASE_I_BASELINE"
    with pytest.raises(ValidationError, match="one explicit physical Bin type"):
        QualityEvaluationRequest.model_validate(
            _request(
                analysis="SBL_GROUPED_LIMIT",
                parameters=[],
                bin_type="ALL_MAPPED_FAILURE",
            )
        )


def test_calculation_hash_pins_rule_parameters_beyond_filter_hash() -> None:
    request = QualityEvaluationRequest.model_validate(_request())
    context_hash = _hashes(request).context_hash
    first = _calculation_hash(request, context_hash, "a" * 64)
    assert first == _calculation_hash(request, context_hash, "a" * 64)
    assert first != _calculation_hash(request, context_hash, "b" * 64)


def test_spc_i_mr_sorts_explicit_sequence_and_matches_golden() -> None:
    values = [
        OrderedKernelValue(4, 20.0, "UNIT:4"),
        OrderedKernelValue(2, 10.0, "UNIT:2"),
        OrderedKernelValue(1, 10.0, "UNIT:1"),
        OrderedKernelValue(3, 10.0, "UNIT:3"),
    ]
    result = spc_i_mr(values)
    assert result.center_line == pytest.approx(12.5)
    assert result.mr_bar == pytest.approx(10.0 / 3.0)
    assert result.lower_control_limit == pytest.approx(3.633333333333333)
    assert result.upper_control_limit == pytest.approx(21.366666666666667)
    assert result.mr_upper_control_limit == pytest.approx(10.89)
    assert [point.sequence for point in result.points] == [1, 2, 3, 4]
    assert [point.moving_range for point in result.points] == [None, 0.0, 0.0, 10.0]


def test_spc_boundary_reset_constant_and_duplicate_order() -> None:
    wafer_one = spc_i_mr(
        [OrderedKernelValue(index, 10.0, f"UNIT:{index}") for index in range(1, 5)]
    )
    wafer_two = spc_i_mr(
        [OrderedKernelValue(index, 20.0, f"UNIT:{index + 10}") for index in range(1, 5)]
    )
    assert wafer_one.mr_bar == 0.0
    assert wafer_two.mr_bar == 0.0
    assert wafer_one.lower_control_limit == wafer_one.upper_control_limit == 10.0
    assert wafer_two.lower_control_limit == wafer_two.upper_control_limit == 20.0
    with pytest.raises(ValueError, match="unique"):
        spc_i_mr(
            [
                OrderedKernelValue(1, 1.0, "UNIT:1"),
                OrderedKernelValue(1, 2.0, "UNIT:2"),
            ]
        )


def test_margin_single_and_two_sided_boundary_semantics() -> None:
    two_sided = margin_oos(0.0, lsl=0.0, usl=10.0, equality_is_in_spec=True)
    assert two_sided.lower_margin == 0.0
    assert two_sided.upper_margin == 10.0
    assert two_sided.nearest_margin == 0.0
    assert two_sided.out_of_spec is False
    strict = margin_oos(0.0, lsl=0.0, usl=10.0, equality_is_in_spec=False)
    assert strict.out_of_spec is True
    upper_only = margin_oos(11.0, lsl=None, usl=10.0, equality_is_in_spec=True)
    assert upper_only.nearest_margin == -1.0
    assert upper_only.out_of_spec is True


def test_bin_cooccurrence_counts_each_physical_unit_once() -> None:
    result = bin_cooccurrence(
        {
            "UNIT:1": {"B1", "B2"},
            "UNIT:2": {"B1"},
            "UNIT:3": {"B2"},
        }
    )
    assert result == (
        ("B1", "B1", 2, ("UNIT:1", "UNIT:2")),
        ("B1", "B2", 1, ("UNIT:1",)),
        ("B2", "B2", 2, ("UNIT:1", "UNIT:3")),
    )


def test_sbl_uses_sample_sigma_over_explicit_physical_groups() -> None:
    result = sbl_grouped_limit(
        {"LOT-A": 0.1, "LOT-B": 0.2, "LOT-C": 0.3},
        upper_multiplier=3.0,
    )
    assert result.mean_rate == pytest.approx(0.2)
    assert result.sample_stddev == pytest.approx(0.1)
    assert result.upper_limit == pytest.approx(0.5)
    assert result.exceeding_groups == ()
    with pytest.raises(ValueError, match="at least two"):
        sbl_grouped_limit({"LOT-A": 0.1}, upper_multiplier=3.0)


class RejectingRuleService:
    def approved_rule_parameters(self, **kwargs):
        del kwargs
        raise DomainError(
            "ANALYSIS_RULE_NOT_APPROVED",
            "请求的规则版本未获批准或未在当前范围激活",
            409,
        )


class RecordingRuleService:
    def __init__(self, parameters: dict[str, object]) -> None:
        self.parameters = parameters
        self.calls: list[dict[str, object]] = []

    def approved_rule_parameters(self, **kwargs):
        self.calls.append(kwargs)
        return self.parameters


def test_zero_approval_gate_is_stable_before_calculation() -> None:
    request = QualityEvaluationRequest.model_validate(_request())
    service = SqlQualityEvaluationService.__new__(SqlQualityEvaluationService)
    service._rules = RejectingRuleService()
    with pytest.raises(DomainError) as caught:
        service._resolve_rule(request, "FT", {(1, 2)})
    assert caught.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert caught.value.status_code == 409


def test_approval_gate_covers_every_supplier_product_scope_and_exact_algorithm() -> (
    None
):
    request = QualityEvaluationRequest.model_validate(_request())
    rules = RecordingRuleService(_rule_parameters())
    service = SqlQualityEvaluationService.__new__(SqlQualityEvaluationService)
    service._rules = rules
    parameters, provenance = service._resolve_rule(request, "FT", {(4, 5), (2, 3)})
    assert parameters.minimum_sample_size == 3
    assert provenance.algorithm_code == "PAT_SHARED_IQR_1_35_V1"
    assert len(provenance.parameters_sha256) == 64
    assert [(call["supplier_id"], call["product_id"]) for call in rules.calls] == [
        (2, 3),
        (4, 5),
    ]
    assert all(call["parameter"] == "VTH" for call in rules.calls)
    assert all(
        call["expected_algorithm_code"] == "PAT_SHARED_IQR_1_35_V1"
        for call in rules.calls
    )


def test_typed_rule_semantics_reject_unapproved_group_or_retest_guess() -> None:
    request = QualityEvaluationRequest.model_validate(_request())
    mismatch = AnalysisRuleParameters.model_validate(
        _rule_parameters(subgroup_dimension="WAFER")
    )
    with pytest.raises(DomainError) as caught:
        SqlQualityEvaluationService._require_supported_rule_semantics(request, mismatch)
    assert caught.value.code == "ANALYSIS_RULE_SCOPE_MISMATCH"

    retest = AnalysisRuleParameters.model_validate(
        _rule_parameters(retest_policy="LATEST_ATTEMPT")
    )
    with pytest.raises(DomainError) as caught:
        SqlQualityEvaluationService._require_supported_rule_semantics(request, retest)
    assert caught.value.code == "ANALYSIS_RETEST_POLICY_UNSUPPORTED"

    formula_drift = AnalysisRuleParameters.model_validate(
        _rule_parameters(lower_multiplier=3.0, upper_multiplier=3.0)
    )
    with pytest.raises(DomainError) as caught:
        SqlQualityEvaluationService._require_supported_rule_semantics(
            request, formula_drift
        )
    assert caught.value.code == "ANALYSIS_RULE_CONTRACT_INVALID"


def test_sync_workload_excess_returns_worker_route() -> None:
    with pytest.raises(DomainError) as caught:
        SqlQualityEvaluationService._workload_guard(
            units=250_001, measurements=1, analysis="PAT_ROBUST_IQR"
        )
    assert caught.value.code == "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED"
    assert caught.value.status_code == 413
    assert caught.value.details[0]["recommended_execution"] == "WORKER"


def test_formal_quality_service_is_read_only_and_canonical_only() -> None:
    source = inspect.getsource(SqlQualityEvaluationService).upper()
    for mutation in ("INSERT ", "UPDATE ", "DELETE ", "MERGE "):
        assert mutation not in source
    assert "TEST.TEST_RUN" in source
    assert "TEST.UNIT_RESULT" in source
    assert "TEST.MEASUREMENT" in source
    assert "QUICK_WORKSPACE" not in source
    analyze_source = inspect.getsource(SqlQualityEvaluationService.analyze).upper()
    assert "CALCULATE_FORMAL_PAT(" in analyze_source
    assert "PAT_ROBUST_IQR(" not in analyze_source
    assert analyze_source.index("SELF._RESOLVE_RULE(") < analyze_source.index(
        "SELF._MEASUREMENT_ROWS("
    )


@dataclass
class AccessCall:
    dataset_id: int
    version_no: int


class StubDatasetService:
    def __init__(self) -> None:
        self.calls: list[AccessCall] = []

    def assert_dataset_access(
        self, dataset_id, principal, mode="READ", *, version_no=None
    ) -> None:
        del principal, mode
        self.calls.append(AccessCall(dataset_id, version_no))


class StubQualityService:
    def __init__(self) -> None:
        self.request: QualityEvaluationRequest | None = None

    def analyze(self, request: QualityEvaluationRequest) -> QualityEvaluationResult:
        self.request = request
        filters = _hashes(request)
        return QualityEvaluationResult(
            "ANALYTICS_QUALITY_EVALUATION_V1",
            request.analysis.value,
            AnalyticsDatasetContext(
                (
                    AnalyticsResolvedDataset(11, 2, "FT-A", "FT", "P1"),
                    AnalyticsResolvedDataset(12, 1, "FT-B", "FT", "P1"),
                ),
                "FT",
                True,
            ),
            filters,
            "c" * 64,
            AnalyticsRuleContext((), (), ("RULE:FT_PAT:V1",)),
            QualityRuleProvenance(
                "FT_PAT",
                "V1",
                "PAT_SHARED_IQR_1_35_V1",
                "APPROVED",
                "ENABLED",
                "a" * 64,
            ),
            None,
            (AnalyticsCapability("PAT_ROBUST_IQR", "AVAILABLE", None, None),),
            QualityCalculationCounts(2, 2, 0, 2, 2, 0, 0),
            AnalyticsSamplingSummary(False, None, 2, 2, 0),
            (),
            (),
            (),
            (),
            (),
            (),
            "2026-08-31T00:00:00+00:00",
        )


def test_quality_api_authorizes_each_dataset_version_before_execution() -> None:
    app = create_app()
    if not any(
        route.path == "/api/v1/analytics/quality-evaluation" for route in app.routes
    ):
        app.include_router(
            quality_router, prefix="/api/v1/analytics/quality-evaluation"
        )
    datasets = StubDatasetService()
    quality = StubQualityService()
    app.state.dataset_service = datasets
    app.state.quality_evaluation_service = quality
    response = TestClient(app).post(
        "/api/v1/analytics/quality-evaluation",
        json=_request(
            datasets=[
                {"dataset_id": 11, "version_no": 2},
                {"dataset_id": 12, "version_no": 1},
            ]
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["rule"]["approval_status"] == "APPROVED"
    assert datasets.calls == [AccessCall(11, 2), AccessCall(12, 1)]
    assert quality.request is not None
