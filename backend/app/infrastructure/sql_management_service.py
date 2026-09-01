from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.management import (
    FailBinSummary,
    QualityBreakdown,
    QualityDatasetDrilldown,
    QualityKpis,
    QualityManagementSummary,
    QualityTrendPoint,
)
from app.infrastructure.sql_visibility import (
    domain_grant_exists_sql,
)

_DATASET_DASHBOARD_SCOPE_SQL = (
    "((:access_scope='PERSONAL' AND d.access_scope='PERSONAL' "
    "AND d.owner_user_id=:user_id) OR "
    "(:access_scope='DOMAIN' AND d.access_scope='DOMAIN' "
    "AND d.data_domain_id=:data_domain_id AND "
    + domain_grant_exists_sql(data_domain_column="d.data_domain_id")
    + "))"
)

_BATCH_DASHBOARD_SCOPE_SQL = (
    "((:access_scope='PERSONAL' AND b.access_scope='PERSONAL' "
    "AND b.owner_user_id=:user_id) OR "
    "(:access_scope='DOMAIN' AND b.access_scope='DOMAIN' "
    "AND b.data_domain_id=:data_domain_id AND "
    + domain_grant_exists_sql(data_domain_column="b.data_domain_id")
    + "))"
)

_CURRENT_SCOPE_CTE = f"""
WITH current_scope AS (
    SELECT DISTINCT
        dv.dataset_version_id,dv.dataset_id,dv.version_no,dv.input_batch_id,
        dv.published_at_utc,d.owner_user_id,d.product_id,
        COALESCE(product_enrichment.value_text,p.product_name,p.product_code,N'UNKNOWN') AS product_name,
        b.business_domain,b.test_stage,b.factory_code,tr.run_id,tr.lot_id
    FROM dataset.dataset_version dv
    JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id
    JOIN ingestion.import_batch b ON b.import_batch_id=dv.input_batch_id
    JOIN dataset.dataset_version_run dvr
      ON dvr.dataset_version_id=dv.dataset_version_id
    JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id
    LEFT JOIN mdm.product p ON p.product_id=d.product_id
    OUTER APPLY(
        SELECT TOP (1) fe.value_text
        FROM ingestion.field_enrichment fe
        WHERE fe.import_batch_id=dv.input_batch_id
          AND fe.source_file_id IS NULL
          AND fe.test_stage=d.test_stage
          AND fe.field_code='PRODUCT_CODE'
          AND fe.action='FILL' AND fe.is_current=1
        ORDER BY fe.enrichment_id DESC
    ) product_enrichment
    WHERE dv.status='PUBLISHED' AND dv.is_current=1
      AND {_DATASET_DASHBOARD_SCOPE_SQL}
      AND dv.published_at_utc>=:from_utc AND dv.published_at_utc<:to_utc
      {{filters}}
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
        raise DomainError("QUALITY_SNAPSHOT_INVALID", "质量汇总时间字段无效", 503)
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
        "product_name": "COALESCE(product_enrichment.value_text,p.product_name,p.product_code,N'UNKNOWN')",
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
        principal: Principal,
        from_utc: datetime,
        to_utc: datetime,
        access_scope: str,
        data_domain_id: int | None = None,
        business_domain: str | None = None,
        test_stage: str | None = None,
        factory_code: str | None = None,
        product_name: str | None = None,
        lot_id: str | None = None,
        recent_limit: int = 20,
    ) -> QualityManagementSummary:
        if access_scope not in {"PERSONAL", "DOMAIN"}:
            raise DomainError(
                "QUALITY_ACCESS_SCOPE_INVALID",
                "质量汇总访问范围无效",
                422,
            )
        if access_scope == "DOMAIN" and data_domain_id is None:
            raise DomainError(
                "QUALITY_DATA_DOMAIN_REQUIRED",
                "查看数据域统计时必须指定 data_domain_id",
                422,
            )
        if access_scope == "PERSONAL" and data_domain_id is not None:
            raise DomainError(
                "QUALITY_DATA_DOMAIN_NOT_ALLOWED",
                "查看我的数据时不能指定 data_domain_id",
                422,
            )
        if recent_limit < 1 or recent_limit > 100:
            raise DomainError(
                "QUALITY_RECENT_LIMIT_INVALID",
                "最近数据集数量必须在 1 到 100 之间",
                422,
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
                "access_scope": access_scope,
                "data_domain_id": data_domain_id,
                "user_id": principal.user_id,
            }
        )
        cte = _CURRENT_SCOPE_CTE.format(filters=filters)
        try:
            with self._engine.connect() as connection:
                if access_scope == "DOMAIN" and not self._has_active_domain_grant(
                    connection,
                    user_id=principal.user_id,
                    data_domain_id=int(data_domain_id or 0),
                ):
                    raise DomainError(
                        "QUALITY_DATA_DOMAIN_ACCESS_DENIED",
                        "数据域不存在、已停用或当前授权已失效",
                        403,
                    )
                observed = connection.execute(
                    text("SELECT CAST(SYSUTCDATETIME() AS datetime2(3))")
                ).scalar_one()
                fact_rows = (
                    connection.execute(text(cte + _QUALITY_FACT_SQL), params)
                    .mappings()
                    .all()
                )
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
        kpis, trends, breakdowns, fail_bins, recent_datasets = (
            _summarize_quality_facts(
                fact_rows,
                observed=observed,
                failed_jobs=failed_jobs,
                recent_limit=recent_limit,
            )
        )
        return QualityManagementSummary(
            observed_at_utc=_iso_utc(observed) or "",
            from_utc=_iso_utc(_naive_utc(from_utc)) or "",
            to_utc=_iso_utc(_naive_utc(to_utc)) or "",
            filters={
                "access_scope": access_scope,
                "data_domain_id": data_domain_id,
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
                "trend_period": "Trend periods are Asia/Shanghai business dates; period_start_utc is the UTC instant of Shanghai local midnight.",
                "failed_job_scope": "Failed Job counts use time, business domain, test stage, and factory filters only; Product and Lot filters do not apply.",
                "access_scope": "PERSONAL is always owner-only; DOMAIN requires an active, unexpired grant. Dashboard queries never use break-glass access.",
            },
            kpis=kpis,
            trends=trends,
            breakdowns=breakdowns,
            fail_bins=fail_bins,
            recent_datasets=recent_datasets,
        )

    @staticmethod
    def _has_active_domain_grant(
        connection: Any, *, user_id: int, data_domain_id: int
    ) -> bool:
        return bool(
            connection.execute(
                text(
                    "SELECT CASE WHEN EXISTS(SELECT 1 FROM iam.data_domain d "
                    "JOIN iam.data_domain_grant g "
                    "ON g.data_domain_id=d.data_domain_id "
                    "WHERE d.data_domain_id=:data_domain_id AND d.active=1 "
                    "AND g.user_id=:user_id AND g.status='ACTIVE' "
                    "AND (g.expires_at_utc IS NULL "
                    "OR g.expires_at_utc>SYSUTCDATETIME())) "
                    "THEN 1 ELSE 0 END"
                ),
                {"user_id": user_id, "data_domain_id": data_domain_id},
            ).scalar_one()
        )


def _naive_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=None)
        if value.tzinfo is None
        else value.astimezone(UTC).replace(tzinfo=None)
    )


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _trend(row: Mapping[str, Any]) -> QualityTrendPoint:
    total = int(row["total_units"] or 0)
    passed = int(row["pass_units"] or 0)
    failed = int(row["fail_units"] or 0)
    unknown = int(row["unknown_units"] or 0)
    period = row["period_start"]
    period_text = period.isoformat() if hasattr(period, "isoformat") else str(period)
    period_start_utc = datetime.combine(
        datetime.fromisoformat(period_text).date(), time.min
    ) - timedelta(hours=8)
    return QualityTrendPoint(
        period_start_utc=_iso_utc(period_start_utc) or "",
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


def _summarize_quality_facts(
    rows: list[Mapping[str, Any]],
    *,
    observed: datetime,
    failed_jobs: int,
    recent_limit: int,
) -> tuple[
    QualityKpis,
    tuple[QualityTrendPoint, ...],
    tuple[QualityBreakdown, ...],
    tuple[FailBinSummary, ...],
    tuple[QualityDatasetDrilldown, ...],
]:
    """Aggregate the single set-based quality fact query without changing KPI rules."""

    dataset_ids: set[int] = set()
    products: set[str] = set()
    lots: set[str] = set()
    totals: defaultdict[str, int] = defaultdict(int)
    latest: datetime | None = None
    trend_groups: dict[Any, dict[str, Any]] = {}
    breakdown_groups: dict[tuple[str, str], dict[str, Any]] = {}
    fail_bin_counts: defaultdict[str, int] = defaultdict(int)
    recent_groups: dict[tuple[int, int, int], dict[str, Any]] = {}

    for row in rows:
        count = int(row["unit_count"] or 0)
        dataset_id = int(row["dataset_id"])
        version_no = int(row["version_no"])
        input_batch_id = int(row["input_batch_id"])
        dataset_key = (dataset_id, version_no)
        result = str(row["overall_result"] or "")
        published = row["published_at_utc"]
        if not isinstance(published, datetime):
            raise DomainError(
                "QUALITY_SNAPSHOT_INVALID",
                "质量汇总发布时间字段无效",
                503,
            )

        dataset_ids.add(int(row["dataset_version_id"]))
        if row["product_name"] is not None:
            products.add(str(row["product_name"]))
        if row["lot_id"] is not None:
            lots.add(str(row["lot_id"]))
        totals["total"] += count
        if result in {"PASS", "FAIL", "ABORT", "UNKNOWN"}:
            totals[result] += count
        if latest is None or _aware_utc(published) > _aware_utc(latest):
            latest = published

        shanghai_day = (_aware_utc(published) + timedelta(hours=8)).date()
        trend = trend_groups.setdefault(
            shanghai_day,
            {"datasets": set(), "total": 0, "PASS": 0, "FAIL": 0, "UNKNOWN": 0},
        )
        trend["datasets"].add(dataset_key)
        trend["total"] += count
        if result in {"PASS", "FAIL", "UNKNOWN"}:
            trend[result] += count

        for dimension, column in _BREAKDOWN_DIMENSIONS:
            raw_key = row[column]
            dimension_key = str(raw_key) if raw_key is not None else "UNKNOWN"
            breakdown = breakdown_groups.setdefault(
                (dimension, dimension_key),
                {
                    "datasets": set(),
                    "lots": set(),
                    "total": 0,
                    "PASS": 0,
                    "FAIL": 0,
                    "UNKNOWN": 0,
                },
            )
            breakdown["datasets"].add(dataset_key)
            if row["lot_id"] is not None:
                breakdown["lots"].add(str(row["lot_id"]))
            breakdown["total"] += count
            if result in {"PASS", "FAIL", "UNKNOWN"}:
                breakdown[result] += count

        if result == "FAIL":
            fail_bin_counts[str(row["fail_bin"] or "UNCLASSIFIED")] += count

        recent_key = (dataset_id, version_no, input_batch_id)
        recent = recent_groups.setdefault(
            recent_key,
            {
                "dataset_id": dataset_id,
                "version_no": version_no,
                "input_batch_id": input_batch_id,
                "job_id": None,
                "product_name": None,
                "lot_id": None,
                "factory_code": None,
                "business_domain": None,
                "test_stage": None,
                "unit_count": 0,
                "pass_count": 0,
                "fail_count": 0,
                "unknown_count": 0,
                "source_file_count": 0,
                "published_at_utc": published,
            },
        )
        for key in (
            "job_id",
            "product_name",
            "lot_id",
            "factory_code",
            "business_domain",
            "test_stage",
        ):
            value = row[key]
            if value is not None and (
                recent[key] is None or str(value) > str(recent[key])
            ):
                recent[key] = value
        recent["unit_count"] += count
        if result == "PASS":
            recent["pass_count"] += count
        elif result == "FAIL":
            recent["fail_count"] += count
        elif result == "UNKNOWN":
            recent["unknown_count"] += count
        recent["source_file_count"] = max(
            int(recent["source_file_count"]), int(row["source_file_count"] or 0)
        )
        if _aware_utc(published) > _aware_utc(recent["published_at_utc"]):
            recent["published_at_utc"] = published

    pass_units = totals["PASS"]
    fail_units = totals["FAIL"]
    unknown_units = totals["UNKNOWN"]
    known = pass_units + fail_units
    freshness = (
        max(0, int((_aware_utc(observed) - _aware_utc(latest)).total_seconds()))
        if latest is not None
        else None
    )
    kpis = QualityKpis(
        dataset_count=len(dataset_ids),
        product_count=len(products),
        lot_count=len(lots),
        total_units=totals["total"],
        pass_units=pass_units,
        fail_units=fail_units,
        abort_units=totals["ABORT"],
        unknown_units=unknown_units,
        known_yield_denominator=known,
        yield_rate=_rate(pass_units, known),
        unknown_rate=_rate(unknown_units, totals["total"]),
        failed_job_count=failed_jobs,
        latest_dataset_at_utc=_iso_utc(latest),
        freshness_seconds=freshness,
    )

    trends = tuple(
        QualityTrendPoint(
            period_start_utc=_iso_utc(datetime.combine(day, time.min) - timedelta(hours=8))
            or "",
            dataset_count=len(group["datasets"]),
            total_units=group["total"],
            pass_units=group["PASS"],
            fail_units=group["FAIL"],
            unknown_units=group["UNKNOWN"],
            yield_rate=_rate(group["PASS"], group["PASS"] + group["FAIL"]),
            unknown_rate=_rate(group["UNKNOWN"], group["total"]),
        )
        for day, group in sorted(trend_groups.items())
    )

    breakdowns_list: list[QualityBreakdown] = []
    for dimension, _column in _BREAKDOWN_DIMENSIONS:
        matching = [
            (key, group)
            for (group_dimension, key), group in breakdown_groups.items()
            if group_dimension == dimension
        ]
        for key, group in sorted(matching, key=lambda item: (-item[1]["total"], item[0])):
            breakdowns_list.append(
                QualityBreakdown(
                    dimension=dimension,
                    key=key,
                    label=key,
                    dataset_count=len(group["datasets"]),
                    lot_count=len(group["lots"]),
                    total_units=group["total"],
                    pass_units=group["PASS"],
                    fail_units=group["FAIL"],
                    unknown_units=group["UNKNOWN"],
                    yield_rate=_rate(group["PASS"], group["PASS"] + group["FAIL"]),
                    unknown_rate=_rate(group["UNKNOWN"], group["total"]),
                )
            )

    fail_bins = tuple(
        FailBinSummary(
            bin_code=code,
            fail_units=count,
            share_of_failed=_rate(count, fail_units),
        )
        for code, count in sorted(
            fail_bin_counts.items(), key=lambda item: (-item[1], item[0])
        )[:20]
    )
    recent_rows = sorted(
        recent_groups.values(),
        key=lambda row: (
            _aware_utc(row["published_at_utc"]),
            int(row["dataset_id"]),
        ),
        reverse=True,
    )[:recent_limit]
    recent_datasets = tuple(_recent(row) for row in recent_rows)
    return kpis, trends, tuple(breakdowns_list), fail_bins, recent_datasets


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


_QUALITY_FACT_SQL = """
, unit_groups AS (
    SELECT su.dataset_version_id,su.dataset_id,su.version_no,su.input_batch_id,
           su.published_at_utc,su.product_name,su.business_domain,su.test_stage,
           su.factory_code,su.lot_id,su.overall_result,su.fail_bin,
           COUNT_BIG(*) AS unit_count
    FROM scoped_units su
    GROUP BY su.dataset_version_id,su.dataset_id,su.version_no,su.input_batch_id,
             su.published_at_utc,su.product_name,su.business_domain,su.test_stage,
             su.factory_code,su.lot_id,su.overall_result,su.fail_bin
), ranked_summary AS (
    SELECT prs.dataset_id,prs.dataset_version_no,prs.job_id,
           ROW_NUMBER() OVER (
               PARTITION BY prs.dataset_id,prs.dataset_version_no
               ORDER BY prs.result_summary_id DESC
           ) AS row_no
    FROM ingestion.processing_result_summary prs
    WHERE prs.status='PROCESSED'
), file_counts AS (
    SELECT ibf.import_batch_id,COUNT_BIG(*) AS source_file_count
    FROM ingestion.import_batch_file ibf
    GROUP BY ibf.import_batch_id
)
SELECT ug.dataset_version_id,ug.dataset_id,ug.version_no,ug.input_batch_id,
       ug.published_at_utc,ug.product_name,ug.business_domain,ug.test_stage,
       ug.factory_code,ug.lot_id,ug.overall_result,ug.fail_bin,ug.unit_count,
       rs.job_id,COALESCE(fc.source_file_count,0) AS source_file_count
FROM unit_groups ug
LEFT JOIN ranked_summary rs
  ON rs.dataset_id=ug.dataset_id
 AND rs.dataset_version_no=ug.version_no
 AND rs.row_no=1
LEFT JOIN file_counts fc ON fc.import_batch_id=ug.input_batch_id;
"""


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
SELECT CONVERT(date,DATEADD(hour,8,published_at_utc)) AS period_start,
       COUNT(DISTINCT dataset_version_id) AS dataset_count,
       COUNT_BIG(*) AS total_units,
       SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_units,
       SUM(CASE WHEN overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_units,
       SUM(CASE WHEN overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_units
FROM scoped_units GROUP BY CONVERT(date,DATEADD(hour,8,published_at_utc))
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
       MAX(latest_summary.job_id) AS job_id,MAX(su.product_name) AS product_name,
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
OUTER APPLY(
    SELECT TOP (1) prs.job_id
    FROM ingestion.processing_result_summary prs
    WHERE prs.dataset_id=su.dataset_id
      AND prs.dataset_version_no=su.version_no
      AND prs.status='PROCESSED'
    ORDER BY prs.result_summary_id DESC
) latest_summary
GROUP BY su.dataset_id,su.version_no,su.input_batch_id
ORDER BY published_at_utc DESC,su.dataset_id DESC;
"""

_FAILED_JOB_SQL = (
    """
SELECT COUNT_BIG(*) FROM ingestion.processing_job j
JOIN ingestion.import_batch b ON b.import_batch_id=j.import_batch_id
WHERE j.status='FAILED' AND COALESCE(j.finished_at_utc,j.requested_at_utc)>=:from_utc
  AND COALESCE(j.finished_at_utc,j.requested_at_utc)<:to_utc
  AND """
    + _BATCH_DASHBOARD_SCOPE_SQL
    + """
  {filters};
"""
)
