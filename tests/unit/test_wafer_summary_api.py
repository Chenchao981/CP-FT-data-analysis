from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsDatasetContext,
    AnalyticsResolvedDataset,
    AnalyticsRuleContext,
)
from app.domain.wafer_summary import (
    WaferParameterSummary,
    WaferSummaryDrilldownContext,
    WaferSummaryRequest,
    WaferSummaryResult,
    WaferSummaryRow,
)
from app.infrastructure.sql_analytics_service import _hashes
from app.infrastructure.sql_wafer_summary_service import SqlWaferSummaryService
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "datasets": [{"dataset_id": 11, "version_no": 2}],
        "filters": {"lot_ids": ["L1"]},
        "parameters": ["VTH1"],
        "page": 1,
        "page_size": 50,
        "sort_by": "YIELD",
        "sort_direction": "DESC",
    }
    value.update(overrides)
    return value


def _row(wafer: str, count: int, yield_rate: float | None) -> WaferSummaryRow:
    known = count if yield_rate is not None else 0
    passed = round((yield_rate or 0) * known)
    return WaferSummaryRow(
        11,
        2,
        "L1",
        wafer,
        count,
        passed,
        known - passed,
        count - known,
        0,
        known,
        yield_rate,
        (),
    )


def test_wafer_summary_request_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        WaferSummaryRequest.model_validate(_payload(page_size=201))
    with pytest.raises(ValidationError):
        WaferSummaryRequest.model_validate(_payload(sort_by="RAW_SQL"))


def test_wafer_summary_sort_keeps_unknown_yield_last() -> None:
    request = WaferSummaryRequest.model_validate(_payload())
    rows = [_row("W1", 10, 0.8), _row("W2", 10, None), _row("W3", 10, 0.9)]
    ordered = SqlWaferSummaryService._sort_rows(rows, request)
    assert [item.wafer_id for item in ordered] == ["W3", "W1", "W2"]


def test_wafer_summary_sql_order_is_whitelisted_and_stable() -> None:
    request = WaferSummaryRequest.model_validate(_payload())
    assert SqlWaferSummaryService._order_by_sql(request) == (
        "CASE WHEN yield_rate IS NULL THEN 1 ELSE 0 END ASC,"
        "yield_rate DESC,dataset_id ASC,version_no ASC,lot_id ASC,wafer_id ASC"
    )


def test_wafer_summary_page_cte_is_one_bounded_values_set() -> None:
    sql, parameters = SqlWaferSummaryService._page_wafers_cte(
        (
            {"dataset_id": 11, "version_no": 2, "lot_id": "L1", "wafer_id": "W1"},
            {"dataset_id": 12, "version_no": 3, "lot_id": "L2", "wafer_id": "W2"},
        )
    )

    assert "page_wafers(dataset_id,version_no,lot_id,wafer_id)" in sql
    assert "FROM (VALUES " in sql
    assert "UNION ALL" not in sql
    assert sql.count("(:page_dataset_") == 2
    assert parameters == {
        "page_dataset_0": 11,
        "page_version_0": 2,
        "page_lot_0": "L1",
        "page_wafer_0": "W1",
        "page_dataset_1": 12,
        "page_version_1": 3,
        "page_lot_1": "L2",
        "page_wafer_1": "W2",
    }


def test_wafer_summary_parameter_batches_are_exact_and_stable() -> None:
    assert SqlWaferSummaryService._parameter_id_batches(
        ("VTH1", "IDSS"),
        {"IDSS": {9}, "VTH1": {7, 3}},
    ) == (("VTH1", (3, 7)), ("IDSS", (9,)))


@dataclass
class AccessCall:
    dataset_id: int
    version_no: int


class StubDatasetService:
    def __init__(self) -> None:
        self.calls: list[AccessCall] = []

    def assert_dataset_access(
        self, dataset_id, principal, mode="READ", *, version_no=None
    ) -> None:
        del principal, mode
        self.calls.append(AccessCall(dataset_id, version_no))


class StubWaferSummaryService:
    def __init__(self) -> None:
        self.request: WaferSummaryRequest | None = None

    def summarize(self, request: WaferSummaryRequest) -> WaferSummaryResult:
        self.request = request
        row = WaferSummaryRow(
            11,
            2,
            "L1",
            "W1",
            10,
            9,
            1,
            0,
            0,
            10,
            0.9,
            (WaferParameterSummary("VTH1", "V", 9, 1, 0, 5.4, 6.0, 5.7),),
            WaferSummaryDrilldownContext(11, 2, "L1", "W1"),
        )
        return WaferSummaryResult(
            contract_version="ANALYTICS_WAFER_SUMMARY_V1",
            dataset_context=AnalyticsDatasetContext(
                (AnalyticsResolvedDataset(11, 2, "CP", "CP", "P1"),), "CP", True
            ),
            filter_summary=_hashes(request),
            rule_context=AnalyticsRuleContext((), (), ()),
            capabilities=(
                AnalyticsCapability("WAFER_SUMMARY", "AVAILABLE", None, None),
            ),
            page=1,
            page_size=50,
            total=1,
            sort_by="YIELD",
            sort_direction="DESC",
            items=(row,),
            warnings=(),
            computed_at="2026-08-31T00:00:00+00:00",
        )


def test_wafer_summary_api_authorizes_context_and_returns_dynamic_parameters() -> None:
    app = create_app()
    datasets = StubDatasetService()
    service = StubWaferSummaryService()
    app.state.dataset_service = datasets
    app.state.wafer_summary_service = service
    response = TestClient(app).post("/api/v1/analytics/wafer-summary", json=_payload())
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["parameters"][0]["parameter"] == "VTH1"
    assert response.json()["items"][0]["drilldown_context"] == {
        "dataset_id": 11,
        "version_no": 2,
        "lot_id": "L1",
        "wafer_id": "W1",
    }
    assert response.json()["filter_summary"]["filter_hash"]
    assert datasets.calls == [AccessCall(11, 2)]
    assert service.request is not None
