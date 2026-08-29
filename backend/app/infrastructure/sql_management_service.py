from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.management import (
    FailBinSummary,
    QualityBreakdown,
    QualityDatasetDrilldown,
    QualityKpis,
    QualityManagementSummary,
    QualityTrendPoint,
)

_CURRENT_SCOPE_CTE = """
WITH current_scope AS (
    SELECT DISTINCT
        dv.dataset_version_id,dv.dataset_id,dv.version_no,dv.input_batch_id,
        dv.published_at_utc,d.owner_user_id,d.product_id,
        COALESCE(p.product_name,p.product_code,N'UNKNOWN') AS product_name,
        b.business_domain,b.test_stage,b.factory_code,tr.run_id,tr.lot_id
    FROM dataset.dataset_version dv
    JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id
    JOIN ingestion.import_batch b ON b.import_batch_id=dv.input_batch_id
    JOIN dataset.dataset_version_run dvr
      ON dvr.dataset_version_id=dv.dataset_version_id
    JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id
    LEFT JOIN mdm.product p ON p.product_id=d.product_id
    WHERE dv.status='PUBLISHED' AND dv.is_current=1
      AND dv.published_at_utc>=:from_utc AND dv.published_at_utc<:to_utc
      {filters}
), scoped_units AS (
    SELECT cs.dataset_version_id,cs.dataset_id,cs.version_no,cs.input_batch_id,
           cs.published_at_utc,cs.product_name,cs.business_domain,cs.test_stage,
           cs.factory_code,cs.lot_id,ur.unit_id,ur.overall_result,
           NULLIF(COALESCE(ur.soft_bin,ur.hard_bin,ur.fail_test_name,N''),N'') AS fail_bin
    FROM current_scope cs
    JOIN test.unit_result ur ON ur.run_id=cs.run_id
)
"""


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DomainError(
            "QUALITY_SNAPSHOT_INVALID", "质量汇总时间字段无效", 503
        )
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rate(numerator: Any, denominator: Any) -> float | None:
    total = int(denominator or 0)
    return None if total == 0 else float(numerator or 0) / total


def _filter_sql(
    *,
    business_domain: str | None,
    test_stage: str | None,
    factory_code: str | None,
    product_name: str | None,
    lot_id: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    values = {
        "business_domain": business_domain,
        "test_stage": test_stage,
        "factory_code": factory_code,
        "product_name": product_name,
        "lot_id": lot_id,
    }
    columns = {
        "business_domain": "b.business_domain",
        "test_stage": "b.test_stage",
        "factory_code": "b.factory_code",
        "product_name": "COALESCE(p.product_name,p.product_code,N'UNKNOWN')",
        "lot_id": "tr.lot_id",
    }
    for key, value in values.items():
        if value is None:
            continue
        clauses.append(f"AND {columns[key]}=:{key}")
        params[key] = value
    return "\n      ".join(clauses), params


class SqlManagementService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def quality_summary(
        self,
        *,
        from_utc: datetime,
        to_utc: datetime,
        business_domain: str | None = None,
        test_stage: str | None = None,
        factory_code: str | None = None,
        product_name: str | None = None,
        lot_id: str | None = None,
        recent_limit: int = 20,
    ) -> QualityManagementSummary:
        if recent_limit < 1 or recent_limit > 100:
            raise DomainError(
                "QUALITY_RECENT_LIMIT_INVALID", "最近数据集数量必须在 1 到 100 之间", 422
            )
        filters, params = _filter_sql(
            business_domain=business_domain,
            test_stage=test_stage,
            factory_code=factory_code,
            product_name=product_name,
            lot_id=lot_id,
        )
        params.update(
            {
                "from_utc": _naive_utc(from_utc),
                "to_utc": _naive_utc(to_utc),
                "recent_limit": recent_limit,
            }
        )
        cte = _CURRENT_SCOPE_CTE.format(filters=filters)
        try:
            with self._engine.connect() as connection:
                observed = connection.execute(
                    text("SELECT CAST(SYSUTCDATETIME() AS datetime2(3))")
                ).scalar_one()
                kpi = connection.execute(text(cte + _KPI_SQL), params).mappings().one()
                trends = connection.execute(
                    text(cte + _TREND_SQL), params
                ).mappings().all()
                breakdown_rows: list[Mapping[str, Any]] = []
                for dimension, expression in _BREAKDOWN_DIMENSIONS:
                    rows = connection.execute(
                        text(
                            cte
                            + _BREAKDOWN_SQL.format(
                                dimension=dimension,
                                expression=expression,
                            )
                        ),
                        params,
                    ).mappings().all()
                    breakdown_rows.extend(rows)
                fail_rows = connection.execute(
                    text(cte + _FAIL_BIN_SQL), params
                ).mappings().all()
                recent_rows = connection.execute(
                    text(cte + _RECENT_DATASET_SQL), params
                ).mappings().all()
                failed_jobs = int(
                    connection.execute(
                        text(_FAILED_JOB_SQL.format(filters=_batch_filter_sql(params))),
                        params,
                    ).scalar_one()
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "QUALITY_SNAPSHOT_UNAVAILABLE",
                "管理质量摘要暂时不可用",
                503,
            ) from exc

        total_units = int(kpi["total_units"] or 0)
        pass_units = int(kpi["pass_units"] or 0)
        fail_units = int(kpi["fail_units"] or 0)
        abort_units = int(kpi["abort_units"] or 0)
        unknown_units = int(kpi["unknown_units"] or 0)
        known = pass_units + fail_units
        latest = kpi["latest_dataset_at_utc"]
        observed_aware = _aware_utc(observed)
        latest_aware = _aware_utc(latest) if latest is not None else None
        freshness = (
            max(0, int((observed_aware - latest_aware).total_seconds()))
            if latest_aware is not None
            else None
        )
        return QualityManagementSummary(
            observed_at_utc=_iso_utc(observed) or "",
            from_utc=_iso_utc(_naive_utc(from_utc)) or "",
            to_utc=_iso_utc(_naive_utc(to_utc)) or "",
            filters={
                "business_domain": business_domain,
                "test_stage": test_stage,
                "factory_code": factory_code,
                "product_name": product_name,
                "lot_id": lot_id,
            },
            methodology={
                "fact_source": "Only PUBLISHED is_current=1 Dataset Versions and their Canonical test.* rows are counted.",
                "yield": "PASS / (PASS + FAIL); UNKNOWN and ABORT never enter the yield denominator.",
                "unknown": "UNKNOWN / all Current units; missing PASS/FAIL remains unknown and is never filled with zero.",
                "product_identity": "Product is the source-observed TMS identity, not an SAP material until an approved crosswalk exists.",
                "time_range": "from_utc is inclusive and to_utc is exclusive, based on Dataset published_at_utc.",
            },
            kpis=QualityKpis(
                dataset_count=int(kpi["dataset_count"] or 0),
                product_count=int(kpi["product_count"] or 0),
                lot_count=int(kpi["lot_count"] or 0),
                total_units=total_units,
                pass_units=pass_units,
                fail_units=fail_units,
                abort_units=abort_units,
                unknown_units=unknown_units,
                known_yield_denominator=known,
                yield_rate=_rate(pass_units, known),
                unknown_rate=_rate(unknown_units, total_units),
                failed_job_count=failed_jobs,
                latest_dataset_at_utc=_iso_utc(latest),
                freshness_seconds=freshness,
            ),
            trends=tuple(_trend(row) for row in trends),
            breakdowns=tuple(_breakdown(row) for row in breakdown_rows),
            fail_bins=tuple(_fail_bin(row, fail_units) for row in fail_rows),
            recent_datasets=tuple(_recent(row) for row in recent_rows),
        )


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(UTC).replace(tzinfo=None)


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _trend(row: Mapping[str, Any]) -> QualityTrendPoint:
    total = int(row["total_units"] or 0)
    passed = int(row["pass_units"] or 0)
    failed = int(row["fail_units"] or 0)
    unknown = int(row["unknown_units"] or 0)
    period = row["period_start"]
    period_text = period.isoformat() if hasattr(period, "isoformat") else str(period)
    return QualityTrendPoint(
        period_start_utc=f"{period_text}T00:00:00.000Z",
        dataset_count=int(row["dataset_count"] or 0),
        total_units=total,
        pass_units=passed,
        fail_units=failed,
        unknown_units=unknown,
        yield_rate=_rate(passed, passed + failed),
        unknown_rate=_rate(unknown, total),
    )


def _breakdown(row: Mapping[str, Any]) -> QualityBreakdown:
    total = int(row["total_units"] or 0)
    passed = int(row["pass_units"] or 0)
    failed = int(row["fail_units"] or 0)
    unknown = int(row["unknown_units"] or 0)
    key = str(row["dimension_key"] or "UNKNOWN")
    return QualityBreakdown(
        dimension=str(row["dimension"]),
        key=key,
        label=key,
        dataset_count=int(row["dataset_count"] or 0),
        lot_count=int(row["lot_count"] or 0),
        total_units=total,
        pass_units=passed,
        fail_units=failed,
        unknown_units=unknown,
        yield_rate=_rate(passed, passed + failed),
        unknown_rate=_rate(unknown, total),
    )


def _fail_bin(row: Mapping[str, Any], fail_units: int) -> FailBinSummary:
    count = int(row["fail_units"] or 0)
    return FailBinSummary(
        bin_code=str(row["bin_code"] or "UNCLASSIFIED"),
        fail_units=count,
        share_of_failed=_rate(count, fail_units),
    )


def _recent(row: Mapping[str, Any]) -> QualityDatasetDrilldown:
    passed = int(row["pass_count"] or 0)
    failed = int(row["fail_count"] or 0)
    return QualityDatasetDrilldown(
        dataset_id=int(row["dataset_id"]),
        version_no=int(row["version_no"]),
        import_batch_id=int(row["input_batch_id"]),
        job_id=int(row["job_id"]) if row["job_id"] is not None else None,
        product_name=str(row["product_name"] or "UNKNOWN"),
        lot_id=str(row["lot_id"] or "UNKNOWN"),
        factory_code=str(row["factory_code"] or "UNKNOWN"),
        business_domain=str(row["business_domain"]),
        test_stage=str(row["test_stage"]),
        unit_count=int(row["unit_count"] or 0),
        pass_count=passed,
        fail_count=failed,
        unknown_count=int(row["unknown_count"] or 0),
        yield_rate=_rate(passed, passed + failed),
        source_file_count=int(row["source_file_count"] or 0),
        published_at_utc=_iso_utc(row["published_at_utc"]) or "",
    )


def _batch_filter_sql(params: Mapping[str, Any]) -> str:
    clauses = []
    for key, column in (
        ("business_domain", "b.business_domain"),
        ("test_stage", "b.test_stage"),
        ("factory_code", "b.factory_code"),
    ):
        if params.get(key) is not None:
            clauses.append(f"AND {column}=:{key}")
    return "\n  ".join(clauses)


_KPI_SQL = """
SELECT COUNT(DISTINCT dataset_version_id) AS dataset_count,
       COUNT(DISTINCT product_name) AS product_count,
       COUNT(DISTINCT lot_id) AS lot_count,
       COUNT_BIG(*) AS total_units,
       SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,
       SUM(CASE WHEN overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,
       SUM(CASE WHEN overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_units,
       SUM(CASE WHEN overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units,
       MAX(published_at_utc) AS latest_dataset_at_utc
FROM scoped_units;
"""

_TREND_SQL = """
SELECT CONVERT(date,published_at_utc) AS period_start,
       COUNT(DISTINCT dataset_version_id) AS dataset_count,
       COUNT_BIG(*) AS total_units,
       SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,
       SUM(CASE WHEN overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,
       SUM(CASE WHEN overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units
FROM scoped_units GROUP BY CONVERT(date,published_at_utc)
ORDER BY period_start;
"""

_BREAKDOWN_DIMENSIONS = (
    ("FACTORY", "factory_code"),
    ("PRODUCT", "product_name"),
    ("TEST_STAGE", "test_stage"),
    ("BUSINESS_DOMAIN", "business_domain"),
)

_BREAKDOWN_SQL = """
SELECT '{dimension}' AS dimension,COALESCE({expression},N'UNKNOWN') AS dimension_key,
       COUNT(DISTINCT dataset_version_id) AS dataset_count,
       COUNT(DISTINCT lot_id) AS lot_count,
       COUNT_BIG(*) AS total_units,
       SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,
       SUM(CASE WHEN overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,
       SUM(CASE WHEN overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units
FROM scoped_units GROUP BY {expression} ORDER BY total_units DESC;
"""

_FAIL_BIN_SQL = """
SELECT TOP (20) COALESCE(fail_bin,N'UNCLASSIFIED') AS bin_code,COUNT_BIG(*) AS fail_units
FROM scoped_units WHERE overall_result='FAIL'
GROUP BY COALESCE(fail_bin,N'UNCLASSIFIED') ORDER BY fail_units DESC,bin_code;
"""

_RECENT_DATASET_SQL = """
SELECT TOP (:recent_limit)
       su.dataset_id,su.version_no,su.input_batch_id,
       MAX(prs.job_id) AS job_id,MAX(su.product_name) AS product_name,
       MAX(su.lot_id) AS lot_id,MAX(su.factory_code) AS factory_code,
       MAX(su.business_domain) AS business_domain,MAX(su.test_stage) AS test_stage,
       COUNT_BIG(*) AS unit_count,
       SUM(CASE WHEN su.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,
       SUM(CASE WHEN su.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,
       SUM(CASE WHEN su.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,
       (SELECT COUNT_BIG(*) FROM ingestion.import_batch_file ibf
        WHERE ibf.import_batch_id=su.input_batch_id) AS source_file_count,
       MAX(su.published_at_utc) AS published_at_utc
FROM scoped_units su
LEFT JOIN ingestion.processing_result_summary prs
  ON prs.dataset_id=su.dataset_id AND prs.dataset_version_no=su.version_no
GROUP BY su.dataset_id,su.version_no,su.input_batch_id
ORDER BY published_at_utc DESC,su.dataset_id DESC;
"""

_FAILED_JOB_SQL = """
SELECT COUNT_BIG(*) FROM ingestion.processing_job j
JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id
WHERE j.status='FAILED' AND COALESCE(j.finished_at_utc,j.requested_at_utc)>=:from_utc
  AND COALESCE(j.finished_at_utc,j.requested_at_utc)<:to_utc
  {filters};
"""
