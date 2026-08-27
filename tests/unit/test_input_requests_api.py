from __future__ import annotations

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

    def resolve(
        self, principal, business_domain, test_stage, import_batch_id, request
    ):
        assert principal.user_id == 1
        assert (business_domain, test_stage, import_batch_id) == (
            "ENGINEERING",
            "FT",
            41,
        )
        assert request.resolutions[0].input_request_id == 81
        assert request.resolutions[0].lot_id == "MANUAL-LOT"
        return LotResolutionResult(41, 74, "QUEUED")


def _client() -> TestClient:
    app = create_app()
    app.state.processing_input_request_service = StubInputRequestService()
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
            "resolutions": [
                {"input_request_id": 81, "lot_id": "MANUAL-LOT"}
            ],
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
            "resolutions": [
                {"input_request_id": 81, "lot_id": " ｍａｎｕａｌ-lot "}
            ],
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
