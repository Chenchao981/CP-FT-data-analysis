from __future__ import annotations

from dataclasses import asdict

import pytest
from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.datasets import (
    BinCountPoint,
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetChartData,
    DatasetComparisonItem,
    DatasetComparisonRequest,
    DatasetComparisonResult,
    DatasetDetailMeasurement,
    DatasetDetailPage,
    DatasetDetailRow,
    DatasetParameterStatistic,
    DatasetRecord,
    DatasetResultSummary,
    DatasetVersionRecord,
    DqGateResult,
    PublishDatasetVersionRequest,
    WaferMapPoint,
    WaferOption,
    WaferYieldPoint,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubDatasetService:
    def __init__(self) -> None:
        self.access_calls: list[tuple[int, Principal, str, int | None]] = []
        self.comparison_request: DatasetComparisonRequest | None = None
        self.detail_request: dict[str, object] | None = None
        self.gate_principal: Principal | None = None
        self.summary_principal: Principal | None = None

    def list_datasets(self, principal) -> tuple[DatasetRecord, ...]:
        return ()

    def assert_dataset_access(
        self,
        dataset_id: int,
        principal,
        mode: str = "READ",
        *,
        version_no: int | None = None,
    ) -> None:
        self.access_calls.append((dataset_id, principal, mode, version_no))

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

    def evaluate_gate(
        self,
        dataset_id: int,
        version_no: int,
        principal: Principal,
    ) -> DqGateResult:
        self.gate_principal = principal
        return DqGateResult(dataset_id, version_no, "PASS", 1, 10, 110, ())

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord:
        assert request.published_by == 1
        return DatasetVersionRecord(2, dataset_id, version_no, 3, "1.0", "PUBLISHED", True, 1)

    def get_summary(
        self,
        dataset_id: int,
        version_no: int,
        principal: Principal,
    ) -> DatasetResultSummary:
        self.summary_principal = principal
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
        source_id: str | None = None,
        parameter: str | None = None,
    ) -> DatasetChartData:
        return DatasetChartData(
            dataset_id,
            version_no,
            "CP",
            "NCE-TEST",
            lot_id,
            wafer_id,
            source_id,
            parameter,
            ("LOT-1",),
            (WaferOption("LOT-1", "001"),),
            (),
            (),
            (
                WaferYieldPoint(
                    lot_id="LOT-1",
                    wafer_id="001",
                    unit_count=12,
                    pass_count=9,
                    fail_count=1,
                    unknown_count=1,
                    abort_count=1,
                    known_yield_denominator=10,
                    yield_rate=0.9,
                ),
            ),
            (BinCountPoint("1", 9, 0.9), BinCountPoint("7", 1, 0.1)),
            (WaferMapPoint(1, 2, "1", "PASS"),) if wafer_id else (),
            (),
            0,
            False,
        )

    def compare(self, request: DatasetComparisonRequest) -> DatasetComparisonResult:
        self.comparison_request = request
        return DatasetComparisonResult(
            test_stage="CP",
            spec_compatibility=(
                "SINGLE_DATASET" if len(request.datasets) == 1 else "COMPATIBLE"
            ),
            lot_ids=tuple(request.lot_ids),
            wafer_ids=tuple(request.wafer_ids),
            bin_codes=tuple(request.bin_codes),
            parameters=tuple(request.parameters),
            items=tuple(
                DatasetComparisonItem(
                    dataset_id=reference.dataset_id,
                    version_no=reference.version_no,
                    test_stage="CP",
                    product_name="NCE-TEST",
                    unit_count=12,
                    pass_count=9,
                    fail_count=1,
                    unknown_count=1,
                    abort_count=1,
                    known_yield_denominator=10,
                    yield_rate=0.9,
                    parameter_statistics=(
                        DatasetParameterStatistic(
                            name="P1",
                            unit="V",
                            lsl=0.0,
                            usl=5.0,
                            test_condition="VGS=0V",
                            measured_count=11,
                            missing_count=1,
                            minimum=0.1,
                            maximum=4.9,
                            average=2.5,
                        ),
                    ),
                )
                for reference in request.datasets
            ),
        )

    def get_detail_page(
        self,
        dataset_id: int,
        version_no: int,
        *,
        page: int,
        page_size: int,
        lot_ids: tuple[str, ...] = (),
        wafer_ids: tuple[str, ...] = (),
        bin_codes: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> DatasetDetailPage:
        self.detail_request = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "page": page,
            "page_size": page_size,
            "lot_ids": lot_ids,
            "wafer_ids": wafer_ids,
            "bin_codes": bin_codes,
            "parameters": parameters,
        }
        return DatasetDetailPage(
            dataset_id=dataset_id,
            version_no=version_no,
            test_stage="CP",
            page=page,
            page_size=page_size,
            total=3,
            lot_options=("LOT-1",),
            wafer_options=("01",),
            bin_options=("1",),
            parameter_options=("P1",),
            items=(
                DatasetDetailRow(
                    unit_id=101,
                    logical_unit_key="CP:SOURCE-GROUP:01:1:2:1",
                    lot_id=None,
                    wafer_id="01",
                    x=1,
                    y=2,
                    soft_bin="1",
                    hard_bin=None,
                    overall_result="UNKNOWN",
                    source_row_no=2,
                    measurements=(
                        DatasetDetailMeasurement(
                            parameter="P1",
                            value_numeric=None,
                            value_text=None,
                            status="MISSING",
                            unit="V",
                            lsl=0.0,
                            usl=5.0,
                        ),
                    ),
                ),
            ),
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
    expected_gate = asdict(
        StubDatasetService().evaluate_gate(
            1,
            1,
            Principal(
                1,
                "system.admin",
                "System Admin",
                ("SYSTEM_ADMIN",),
                frozenset({"DATASET_READ"}),
            ),
        )
    )
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
    assert charts.json()["wafer_yield"][0] == {
        "lot_id": "LOT-1",
        "wafer_id": "001",
        "unit_count": 12,
        "pass_count": 9,
        "fail_count": 1,
        "unknown_count": 1,
        "abort_count": 1,
        "known_yield_denominator": 10,
        "yield_rate": 0.9,
    }
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


def test_manager_version_reads_are_authorized_against_each_requested_version() -> None:
    app = create_app()
    stub = StubDatasetService()
    manager = Principal(
        8,
        "manager.viewer",
        "Manager Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )
    app.state.dataset_service = stub
    app.dependency_overrides[current_principal] = lambda: manager
    client = TestClient(app)

    comparison = client.post(
        "/api/v1/datasets/compare",
        json={
            "datasets": [
                {"dataset_id": 11, "version_no": 2},
                {"dataset_id": 12, "version_no": 1},
            ],
            "lot_ids": ["LOT-1"],
            "parameters": ["P1"],
        },
    )
    details = client.get(
        "/api/v1/datasets/11/versions/2/details",
        params=[
            ("page", "2"),
            ("page_size", "2"),
            ("lot_id", " LOT-1 "),
            ("wafer_id", "01"),
            ("bin_code", "1"),
            ("parameter", "P1"),
        ],
    )
    gate = client.get("/api/v1/datasets/11/versions/3/gate")
    summary = client.get("/api/v1/datasets/11/versions/4/summary")
    charts = client.get("/api/v1/datasets/11/versions/5/charts")

    assert comparison.status_code == 200
    assert comparison.json()["items"][0]["known_yield_denominator"] == 10
    assert details.status_code == 200
    assert details.json()["items"][0]["lot_id"] is None
    assert details.json()["items"][0]["measurements"][0]["value_numeric"] is None
    assert gate.status_code == 200
    assert summary.status_code == 200
    assert charts.status_code == 200
    assert [(call[0], call[3]) for call in stub.access_calls] == [
        (11, 2),
        (12, 1),
        (11, 2),
        (11, 3),
        (11, 4),
        (11, 5),
    ]
    assert all(call[1] == manager and call[2] == "READ" for call in stub.access_calls)
    assert stub.gate_principal == manager
    assert stub.summary_principal == manager
    assert stub.detail_request == {
        "dataset_id": 11,
        "version_no": 2,
        "page": 2,
        "page_size": 2,
        "lot_ids": ("LOT-1",),
        "wafer_ids": ("01",),
        "bin_codes": ("1",),
        "parameters": ("P1",),
    }


def test_compare_and_details_require_dataset_read_permission() -> None:
    app = create_app()
    app.state.dataset_service = StubDatasetService()
    app.dependency_overrides[current_principal] = lambda: Principal(
        9,
        "quick.only",
        "Quick Only",
        ("QUICK_ANALYST",),
        frozenset({"ANALYSIS_RUN"}),
    )
    client = TestClient(app)

    comparison = client.post(
        "/api/v1/datasets/compare",
        json={"datasets": [{"dataset_id": 1, "version_no": 1}]},
    )
    details = client.get("/api/v1/datasets/1/versions/1/details")

    assert comparison.status_code == 403
    assert details.status_code == 403
    assert comparison.json()["error"]["code"] == "PERMISSION_DENIED"
    assert details.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    "datasets",
    [
        [],
        [{"dataset_id": index, "version_no": 1} for index in range(1, 10)],
        [
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 1, "version_no": 2},
        ],
    ],
    ids=("empty", "more-than-eight", "same-dataset-twice"),
)
def test_dataset_compare_enforces_one_to_eight_unique_datasets(datasets) -> None:
    response = client_with_service().post(
        "/api/v1/datasets/compare", json={"datasets": datasets}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("count", (1, 8))
def test_dataset_compare_accepts_selection_boundaries(count: int) -> None:
    response = client_with_service().post(
        "/api/v1/datasets/compare",
        json={
            "datasets": [
                {"dataset_id": index, "version_no": 1}
                for index in range(1, count + 1)
            ]
        },
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == count


@pytest.mark.parametrize(
    ("params", "expected_code"),
    [
        (
            [("parameter", f"P{index}") for index in range(21)],
            "ANALYSIS_FILTER_LIMIT_EXCEEDED",
        ),
        (
            [("lot_id", f"LOT-{index}") for index in range(51)],
            "ANALYSIS_FILTER_LIMIT_EXCEEDED",
        ),
        (
            [("parameter", "P1"), ("parameter", " P1 ")],
            "ANALYSIS_FILTER_INVALID",
        ),
    ],
    ids=("parameters", "lots", "duplicate-after-trim"),
)
def test_dataset_details_rejects_unbounded_or_ambiguous_filters(
    params, expected_code: str
) -> None:
    response = client_with_service().get(
        "/api/v1/datasets/1/versions/1/details", params=params
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.parametrize(
    "params",
    ({"page": 0}, {"page_size": 0}, {"page_size": 201}),
)
def test_dataset_details_enforces_page_boundaries(params) -> None:
    response = client_with_service().get(
        "/api/v1/datasets/1/versions/1/details", params=params
    )

    assert response.status_code == 422
