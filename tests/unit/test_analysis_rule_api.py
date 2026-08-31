from __future__ import annotations

from dataclasses import asdict

import pytest
from app.core.errors import DomainError
from app.domain.analysis_rules import (
    ActivateAnalysisRuleRequest,
    AnalysisRuleActivationRecord,
    AnalysisRuleSetRecord,
    AnalysisRuleVersionRecord,
    CreateAnalysisRuleSetRequest,
    CreateAnalysisRuleVersionRequest,
    DecideAnalysisRuleRequest,
)
from app.domain.auth import Principal
from app.domain.formal_pat_contract import FORMAL_PAT_ADAPTER_MANIFEST_SHA256
from app.infrastructure.sql_analysis_rule_service import (
    SqlAnalysisRuleService,
    _pattern_matches,
    _pattern_overlaps,
)
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _parameters(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "missing_value_policy": "EXCLUDE_AND_COUNT",
        "retest_policy": "LATEST_ATTEMPT",
        "outlier_policy": "MARK_ONLY",
        "minimum_sample_size": 30,
    }
    value.update(overrides)
    return value


def _version_payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "version_code": "V1",
        "implementation_version": "analytics-1.0",
        "algorithm_code": "TUKEY_BOX_V1",
        "parameters": _parameters(whisker_multiplier=1.5),
        "applicability": {
            "test_stages": ["CP", "FT"],
            "supplier_ids": [],
            "product_ids": [],
            "parameter_patterns": ["VTH*"],
        },
        "algorithm_sha256": "a" * 64,
        "golden_manifest_sha256": "b" * 64,
    }
    value.update(overrides)
    return value


def test_rule_contract_requires_separated_owners_and_algorithm_parameters() -> None:
    with pytest.raises(ValidationError, match="must be distinct"):
        CreateAnalysisRuleSetRequest.model_validate(
            {
                "rule_code": "CP_BOX",
                "rule_name": "CP Box",
                "evaluation_type": "BOX_PLOT",
                "business_owner_user_id": 1,
                "technical_owner_user_id": 1,
                "quality_validator_user_id": 3,
                "description": "Versioned CP box-plot rule",
            }
        )

    payload = _version_payload(parameters=_parameters())
    with pytest.raises(ValidationError, match="whisker_multiplier"):
        CreateAnalysisRuleVersionRequest.model_validate(payload)

    pat = _version_payload(
        algorithm_code="PAT_SHARED_IQR_1_35_V1",
        algorithm_sha256=FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
        parameters=_parameters(lower_multiplier=6.0, upper_multiplier=6.0),
    )
    with pytest.raises(ValidationError, match="subgroup_dimension"):
        CreateAnalysisRuleVersionRequest.model_validate(pat)

    invalid_pat_sha = _version_payload(
        algorithm_code="PAT_SHARED_IQR_1_35_V1",
        parameters=_parameters(
            subgroup_dimension="LOT", lower_multiplier=6.0, upper_multiplier=6.0
        ),
    )
    with pytest.raises(ValidationError, match="frozen Adapter manifest"):
        CreateAnalysisRuleVersionRequest.model_validate(invalid_pat_sha)

    sbl = _version_payload(
        algorithm_code="SBL_GROUPED_LIMIT_V1",
        parameters=_parameters(subgroup_dimension="LOT"),
    )
    with pytest.raises(ValidationError, match="upper_multiplier and SAMPLE"):
        CreateAnalysisRuleVersionRequest.model_validate(sbl)

    spc = _version_payload(
        algorithm_code="SPC_I_MR_V1",
        parameters=_parameters(subgroup_dimension="RUN", sigma_definition="SAMPLE"),
    )
    with pytest.raises(ValidationError, match="POOLED_WITHIN"):
        CreateAnalysisRuleVersionRequest.model_validate(spc)


def test_zone_v2_requires_explicit_quadrant_semantics_while_v1_stays_readable() -> None:
    radial = _parameters(
        zone_layout_center_x=0.0,
        zone_layout_center_y=0.0,
        zone_layout_radius_die=100.0,
        zone_center_ratio=0.33,
        zone_mid_ratio=0.66,
    )
    legacy = CreateAnalysisRuleVersionRequest.model_validate(
        _version_payload(algorithm_code="WAFER_ZONE_GEOMETRY_V1", parameters=radial)
    )
    assert legacy.algorithm_code.value == "WAFER_ZONE_GEOMETRY_V1"

    with pytest.raises(ValidationError, match="Zone geometry V2 requires explicit"):
        CreateAnalysisRuleVersionRequest.model_validate(
            _version_payload(algorithm_code="WAFER_ZONE_GEOMETRY_V2", parameters=radial)
        )

    explicit = {
        **radial,
        "quadrant_axis_rotation_degrees": 17.5,
        "quadrant_y_direction": "UP",
        "quadrant_labels_ccw": ["FAB_A", "FAB_B", "FAB_C", "FAB_D"],
    }
    version = CreateAnalysisRuleVersionRequest.model_validate(
        _version_payload(algorithm_code="WAFER_ZONE_GEOMETRY_V2", parameters=explicit)
    )
    assert version.parameters.quadrant_labels_ccw == [
        "FAB_A",
        "FAB_B",
        "FAB_C",
        "FAB_D",
    ]

    with pytest.raises(ValidationError, match="four unique"):
        CreateAnalysisRuleVersionRequest.model_validate(
            _version_payload(
                algorithm_code="WAFER_ZONE_GEOMETRY_V2",
                parameters={
                    **explicit,
                    "quadrant_labels_ccw": ["A", "A", "C", "D"],
                },
            )
        )


def test_quality_approval_requires_matching_golden_reference() -> None:
    with pytest.raises(ValidationError, match="Golden manifest"):
        DecideAnalysisRuleRequest.model_validate(
            {
                "approval_role": "QUALITY",
                "decision": "APPROVED",
                "decision_note": "Golden reconciliation passed",
            }
        )


def test_parameter_rule_scope_supports_only_safe_trailing_prefix_wildcards() -> None:
    payload = _version_payload()
    payload["applicability"] = {
        "test_stages": ["CP"],
        "parameter_patterns": ["VT*H"],
    }
    with pytest.raises(ValidationError, match="parameter patterns"):
        CreateAnalysisRuleVersionRequest.model_validate(payload)
    assert _pattern_matches("VTH*", "VTH1")
    assert not _pattern_matches("VTH*", "BVCES")
    assert _pattern_overlaps("VTH*", "VTH1")
    assert _pattern_overlaps(None, "VTH1")
    assert not _pattern_overlaps("VTH*", "BVCES*")


def test_rule_service_fails_closed_without_permission() -> None:
    principal = Principal(8, "reader", "Reader", (), frozenset())
    with pytest.raises(DomainError) as caught:
        SqlAnalysisRuleService._require_govern(principal)
    assert caught.value.code == "PERMISSION_DENIED"
    assert caught.value.status_code == 403


class StubAnalysisRuleService:
    def __init__(self) -> None:
        self.created: CreateAnalysisRuleSetRequest | None = None
        self.version: CreateAnalysisRuleVersionRequest | None = None
        self.decision: DecideAnalysisRuleRequest | None = None
        self.activation: ActivateAnalysisRuleRequest | None = None

    @staticmethod
    def _set() -> AnalysisRuleSetRecord:
        return AnalysisRuleSetRecord(7, "CP_BOX", "CP Box", "BOX_PLOT", 1, 2, 3, True)

    @staticmethod
    def _version() -> AnalysisRuleVersionRecord:
        return AnalysisRuleVersionRecord(
            11,
            7,
            "CP_BOX",
            "V1",
            "analytics-1.0",
            "DRAFT",
            "DISABLED",
            "TUKEY_BOX_V1",
            (),
        )

    def list_rule_sets(self, principal: Principal) -> tuple[AnalysisRuleSetRecord, ...]:
        del principal
        return (self._set(),)

    def list_versions(
        self, rule_code: str, principal: Principal
    ) -> tuple[AnalysisRuleVersionRecord, ...]:
        del principal
        assert rule_code == "CP_BOX"
        return (self._version(),)

    def create_rule_set(
        self, request: CreateAnalysisRuleSetRequest, principal: Principal
    ) -> AnalysisRuleSetRecord:
        del principal
        self.created = request
        return self._set()

    def create_version(
        self,
        rule_code: str,
        request: CreateAnalysisRuleVersionRequest,
        principal: Principal,
    ) -> AnalysisRuleVersionRecord:
        del principal
        assert rule_code == "CP_BOX"
        self.version = request
        return self._version()

    def decide(
        self,
        rule_version_id: int,
        request: DecideAnalysisRuleRequest,
        principal: Principal,
    ) -> AnalysisRuleVersionRecord:
        del principal
        assert rule_version_id == 11
        self.decision = request
        return self._version()

    def activate(
        self,
        rule_version_id: int,
        request: ActivateAnalysisRuleRequest,
        principal: Principal,
    ) -> AnalysisRuleActivationRecord:
        del principal
        assert rule_version_id == 11
        self.activation = request
        return AnalysisRuleActivationRecord(19, 11, "CP", None, None, None, True)

    def get_version(
        self, rule_version_id: int, principal: Principal
    ) -> AnalysisRuleVersionRecord:
        del principal
        assert rule_version_id == 11
        return self._version()


def test_rule_registry_api_preserves_disabled_by_default_contract() -> None:
    app = create_app()
    service = StubAnalysisRuleService()
    app.state.analysis_rule_service = service
    client = TestClient(app)

    created = client.post(
        "/api/v1/analysis-rules",
        json={
            "rule_code": "CP_BOX",
            "rule_name": "CP Box",
            "evaluation_type": "BOX_PLOT",
            "business_owner_user_id": 1,
            "technical_owner_user_id": 2,
            "quality_validator_user_id": 3,
            "description": "Versioned CP box-plot rule",
        },
    )
    assert created.status_code == 201, created.text
    assert service.created is not None
    assert created.json()["rule_code"] == "CP_BOX"

    version = client.post(
        "/api/v1/analysis-rules/CP_BOX/versions", json=_version_payload()
    )
    assert version.status_code == 201, version.text
    assert service.version is not None
    assert version.json()["status"] == "DRAFT"
    assert version.json()["activation_status"] == "DISABLED"

    versions = client.get("/api/v1/analysis-rules/CP_BOX/versions")
    assert versions.status_code == 200, versions.text
    expected_version = asdict(service._version())
    expected_version["approvals"] = []
    assert versions.json() == [expected_version]

    quality = client.post(
        "/api/v1/analysis-rules/versions/11/decisions",
        json={
            "approval_role": "QUALITY",
            "decision": "APPROVED",
            "decision_note": "Golden reconciliation passed",
            "golden_manifest_sha256": "b" * 64,
        },
    )
    assert quality.status_code == 200, quality.text
    assert service.decision is not None

    activation = client.post(
        "/api/v1/analysis-rules/versions/11/activations",
        json={"confirmation": "ACTIVATE", "test_stage": "CP"},
    )
    assert activation.status_code == 201, activation.text
    assert service.activation is not None
    assert activation.json() == asdict(
        AnalysisRuleActivationRecord(19, 11, "CP", None, None, None, True)
    )
