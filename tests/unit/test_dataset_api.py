from __future__ import annotations

from dataclasses import asdict

from fastapi.testclient import TestClient

from app.domain.datasets import (
    BinCountPoint,
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetRecord,
    DatasetChartData,
    DatasetResultSummary,
    DatasetVersionRecord,
    DqGateResult,
    PublishDatasetVersionRequest,
    WaferMapPoint,
    WaferOption,
    WaferYieldPoint,
)
from app.main import create_app


class StubDatasetService:
    def list_datasets(self, principal) -> tuple[DatasetRecord, ...]:
        return ()

    def assert_dataset_access(self, dataset_id: int, principal, mode: str = "READ") -> None:
        return None

    def create_dataset(self, request: CreateDatasetRequest) -> DatasetRecord:
        return DatasetRecord(
            1,
            request.dataset_code,
            request.dataset_name,
            request.dataset_type.value,
            request.test_stage.value,
            request.supplier_id,
            request.product_id,
            request.owner_user_id,
        )

    def create_version(
        self, dataset_id: int, request: CreateDatasetVersionRequest
    ) -> DatasetVersionRecord:
        return DatasetVersionRecord(
            2,
            dataset_id,
            1,
            request.input_batch_id,
            request.canonical_model_version,
            "VALIDATING",
            False,
            len(request.processing_run_ids),
        )

    def evaluate_gate(self, dataset_id: int, version_no: int) -> DqGateResult:
        return DqGateResult(dataset_id, version_no, "PASS", 1, 10, 110, ())

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord:
        assert request.published_by == 9
        return DatasetVersionRecord(2, dataset_id, version_no, 3, "1.0", "PUBLISHED", True, 1)

    def get_summary(self, dataset_id: int, version_no: int) -> DatasetResultSummary:
        return DatasetResultSummary(
            dataset_id,
            "HH_CP",
            "HuaHong CP",
            version_no,
            "PUBLISHED",
            True,
            1,
            1,
            1,
            10,
            9,
            1,
            0.9,
            110,
            {"1": 9, "7": 1},
        )

    def get_chart_data(
        self,
        dataset_id: int,
        version_no: int,
        lot_id: str | None = None,
        wafer_id: str | None = None,
    ) -> DatasetChartData:
        return DatasetChartData(
            dataset_id,
            version_no,
            lot_id,
            wafer_id,
            ("LOT-1",),
            (WaferOption("LOT-1", "001"),),
            (WaferYieldPoint("LOT-1", "001", 10, 9, 1, 0.9),),
            (BinCountPoint("1", 9, 0.9), BinCountPoint("7", 1, 0.1)),
            (WaferMapPoint(1, 2, "1", "PASS"),) if wafer_id else (),
        )


def client_with_service() -> TestClient:
    app = create_app()
    app.state.dataset_service = StubDatasetService()
    return TestClient(app)


def test_dataset_endpoints_cover_create_gate_publish_and_summary() -> None:
    client = client_with_service()
    created = client.post(
        "/api/v1/datasets",
        json={
            "dataset_code": "HH_CP",
            "dataset_name": "HuaHong CP",
            "dataset_type": "CP_DETAIL",
            "test_stage": "CP",
            "supplier_id": 2,
            "product_id": 3,
            "owner_user_id": 9,
        },
    )
    assert created.status_code == 201
    version = client.post(
        "/api/v1/datasets/1/versions",
        json={"input_batch_id": 3, "processing_run_ids": [7]},
    )
    assert version.status_code == 201
    assert version.json()["status"] == "VALIDATING"
    gate = client.get("/api/v1/datasets/1/versions/1/gate")
    expected_gate = asdict(StubDatasetService().evaluate_gate(1, 1))
    expected_gate["reasons"] = []
    assert gate.json() == expected_gate
    published = client.post(
        "/api/v1/datasets/1/versions/1/publish", json={"published_by": 9}
    )
    assert published.json()["is_current"] is True
    summary = client.get("/api/v1/datasets/1/versions/1/summary")
    assert summary.json()["bin_counts"] == {"1": 9, "7": 1}
    charts = client.get(
        "/api/v1/datasets/1/versions/1/charts",
        params={"lot_id": "LOT-1", "wafer_id": "001"},
    )
    assert charts.json()["wafer_yield"][0]["yield_rate"] == 0.9
    assert charts.json()["wafer_map"][0] == {
        "x": 1,
        "y": 2,
        "soft_bin": "1",
        "result": "PASS",
    }


def test_dataset_api_fails_closed_without_database_configuration() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/datasets/1/versions/1/gate")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"


def test_dataset_version_rejects_duplicate_processing_runs() -> None:
    client = client_with_service()
    response = client.post(
        "/api/v1/datasets/1/versions",
        json={"input_batch_id": 3, "processing_run_ids": [7, 7]},
    )
    assert response.status_code == 422


def test_cp_dataset_allows_missing_product_but_ft_requires_it() -> None:
    client = client_with_service()
    cp_response = client.post(
        "/api/v1/datasets",
        json={
            "dataset_code": "CP_LOT_ONLY",
            "dataset_name": "CP lot scoped",
            "dataset_type": "CP_DETAIL",
            "test_stage": "CP",
            "supplier_id": 2,
            "owner_user_id": 9,
        },
    )
    assert cp_response.status_code == 201
    assert cp_response.json()["product_id"] is None

    ft_response = client.post(
        "/api/v1/datasets",
        json={
            "dataset_code": "FT_NO_PRODUCT",
            "dataset_name": "invalid FT",
            "dataset_type": "FT_DETAIL",
            "test_stage": "FT",
            "owner_user_id": 9,
        },
    )
    assert ft_response.status_code == 422

    cp_without_source = client.post(
        "/api/v1/datasets",
        json={
            "dataset_code": "CP_NO_SOURCE",
            "dataset_name": "invalid CP",
            "dataset_type": "CP_DETAIL",
            "test_stage": "CP",
            "owner_user_id": 9,
        },
    )
    assert cp_without_source.status_code == 422
