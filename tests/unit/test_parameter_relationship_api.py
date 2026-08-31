from __future__ import annotations

from app.api.dependencies import current_principal
from app.api.parameter_relationship import router
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsCounts,
    AnalyticsDatasetContext,
    AnalyticsFilterSummary,
    AnalyticsNormalizedFilters,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
)
from app.domain.auth import Principal
from app.domain.parameter_relationship import ParameterRelationshipResult
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

_READER = Principal(
    user_id=7,
    login_name="reader",
    display_name="Reader",
    roles=("ENGINEER",),
    permissions=frozenset({"DATASET_READ"}),
)
_NO_READ = Principal(
    user_id=8,
    login_name="blocked",
    display_name="Blocked",
    roles=("VIEWER",),
    permissions=frozenset(),
)


class _DatasetAccess:
    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[tuple[int, int | None]] = []

    def assert_dataset_access(
        self, dataset_id, principal, mode="READ", *, version_no=None
    ) -> None:
        self.calls.append((dataset_id, version_no))
        if self.deny:
            raise DomainError("DATASET_ACCESS_DENIED", "denied", 403)


class _RelationshipService:
    def __init__(self) -> None:
        self.requests = []

    def relationship(self, request):
        self.requests.append(request)
        return ParameterRelationshipResult(
            contract_version="PARAMETER_RELATIONSHIP_V1",
            dataset_context=AnalyticsDatasetContext(
                resolved_datasets=(
                    AnalyticsResolvedDataset(1, 1, "FT Dataset", "FT", "P1"),
                ),
                test_stage="FT",
                current_published_verified=True,
            ),
            filter_summary=AnalyticsFilterSummary(
                normalized_filters=AnalyticsNormalizedFilters(
                    (), (), (), (), (), (), (), ()
                ),
                parameters=("PX", "PY"),
                filter_hash="a" * 64,
                context_hash="b" * 64,
            ),
            rule_context=AnalyticsRuleContext((), (), ()),
            capabilities=(
                AnalyticsCapability("PARAMETER_SCATTER", "AVAILABLE", None, None),
            ),
            counts=AnalyticsCounts(1, 1, 0, 1, 0, 0, 0, 1, 0),
            sampling_summary=AnalyticsSamplingSummary(False, None, 1, 1, 0),
            group_by="DATASET",
            trend_order_basis=(
                "DATASET_ORDINAL_THEN_RUN_SOURCE_TIME_THEN_RUN_ID_"
                "THEN_UNIT_SEQUENCE_THEN_UNIT_ID"
            ),
            items=(),
            warnings=(),
            computed_at="2026-08-31T00:00:00+00:00",
        )


class _GatedRelationshipService:
    def __init__(self) -> None:
        self.requests = []

    def relationship(self, request):
        self.requests.append(request)
        raise DomainError(
            "ANALYSIS_RULE_NOT_APPROVED",
            "correlation rule is not approved for this exact scope",
            409,
        )


def _client(
    principal: Principal,
    *,
    deny_access: bool = False,
    relationship_service=None,
):
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(router, prefix="/api/v1/analytics")
    access = _DatasetAccess(deny=deny_access)
    relationship = relationship_service or _RelationshipService()
    app.state.dataset_service = access
    app.state.parameter_relationship_service = relationship
    app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app), access, relationship


def _payload():
    return {
        "datasets": [{"dataset_id": 1, "version_no": 1}],
        "x_parameter": "PX",
        "y_parameters": ["PY"],
        "analyses": ["SCATTER"],
        "group_by": "DATASET",
        "max_points": 100,
    }


def test_parameter_relationship_api_requires_dataset_read_permission() -> None:
    client, access, relationship = _client(_NO_READ)

    response = client.post("/api/v1/analytics/parameter-relationship", json=_payload())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert access.calls == []
    assert relationship.requests == []


def test_parameter_relationship_api_checks_dataset_access_before_service_gate() -> None:
    client, access, relationship = _client(_READER, deny_access=True)
    payload = {
        **_payload(),
        "analyses": ["CORRELATION"],
        "correlation": {
            "method": "PEARSON_PAIRWISE_V1",
            "rule_code": "CORRELATION_RULE",
            "version_code": "v1",
        },
    }

    response = client.post("/api/v1/analytics/parameter-relationship", json=payload)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DATASET_ACCESS_DENIED"
    assert access.calls == [(1, 1)]
    assert relationship.requests == []


def test_parameter_relationship_api_exposes_default_correlation_owner_gate() -> None:
    service = _GatedRelationshipService()
    client, access, _relationship = _client(_READER, relationship_service=service)
    payload = {
        **_payload(),
        "analyses": ["CORRELATION"],
        "correlation": {
            "method": "PEARSON_PAIRWISE_V1",
            "rule_code": "CORRELATION_RULE",
            "version_code": "v1",
        },
    }

    response = client.post("/api/v1/analytics/parameter-relationship", json=payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ANALYSIS_RULE_NOT_APPROVED"
    assert access.calls == [(1, 1)]
    assert service.requests[0].correlation.rule_code == "CORRELATION_RULE"
    assert service.requests[0].correlation.version_code == "v1"


def test_parameter_relationship_api_returns_unified_envelope() -> None:
    client, access, relationship = _client(_READER)

    response = client.post("/api/v1/analytics/parameter-relationship", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "PARAMETER_RELATIONSHIP_V1"
    assert body["trend_order_basis"].startswith("DATASET_ORDINAL_THEN_RUN_SOURCE_TIME")
    assert body["dataset_context"]["test_stage"] == "FT"
    assert len(body["filter_summary"]["context_hash"]) == 64
    assert body["sampling_summary"]["returned_points"] == 1
    assert access.calls == [(1, 1)]
    assert relationship.requests[0].x_parameter == "PX"


def test_parameter_relationship_api_rejects_x_reused_as_y() -> None:
    client, access, relationship = _client(_READER)
    payload = {**_payload(), "y_parameters": ["PX"]}

    response = client.post("/api/v1/analytics/parameter-relationship", json=payload)

    assert response.status_code == 422
    assert access.calls == []
    assert relationship.requests == []


def test_parameter_relationship_api_rejects_more_than_five_y_parameters() -> None:
    client, access, relationship = _client(_READER)
    payload = {
        **_payload(),
        "y_parameters": ["P1", "P2", "P3", "P4", "P5", "P6"],
    }

    response = client.post("/api/v1/analytics/parameter-relationship", json=payload)

    assert response.status_code == 422
    assert access.calls == []
    assert relationship.requests == []


def test_parameter_relationship_api_rejects_more_than_eight_datasets() -> None:
    client, access, relationship = _client(_READER)
    payload = {
        **_payload(),
        "datasets": [
            {"dataset_id": dataset_id, "version_no": 1} for dataset_id in range(1, 10)
        ],
    }

    response = client.post("/api/v1/analytics/parameter-relationship", json=payload)

    assert response.status_code == 422
    assert access.calls == []
    assert relationship.requests == []
