from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.infrastructure.sql_management_service import (
    _filter_sql,
    _rate,
    _trend,
)


def test_management_filters_only_emit_fixed_allowlisted_columns() -> None:
    sql, params = _filter_sql(
        business_domain="PRODUCTION",
        test_stage="FT",
        factory_code="RIYUEXIN",
        product_name="NCEAP40PT15D(M)-2B00",
        lot_id="FA59-3997",
    )

    assert "b.business_domain=:business_domain" in sql
    assert "b.test_stage=:test_stage" in sql
    assert "b.factory_code=:factory_code" in sql
    assert "tr.lot_id=:lot_id" in sql
    assert set(params) == {
        "business_domain",
        "test_stage",
        "factory_code",
        "product_name",
        "lot_id",
    }
    assert "FA59-3997" not in sql


def test_management_rates_preserve_unknown_denominators() -> None:
    assert _rate(80, 90) == pytest.approx(80 / 90)
    assert _rate(10, 100) == 0.1
    assert _rate(0, 0) is None


def test_management_trend_does_not_add_unknown_to_yield() -> None:
    point = _trend(
        {
            "period_start": datetime(2026, 8, 29, tzinfo=UTC).date(),
            "dataset_count": 1,
            "total_units": 100,
            "pass_units": 80,
            "fail_units": 10,
            "unknown_units": 10,
        }
    )

    assert point.yield_rate == pytest.approx(80 / 90)
    assert point.unknown_rate == 0.1
    assert point.period_start_utc == "2026-08-29T00:00:00.000Z"
