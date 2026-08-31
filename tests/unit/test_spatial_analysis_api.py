from __future__ import annotations

import inspect
from dataclasses import dataclass

import pytest
from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsDatasetContext,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
)
from app.domain.spatial_analysis import (
    SpatialAnalysisRequest,
    SpatialAnalysisResult,
    SpatialDataQuality,
    SpatialPoint,
    SpatialWaferIdentity,
    SpatialZoneGeometry,
)
from app.infrastructure.sql_analytics_service import _hashes
from app.infrastructure.sql_spatial_analysis_service import (
    SqlSpatialAnalysisService,
    _color_domain,
    _quadrant_name,
    _response_item_count,
    _spec_status,
    _zone_name,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "datasets": [{"dataset_id": 11, "version_no": 2}],
        "filters": {"wafer_ids": ["W01"]},
        "parameters": [],
        "mode": "BIN_MAP",
    }
    value.update(overrides)
    return value


def test_spatial_request_enforces_parameter_and_rule_contracts() -> None:
    with pytest.raises(ValidationError, match="exactly one parameter"):
        SpatialAnalysisRequest.model_validate(
            _request(mode="PARAMETER_HEATMAP", parameters=[])
        )
    with pytest.raises(ValidationError, match="does not accept parameters"):
        SpatialAnalysisRequest.model_validate(_request(parameters=["VTH"]))
    with pytest.raises(ValidationError, match="approved rule reference"):
        SpatialAnalysisRequest.model_validate(_request(mode="ZONE_COMPARISON"))
    with pytest.raises(ValidationError, match="only accepted"):
        SpatialAnalysisRequest.model_validate(
            _request(rule_code="CP_ZONE", rule_version="V1")
        )


def test_spatial_numeric_and_spec_helpers_are_deterministic() -> None:
    domain = _color_domain([100.0, 0.0, 50.0])
    assert domain is not None
    assert domain.minimum == 0.0
    assert domain.maximum == 100.0
    assert domain.p02 == pytest.approx(2.0)
    assert domain.p98 == pytest.approx(98.0)
    assert _spec_status(None, "MISSING", "NOT_EVALUATED") == "MISSING"
    assert _spec_status(2.0, "MEASURED", "FAIL") == "OUT_OF_SPEC"
    assert _spec_status(1.0, "MEASURED", "PASS") == "IN_SPEC"
    common = {
        "center_x": 0,
        "center_y": 0,
        "radius": 10,
        "center_ratio": 0.33,
        "mid_ratio": 0.66,
    }
    assert _zone_name(0, 0, **common) == "CENTER"
    assert _zone_name(5, 0, **common) == "MID"
    assert _zone_name(10, 0, **common) == "EDGE"
    assert _zone_name(11, 0, **common) == "OUTSIDE_LAYOUT"
    labels = ("NORTH_EAST", "NORTH_WEST", "SOUTH_WEST", "SOUTH_EAST")
    assert (
        _quadrant_name(
            10,
            0,
            center_x=0,
            center_y=0,
            axis_rotation_degrees=0,
            y_direction="UP",
            labels_ccw=labels,
        )
        == "NORTH_EAST"
    )
    assert (
        _quadrant_name(
            0,
            -10,
            center_x=0,
            center_y=0,
            axis_rotation_degrees=0,
            y_direction="UP",
            labels_ccw=labels,
        )
        == "NORTH_WEST"
    )
    assert (
        _quadrant_name(
            10,
            0,
            center_x=0,
            center_y=0,
            axis_rotation_degrees=45,
            y_direction="UP",
            labels_ccw=labels,
        )
        == "SOUTH_EAST"
    )


def test_coordinate_validation_and_composite_failure_reconcile() -> None:
    rows = (
        {
            "dataset_id": 1,
            "version_no": 1,
            "lot_id": "L1",
            "wafer_id": "W1",
            "unit_id": 1,
            "x_coord": 1,
            "y_coord": 2,
            "overall_result": "FAIL",
        },
        {
            "dataset_id": 1,
            "version_no": 1,
            "lot_id": "L1",
            "wafer_id": "W2",
            "unit_id": 2,
            "x_coord": 1,
            "y_coord": 2,
            "overall_result": "PASS",
        },
    )
    missing, duplicates, wafers = SqlSpatialAnalysisService._validate_coordinates(rows)
    assert (missing, duplicates, len(wafers)) == (0, 0, 2)
    points = SqlSpatialAnalysisService._composite_points(rows)
    assert len(points) == 1
    assert points[0].observed_count == 2
    assert points[0].fail_count == 1
    assert points[0].fail_ratio == 0.5
    assert points[0].wafer_count == 2
    assert points[0].member_drilldown_keys == ("UNIT:1", "UNIT:2")
    total, member_count, zone_member_count, quadrant_member_count = (
        _response_item_count(points, (), (), ())
    )
    assert (total, member_count, zone_member_count, quadrant_member_count) == (
        3,
        2,
        0,
        0,
    )


def _spatial_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "dataset_id": 1,
        "version_no": 1,
        "lot_id": "L1",
        "wafer_id": "W1",
        "unit_id": 9,
        "x_coord": 1,
        "y_coord": 2,
        "soft_bin": "8",
        "hard_bin": None,
        "overall_result": "PASS",
        "measurement_id": 90,
        "measurement_test_item_id": 61,
        "value_numeric": 1.5,
        "measurement_status": "MEASURED",
        "unit_code": "V",
        "program_condition_json": '{"text":"1V"}',
        "spec_evaluation_count": 1,
        "spec_evaluation_id": 701,
        "formal_evaluation_result": "PASS",
        "spec_binding_id": None,
        "binding_spec_set_id": None,
        "spec_set_id": 7,
        "spec_version": "SPEC-V7",
        "spec_set_status": "RELEASED",
        "spec_item_id": 71,
        "spec_test_item_id": 61,
        "spec_unit_code": "V",
        "spec_condition_json": '{"text":"1V"}',
        "formal_lsl": 1.0,
        "formal_usl": 2.0,
        "formal_lower_operator": ">=",
        "formal_upper_operator": "<=",
        "evaluation_count": 1,
        "matched_mapping_count": 1,
        "bin_mapping_set_id": 3,
        "mapping_version": "BIN-V3",
        "bin_definition_id": 31,
        "mapped_bin_code": "8",
        "bin_name": "LEAKAGE_FAIL",
        "failure_mode": "LEAKAGE",
        "is_pass_snapshot": False,
    }
    row.update(overrides)
    return row


def test_spatial_formal_spec_and_mapping_contracts_fail_closed() -> None:
    valid = _spatial_row()
    SqlSpatialAnalysisService._validate_formal_specs((valid,))
    SqlSpatialAnalysisService._validate_bin_mappings((valid,))

    with pytest.raises(DomainError) as missing_spec:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_item_id=None),)
        )
    assert missing_spec.value.code == "ANALYSIS_SPEC_MISSING"

    with pytest.raises(DomainError) as missing_evaluation:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_evaluation_count=0),)
        )
    assert missing_evaluation.value.code == "ANALYSIS_SPEC_MISSING"

    with pytest.raises(DomainError) as duplicate_spec:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_evaluation_count=2),)
        )
    assert duplicate_spec.value.code == "ANALYSIS_SPEC_AMBIGUOUS"

    with pytest.raises(DomainError) as invalid_provenance:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_binding_id=81, binding_spec_set_id=99),)
        )
    assert invalid_provenance.value.code == "ANALYSIS_SPEC_PROVENANCE_INVALID"

    with pytest.raises(DomainError) as wrong_item:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_test_item_id=62),)
        )
    assert wrong_item.value.code == "ANALYSIS_SPEC_PROVENANCE_INVALID"

    with pytest.raises(DomainError) as incompatible_spec:
        SqlSpatialAnalysisService._validate_formal_specs(
            (_spatial_row(spec_unit_code="mV"),)
        )
    assert incompatible_spec.value.code == "ANALYSIS_SPEC_INCOMPATIBLE"

    with pytest.raises(DomainError) as ambiguous_mapping:
        SqlSpatialAnalysisService._validate_bin_mappings(
            (_spatial_row(evaluation_count=2, matched_mapping_count=2),)
        )
    assert ambiguous_mapping.value.code == "ANALYSIS_BIN_MAPPING_INCOMPLETE"


def test_spatial_uses_exact_snapshot_result_at_exclusive_spec_boundaries() -> None:
    lower_boundary = _spatial_row(
        value_numeric=1.0,
        formal_lower_operator=">",
        formal_evaluation_result="FAIL",
    )
    upper_boundary = _spatial_row(
        value_numeric=2.0,
        formal_upper_operator="<",
        formal_evaluation_result="FAIL",
    )

    SqlSpatialAnalysisService._validate_formal_specs((lower_boundary, upper_boundary))
    assert SqlSpatialAnalysisService._single_point(lower_boundary).spec_status == (
        "OUT_OF_SPEC"
    )
    assert SqlSpatialAnalysisService._single_point(upper_boundary).spec_status == (
        "OUT_OF_SPEC"
    )


def test_spatial_query_consumes_frozen_spec_evaluation_not_live_spec_limits() -> None:
    source = inspect.getsource(SqlSpatialAnalysisService._rows_for_context)

    assert "test.measurement_evaluation me" in source
    assert "me.evaluation_type='SPEC'" in source
    assert "me.evaluation_scope_key=N'FORMAL_SPEC'" in source
    assert "me.is_current=1" in source
    assert "me.lsl_applied" in source
    assert "me.usl_applied" in source
    assert "me.lower_operator_applied" in source
    assert "me.upper_operator_applied" in source
    assert "spec_eval.spec_item_id,spec_eval.spec_test_item_id," in source
    assert "si.lsl AS formal_lsl" not in source
    assert "si.usl AS formal_usl" not in source


def test_spatial_bin_point_uses_mapping_snapshot_not_raw_result() -> None:
    mapped_fail = SqlSpatialAnalysisService._single_point(
        _spatial_row(overall_result="PASS", is_pass_snapshot=False),
        use_mapped_bin=True,
    )
    assert mapped_fail.result == "FAIL"
    assert mapped_fail.fail_count == 1
    assert mapped_fail.fail_ratio == 1.0
    assert mapped_fail.bin_is_pass is False
    assert mapped_fail.bin_mapping_version == "BIN-V3"
    assert mapped_fail.failure_mode == "LEAKAGE"
    assert mapped_fail.spec_version == "SPEC-V7"

    mapped_pass = SqlSpatialAnalysisService._single_point(
        _spatial_row(overall_result="FAIL", is_pass_snapshot=True),
        use_mapped_bin=True,
    )
    assert mapped_pass.result == "PASS"
    assert mapped_pass.fail_count == 0
    assert mapped_pass.fail_ratio == 0.0


def test_zone_assignment_returns_point_identity_geometry_and_stable_area_drilldown() -> (
    None
):
    def point(unit_id: int, x: int, y: int) -> SpatialPoint:
        return SpatialPoint(
            dataset_id=1,
            version_no=1,
            lot_id="L1",
            wafer_id="W1",
            x=x,
            y=y,
            bin_code="1",
            result="PASS",
            value=1.0,
            unit="V",
            lsl=0.0,
            usl=2.0,
            spec_status="IN_SPEC",
            drilldown_key=f"UNIT:{unit_id}",
            observed_count=1,
            fail_count=0,
            fail_ratio=0.0,
            wafer_count=1,
        )

    geometry = SpatialZoneGeometry(
        0.0,
        0.0,
        10.0,
        0.33,
        0.66,
        0.0,
        "UP",
        ("NORTH_EAST", "NORTH_WEST", "SOUTH_WEST", "SOUTH_EAST"),
    )
    zoned = SqlSpatialAnalysisService._assign_zones(
        (point(12, 5, 0), point(11, 0, 0), point(13, 10, 0)), geometry
    )
    assert [item.zone for item in zoned] == ["MID", "CENTER", "EDGE"]
    assert [item.quadrant for item in zoned] == [
        "NORTH_EAST",
        "NORTH_EAST",
        "NORTH_EAST",
    ]
    summaries = SqlSpatialAnalysisService._zone_summaries(
        zoned,
        center_x=geometry.center_x,
        center_y=geometry.center_y,
        radius=geometry.radius,
        center_ratio=geometry.center_ratio,
        mid_ratio=geometry.mid_ratio,
    )
    assert {item.zone: item.drilldown_key for item in summaries} == {
        "CENTER": "UNIT:11",
        "EDGE": "UNIT:13",
        "MID": "UNIT:12",
    }
    quadrants = SqlSpatialAnalysisService._quadrant_summaries(
        zoned, geometry.quadrant_labels_ccw
    )
    assert [item.quadrant for item in quadrants] == list(geometry.quadrant_labels_ccw)
    assert quadrants[0].member_drilldown_keys == (
        "UNIT:11",
        "UNIT:12",
        "UNIT:13",
    )
    assert [item.unit_count for item in quadrants] == [3, 0, 0, 0]

    with pytest.raises(DomainError) as error:
        SqlSpatialAnalysisService._assign_zones((point(14, 11, 0),), geometry)
    assert error.value.code == "ANALYSIS_SPATIAL_LAYOUT_INCOMPATIBLE"


class _ScopedZoneRuleService:
    def __init__(self, *, deny_product_id: int | None = None) -> None:
        self.deny_product_id = deny_product_id
        self.calls: list[dict[str, object]] = []

    def approved_rule_parameters(self, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs["product_id"] == self.deny_product_id:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED", "zone scope is not approved", 409
            )
        return {
            "zone_layout_center_x": 0.0,
            "zone_layout_center_y": 0.0,
            "zone_layout_radius_die": 10.0,
            "zone_center_ratio": 0.33,
            "zone_mid_ratio": 0.66,
            "quadrant_axis_rotation_degrees": 15.0,
            "quadrant_y_direction": "DOWN",
            "quadrant_labels_ccw": ["A", "B", "C", "D"],
        }


def test_zone_registry_resolution_passes_exact_dataset_and_parameter_scope() -> None:
    rules = _ScopedZoneRuleService()
    service = SqlSpatialAnalysisService(
        object(),
        rule_service=rules,  # type: ignore[arg-type]
    )
    request = SpatialAnalysisRequest.model_validate(
        _request(
            mode="ZONE_COMPARISON",
            parameters=["VTH"],
            rule_code="CP_ZONE",
            rule_version="v2",
        )
    )
    contexts = (
        {"test_stage": "CP", "supplier_id": 11, "product_id": 21},
        {"test_stage": "CP", "supplier_id": 12, "product_id": 22},
    )

    resolved = service._approved_zone_rule(request, contexts)

    assert resolved["zone_layout_radius_die"] == 10.0
    assert [(call["supplier_id"], call["product_id"]) for call in rules.calls] == [
        (11, 21),
        (12, 22),
    ]
    assert all(call["parameter"] == "VTH" for call in rules.calls)
    assert all(
        call["expected_algorithm_code"] == "WAFER_ZONE_GEOMETRY_V2"
        for call in rules.calls
    )


def test_zone_registry_resolution_fails_when_any_dataset_scope_is_not_active() -> None:
    rules = _ScopedZoneRuleService(deny_product_id=22)
    service = SqlSpatialAnalysisService(
        object(),
        rule_service=rules,  # type: ignore[arg-type]
    )
    request = SpatialAnalysisRequest.model_validate(
        _request(
            mode="ZONE_COMPARISON",
            parameters=["VTH"],
            rule_code="CP_ZONE",
            rule_version="v2",
        )
    )

    with pytest.raises(DomainError) as error:
        service._approved_zone_rule(
            request,
            (
                {"test_stage": "CP", "supplier_id": 11, "product_id": 21},
                {"test_stage": "CP", "supplier_id": 12, "product_id": 22},
            ),
        )

    assert error.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert len(rules.calls) == 2


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


class StubSpatialService:
    def __init__(self) -> None:
        self.request: SpatialAnalysisRequest | None = None

    def analyze(self, request: SpatialAnalysisRequest) -> SpatialAnalysisResult:
        self.request = request
        return SpatialAnalysisResult(
            contract_version="ANALYTICS_SPATIAL_V1",
            dataset_context=AnalyticsDatasetContext(
                (AnalyticsResolvedDataset(11, 2, "CP", "CP", "P1"),), "CP", True
            ),
            filter_summary=_hashes(request),
            rule_context=AnalyticsRuleContext((), (), ()),
            capabilities=(AnalyticsCapability("BIN_MAP", "AVAILABLE", None, None),),
            mode="BIN_MAP",
            parameter=None,
            color_domain=None,
            data_quality=SpatialDataQuality(1, 1, 1, 0, 0, 0, 0, 0),
            points=(
                SpatialPoint(
                    11,
                    2,
                    "L1",
                    "W01",
                    1,
                    2,
                    "1",
                    "PASS",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "UNIT:9",
                    1,
                    0,
                    0.0,
                    1,
                ),
            ),
            wafer_manifest=(
                SpatialWaferIdentity("11:V2:LOT:L1:WAFER:W01", 11, 2, "L1", "W01"),
            ),
            wafer_layers=(),
            zones=(),
            warnings=(),
            computed_at="2026-08-31T00:00:00+00:00",
        )


def test_spatial_api_authorizes_every_dataset_before_analysis() -> None:
    app = create_app()
    datasets = StubDatasetService()
    spatial = StubSpatialService()
    app.state.dataset_service = datasets
    app.state.spatial_analysis_service = spatial
    response = TestClient(app).post("/api/v1/analytics/spatial", json=_request())
    assert response.status_code == 200, response.text
    assert response.json()["contract_version"] == "ANALYTICS_SPATIAL_V1"
    assert response.json()["points"][0]["drilldown_key"] == "UNIT:9"
    assert datasets.calls == [AccessCall(11, 2)]
    assert spatial.request is not None
    assert spatial.request.filters.wafer_ids == ["W01"]
