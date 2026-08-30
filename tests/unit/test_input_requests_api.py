from __future__ import annotations

import pytest
from app.api.dependencies import current_principal
from app.core.errors import DomainError
from app.domain.auth import ALL_PERMISSIONS, Principal
from app.domain.input_requests import (
    LotResolutionResult,
    ProcessingInputRequestFile,
    ProcessingInputRequestSummary,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubInputRequestService:
    def list_open(self, principal, business_domain, test_stage, import_batch_id):
        assert principal.user_id == 1
        assert (business_domain, test_stage, import_batch_id) == (
            "ENGINEERING",
            "FT",
            41,
        )
        return ProcessingInputRequestSummary(
            import_batch_id=41,
            status="NEEDS_INPUT",
            field_code="LOT_ID",
            prompt="请确认批次号",
            latest_job_id=73,
            requests=(
                ProcessingInputRequestFile(
                    input_request_id=81,
                    source_file_id=7001,
                    original_file_name="missing-lot.xlsx",
                ),
            ),
        )

    def resolve(self, principal, business_domain, test_stage, import_batch_id, request):
        assert principal.user_id == 1
        assert (business_domain, test_stage, import_batch_id) == (
            "ENGINEERING",
            "FT",
            41,
        )
        assert request.resolutions[0].input_request_id == 81
        assert request.resolutions[0].lot_id == "MANUAL-LOT"
        return LotResolutionResult(41, 74, "QUEUED")


class VisibilityStubInputRequestService(StubInputRequestService):
    @staticmethod
    def _assert_owner_or_admin(principal: Principal) -> None:
        if principal.user_id != 7 and "SYSTEM_ADMIN" not in principal.roles:
            raise DomainError("IMPORT_BATCH_NOT_FOUND", "上传任务不存在或无权访问", 404)

    def list_open(self, principal, business_domain, test_stage, import_batch_id):
        self._assert_owner_or_admin(principal)
        return ProcessingInputRequestSummary(
            import_batch_id=41,
            status="NEEDS_INPUT",
            field_code="LOT_ID",
            prompt="private prompt",
            latest_job_id=73,
            requests=(
                ProcessingInputRequestFile(
                    input_request_id=81,
                    source_file_id=7001,
                    original_file_name="private-file.xlsx",
                ),
            ),
        )

    def resolve(self, principal, business_domain, test_stage, import_batch_id, request):
        self._assert_owner_or_admin(principal)
        return LotResolutionResult(41, 74, "QUEUED")


def _principal(user_id: int, *, admin: bool = False) -> Principal:
    return Principal(
        user_id,
        f"user-{user_id}",
        f"User {user_id}",
        ("SYSTEM_ADMIN",) if admin else ("ENGINEER",),
        ALL_PERMISSIONS if admin else frozenset({"DATASET_READ", "TASK_CREATE"}),
    )


def _client(
    *,
    principal: Principal | None = None,
    input_request_service=None,
) -> TestClient:
    app = create_app()
    app.state.processing_input_request_service = (
        input_request_service or StubInputRequestService()
    )
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def test_lists_open_lot_requests_with_frozen_fields() -> None:
    response = _client().get("/api/v1/engineering/ft/uploads/41/input-requests")
    assert response.status_code == 200
    assert response.json() == {
        "import_batch_id": 41,
        "status": "NEEDS_INPUT",
        "field_code": "LOT_ID",
        "prompt": "请确认批次号",
        "latest_job_id": 73,
        "requests": [
            {
                "input_request_id": 81,
                "source_file_id": 7001,
                "original_file_name": "missing-lot.xlsx",
                "current_value": None,
            }
        ],
    }


def test_resolves_all_requests_and_returns_child_job() -> None:
    response = _client().post(
        "/api/v1/engineering/ft/uploads/41/input-requests/resolve",
        json={
            "resolutions": [{"input_request_id": 81, "lot_id": "MANUAL-LOT"}],
            "reason": "根据客户原始记录确认",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "import_batch_id": 41,
        "job_id": 74,
        "status": "QUEUED",
    }


def test_resolution_normalizes_lot_before_service_call() -> None:
    response = _client().post(
        "/api/v1/engineering/ft/uploads/41/input-requests/resolve",
        json={
            "resolutions": [{"input_request_id": 81, "lot_id": " ｍａｎｕａｌ-lot "}],
            "reason": "根据客户原始记录确认",
        },
    )
    assert response.status_code == 200


def test_resolution_rejects_whitespace_and_unsupported_punctuation() -> None:
    for lot_id in ("LOT WITH SPACE", "LOT@001", "-LOT-001"):
        response = _client().post(
            "/api/v1/engineering/ft/uploads/41/input-requests/resolve",
            json={
                "resolutions": [{"input_request_id": 81, "lot_id": lot_id}],
                "reason": "格式校验",
            },
        )
        assert response.status_code == 422


def test_resolution_rejects_duplicate_request_ids() -> None:
    response = _client().post(
        "/api/v1/engineering/ft/uploads/41/input-requests/resolve",
        json={
            "resolutions": [
                {"input_request_id": 81, "lot_id": "LOT-A"},
                {"input_request_id": 81, "lot_id": "LOT-A"},
            ],
            "reason": "重复请求",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("principal", (_principal(7), _principal(1, admin=True)))
def test_owner_and_admin_can_read_and_resolve_input_requests(principal) -> None:
    client = _client(
        principal=principal,
        input_request_service=VisibilityStubInputRequestService(),
    )

    read_response = client.get("/api/v1/production/ft/uploads/41/input-requests")
    resolve_response = client.post(
        "/api/v1/production/ft/uploads/41/input-requests/resolve",
        json={
            "resolutions": [{"input_request_id": 81, "lot_id": "LOT-001"}],
            "reason": "owner boundary regression",
        },
    )

    assert read_response.status_code == 200
    assert read_response.json()["requests"][0]["source_file_id"] == 7001
    assert resolve_response.status_code == 200


@pytest.mark.parametrize("business_domain", ("engineering", "production"))
@pytest.mark.parametrize("method", ("read", "resolve"))
def test_non_owner_input_request_api_returns_404_without_private_details(
    business_domain: str, method: str
) -> None:
    client = _client(
        principal=_principal(8),
        input_request_service=VisibilityStubInputRequestService(),
    )
    path = f"/api/v1/{business_domain}/ft/uploads/41/input-requests"

    if method == "read":
        response = client.get(path)
    else:
        response = client.post(
            path + "/resolve",
            json={
                "resolutions": [{"input_request_id": 81, "lot_id": "LOT-001"}],
                "reason": "non-owner boundary regression",
            },
        )

    assert response.status_code == 404
    body = response.text
    assert "private prompt" not in body
    assert "private-file.xlsx" not in body
    assert "7001" not in body
