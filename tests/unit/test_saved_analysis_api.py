from __future__ import annotations

from dataclasses import replace

import pytest
from app.api.dependencies import current_principal
from app.api.saved_analyses import router
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    SavedAnalysisDatasetRecord,
    SavedAnalysisDatasetStatus,
    SavedAnalysisPage,
    SavedAnalysisRecord,
    SavedAnalysisRestoreStatus,
    SavedAnalysisRevisionRecord,
    SavedAnalysisRuleContext,
)
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from tests.unit.test_sql_saved_analysis_service import _empty_analysis_view_state


def _principal(
    user_id: int,
    *,
    admin: bool = False,
    permissions: frozenset[str] = frozenset({"DATASET_READ", "ANALYSIS_RUN"}),
) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name=f"User {user_id}",
        roles=("SYSTEM_ADMIN",) if admin else ("ENGINEER",),
        permissions=permissions,
    )


def _record(owner_user_id: int = 10) -> SavedAnalysisRecord:
    revision = SavedAnalysisRevisionRecord(
        saved_analysis_revision_id=101,
        revision_no=1,
        contract_version="ANALYTICS_CONTEXT_V1",
        filters={
            "lot_ids": [],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": [],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        parameters=(),
        filter_hash="1" * 64,
        context_hash="2" * 64,
        rule_context=SavedAnalysisRuleContext(),
        chart_config={},
        display_config={},
        datasets=(
            SavedAnalysisDatasetRecord(
                dataset_version_id=1001,
                dataset_id=1,
                version_no=1,
                ordinal_no=1,
                test_stage="FT",
                status=SavedAnalysisDatasetStatus.CURRENT,
            ),
        ),
        created_by_user_id=owner_user_id,
        created_at_utc="2026-08-31T01:00:00",
    )
    return SavedAnalysisRecord(
        saved_analysis_id=1,
        analysis_name="Saved view",
        owner_user_id=owner_user_id,
        lifecycle_status="ACTIVE",
        current_revision_no=1,
        row_version="0000000000000001",
        restore_status=SavedAnalysisRestoreStatus.CURRENT,
        revision=revision,
        created_at_utc="2026-08-31T01:00:00",
        updated_at_utc="2026-08-31T01:00:00",
    )


class _StubService:
    def __init__(self) -> None:
        self.record = _record()
        self.calls: list[tuple[str, object]] = []

    def create(self, request, principal):
        self.calls.append(("create", request))
        self.record = replace(
            self.record,
            owner_user_id=principal.user_id,
            analysis_name=request.analysis_name,
        )
        return self.record

    def list_page(self, principal, *, page, page_size, include_deleted=False):
        self.calls.append(
            ("list", (principal.user_id, page, page_size, include_deleted))
        )
        if include_deleted and "SYSTEM_ADMIN" not in principal.roles:
            raise DomainError(
                "SAVED_ANALYSIS_ADMIN_REQUIRED", "System Admin required", 403
            )
        return SavedAnalysisPage((self.record,), 1, page, page_size)

    def get(self, saved_analysis_id, principal, *, revision_no=None):
        self.calls.append(("get", (saved_analysis_id, principal.user_id, revision_no)))
        return self.record

    def create_revision(self, saved_analysis_id, request, principal):
        self.calls.append(("revision", request))
        self._manage(principal)
        return replace(
            self.record,
            current_revision_no=2,
            row_version="0000000000000002",
        )

    def delete(self, saved_analysis_id, request, principal):
        self.calls.append(("delete", request))
        self._manage(principal)
        return replace(self.record, lifecycle_status="DELETED")

    def _manage(self, principal: Principal) -> None:
        if (
            principal.user_id != self.record.owner_user_id
            and "SYSTEM_ADMIN" not in principal.roles
        ):
            raise DomainError(
                "SAVED_ANALYSIS_OWNER_REQUIRED", "Saved Analysis owner required", 403
            )


def _payload(dataset_count: int = 1) -> dict:
    return {
        "analysis_name": "Saved view",
        "change_reason": "Freeze the selected analytics context",
        "contract_version": "SAVED_ANALYSIS_V1",
        "datasets": [
            {"dataset_id": dataset_id, "version_no": 1}
            for dataset_id in range(1, dataset_count + 1)
        ],
        "filters": {
            "lot_ids": [],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": [],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        "parameters": [],
        "rule_context": {
            "spec_versions": [],
            "bin_mapping_versions": [],
            "evaluation_rule_versions": [],
        },
        "chart_config": {"analysis_view_state": _empty_analysis_view_state()},
        "display_config": {},
    }


def _client(
    principal: Principal,
    stub: _StubService | None = None,
) -> tuple[TestClient, _StubService]:
    application = FastAPI()
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(router, prefix="/api/v1")
    service = stub or _StubService()
    application.state.saved_analysis_service = service
    application.dependency_overrides[current_principal] = lambda: principal
    return TestClient(application), service


def test_api_create_list_get_revision_and_delete_contract() -> None:
    client, stub = _client(_principal(10))
    created = client.post("/api/v1/saved-analyses", json=_payload(8))
    assert created.status_code == 201, created.text
    assert created.json()["owner_user_id"] == 10
    assert len(stub.calls[0][1].datasets) == 8

    listed = client.get("/api/v1/saved-analyses", params={"page_size": 10})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    fetched = client.get("/api/v1/saved-analyses/1", params={"revision_no": 1})
    assert fetched.status_code == 200
    assert ("get", (1, 10, 1)) in stub.calls

    revision_payload = _payload()
    revision_payload.pop("analysis_name")
    revision_payload["expected_row_version"] = "0000000000000001"
    revision_payload["analysis_name"] = "Saved view revision"
    revised = client.post("/api/v1/saved-analyses/1/revisions", json=revision_payload)
    assert revised.status_code == 201
    assert revised.json()["current_revision_no"] == 2

    deleted = client.request(
        "DELETE",
        "/api/v1/saved-analyses/1",
        json={
            "expected_row_version": "0000000000000001",
            "reason": "Retire obsolete saved analysis configuration",
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["lifecycle_status"] == "DELETED"


def test_api_write_requires_analysis_run_and_dataset_read() -> None:
    read_only, _ = _client(_principal(10, permissions=frozenset({"DATASET_READ"})))
    missing_run = read_only.post("/api/v1/saved-analyses", json=_payload())
    assert missing_run.status_code == 403
    assert missing_run.json()["error"]["code"] == "PERMISSION_DENIED"

    run_only, _ = _client(_principal(10, permissions=frozenset({"ANALYSIS_RUN"})))
    missing_read = run_only.post("/api/v1/saved-analyses", json=_payload())
    assert missing_read.status_code == 403
    assert missing_read.json()["error"]["code"] == "PERMISSION_DENIED"


def test_cross_owner_can_read_shared_record_but_cannot_change_or_delete() -> None:
    client, _ = _client(_principal(20))
    assert client.get("/api/v1/saved-analyses/1").status_code == 200

    revision_payload = _payload()
    revision_payload.pop("analysis_name")
    revision_payload["expected_row_version"] = "0000000000000001"
    revision_payload["analysis_name"] = "Cross owner revision"
    revised = client.post("/api/v1/saved-analyses/1/revisions", json=revision_payload)
    assert revised.status_code == 403
    assert revised.json()["error"]["code"] == "SAVED_ANALYSIS_OWNER_REQUIRED"

    deleted = client.request(
        "DELETE",
        "/api/v1/saved-analyses/1",
        json={
            "expected_row_version": "0000000000000001",
            "reason": "Cross-owner delete must remain forbidden",
        },
    )
    assert deleted.status_code == 403
    assert deleted.json()["error"]["code"] == "SAVED_ANALYSIS_OWNER_REQUIRED"


def test_system_admin_can_maintain_cross_owner_and_list_deleted() -> None:
    client, _ = _client(_principal(1, admin=True))
    revision_payload = _payload()
    revision_payload.pop("analysis_name")
    revision_payload["expected_row_version"] = "0000000000000001"
    revised = client.post("/api/v1/saved-analyses/1/revisions", json=revision_payload)
    assert revised.status_code == 201
    assert (
        client.get(
            "/api/v1/saved-analyses", params={"include_deleted": True}
        ).status_code
        == 200
    )


def test_api_rejects_nine_datasets_and_unbounded_or_unknown_config() -> None:
    client, _ = _client(_principal(10))
    too_many = client.post("/api/v1/saved-analyses", json=_payload(9))
    assert too_many.status_code == 422

    unknown = _payload()
    unknown["client_context_hash"] = "0" * 64
    response = client.post("/api/v1/saved-analyses", json=unknown)
    assert response.status_code == 422

    oversized = _payload()
    oversized["display_config"] = {"title": "x" * 4_001}
    response = client.post("/api/v1/saved-analyses", json=oversized)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"contract_version": "ANALYTICS_CONTEXT_V1"}),
        lambda payload: payload.update({"chart_config": {}}),
        lambda payload: payload["chart_config"].update({"analysis_view_state": []}),
        lambda payload: payload["chart_config"]["analysis_view_state"].update(
            {"contract_version": "ANALYSIS_VIEW_STATE_V0"}
        ),
    ],
)
def test_api_rejects_incomplete_or_stale_saved_view_state(mutation) -> None:
    client, stub = _client(_principal(10))
    payload = _payload()
    mutation(payload)

    response = client.post("/api/v1/saved-analyses", json=payload)

    assert response.status_code == 422
    assert stub.calls == []
