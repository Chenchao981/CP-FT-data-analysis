from __future__ import annotations

from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.operations import (
    ConsistencyIssueCounts,
    OperationalStatusCount,
    RecentFailedJob,
    SystemConsistencySummary,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubOperationsService:
    def consistency_summary(
        self, *, recent_failure_limit: int = 5
    ) -> SystemConsistencySummary:
        return SystemConsistencySummary(
            observed_at_utc="2026-08-29T01:02:03.000Z",
            database_ready=True,
            schema_revision="sql2014_0015",
            atomic_schema_ready=True,
            overall_state="HEALTHY",
            management_message="未发现发布链路一致性异常，可继续按计划灰度。",
            job_status_counts=(OperationalStatusCount("FAILED", 2),),
            active_atomic_initial_import_count=1,
            intent_status_counts=(OperationalStatusCount("STAGED", 1),),
            issue_counts=ConsistencyIssueCounts(0, 0),
            current_unknown_result_count=13,
            recent_failed_jobs=(
                RecentFailedJob(
                    job_id=91,
                    job_type="INITIAL_IMPORT",
                    lifecycle_action_type="REPROCESS_UPDATE",
                    import_batch_id=7,
                    business_domain="PRODUCTION",
                    test_stage="FT",
                    error_code="CLEANER_FAILED",
                    attempt_count=2,
                    failed_at_utc="2026-08-29T00:59:00.000Z",
                ),
            )[:recent_failure_limit],
            environment="test",
            database_name="TMS_G0_DEV",
            database_server="sql-dev\\TMS",
        )


def _client() -> TestClient:
    app = create_app()
    app.state.operations_service = StubOperationsService()
    return TestClient(app)


def test_consistency_summary_is_management_readable_and_sanitized() -> None:
    response = _client().get(
        "/api/v1/operations/consistency", params={"recent_failure_limit": 1}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_state"] == "HEALTHY"
    assert payload["schema_revision"] == "sql2014_0015"
    assert payload["environment"] == "test"
    assert payload["database_name"] == "TMS_G0_DEV"
    assert payload["issue_counts"] == {
        "batch_job_intent": 0,
        "dataset_current": 0,
    }
    assert payload["recent_failed_jobs"][0]["error_code"] == "CLEANER_FAILED"
    assert payload["recent_failed_jobs"][0]["lifecycle_action_type"] == (
        "REPROCESS_UPDATE"
    )
    assert "error_message" not in payload["recent_failed_jobs"][0]
    assert "path" not in payload["recent_failed_jobs"][0]


def test_consistency_summary_requires_audit_permission() -> None:
    app = create_app()
    app.state.operations_service = StubOperationsService()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        roles=("OPERATOR",),
        permissions=frozenset({"DATASET_READ"}),
    )

    response = TestClient(app).get("/api/v1/operations/consistency")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_consistency_summary_fails_closed_without_database() -> None:
    app = create_app()
    app.state.operations_service = None

    response = TestClient(app).get("/api/v1/operations/consistency")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"
