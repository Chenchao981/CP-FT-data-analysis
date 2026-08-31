from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.core.errors import DomainError
from app.domain.analytics import AnalyticsFilterSummary, AnalyticsNormalizedFilters
from app.domain.analytics_risk import (
    AnalyticsInstantRiskRequest,
    AnalyticsInstantRiskResult,
    AnalyticsRiskEvaluationConfig,
)
from app.infrastructure.analytics_instant_risk_service import (
    AnalyticsInstantRiskService,
)
from app.infrastructure.sql_analytics_service import _hashes
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _request(analysis: str = "CAPABILITY") -> AnalyticsInstantRiskRequest:
    evaluation: dict[str, object] = {
        "analysis": analysis,
        "rule": {"rule_code": f"{analysis}_RULE", "version_code": "v1"},
    }
    if analysis in {"CAPABILITY", "PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS"}:
        evaluation["parameter"] = "VTH"
    if analysis == "CAPABILITY":
        evaluation["capability_method"] = "CPK_POOLED_WITHIN_RUN_V1"
    else:
        evaluation["group_by"] = "LOT"
    if analysis == "SPC_I_MR":
        evaluation["spc_order"] = "UNIT_SEQUENCE"
        evaluation["spc_phase"] = "PHASE_I_BASELINE"
    if analysis == "SBL_GROUPED_LIMIT":
        evaluation["bin_type"] = "SOFT_BIN"
    return AnalyticsInstantRiskRequest.model_validate(
        {
            "datasets": [{"dataset_id": 11, "version_no": 2}],
            "filters": {"lot_ids": ["LOT-1"]},
            "parameters": ["VTH"],
            "evaluations": [evaluation],
        }
    )


def test_instant_risk_contract_requires_exact_method_specific_inputs() -> None:
    from app.domain.analysis_rules import CreateAnalysisRuleVersionRequest

    capability_payload = {
        "version_code": "v1",
        "implementation_version": "1.0",
        "algorithm_code": "CPK_POOLED_WITHIN_RUN_V1",
        "parameters": {
            "missing_value_policy": "EXCLUDE_AND_COUNT",
            "retest_policy": "EACH_ATTEMPT",
            "outlier_policy": "MARK_ONLY",
            "minimum_sample_size": 30,
            "sigma_definition": "POOLED_WITHIN",
            "subgroup_dimension": "RUN",
        },
        "applicability": {"test_stages": ["CP"]},
        "algorithm_sha256": "a" * 64,
        "golden_manifest_sha256": "b" * 64,
    }
    with pytest.raises(
        ValidationError, match="risk metric and threshold|field required"
    ):
        CreateAnalysisRuleVersionRequest.model_validate(capability_payload)
    approved_shape = CreateAnalysisRuleVersionRequest.model_validate(
        {
            **capability_payload,
            "parameters": {
                **capability_payload["parameters"],
                "capability_risk_metric": "MIN_CPK_PPK",
                "capability_risk_threshold": 1.33,
            },
        }
    )
    assert approved_shape.parameters.capability_risk_metric == "MIN_CPK_PPK"
    assert approved_shape.parameters.capability_risk_threshold == 1.33

    with pytest.raises(ValidationError, match="Capability risk requires"):
        AnalyticsRiskEvaluationConfig.model_validate(
            {
                "analysis": "CAPABILITY",
                "rule": {"rule_code": "CPK_RULE", "version_code": "v1"},
                "parameter": "VTH",
                "group_by": "LOT",
            }
        )
    with pytest.raises(ValidationError, match="each instant-risk method"):
        request = _request()
        AnalyticsInstantRiskRequest(
            datasets=request.datasets,
            filters=request.filters,
            parameters=request.parameters,
            evaluations=[request.evaluations[0], request.evaluations[0]],
        )


def test_capability_risk_uses_only_approved_rule_metric_and_threshold() -> None:
    config = _request().evaluations[0]
    capability = SimpleNamespace(
        risk_metric="MIN_CPK_PPK",
        risk_threshold=1.33,
        parameters_sha256="a" * 64,
        cpk=1.1,
        ppk=1.4,
        reason_codes=(),
        drilldown_context=SimpleNamespace(
            dataset_id=11,
            version_no=2,
            parameter="VTH",
            lower_bound=None,
            upper_bound=None,
            lower_inclusive=True,
            upper_inclusive=True,
        ),
    )
    result = SimpleNamespace(
        items=(
            SimpleNamespace(
                dataset_id=11,
                version_no=2,
                group_key="DATASET:11",
                parameters=(
                    SimpleNamespace(
                        identity=SimpleNamespace(name="VTH"), capability=capability
                    ),
                ),
            ),
        )
    )

    items = AnalyticsInstantRiskService._capability_items(config, result)

    assert len(items) == 1
    assert items[0].status == "ACTIVE"
    assert items[0].metric_code == "MIN_CPK_PPK"
    assert items[0].metric_value == 1.1
    assert items[0].threshold_value == 1.33
    assert items[0].evidence_drilldown_keys == ()
    assert items[0].aggregate_drilldown_context is capability.drilldown_context
    assert items[0].rule.rule_code == "CAPABILITY_RULE"


def test_approved_capability_without_risk_policy_still_fails_closed() -> None:
    config = _request().evaluations[0]
    result = SimpleNamespace(
        items=(
            SimpleNamespace(
                dataset_id=11,
                version_no=2,
                group_key="DATASET:11",
                parameters=(
                    SimpleNamespace(
                        identity=SimpleNamespace(name="VTH"),
                        capability=SimpleNamespace(
                            risk_metric=None,
                            risk_threshold=None,
                            parameters_sha256="a" * 64,
                            cpk=1.1,
                            ppk=1.2,
                            reason_codes=(),
                            drilldown_context=None,
                        ),
                    ),
                ),
            ),
        )
    )

    with pytest.raises(DomainError) as caught:
        AnalyticsInstantRiskService._capability_items(config, result)

    assert caught.value.code == "ANALYSIS_RISK_POLICY_REQUIRED"


class _CapabilityDatasetService:
    def __init__(self, filter_hash: str) -> None:
        self.filter_hash = filter_hash

    def analyze_parameters(self, request):
        parameter = request.parameters[0]
        return SimpleNamespace(
            filter_summary=SimpleNamespace(filter_hash=self.filter_hash),
            dataset_context=SimpleNamespace(
                resolved_datasets=(SimpleNamespace(dataset_id=11, version_no=2),)
            ),
            items=(
                SimpleNamespace(
                    dataset_id=11,
                    version_no=2,
                    group_key="DATASET:11",
                    parameters=(
                        SimpleNamespace(
                            identity=SimpleNamespace(name=parameter),
                            capability=SimpleNamespace(
                                risk_metric="CPK",
                                risk_threshold=1.33,
                                parameters_sha256="a" * 64,
                                cpk=1.2,
                                ppk=1.1,
                                reason_codes=(),
                                drilldown_context=None,
                            ),
                        ),
                    ),
                ),
            ),
            warnings=(),
        )


def test_capability_calculation_hash_includes_explicit_risk_parameter() -> None:
    first = _request()
    summary = _hashes(first)
    service = AnalyticsInstantRiskService(
        _CapabilityDatasetService(summary.filter_hash), _UnusedQualityService()
    )
    second = first.model_copy(deep=True)
    second.evaluations[0].parameter = "IDSS"

    first_result = service.evaluate(first)
    second_result = service.evaluate(second)

    assert first.parameters == ["VTH"]
    assert (
        first_result.filter_summary.context_hash
        == second_result.filter_summary.context_hash
    )
    assert (
        first_result.calculation_context_hash != second_result.calculation_context_hash
    )


def test_capability_result_filter_mismatch_fails_closed() -> None:
    service = AnalyticsInstantRiskService(
        _CapabilityDatasetService("f" * 64), _UnusedQualityService()
    )

    with pytest.raises(DomainError) as caught:
        service.evaluate(_request())

    assert caught.value.code == "ANALYSIS_CONTEXT_MISMATCH"


def test_quality_risk_reducer_uses_server_status_limits_and_evidence() -> None:
    provenance = SimpleNamespace(
        rule_code="SPC_RULE",
        version_code="v2",
        algorithm_code="SPC_I_MR_V1",
        parameters_sha256="b" * 64,
    )
    result = SimpleNamespace(
        rule=provenance,
        parameter_identity=SimpleNamespace(name="VTH"),
        analysis="SPC_I_MR",
        pat=(),
        spc=(
            SimpleNamespace(
                dataset_id=11,
                version_no=2,
                group_key="LOT:LOT-1",
                status="ASSESSABLE",
                valid_n=3,
                lower_control_limit=0.8,
                upper_control_limit=1.2,
                points=(
                    SimpleNamespace(value=1.0, rule_hits=(), drilldown_key="UNIT:1"),
                    SimpleNamespace(value=1.3, rule_hits=(), drilldown_key="UNIT:2"),
                    SimpleNamespace(
                        value=1.1,
                        rule_hits=("SAME_SIDE",),
                        drilldown_key="UNIT:3",
                    ),
                ),
            ),
        ),
        margin=(),
        sbl=(),
        syl=(),
    )

    items = AnalyticsInstantRiskService._quality_items(result)

    assert len(items) == 1
    assert items[0].status == "ACTIVE"
    assert items[0].affected_count == 2
    assert items[0].denominator_count == 3
    assert items[0].evidence_drilldown_keys == ("UNIT:2", "UNIT:3")
    assert items[0].rule.version_code == "v2"


def _quality_result(analysis: str, **rows: tuple[object, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        rule=SimpleNamespace(
            rule_code=f"{analysis}_RULE",
            version_code="v1",
            algorithm_code={
                "PAT_ROBUST_IQR": "PAT_SHARED_IQR_1_35_V1",
                "MARGIN_OOS": "SPEC_MARGIN_V1",
                "SBL_GROUPED_LIMIT": "SBL_GROUPED_LIMIT_V1",
                "SYL_GROUPED_LIMIT": "SYL_GROUPED_LIMIT_V1",
            }[analysis],
            parameters_sha256="c" * 64,
        ),
        parameter_identity=(
            SimpleNamespace(name="VTH")
            if analysis in {"PAT_ROBUST_IQR", "MARGIN_OOS"}
            else None
        ),
        analysis=analysis,
        pat=rows.get("pat", ()),
        spc=(),
        margin=rows.get("margin", ()),
        sbl=rows.get("sbl", ()),
        syl=rows.get("syl", ()),
    )


def test_pat_margin_sbl_syl_risks_reduce_only_authoritative_server_flags() -> None:
    pat = AnalyticsInstantRiskService._quality_items(
        _quality_result(
            "PAT_ROBUST_IQR",
            pat=(
                SimpleNamespace(
                    dataset_id=11,
                    version_no=2,
                    group_key="LOT:A",
                    status="ASSESSABLE",
                    outlier_rate=0.25,
                    outlier_count=1,
                    valid_n=4,
                    evidence=(SimpleNamespace(drilldown_key="UNIT:4"),),
                ),
            ),
        )
    )[0]
    assert (pat.status, pat.affected_count, pat.denominator_count) == ("ACTIVE", 1, 4)
    assert pat.evidence_drilldown_keys == ("UNIT:4",)

    margin = AnalyticsInstantRiskService._quality_items(
        _quality_result(
            "MARGIN_OOS",
            margin=(
                SimpleNamespace(
                    dataset_id=11,
                    version_no=2,
                    group_key="LOT:A",
                    out_of_spec_rate=0.0,
                    out_of_spec_count=0,
                    valid_n=2,
                    points=(
                        SimpleNamespace(out_of_spec=False, drilldown_key="UNIT:1"),
                        SimpleNamespace(out_of_spec=False, drilldown_key="UNIT:2"),
                    ),
                ),
            ),
        )
    )[0]
    assert (margin.status, margin.affected_count, margin.denominator_count) == (
        "CLEAR",
        0,
        2,
    )
    assert margin.evidence_drilldown_keys == ()

    sbl = AnalyticsInstantRiskService._quality_items(
        _quality_result(
            "SBL_GROUPED_LIMIT",
            sbl=(
                SimpleNamespace(
                    dataset_id=11,
                    version_no=2,
                    bin_code="7",
                    subgroup_count=2,
                    status="ASSESSABLE",
                    exceeding_groups=("LOT:B",),
                    groups=(
                        SimpleNamespace(group_key="LOT:A", drilldown_keys=("UNIT:1",)),
                        SimpleNamespace(
                            group_key="LOT:B",
                            drilldown_keys=("UNIT:2", "UNIT:3"),
                        ),
                    ),
                ),
            ),
        )
    )[0]
    assert (sbl.status, sbl.affected_count, sbl.denominator_count) == (
        "ACTIVE",
        1,
        2,
    )
    assert sbl.evidence_drilldown_keys == ("UNIT:2", "UNIT:3")

    syl = AnalyticsInstantRiskService._quality_items(
        _quality_result(
            "SYL_GROUPED_LIMIT",
            syl=(
                SimpleNamespace(
                    dataset_id=11,
                    version_no=2,
                    subgroup_count=3,
                    status="ASSESSABLE",
                    below_limit_groups=("WAFER:W2",),
                    groups=(
                        SimpleNamespace(
                            group_key="WAFER:W1", drilldown_keys=("UNIT:1",)
                        ),
                        SimpleNamespace(
                            group_key="WAFER:W2", drilldown_keys=("UNIT:9",)
                        ),
                    ),
                ),
            ),
        )
    )[0]
    assert (syl.status, syl.affected_count, syl.denominator_count) == (
        "ACTIVE",
        1,
        3,
    )
    assert syl.evidence_drilldown_keys == ("UNIT:9",)


class _DeniedDatasetService:
    def analyze_parameters(self, request):
        del request
        raise DomainError(
            "ANALYSIS_RULE_NOT_APPROVED", "requested rule is not approved", 409
        )


class _UnusedQualityService:
    def analyze(self, request):  # pragma: no cover - must not be called
        del request
        raise AssertionError("quality service must not run")


def test_no_approved_rule_fails_closed_without_fabricated_risk() -> None:
    service = AnalyticsInstantRiskService(
        _DeniedDatasetService(), _UnusedQualityService()
    )
    with pytest.raises(DomainError) as caught:
        service.evaluate(_request())
    assert caught.value.code == "ANALYSIS_RULE_NOT_APPROVED"


class _Access:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def assert_dataset_access(
        self, dataset_id, principal, mode="READ", *, version_no=None
    ):
        del principal, mode
        self.calls.append((dataset_id, version_no))


class _ApiRisk:
    def evaluate(self, request):
        summary = AnalyticsFilterSummary(
            AnalyticsNormalizedFilters(("LOT-1",), (), (), (), (), (), (), ()),
            ("VTH",),
            "a" * 64,
            "b" * 64,
        )
        return AnalyticsInstantRiskResult(
            "ANALYTICS_INSTANT_RISK_V1",
            summary,
            "c" * 64,
            tuple(item.analysis.value for item in request.evaluations),
            (),
            (),
            "2026-08-31T00:00:00+00:00",
        )


def test_instant_risk_api_authorizes_dataset_and_returns_versioned_contract() -> None:
    app = create_app()
    access = _Access()
    app.state.dataset_service = access
    app.state.analytics_service = object()
    app.state.analytics_instant_risk_service = _ApiRisk()

    response = TestClient(app).post(
        "/api/v1/analytics/instant-risk",
        json=_request().model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.json()["contract_version"] == "ANALYTICS_INSTANT_RISK_V1"
    assert response.json()["requested_analyses"] == ["CAPABILITY"]
    assert access.calls == [(11, 2)]
