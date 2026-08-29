from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.infrastructure.sql_management_service import (
    _CURRENT_SCOPE_CTE,
    _RECENT_DATASET_SQL,
    _TREND_SQL,
    _filter_sql,
    _rate,
    _trend,
)
from sqlalchemy import text
from sqlalchemy.dialects import mssql


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
    assert point.period_start_utc == "2026-08-28T16:00:00.000Z"


def test_management_sql_uses_shanghai_days_and_one_deterministic_summary() -> None:
    assert "CONVERT(date,DATEADD(hour,8,published_at_utc))" in _TREND_SQL
    assert "GROUP BY CONVERT(date,DATEADD(hour,8,published_at_utc))" in _TREND_SQL

    normalized = " ".join(_RECENT_DATASET_SQL.split())
    assert "OUTER APPLY( SELECT TOP (1) prs.job_id" in normalized
    assert "prs.status='PROCESSED'" in normalized
    assert "ORDER BY prs.result_summary_id DESC" in normalized
    assert "LEFT JOIN ingestion.processing_result_summary prs" not in normalized

    compiled = text(_RECENT_DATASET_SQL).compile(
        dialect=mssql.dialect(paramstyle="qmark")
    )
    assert "TOP (?)" in str(compiled)
    assert compiled.positiontup == ["recent_limit"]


def test_management_filter_parameters_are_placed_after_enrichment_scope() -> None:
    filters, params = _filter_sql(
        business_domain="PRODUCTION",
        test_stage="FT",
        factory_code="RIYUEXIN",
        product_name="NCE-1",
        lot_id="LOT-1",
    )
    cte = _CURRENT_SCOPE_CTE.format(filters=filters)

    assert cte.index("OUTER APPLY(") < cte.index("WHERE dv.status='PUBLISHED'")
    assert (
        "COALESCE(product_enrichment.value_text,p.product_name,p.product_code,N'UNKNOWN')=:product_name"
        in cte
    )
    assert "tr.lot_id=:lot_id" in cte
    assert set(params) == {
        "business_domain",
        "test_stage",
        "factory_code",
        "product_name",
        "lot_id",
    }
    for unsupported in (
        "STRING_AGG(",
        "OPENJSON(",
        "JSON_VALUE(",
        "AT TIME ZONE",
        "STRING_SPLIT(",
        "CONCAT_WS(",
        "FETCH FIRST",
    ):
        assert unsupported not in (cte + _TREND_SQL + _RECENT_DATASET_SQL).upper()
