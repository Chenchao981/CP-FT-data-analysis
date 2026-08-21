from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.unit.test_huahong_dcp import source_text


def test_huahong_inspection_returns_identity_schema_and_quality() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/cleaners/huahong/inspect",
        files={
            "file": (
                "FA00-0001-000A-260820@203_001.TXT",
                source_text().encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["business_lot_id"] == "FA00-0001"
    assert body["identity"]["lot_number"] == "FA00-0001-000A-260820@203"
    assert body["quality"] == {
        "status": "PASS",
        "row_count": 1,
        "pass_bin": 1,
        "pass_count": 1,
        "yield_rate": 1.0,
        "bin_counts": {"1": 1},
    }


def test_huahong_inspection_rejects_unknown_schema() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/cleaners/huahong/inspect",
        files={
            "file": (
                "FA00-0001-000A-260820@203_001.TXT",
                source_text(parameters="UNKNOWN").encode("utf-8"),
                "text/plain",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "HUAHONG_FORMAT_INVALID"
