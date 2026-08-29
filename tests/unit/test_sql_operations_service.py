from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.core.errors import DomainError
from app.infrastructure.sql_operations_service import (
    _CONSISTENCY_ISSUE_SQL,
    SqlOperationsService,
)


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self):
        return self._rows

    def scalar_one(self):
        assert self._scalar is not None
        return self._scalar


class _Connection:
    def __init__(self, *, atomic_schema_ready: bool = True) -> None:
        self.atomic_schema_ready = atomic_schema_ready
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "AS observed_at_utc" in sql:
            return _Result(
                rows=[
                    {
                        "observed_at_utc": datetime(2026, 8, 29, 1, 2, 3, tzinfo=UTC),
                        "database_name": "TMS_G0_DEV",
                        "database_server": "sql-dev\\TMS",
                        "schema_revision": "sql2014_0015",
                        "atomic_schema_ready": self.atomic_schema_ready,
                    }
                ]
            )
        if "GROUP BY status" in sql and "processing_job" in sql:
            return _Result(
                rows=[
                    {"status": "RUNNING", "item_count": 2},
                    {"status": "FAILED", "item_count": 3},
                ]
            )
        if "batch_job_intent_anomaly_count" in sql:
            return _Result(
                rows=[
                    {
                        "batch_job_intent_anomaly_count": 0,
                        "dataset_current_anomaly_count": 0,
                    }
                ]
            )
        if "finalize_protocol='ATOMIC_V1'" in sql and "COUNT_BIG" in sql:
            return _Result(scalar=2)
        if "GROUP BY status" in sql and "finalize_intent" in sql:
            return _Result(
                rows=[
                    {"status": "STAGED", "item_count": 1},
                    {"status": "FINALIZED", "item_count": 8},
                ]
            )
        if "v_current_unit_result" in sql:
            return _Result(scalar=17)
        if "SELECT TOP (:limit)" in sql:
            return _Result(
                rows=[
                    {
                        "job_id": 51,
                        "job_type": "INITIAL_IMPORT",
                        "lifecycle_action_type": "REPROCESS_UPDATE",
                        "import_batch_id": 9,
                        "business_domain": "PRODUCTION",
                        "test_stage": "CP",
                        "error_code": "C:/secret/path.txt",
                        "attempt_count": 2,
                        "failed_at_utc": datetime(2026, 8, 29, 0, 59, tzinfo=UTC),
                    }
                ]
            )
        raise AssertionError(f"unexpected SQL: {sql}")


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def test_sql_summary_normalizes_counts_and_does_not_expose_failure_details() -> None:
    connection = _Connection()
    summary = SqlOperationsService(
        _Engine(connection), environment="test"
    ).consistency_summary(  # type: ignore[arg-type]
        recent_failure_limit=7
    )

    assert summary.overall_state == "HEALTHY"
    assert summary.database_ready is True
    assert summary.schema_revision == "sql2014_0015"
    assert summary.environment == "test"
    assert summary.database_name == "TMS_G0_DEV"
    assert summary.database_server == "sql-dev\\TMS"
    assert summary.active_atomic_initial_import_count == 2
    assert {item.status: item.count for item in summary.job_status_counts} == {
        "QUEUED": 0,
        "RUNNING": 2,
        "NEEDS_INPUT": 0,
        "SUCCESS": 0,
        "FAILED": 3,
        "CANCELLED": 0,
    }
    assert {item.status: item.count for item in summary.intent_status_counts or ()} == {
        "STAGED": 1,
        "FINALIZED": 8,
        "ABORTED": 0,
    }
    assert summary.current_unknown_result_count == 17
    failure = summary.recent_failed_jobs[0]
    assert failure.lifecycle_action_type == "REPROCESS_UPDATE"
    assert failure.error_code == "UNCLASSIFIED_FAILURE"
    assert failure.failed_at_utc == "2026-08-29T00:59:00.000Z"
    failure_query, parameters = next(
        call for call in connection.calls if "SELECT TOP (:limit)" in call[0]
    )
    assert parameters == {"limit": 7}
    assert "error_message" not in failure_query
    assert "output_uri" not in failure_query
    assert "canonical_storage_uri" not in failure_query


def test_sql_summary_reports_schema_upgrade_without_querying_atomic_tables() -> None:
    connection = _Connection(atomic_schema_ready=False)
    summary = SqlOperationsService(_Engine(connection)).consistency_summary()  # type: ignore[arg-type]

    assert summary.overall_state == "SCHEMA_UPGRADE_REQUIRED"
    assert summary.active_atomic_initial_import_count is None
    assert summary.intent_status_counts is None
    assert summary.issue_counts.batch_job_intent is None
    assert not any(
        "GROUP BY status" in sql and "finalize_intent" in sql
        for sql, _parameters in connection.calls
    )


@pytest.mark.parametrize("limit", [0, 21])
def test_sql_summary_rejects_invalid_recent_failure_limit(limit: int) -> None:
    with pytest.raises(DomainError) as exc_info:
        SqlOperationsService(_Engine(_Connection())).consistency_summary(  # type: ignore[arg-type]
            recent_failure_limit=limit
        )

    assert exc_info.value.code == "OPERATIONS_LIMIT_INVALID"


def test_sql_summary_converts_database_failures_to_a_sanitized_error() -> None:
    class BrokenEngine:
        @contextmanager
        def connect(self):
            raise RuntimeError("server=secret;password=secret")
            yield

    with pytest.raises(DomainError) as exc_info:
        SqlOperationsService(BrokenEngine()).consistency_summary()  # type: ignore[arg-type]

    assert exc_info.value.code == "OPERATIONS_SNAPSHOT_UNAVAILABLE"
    assert "secret" not in exc_info.value.message


def test_consistency_contract_accepts_logically_archived_finalized_versions() -> None:
    assert "dv.status NOT IN('PUBLISHED','SUPERSEDED','ARCHIVED')" in (
        _CONSISTENCY_ISSUE_SQL
    )
    assert "dv.status='ARCHIVED' AND (" in _CONSISTENCY_ISSUE_SQL
    assert "pr.status<>'SUPERSEDED'" in _CONSISTENCY_ISSUE_SQL
    assert "d.lifecycle_status='ARCHIVED'" in _CONSISTENCY_ISSUE_SQL
