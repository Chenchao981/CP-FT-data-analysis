from __future__ import annotations

from datetime import UTC, datetime

from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.management import (
    FailBinSummary,
    QualityBreakdown,
    QualityDatasetDrilldown,
    QualityKpis,
    QualityManagementSummary,
    QualityTrendPoint,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubManagementService:
    def quality_summary(self, **kwargs) -> QualityManagementSummary:
        assert kwargs["principal"].user_id == 9
        assert kwargs["from_utc"].tzinfo is UTC
        assert kwargs["to_utc"].tzinfo is UTC
        return QualityManagementSummary(
            observed_at_utc="2026-08-29T06:00:00.000Z",
            from_utc="2026-08-01T00:00:00.000Z",
            to_utc="2026-09-01T00:00:00.000Z",
            filters={"factory_code": kwargs["factory_code"]},
            methodology={
                "yield": "PASS / (PASS + FAIL); UNKNOWN is excluded.",
                "trend_period": "Trend periods are Asia/Shanghai business dates.",
                "failed_job_scope": "Product and Lot filters do not apply to failed jobs.",
            },
            kpis=QualityKpis(
                dataset_count=2,
                product_count=1,
                lot_count=2,
                total_units=100,
                pass_units=80,
                fail_units=10,
                abort_units=0,
                unknown_units=10,
                known_yield_denominator=90,
                yield_rate=80 / 90,
                unknown_rate=0.1,
                failed_job_count=1,
                latest_dataset_at_utc="2026-08-29T05:00:00.000Z",
                freshness_seconds=3600,
            ),
            trends=(
                QualityTrendPoint(
                    "2026-08-29T00:00:00.000Z", 2, 100, 80, 10, 10, 80 / 90, 0.1
                ),
            ),
            breakdowns=(
                QualityBreakdown(
                    "FACTORY",
                    "RIYUEXIN",
                    "RIYUEXIN",
                    2,
                    2,
                    100,
                    80,
                    10,
                    10,
                    80 / 90,
                    0.1,
                ),
            ),
            fail_bins=(FailBinSummary("BIN2", 10, 1.0),),
            recent_datasets=(
                QualityDatasetDrilldown(
                    44,
                    1,
                    77,
                    96,
                    "NCEAP40PT15D(M)-2B00",
                    "FA59-3997",
                    "RIYUEXIN",
                    "ENGINEERING",
                    "FT",
                    100,
                    80,
                    10,
                    10,
                    80 / 90,
                    6,
                    "2026-08-29T05:00:00.000Z",
                ),
            ),
        )


def _client(permission: str = "MANAGEMENT_READ") -> TestClient:
    app = create_app()
    app.state.management_service = StubManagementService()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=9,
        login_name="quality",
        display_name="质量经理",
        roles=("QUALITY_MANAGER",),
        permissions=frozenset({permission}),
    )
    return TestClient(app)


def test_quality_summary_keeps_unknown_out_of_yield_denominator() -> None:
    response = _client().get(
        "/api/v1/management/quality-summary",
        params={
            "from_utc": "2026-08-01T00:00:00Z",
            "to_utc": "2026-09-01T00:00:00Z",
            "factory_code": " RIYUEXIN ",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kpis"]["known_yield_denominator"] == 90
    assert payload["kpis"]["yield_rate"] == 80 / 90
    assert payload["kpis"]["unknown_rate"] == 0.1
    assert payload["filters"]["factory_code"] == "RIYUEXIN"
    assert payload["recent_datasets"][0]["job_id"] == 96
    assert "Asia/Shanghai" in payload["methodology"]["trend_period"]
    assert "Product and Lot" in payload["methodology"]["failed_job_scope"]


def test_quality_summary_requires_management_read() -> None:
    response = _client("DATASET_READ").get("/api/v1/management/quality-summary")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_quality_summary_rejects_inverted_range() -> None:
    response = _client().get(
        "/api/v1/management/quality-summary",
        params={
            "from_utc": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
            "to_utc": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUALITY_TIME_RANGE_INVALID"


def test_quality_summary_fails_closed_without_database() -> None:
    app = create_app()
    app.state.management_service = None

    response = TestClient(app).get("/api/v1/management/quality-summary")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"
