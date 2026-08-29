from __future__ import annotations

from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.master_data import ProductCrosswalk, ProductCrosswalkPage
from app.main import create_app
from fastapi.testclient import TestClient


def _record(*, status: str = "PENDING", key: str | None = None) -> ProductCrosswalk:
    return ProductCrosswalk(
        crosswalk_id=8,
        supplier_id=3,
        supplier_code="RIYUEXIN",
        supplier_name="日月新",
        test_stage="FT",
        raw_product_code="NCEAP40PT15D(M)-2B00",
        product_id=9,
        tms_product_code="NCEAP40PT15D(M)-2B00",
        identity_class=(
            "ENTERPRISE_MAPPED" if status == "APPROVED" else "SOURCE_OBSERVED"
        ),
        enterprise_system="SAP_B1",
        enterprise_key=key,
        status=status,
        first_observed_at_utc="2026-08-25T00:00:00.000Z",
        last_observed_at_utc="2026-08-29T00:00:00.000Z",
        approved_by_login="admin" if status == "APPROVED" else None,
        approved_at_utc=(
            "2026-08-29T01:00:00.000Z" if status == "APPROVED" else None
        ),
        decision_reason="业务确认" if status == "APPROVED" else None,
    )


class StubMasterDataService:
    def list_product_crosswalks(self, **kwargs) -> ProductCrosswalkPage:
        return ProductCrosswalkPage((_record(),), 1, kwargs["page"], kwargs["page_size"])

    def approve_product_crosswalk(self, crosswalk_id, request, principal):
        assert crosswalk_id == 8
        assert principal.login_name == "governor"
        return _record(status="APPROVED", key=request.enterprise_key)

    def reject_product_crosswalk(self, crosswalk_id, request, principal):
        assert request.reason
        return _record(status="REJECTED")


def _client(*permissions: str) -> TestClient:
    app = create_app()
    app.state.master_data_service = StubMasterDataService()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=7,
        login_name="governor",
        display_name="主数据管理员",
        roles=("DATA_ADMIN",),
        permissions=frozenset(permissions),
    )
    return TestClient(app)


def test_manager_can_read_source_identity_without_sap_claim() -> None:
    response = _client("MANAGEMENT_READ").get(
        "/api/v1/master-data/product-crosswalks"
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["identity_class"] == "SOURCE_OBSERVED"
    assert item["enterprise_key"] is None
    assert item["status"] == "PENDING"


def test_rule_governor_can_approve_explicit_sap_mapping() -> None:
    response = _client("RULE_GOVERN").post(
        "/api/v1/master-data/product-crosswalks/8/approve",
        json={
            "enterprise_system": "SAP_B1",
            "enterprise_key": "NCE-MAT-0009",
            "reason": "SAP物料主数据Owner确认",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    assert response.json()["enterprise_key"] == "NCE-MAT-0009"


def test_management_reader_cannot_approve_mapping() -> None:
    response = _client("MANAGEMENT_READ").post(
        "/api/v1/master-data/product-crosswalks/8/approve",
        json={
            "enterprise_system": "SAP_B1",
            "enterprise_key": "NCE-MAT-0009",
            "reason": "仅查看角色不能审批",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
