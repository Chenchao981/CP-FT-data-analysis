from __future__ import annotations

from datetime import datetime

from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.m2_queries import (
    AvailableAction,
    BatchIdentitySummary,
    CleanerReleaseSummary,
    CurrentDatasetCatalogItem,
    DatasetSummary,
    FinalizeIntentSummary,
    JobDetails,
    JobSafeDetails,
    JobSafeSummary,
    JobTimelineEvent,
    M2Page,
    ProcessingRunSummary,
    SourceLineageSummary,
    StageResultPageItem,
    StageUploadPageItem,
)
from app.main import create_app
from fastapi.testclient import TestClient


class StubM2QueryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    def list_uploads_page(
        self, principal, business_domain, test_stage, filters
    ) -> M2Page:
        self.calls.append((f"uploads:{business_domain}:{test_stage}", principal, filters))
        return M2Page(
            items=(
                StageUploadPageItem(
                    import_batch_id=41,
                    sequence_no=1,
                    receipt_id=81,
                    original_file_name="sample.xlsx",
                    extension="xlsx",
                    size_bytes=120,
                    factory_code="RIYUEXIN",
                    upload_time_utc="2026-08-29T01:00:00.000Z",
                    completion_time_utc=None,
                    uploader_login="owner",
                    uploader_name="Owner",
                    status="QUEUED",
                    source_file_id=91,
                    latest_job_id=101,
                    error_code=None,
                    error_message=None,
                    action_required=None,
                    queue_age_seconds=73,
                ),
            ),
            total=21,
            page=filters.page,
            page_size=filters.page_size,
        )

    def list_results_page(
        self, principal, business_domain, test_stage, filters
    ) -> M2Page:
        self.calls.append((f"results:{business_domain}:{test_stage}", principal, filters))
        return M2Page(
            items=(
                StageResultPageItem(
                    result_summary_id=8,
                    import_batch_id=41,
                    job_id=101,
                    data_name="FT result",
                    product_name="NCE-1",
                    lot_id="LOT-1",
                    wafer_count=None,
                    factory_code="RIYUEXIN",
                    test_item_count=20,
                    unit_count=100,
                    pass_count=None,
                    yield_rate=None,
                    status="PROCESSED",
                    data_type="FT",
                    dataset_id=201,
                    dataset_version_no=1,
                    created_at_utc="2026-08-29T01:10:00.000Z",
                ),
            ),
            total=1,
            page=filters.page,
            page_size=filters.page_size,
        )

    def list_current_datasets(self, principal, filters) -> M2Page:
        self.calls.append(("catalog", principal, filters))
        return M2Page(
            items=(
                CurrentDatasetCatalogItem(
                    dataset_id=201,
                    dataset_version_id=301,
                    version_no=1,
                    import_batch_id=41,
                    job_id=101,
                    processing_run_id=501,
                    product_name="NCE-1",
                    lot_id="LOT-1",
                    factory_code="RIYUEXIN",
                    business_domain="PRODUCTION",
                    test_stage="FT",
                    status="PUBLISHED",
                    unit_count=100,
                    pass_count=None,
                    yield_rate=None,
                    source_file_count=1,
                    processed_at_utc="2026-08-29T01:10:00.000Z",
                ),
            ),
            total=1,
            page=filters.page,
            page_size=filters.page_size,
        )

    def get_job_details(self, principal, job_id) -> JobDetails:
        self.calls.append(("details", principal, job_id))
        parent = JobSafeSummary(
            job_id=100,
            job_type="INITIAL_IMPORT",
            lifecycle_action_type=None,
            status="NEEDS_INPUT",
            import_batch_id=41,
            parent_job_id=None,
            requested_at_utc="2026-08-29T00:30:00.000Z",
            started_at_utc="2026-08-29T00:31:00.000Z",
            finished_at_utc="2026-08-29T00:32:00.000Z",
            error_code=None,
            error_message=None,
            attempt_count=1,
            max_attempts=3,
        )
        job = JobSafeDetails(
            job_id=job_id,
            job_type="INITIAL_IMPORT",
            lifecycle_action_type="REPROCESS_UPDATE",
            status="SUCCESS",
            import_batch_id=41,
            parent_job_id=100,
            requested_at_utc="2026-08-29T01:00:00.000Z",
            started_at_utc="2026-08-29T01:01:00.000Z",
            finished_at_utc="2026-08-29T01:10:00.000Z",
            error_code=None,
            error_message=None,
            attempt_count=1,
            max_attempts=3,
            source_file_id=None,
            analysis_session_id=None,
            cleaner_release_id=11,
            trigger_type="SYSTEM",
            requested_by="owner",
            reason="Lot recovery",
            not_before_utc="2026-08-29T01:00:00.000Z",
            heartbeat_at_utc="2026-08-29T01:09:00.000Z",
            lease_expires_at_utc=None,
            finalize_protocol="ATOMIC_V1",
            queue_age_seconds=None,
        )
        return JobDetails(
            job=job,
            parent=parent,
            children=(),
            release=CleanerReleaseSummary(11, "FT_CLEANER", "1.2.3", "a" * 64),
            batch=BatchIdentitySummary(
                41, "PRODUCTION", "FT", "RIYUEXIN", "PROCESSED", 1
            ),
            intent=FinalizeIntentSummary(
                "FINALIZED",
                "2026-08-29T01:08:00.000Z",
                "2026-08-29T01:10:00.000Z",
                None,
            ),
            run=ProcessingRunSummary(
                501,
                "PUBLISHED",
                "2026-08-29T01:01:00.000Z",
                "2026-08-29T01:10:00.000Z",
            ),
            dataset=DatasetSummary(201, 301, 1, "PUBLISHED", True),
            timeline=(
                JobTimelineEvent(
                    "JOB_QUEUED", "QUEUED", "2026-08-29T01:00:00.000Z"
                ),
            ),
            actions=(AvailableAction("VIEW_RESULT", "查看结果", True, None),),
            sources=(
                SourceLineageSummary(
                    81,
                    1,
                    "source.xlsx",
                    1234,
                    "b" * 64,
                    "WRITER_VERIFIED",
                ),
            ),
        )


def _client() -> tuple[TestClient, StubM2QueryService]:
    app = create_app()
    stub = StubM2QueryService()
    app.state.m2_query_service = stub
    return TestClient(app), stub


def test_upload_page_has_stable_shape_and_validates_ignored_filters() -> None:
    client, stub = _client()

    response = client.get(
        "/api/v1/production/ft/uploads/page",
        params={
            "page": 2,
            "page_size": 10,
            "factory_code": "riyuexin",
            "status": "queued",
            "product_name": "validated but not applicable",
            "lot_id": "LOT-IGNORED",
            "from_utc": "2026-08-01T08:00:00+08:00",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "import_batch_id": 41,
                "sequence_no": 1,
                "receipt_id": 81,
                "original_file_name": "sample.xlsx",
                "extension": "xlsx",
                "size_bytes": 120,
                "factory_code": "RIYUEXIN",
                "upload_time_utc": "2026-08-29T01:00:00.000Z",
                "completion_time_utc": None,
                "uploader_login": "owner",
                "uploader_name": "Owner",
                "status": "QUEUED",
                "source_file_id": 91,
                "latest_job_id": 101,
                "error_code": None,
                "error_message": None,
                "action_required": None,
                "queue_age_seconds": 73,
            }
        ],
        "total": 21,
        "page": 2,
        "page_size": 10,
    }
    call, principal, filters = stub.calls[0]
    assert call == "uploads:PRODUCTION:FT"
    assert principal.user_id == 1
    assert filters.factory_code == "RIYUEXIN"
    assert filters.status == "QUEUED"
    assert filters.product_name == "validated but not applicable"
    assert filters.from_utc == datetime(2026, 8, 1, 0, 0)


def test_results_page_returns_nullable_metrics_and_job_id() -> None:
    response = _client()[0].get(
        "/api/v1/engineering/cp/results/page",
        params={"page": 1, "page_size": 100, "lot_id": "LOT-1"},
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["job_id"] == 101
    assert item["pass_count"] is None
    assert item["yield_rate"] is None


def test_current_catalog_applies_catalog_filters() -> None:
    client, stub = _client()

    response = client.get(
        "/api/v1/catalog/datasets/current",
        params={
            "business_domain": "production",
            "test_stage": "ft",
            "status": "published",
            "product_name": "NCE-1",
            "to_utc": "2026-08-29T02:00:00Z",
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["dataset_id"] == 201
    assert item["source_file_count"] == 1
    filters = stub.calls[0][2]
    assert filters.business_domain == "PRODUCTION"
    assert filters.test_stage == "FT"
    assert filters.status == "PUBLISHED"


def test_job_details_matches_frontend_safe_contract() -> None:
    response = _client()[0].get("/api/v1/jobs/101/details")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["job_id"] == 101
    assert payload["job"]["lifecycle_action_type"] == "REPROCESS_UPDATE"
    assert payload["job"]["not_before_utc"] == "2026-08-29T01:00:00.000Z"
    assert payload["parent"]["job_id"] == 100
    assert payload["release"]["content_sha256"] == "a" * 64
    assert payload["batch"]["source_file_count"] == 1
    assert payload["intent"]["status"] == "FINALIZED"
    assert payload["run"]["processing_run_id"] == 501
    assert payload["dataset"]["is_current"] is True
    assert payload["timeline"][0]["event_code"] == "JOB_QUEUED"
    assert payload["sources"][0]["original_file_name"] == "source.xlsx"
    assert payload["sources"][0]["lineage_basis"] == "WRITER_VERIFIED"
    assert payload["actions"] == [
        {"code": "VIEW_RESULT", "label": "查看结果", "enabled": True, "reason": None}
    ]
    assert "lease_token" not in payload["job"]


def test_page_filters_fail_closed() -> None:
    client, _stub = _client()

    timezone_response = client.get(
        "/api/v1/production/ft/uploads/page",
        params={"from_utc": "2026-08-29T01:00:00"},
    )
    status_response = client.get(
        "/api/v1/catalog/datasets/current", params={"status": "SUPERSEDED"}
    )
    factory_response = client.get(
        "/api/v1/production/ft/results/page", params={"factory_code": "bad value"}
    )
    page_size_response = client.get(
        "/api/v1/production/ft/results/page", params={"page_size": 101}
    )

    assert timezone_response.status_code == 422
    assert timezone_response.json()["error"]["code"] == "FILTER_TIMEZONE_REQUIRED"
    assert status_response.status_code == 422
    assert status_response.json()["error"]["code"] == "FILTER_VALUE_INVALID"
    assert factory_response.status_code == 422
    assert factory_response.json()["error"]["code"] == "FILTER_VALUE_INVALID"
    assert page_size_response.status_code == 422
    assert page_size_response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_m2_query_endpoints_fail_closed_without_database_service() -> None:
    app = create_app()
    app.state.m2_query_service = None
    client = TestClient(app)

    upload = client.get("/api/v1/production/ft/uploads/page")
    catalog = client.get("/api/v1/catalog/datasets/current")
    details = client.get("/api/v1/jobs/101/details")

    assert upload.status_code == 503
    assert catalog.status_code == 503
    assert details.status_code == 503
    assert upload.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"


def test_management_reader_can_reach_scoped_catalog_results_and_job_details() -> None:
    app = create_app()
    stub = StubM2QueryService()
    app.state.m2_query_service = stub
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=23,
        login_name="manager",
        display_name="Manager",
        roles=("MANAGER_VIEWER",),
        permissions=frozenset({"DATASET_READ"}),
    )
    client = TestClient(app)

    catalog = client.get("/api/v1/catalog/datasets/current")
    results = client.get("/api/v1/production/ft/results/page")
    details = client.get("/api/v1/jobs/101/details")

    assert catalog.status_code == 200
    assert results.status_code == 200
    assert details.status_code == 200
    assert all(call[1].user_id == 23 for call in stub.calls)


def test_current_data_scope_does_not_replace_dataset_read_permission() -> None:
    app = create_app()
    stub = StubM2QueryService()
    app.state.m2_query_service = stub
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=24,
        login_name="ungranted",
        display_name="Ungranted",
        roles=("MANAGER_VIEWER",),
        permissions=frozenset(),
    )

    response = TestClient(app).get("/api/v1/catalog/datasets/current")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert stub.calls == []
