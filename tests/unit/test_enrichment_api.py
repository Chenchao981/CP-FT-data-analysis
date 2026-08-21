from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.enrichments import CreateFieldEnrichmentRequest, FieldEnrichmentRecord
from app.main import create_app


class StubEnrichmentService:
    def __init__(self) -> None:
        self.records: list[FieldEnrichmentRecord] = []

    def create(self, request: CreateFieldEnrichmentRequest) -> FieldEnrichmentRecord:
        record = FieldEnrichmentRecord(
            len(self.records) + 1,
            request.import_batch_id,
            request.source_file_id,
            request.test_stage.value,
            request.field_code,
            request.action.value,
            request.value_text,
            request.entered_by,
            request.reason,
            True,
        )
        self.records.append(record)
        return record

    def list_current(self, import_batch_id: int) -> tuple[FieldEnrichmentRecord, ...]:
        return tuple(item for item in self.records if item.import_batch_id == import_batch_id)


def client_with_service() -> TestClient:
    app = create_app()
    app.state.field_enrichment_service = StubEnrichmentService()
    return TestClient(app)


def test_cp_and_ft_use_separate_approved_manual_fields() -> None:
    client = client_with_service()
    cp = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 7,
            "test_stage": "CP",
            "field_code": "SUPPLIER_CODE",
            "action": "FILL",
            "value_text": "HUAHONG",
            "entered_by": 9,
            "reason": "源目录确认晶圆厂",
        },
    )
    assert cp.status_code == 201
    assert cp.json()["test_stage"] == "CP"

    invalid_cp_lot = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 7,
            "test_stage": "CP",
            "field_code": "LOT_ID",
            "action": "FILL",
            "value_text": "SHOULD-COME-FROM-CP-CLEANER",
            "entered_by": 9,
            "reason": "invalid",
        },
    )
    assert invalid_cp_lot.status_code == 422

    ft = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 8,
            "test_stage": "FT",
            "field_code": "PRODUCT_CODE",
            "action": "FILL",
            "value_text": "NCE-FT-PRODUCT",
            "entered_by": 9,
            "reason": "人工确认产品型号",
        },
    )
    assert ft.status_code == 201


def test_ignore_requires_no_value_and_keeps_reason() -> None:
    client = client_with_service()
    response = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 8,
            "test_stage": "FT",
            "field_code": "LOT_ID",
            "action": "IGNORE",
            "entered_by": 9,
            "reason": "该FT文件不提供且本次分析不使用Lot",
        },
    )
    assert response.status_code == 201
    assert response.json()["value_text"] is None


def test_field_catalog_is_stage_specific() -> None:
    client = client_with_service()
    cp = client.get("/api/v1/enrichments/fields/CP")
    ft = client.get("/api/v1/enrichments/fields/FT")
    assert [item["field_code"] for item in cp.json()] == [
        "SUPPLIER_CODE",
        "PRODUCT_CODE",
        "PROJECT_CODE",
    ]
    assert [item["field_code"] for item in ft.json()] == [
        "PRODUCT_CODE",
        "SUPPLIER_CODE",
        "LOT_ID",
        "PROJECT_CODE",
    ]
    assert cp.json()[0]["required_for_analysis"] is True
    assert ft.json()[0]["required_for_analysis"] is True
