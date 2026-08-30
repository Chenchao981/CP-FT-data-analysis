from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.auth import Principal


@dataclass(frozen=True, slots=True)
class QualityKpis:
    dataset_count: int
    product_count: int
    lot_count: int
    total_units: int
    pass_units: int
    fail_units: int
    abort_units: int
    unknown_units: int
    known_yield_denominator: int
    yield_rate: float | None
    unknown_rate: float | None
    failed_job_count: int
    latest_dataset_at_utc: str | None
    freshness_seconds: int | None


@dataclass(frozen=True, slots=True)
class QualityBreakdown:
    dimension: str
    key: str
    label: str
    dataset_count: int
    lot_count: int
    total_units: int
    pass_units: int
    fail_units: int
    unknown_units: int
    yield_rate: float | None
    unknown_rate: float | None


@dataclass(frozen=True, slots=True)
class QualityTrendPoint:
    period_start_utc: str
    dataset_count: int
    total_units: int
    pass_units: int
    fail_units: int
    unknown_units: int
    yield_rate: float | None
    unknown_rate: float | None


@dataclass(frozen=True, slots=True)
class FailBinSummary:
    bin_code: str
    fail_units: int
    share_of_failed: float | None


@dataclass(frozen=True, slots=True)
class QualityDatasetDrilldown:
    dataset_id: int
    version_no: int
    import_batch_id: int
    job_id: int | None
    product_name: str
    lot_id: str
    factory_code: str
    business_domain: str
    test_stage: str
    unit_count: int
    pass_count: int
    fail_count: int
    unknown_count: int
    yield_rate: float | None
    source_file_count: int
    published_at_utc: str


@dataclass(frozen=True, slots=True)
class QualityManagementSummary:
    observed_at_utc: str
    from_utc: str
    to_utc: str
    filters: dict[str, str | None]
    methodology: dict[str, str]
    kpis: QualityKpis
    trends: tuple[QualityTrendPoint, ...]
    breakdowns: tuple[QualityBreakdown, ...]
    fail_bins: tuple[FailBinSummary, ...]
    recent_datasets: tuple[QualityDatasetDrilldown, ...]


class ManagementService(Protocol):
    def quality_summary(
        self,
        *,
        principal: Principal,
        from_utc: datetime,
        to_utc: datetime,
        business_domain: str | None = None,
        test_stage: str | None = None,
        factory_code: str | None = None,
        product_name: str | None = None,
        lot_id: str | None = None,
        recent_limit: int = 20,
    ) -> QualityManagementSummary: ...
