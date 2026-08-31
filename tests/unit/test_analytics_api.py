from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest
from app.core.middleware import _analytics_group
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsCounts,
    AnalyticsDatasetContext,
    AnalyticsDatasetOverview,
    AnalyticsDetailRequest,
    AnalyticsOptionSet,
    AnalyticsOverviewRequest,
    AnalyticsOverviewResult,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
    AnalyticsShellContextResult,
)
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _hashes,
    _risk_summary,
    _source_identity,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


@dataclass
class AccessCall:
    dataset_id: int
    version_no: int | None


class StubDatasetService:
    def __init__(self) -> None:
        self.calls: list[AccessCall] = []

    def assert_dataset_access(
        self, dataset_id, principal, mode="READ", *, version_no=None
    ) -> None:
        del principal, mode
        self.calls.append(AccessCall(dataset_id, version_no))


class StubAnalyticsService:
    def __init__(self) -> None:
        self.request: AnalyticsOverviewRequest | None = None

    def overview(self, request: AnalyticsOverviewRequest) -> AnalyticsOverviewResult:
        self.request = request
        summary = _hashes(request)
        return AnalyticsOverviewResult(
            contract_version="ANALYTICS_CONTEXT_V1",
            dataset_context=AnalyticsDatasetContext(
                (AnalyticsResolvedDataset(11, 2, "CP Dataset", "CP", "PRODUCT"),),
                "CP",
                True,
            ),
            filter_summary=summary,
            rule_context=AnalyticsRuleContext(("SPEC:1:V1",), (), ()),
            capabilities=(AnalyticsCapability("OVERVIEW", "AVAILABLE", None, None),),
            counts=AnalyticsCounts(12, 10, 2, 8, 1, 1, 0, 9, 0),
            sampling_summary=AnalyticsSamplingSummary(False, None, 0, 0, 0),
            options=AnalyticsOptionSet(
                ("LOT",), ("W01",), ("1",), (), (), (), (), ("P1",)
            ),
            datasets=(AnalyticsDatasetOverview(11, 2, 10, 8, 1, 1, 0, 9, 8 / 9),),
            yield_trend=(),
            bin_pareto=(),
            wafer_map=(),
            risk_summary=(),
            warnings=(),
            computed_at="2026-08-31T00:00:00+00:00",
        )

    def shell_context(
        self, request: AnalyticsOverviewRequest
    ) -> AnalyticsShellContextResult:
        result = self.overview(request)
        return AnalyticsShellContextResult(
            result.contract_version,
            result.dataset_context,
            result.filter_summary,
            result.rule_context,
            result.capabilities,
            result.counts,
            result.sampling_summary,
            result.options,
            result.warnings,
            result.computed_at,
        )


def test_risk_summary_reports_data_gaps_and_rule_gate_without_inventing_values() -> (
    None
):
    risks = _risk_summary(
        capabilities=(
            AnalyticsCapability("OVERVIEW", "AVAILABLE", None, None),
            AnalyticsCapability(
                "BIN_PARETO",
                "UNAVAILABLE",
                "ANALYSIS_BIN_MAPPING_REQUIRED",
                "mapping missing",
            ),
        ),
        counts=AnalyticsCounts(12, 10, 2, 0, 0, 7, 3, 0, 4),
        rule_context=AnalyticsRuleContext((), (), ()),
        evaluation_counts={},
    )
    by_code = {item.code: item for item in risks}
    assert by_code["UNKNOWN_OR_ABORT_RESULT"].affected_count == 10
    assert by_code["UNKNOWN_OR_ABORT_RESULT"].rate == 1.0
    assert by_code["MISSING_MEASUREMENT"].affected_count == 4
    assert by_code["YIELD_NOT_ASSESSABLE"].rate is None
    assert by_code["CAPABILITY_BIN_PARETO"].status == "GATED"
    assert (
        by_code["STATISTICAL_RISK_NOT_EVALUATED"].reason_code
        == "ANALYSIS_RULE_NOT_APPROVED"
    )


def test_risk_summary_does_not_claim_rule_gate_after_evaluation_version_exists() -> (
    None
):
    risks = _risk_summary(
        capabilities=(AnalyticsCapability("OVERVIEW", "AVAILABLE", None, None),),
        counts=AnalyticsCounts(10, 10, 0, 9, 1, 0, 0, 10, 0),
        rule_context=AnalyticsRuleContext((), (), ("RULE:PAT:V1",)),
        evaluation_counts={
            ("PAT", "FAIL", "CP_PAT", "V1"): 2,
            ("PAT", "PASS", "CP_PAT", "V1"): 8,
        },
    )
    assert all(item.code != "STATISTICAL_RISK_NOT_EVALUATED" for item in risks)
    pat = next(item for item in risks if item.code == "EVALUATION_PAT:CP_PAT:V1")
    assert (pat.affected_count, pat.denominator_count, pat.rate) == (2, 10, 0.2)
    assert pat.rule_versions == ("V1",)
    assert pat.aggregate_drilldown_context is not None
    assert pat.aggregate_drilldown_context.evaluation_type == "PAT"
    assert pat.aggregate_drilldown_context.evaluation_results == ("FAIL",)
    assert pat.aggregate_drilldown_context.rule_code == "CP_PAT"
    assert pat.aggregate_drilldown_context.rule_version == "V1"


def test_persisted_risk_never_merges_rule_versions_or_nonpass_results() -> None:
    risks = _risk_summary(
        capabilities=(AnalyticsCapability("OVERVIEW", "AVAILABLE", None, None),),
        counts=AnalyticsCounts(20, 20, 0, 15, 5, 0, 0, 20, 0),
        rule_context=AnalyticsRuleContext((), (), ("RULE:CP_PAT:V1", "RULE:CP_PAT:V2")),
        evaluation_counts={
            ("PAT", "PASS", "CP_PAT", "V1"): 8,
            ("PAT", "FAIL", "CP_PAT", "V1"): 2,
            ("PAT", "PASS", "CP_PAT", "V2"): 7,
            ("PAT", "UNKNOWN", "CP_PAT", "V2"): 1,
        },
    )
    evaluation = [item for item in risks if item.category == "EVALUATION"]
    assert [item.code for item in evaluation] == [
        "EVALUATION_PAT:CP_PAT:V1",
        "EVALUATION_PAT:CP_PAT:V2",
    ]
    assert evaluation[0].aggregate_drilldown_context is not None
    assert evaluation[0].aggregate_drilldown_context.evaluation_results == ("FAIL",)
    assert evaluation[1].aggregate_drilldown_context is not None
    assert evaluation[1].aggregate_drilldown_context.evaluation_results == ("UNKNOWN",)


def test_feature_flag_path_mapping_covers_every_analytics_group() -> None:
    assert _analytics_group("/api/v1/analytics/overview") == "OVERVIEW"
    assert _analytics_group("/api/v1/analytics/instant-risk") == "OVERVIEW"
    assert _analytics_group("/api/v1/analytics/detail") == "DETAIL"
    assert _analytics_group("/api/v1/datasets/parameter-analysis") == "PARAMETER"
    assert _analytics_group("/api/v1/analytics/spatial") == "SPATIAL"
    assert _analytics_group("/api/v1/analytics/quality-evaluation") == "QUALITY"
    assert _analytics_group("/api/v1/analytics/wafer-summary") == "DELIVERY"
    assert _analytics_group("/api/v1/analytics/saved-analyses") == "DELIVERY"
    assert _analytics_group("/api/v1/analytics/exports/9") == "DELIVERY"
    assert _analytics_group("/api/v1/analytics/features") is None
    assert _analytics_group("/api/v1/analytics/context") is None


def test_feature_flags_are_visible_and_backend_kill_switch_blocks_direct_url() -> None:
    app = create_app()
    app.state.analytics_feature_flags = {
        "OVERVIEW": False,
        "DETAIL": True,
        "PARAMETER": True,
        "SPATIAL": True,
        "QUALITY": True,
        "DELIVERY": True,
    }
    client = TestClient(app)
    feature_response = client.get("/api/v1/analytics/features")
    assert feature_response.status_code == 200
    overview_flag = next(
        item for item in feature_response.json()["groups"] if item["code"] == "OVERVIEW"
    )
    assert overview_flag == {
        "code": "OVERVIEW",
        "enabled": False,
        "reason_code": "ANALYSIS_FEATURE_DISABLED",
        "message": "OVERVIEW analytics is disabled by the release kill switch",
    }
    blocked = client.post(
        "/api/v1/analytics/overview",
        json={"datasets": [{"dataset_id": 1, "version_no": 1}]},
    )
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "ANALYSIS_FEATURE_DISABLED"
    assert blocked.json()["error"]["details"] == [{"feature_group": "OVERVIEW"}]


def test_hashes_are_order_independent_but_context_includes_datasets_and_parameters() -> (
    None
):
    left = AnalyticsOverviewRequest.model_validate(
        {
            "datasets": [
                {"dataset_id": 2, "version_no": 1},
                {"dataset_id": 1, "version_no": 3},
            ],
            "filters": {
                "lot_ids": ["B", "A"],
                "overall_results": ["FAIL", "PASS"],
                "tester_ids": ["T2", "T1"],
            },
            "parameters": ["P2", "P1"],
        }
    )
    right = AnalyticsOverviewRequest.model_validate(
        {
            "datasets": [
                {"dataset_id": 1, "version_no": 3},
                {"dataset_id": 2, "version_no": 1},
            ],
            "filters": {
                "lot_ids": ["A", "B"],
                "overall_results": ["PASS", "FAIL"],
                "tester_ids": ["T1", "T2"],
            },
            "parameters": ["P1", "P2"],
        }
    )
    assert _hashes(left) == _hashes(right)
    changed = right.model_copy(update={"parameters": ["P1"]})
    assert _hashes(changed).filter_hash == _hashes(right).filter_hash
    assert _hashes(changed).context_hash != _hashes(right).context_hash


def test_context_rejects_duplicate_dataset_and_unselected_focus() -> None:
    with pytest.raises(ValidationError, match="each dataset may appear only once"):
        AnalyticsOverviewRequest.model_validate(
            {
                "datasets": [
                    {"dataset_id": 1, "version_no": 1},
                    {"dataset_id": 1, "version_no": 2},
                ]
            }
        )
    with pytest.raises(ValidationError, match="focus_dataset_id"):
        AnalyticsOverviewRequest.model_validate(
            {
                "datasets": [{"dataset_id": 1, "version_no": 1}],
                "focus_dataset_id": 2,
            }
        )


def test_detail_requires_focus_inside_context_and_strict_filter_values() -> None:
    with pytest.raises(ValidationError, match="focus_dataset_id"):
        AnalyticsDetailRequest.model_validate(
            {
                "datasets": [{"dataset_id": 1, "version_no": 1}],
                "focus_dataset_id": 9,
            }
        )
    with pytest.raises(ValidationError, match="must be unique"):
        AnalyticsOverviewRequest.model_validate(
            {
                "datasets": [{"dataset_id": 1, "version_no": 1}],
                "filters": {"source_ids": ["SOURCE", "SOURCE"]},
            }
        )
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "evaluation_filter": {
                "evaluation_type": "PAT",
                "evaluation_results": ["fail", "UNKNOWN"],
                "rule_code": "CP_PAT",
                "rule_version": "V2",
            },
        }
    )
    assert request.evaluation_filter is not None
    assert request.evaluation_filter.evaluation_results == ["FAIL", "UNKNOWN"]
    with pytest.raises(ValidationError, match="evaluation results"):
        AnalyticsDetailRequest.model_validate(
            {
                "datasets": [{"dataset_id": 1, "version_no": 1}],
                "focus_dataset_id": 1,
                "evaluation_filter": {
                    "evaluation_type": "PAT",
                    "evaluation_results": ["FAIL", "FAIL"],
                    "rule_code": "CP_PAT",
                    "rule_version": "V2",
                },
            }
        )


def test_source_identity_never_uses_tester_as_unique_fallback() -> None:
    assert (
        _source_identity({"run_id": 7, "tester_id": "TESTER-A", "metadata_json": "{}"})
        == "RUN-7"
    )
    assert (
        _source_identity(
            {
                "run_id": 7,
                "tester_id": "TESTER-A",
                "metadata_json": '{"source_id":"FILE-1"}',
            }
        )
        == "FILE-1"
    )


def test_overview_api_authorizes_every_dataset_and_preserves_all_filters() -> None:
    app = create_app()
    datasets = StubDatasetService()
    analytics = StubAnalyticsService()
    app.state.dataset_service = datasets
    app.state.analytics_service = analytics
    response = TestClient(app).post(
        "/api/v1/analytics/overview",
        json={
            "datasets": [{"dataset_id": 11, "version_no": 2}],
            "filters": {
                "lot_ids": ["LOT"],
                "wafer_ids": ["W01"],
                "bin_codes": ["1"],
                "overall_results": ["PASS"],
                "source_ids": ["FILE-1"],
                "tester_ids": ["TESTER-1"],
                "program_versions": ["V1"],
                "test_conditions": ["BIAS=1"],
            },
            "parameters": ["P1"],
            "focus_dataset_id": 11,
        },
    )
    assert response.status_code == 200
    assert response.json()["contract_version"] == "ANALYTICS_CONTEXT_V1"
    assert len(response.json()["filter_summary"]["filter_hash"]) == 64
    assert datasets.calls == [AccessCall(11, 2)]
    assert analytics.request is not None
    assert analytics.request.filters.test_conditions == ["BIAS=1"]
    assert analytics.request.parameters == ["P1"]


def test_shell_context_remains_available_when_overview_feature_is_disabled() -> None:
    app = create_app()
    datasets = StubDatasetService()
    analytics = StubAnalyticsService()
    app.state.dataset_service = datasets
    app.state.analytics_service = analytics
    app.state.analytics_feature_flags = {
        "OVERVIEW": False,
        "DETAIL": True,
        "PARAMETER": True,
        "SPATIAL": True,
        "QUALITY": True,
        "DELIVERY": True,
    }

    response = TestClient(app).post(
        "/api/v1/analytics/context",
        json={
            "datasets": [{"dataset_id": 11, "version_no": 2}],
            "focus_dataset_id": 11,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_context"]["test_stage"] == "CP"
    assert "yield_trend" not in payload
    assert "bin_pareto" not in payload
    assert "risk_summary" not in payload
    assert datasets.calls == [AccessCall(11, 2)]


def test_shell_and_detail_do_not_reenter_overview_query_group() -> None:
    shell_source = inspect.getsource(SqlAnalyticsService.shell_context)
    detail_source = inspect.getsource(SqlAnalyticsService.detail)

    assert "self.overview(" not in shell_source
    assert "self.overview(" not in detail_source
    assert "self.shell_context(" in detail_source
