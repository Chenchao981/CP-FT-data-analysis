from __future__ import annotations

import pytest
from app.core.errors import DomainError
from app.domain.enrichments import CreateFieldEnrichmentRequest, FieldEnrichmentRecord
from app.infrastructure.sql_enrichment_service import (
    _assert_direct_lot_enrichment_allowed,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubEnrichmentService:
    def __init__(self) -> None:
        self.records: list[FieldEnrichmentRecord] = []

    def create(
        self, request: CreateFieldEnrichmentRequest, principal
    ) -> FieldEnrichmentRecord:
        assert request.entered_by == principal.user_id
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

    def list_current(
        self, import_batch_id: int, principal
    ) -> tuple[FieldEnrichmentRecord, ...]:
        return tuple(
            item for item in self.records if item.import_batch_id == import_batch_id
        )


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
            "entered_by": 999,
            "reason": "源目录确认晶圆厂",
        },
    )
    assert cp.status_code == 201
    assert cp.json()["test_stage"] == "CP"
    assert cp.json()["entered_by"] == 1

    cp_lot = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 7,
            "test_stage": "CP",
            "field_code": "LOT_ID",
            "action": "FILL",
            "value_text": " ｍａｎｕａｌ-lot ",
            "reason": "CP源数据缺少Lot，任务级补录",
        },
    )
    assert cp_lot.status_code == 201
    assert cp_lot.json()["value_text"] == "MANUAL-LOT"

    ft = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 8,
            "test_stage": "FT",
            "field_code": "PRODUCT_CODE",
            "action": "FILL",
            "value_text": "NCE-FT-PRODUCT",
            "reason": "人工确认产品型号",
        },
    )
    assert ft.status_code == 201


def test_lot_id_cannot_be_ignored_for_formal_import() -> None:
    client = client_with_service()
    response = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 8,
            "test_stage": "FT",
            "field_code": "LOT_ID",
            "action": "IGNORE",
            "reason": "该FT文件不提供且本次分析不使用Lot",
        },
    )
    assert response.status_code == 422


def test_optional_project_code_can_still_be_ignored() -> None:
    client = client_with_service()
    response = client.post(
        "/api/v1/enrichments",
        json={
            "import_batch_id": 8,
            "test_stage": "FT",
            "field_code": "PROJECT_CODE",
            "action": "IGNORE",
            "reason": "当前任务没有项目代码",
        },
    )
    assert response.status_code == 201
    assert response.json()["value_text"] is None


def test_required_stage_fields_cannot_be_ignored() -> None:
    client = client_with_service()
    for stage, field_code in (("CP", "SUPPLIER_CODE"), ("FT", "PRODUCT_CODE")):
        response = client.post(
            "/api/v1/enrichments",
            json={
                "import_batch_id": 8,
                "test_stage": stage,
                "field_code": field_code,
                "action": "IGNORE",
                "reason": "验证必填字段不可忽略",
            },
        )
        assert response.status_code == 422


def test_field_catalog_is_stage_specific() -> None:
    client = client_with_service()
    cp = client.get("/api/v1/enrichments/fields/CP")
    ft = client.get("/api/v1/enrichments/fields/FT")
    assert [item["field_code"] for item in cp.json()] == [
        "SUPPLIER_CODE",
        "PRODUCT_CODE",
        "LOT_ID",
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
    cp_lot = next(item for item in cp.json() if item["field_code"] == "LOT_ID")
    ft_lot = next(item for item in ft.json() if item["field_code"] == "LOT_ID")
    assert cp_lot["required_for_formal_import"] is True
    assert ft_lot["required_for_formal_import"] is True
    assert cp_lot["can_ignore"] is False
    assert ft_lot["can_ignore"] is False
    assert all("can_ignore" in item for item in cp.json())
    assert all("can_ignore" in item for item in ft.json())
    assert (
        next(item for item in cp.json() if item["field_code"] == "PRODUCT_CODE")[
            "can_ignore"
        ]
        is True
    )
    assert (
        next(item for item in ft.json() if item["field_code"] == "SUPPLIER_CODE")[
            "can_ignore"
        ]
        is True
    )


@pytest.mark.parametrize("batch_status", ("NEEDS_INPUT", "QUEUED", "PROCESSING"))
def test_direct_lot_enrichment_rejects_controlled_or_active_states(
    batch_status: str,
) -> None:
    expected_code = (
        "LOT_INPUT_RESOLUTION_REQUIRED"
        if batch_status == "NEEDS_INPUT"
        else "LOT_ENRICHMENT_BATCH_ACTIVE"
    )
    with pytest.raises(DomainError) as captured:
        _assert_direct_lot_enrichment_allowed(batch_status, has_open_lot_request=False)
    assert captured.value.code == expected_code


def test_direct_lot_enrichment_rejects_open_request_even_if_status_drifted() -> None:
    with pytest.raises(DomainError) as captured:
        _assert_direct_lot_enrichment_allowed("FAILED", has_open_lot_request=True)
    assert captured.value.code == "LOT_INPUT_RESOLUTION_REQUIRED"


@pytest.mark.parametrize("batch_status", ("RECEIVED", "FAILED", "PROCESSED"))
def test_direct_lot_enrichment_policy_allows_inactive_batch_without_open_request(
    batch_status: str,
) -> None:
    _assert_direct_lot_enrichment_allowed(batch_status, has_open_lot_request=False)
