from __future__ import annotations

import copy

import pytest
from app.domain.analysis_rule_pinning import (
    required_rules_from_analysis_view_state,
)
from app.domain.analytics_export_analysis import (
    AnalyticsExportAnalysisConfig,
    analytics_export_required_rules,
)
from pydantic import ValidationError


def _rule(code: str = "", version: str = "") -> dict[str, str]:
    return {"ruleCode": code, "versionCode": version}


def _analysis_view_state() -> dict[str, object]:
    return {
        "contract_version": "ANALYSIS_VIEW_STATE_V1",
        "components": {
            "overviewRisk": {
                "analyses": [
                    "CAPABILITY",
                    "PAT_ROBUST_IQR",
                    "SPC_I_MR",
                    "MARGIN_OOS",
                    "SBL_GROUPED_LIMIT",
                    "SYL_GROUPED_LIMIT",
                ],
                "parameter": "VTH",
                "groupBy": "DATASET",
                "capability": {
                    "method": "CPK_POOLED_WITHIN_RUN_V1",
                    **_rule("OVERVIEW_CPK", "V1"),
                },
                "pat": _rule("OVERVIEW_PAT", "V2"),
                "spc": _rule("OVERVIEW_SPC", "V3"),
                "margin": _rule("OVERVIEW_MARGIN", "V4"),
                "sbl": {
                    **_rule("OVERVIEW_SBL", "V5"),
                    "binType": "SOFT_BIN",
                },
                "syl": _rule("OVERVIEW_SYL", "V6"),
            },
            "detail": {
                "view": "WIDE",
                "sortBy": "UNIT_SEQUENCE",
                "sortDirection": "ASC",
            },
            "parameterAnalysis": {
                "groupBy": "DATASET",
                "analyses": ["DESCRIPTIVE", "HISTOGRAM", "CAPABILITY"],
                "boxPlot": _rule(),
                "histogram": _rule("PARAM_HIST", "V1"),
                "normalFit": _rule(),
                "capability": {
                    "method": "CPK_POOLED_WITHIN_LOT_WAFER_V1",
                    **_rule("PARAM_CPK", "V2"),
                },
                "boxParameter": "",
                "histogramDataset": "",
                "histogramParameter": "",
                "normalFitDataset": "",
                "normalFitParameter": "",
            },
            "parameterRelationship": {
                "xParameter": "VTH",
                "yParameters": ["IDSS"],
                "analyses": ["SCATTER", "CORRELATION"],
                "groupBy": "DATASET",
                "maxPoints": 10000,
                "correlation": {
                    "method": "PEARSON_PAIRWISE_V1",
                    **_rule("REL_CORR", "V1"),
                },
                "scatterY": "",
                "scatterDataset": "",
                "trendParameter": "",
                "correlationScope": "",
                "displayGroups": [],
                "pointVisibility": ["IN_SPEC", "OUT_OF_SPEC"],
            },
            "spatial": {
                "mode": "ZONE_COMPARISON",
                "parameter": "VTH",
                "maxPoints": 20000,
                "rule": _rule("SPATIAL_ZONE", "V1"),
                "colorScale": "ROBUST",
                "symbolSize": 12,
                "showMissing": True,
            },
            "quality": {
                "analysis": "PAT_ROBUST_IQR",
                "parameter": "IDSS",
                "groupBy": "LOT",
                "rule": _rule("QUALITY_PAT", "V1"),
                "spcOrder": None,
                "spcPhase": None,
                "binType": None,
                "spcDisplayGroup": "",
                "distributionDisplayGroup": "",
                "marginDisplayGroup": "",
                "cooccurrenceDisplayGroup": "",
                "sblDisplayBin": "",
                "sylDisplayDataset": "",
                "percentAxisMode": "AUTO",
            },
            "waferSummary": {
                "sortBy": "DATASET",
                "sortDirection": "ASC",
            },
        },
    }


def test_extracts_selected_exact_rules_with_algorithm_and_parameter_scopes() -> None:
    required = required_rules_from_analysis_view_state(
        {"analysis_view_state": _analysis_view_state()}, ("IDSS", "VTH")
    )
    contracts = {
        item.identity: (item.algorithm_code, item.parameters) for item in required
    }

    assert contracts["RULE:OVERVIEW_CPK:V1"] == (
        "CPK_POOLED_WITHIN_RUN_V1",
        ("VTH",),
    )
    assert contracts["RULE:OVERVIEW_PAT:V2"] == ("PAT_SHARED_IQR_1_35_V1", ("VTH",))
    assert contracts["RULE:OVERVIEW_SPC:V3"] == ("SPC_I_MR_V1", ("VTH",))
    assert contracts["RULE:OVERVIEW_MARGIN:V4"] == (
        "SPEC_MARGIN_V1",
        ("VTH",),
    )
    assert contracts["RULE:OVERVIEW_SBL:V5"] == (
        "SBL_GROUPED_LIMIT_V1",
        ("SOFT_BIN",),
    )
    assert contracts["RULE:OVERVIEW_SYL:V6"] == (
        "SYL_GROUPED_LIMIT_V1",
        (None,),
    )
    assert contracts["RULE:PARAM_HIST:V1"] == (
        "EQUAL_WIDTH_HISTOGRAM_V1",
        ("IDSS", "VTH"),
    )
    assert contracts["RULE:PARAM_CPK:V2"] == (
        "CPK_POOLED_WITHIN_LOT_WAFER_V1",
        ("IDSS", "VTH"),
    )
    assert contracts["RULE:REL_CORR:V1"] == (
        "PEARSON_PAIRWISE_V1",
        ("VTH", "IDSS"),
    )
    assert contracts["RULE:SPATIAL_ZONE:V1"] == (
        "WAFER_ZONE_GEOMETRY_V2",
        ("VTH",),
    )
    assert contracts["RULE:QUALITY_PAT:V1"] == (
        "PAT_SHARED_IQR_1_35_V1",
        ("IDSS",),
    )


def test_versioned_state_fails_closed_for_missing_selected_exact_rule() -> None:
    state = _analysis_view_state()
    state["components"]["overviewRisk"]["pat"] = _rule()  # type: ignore[index]

    with pytest.raises(ValidationError, match="exact rule"):
        required_rules_from_analysis_view_state(
            {"analysis_view_state": state}, ("IDSS", "VTH")
        )


def test_versioned_state_fails_closed_when_one_rule_claims_two_algorithms() -> None:
    state = copy.deepcopy(_analysis_view_state())
    state["components"]["overviewRisk"]["pat"] = _rule(  # type: ignore[index]
        "OVERVIEW_CPK", "V1"
    )

    with pytest.raises(ValueError, match="multiple algorithms"):
        required_rules_from_analysis_view_state(
            {"analysis_view_state": state}, ("IDSS", "VTH")
        )


def test_legacy_chart_config_has_no_implicit_rule_requirements() -> None:
    assert required_rules_from_analysis_view_state({"chart": "yield"}, ("VTH",)) == ()


def test_versioned_saved_detail_filters_are_bounded_and_backward_compatible() -> None:
    state = copy.deepcopy(_analysis_view_state())
    detail = state["components"]["detail"]  # type: ignore[index]
    detail["evaluation_filter"] = {  # type: ignore[index]
        "evaluation_type": "PAT",
        "evaluation_results": ["FAIL", "NOT_EVALUATED"],
        "rule_code": "CP_PAT",
        "rule_version": "V2",
    }
    detail["measurement_filter"] = {  # type: ignore[index]
        "parameter": "VTH",
        "lower_bound": 1.2,
        "upper_bound": 2.4,
        "lower_inclusive": True,
        "upper_inclusive": False,
    }

    assert required_rules_from_analysis_view_state(
        {"analysis_view_state": state}, ("IDSS", "VTH")
    )

    detail["measurement_filter"]["predicate"] = "value > 1"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        required_rules_from_analysis_view_state(
            {"analysis_view_state": state}, ("IDSS", "VTH")
        )


def test_saved_and_export_zone_pinning_both_require_geometry_v2() -> None:
    saved = required_rules_from_analysis_view_state(
        {"analysis_view_state": _analysis_view_state()}, ("VTH",)
    )
    saved_zone = next(item for item in saved if item.rule_code == "SPATIAL_ZONE")
    export = AnalyticsExportAnalysisConfig.model_validate(
        {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "SPATIAL_ANALYSIS",
            "spatial_analysis": {
                "mode": "ZONE_COMPARISON",
                "parameter": "VTH",
                "focus_dataset_id": 20,
                "max_points": 20000,
                "rule_code": "SPATIAL_ZONE",
                "rule_version": "V1",
            },
        }
    )
    export_zone = analytics_export_required_rules(export)[0]
    assert saved_zone.algorithm_code == "WAFER_ZONE_GEOMETRY_V2"
    assert export_zone.algorithm_code == "WAFER_ZONE_GEOMETRY_V2"
