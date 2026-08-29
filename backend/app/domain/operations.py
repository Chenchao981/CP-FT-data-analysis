from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OperationalStatusCount:
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class ConsistencyIssueCounts:
    batch_job_intent: int | None
    dataset_current: int | None


@dataclass(frozen=True, slots=True)
class RecentFailedJob:
    job_id: int
    job_type: str
    lifecycle_action_type: str | None
    import_batch_id: int | None
    business_domain: str | None
    test_stage: str | None
    error_code: str
    attempt_count: int
    failed_at_utc: str


@dataclass(frozen=True, slots=True)
class SystemConsistencySummary:
    observed_at_utc: str
    database_ready: bool
    schema_revision: str
    atomic_schema_ready: bool
    overall_state: str
    management_message: str
    job_status_counts: tuple[OperationalStatusCount, ...]
    active_atomic_initial_import_count: int | None
    intent_status_counts: tuple[OperationalStatusCount, ...] | None
    issue_counts: ConsistencyIssueCounts
    current_unknown_result_count: int
    recent_failed_jobs: tuple[RecentFailedJob, ...]
    environment: str = "unknown"
    database_name: str = "unknown"
    database_server: str = "unknown"


class OperationsService(Protocol):
    def consistency_summary(
        self, *, recent_failure_limit: int = 5
    ) -> SystemConsistencySummary: ...
