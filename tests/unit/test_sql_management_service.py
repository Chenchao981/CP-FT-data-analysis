from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.infrastructure.sql_management_service import (
    _CURRENT_SCOPE_CTE,
    _QUALITY_FACT_SQL,
    _RECENT_DATASET_SQL,
    _TREND_SQL,
    _filter_sql,
    _rate,
    _summarize_quality_facts,
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


def test_quality_fact_query_is_set_based_and_sql2014_compatible() -> None:
    normalized = " ".join(_QUALITY_FACT_SQL.split()).upper()

    assert "GROUP BY SU.DATASET_VERSION_ID" in normalized
    assert "ROW_NUMBER() OVER" in normalized
    assert "RS.ROW_NO=1" in normalized
    assert "FILE_COUNTS" in normalized
    assert "OPENJSON(" not in normalized
    assert "STRING_AGG(" not in normalized


def test_quality_fact_aggregation_preserves_status_and_recent_semantics() -> None:
    observed = datetime(2026, 8, 31, 4, 0, tzinfo=UTC)
    published = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
    base = {
        "dataset_version_id": 101,
        "dataset_id": 55,
        "version_no": 1,
        "input_batch_id": 77,
        "published_at_utc": published,
        "product_name": "PRODUCT-A",
        "business_domain": "PRODUCTION",
        "test_stage": "FT",
        "factory_code": "RIYUEXIN",
        "lot_id": "LOT-A",
        "job_id": 88,
        "source_file_count": 5,
        "fail_bin": None,
    }
    rows = [
        {**base, "overall_result": "PASS", "unit_count": 80},
        {**base, "overall_result": "FAIL", "unit_count": 10, "fail_bin": "BIN2"},
        {**base, "overall_result": "UNKNOWN", "unit_count": 7},
        {**base, "overall_result": "ABORT", "unit_count": 3},
    ]

    kpis, trends, breakdowns, fail_bins, recent = _summarize_quality_facts(
        rows,
        observed=observed,
        failed_jobs=2,
        recent_limit=20,
    )

    assert kpis.total_units == 100
    assert kpis.known_yield_denominator == 90
    assert kpis.yield_rate == pytest.approx(80 / 90)
    assert kpis.unknown_rate == 0.07
    assert kpis.abort_units == 3
    assert kpis.failed_job_count == 2
    assert kpis.freshness_seconds == 7200
    assert trends[0].period_start_utc == "2026-08-30T16:00:00.000Z"
    assert trends[0].total_units == 100
    assert breakdowns[0].dimension == "FACTORY"
    assert fail_bins[0].bin_code == "BIN2"
    assert fail_bins[0].share_of_failed == 1.0
    assert recent[0].unit_count == 100
    assert recent[0].unknown_count == 7
    assert recent[0].source_file_count == 5
