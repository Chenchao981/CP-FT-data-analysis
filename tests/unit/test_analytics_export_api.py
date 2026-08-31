from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from app.api.analytics_exports import router
from app.api.dependencies import current_principal
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler, validation_error_handler
from app.domain.analytics_exports import (
    AnalyticsExportDatasetRecord,
    AnalyticsExportDownloadMetadata,
    AnalyticsExportDownloadTarget,
    AnalyticsExportPage,
    AnalyticsExportRecord,
    resolve_analytics_export_template,
)
from app.domain.auth import Principal
from app.domain.saved_analyses import SavedAnalysisRuleContext
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def _principal(*, export: bool = True, read: bool = True) -> Principal:
    permissions = set()
    if export:
        permissions.add("EXPORT_DATA")
    if read:
        permissions.add("DATASET_READ")
    return Principal(
        user_id=7,
        login_name="export.user",
        display_name="Export User",
        roles=("CP_ENGINEER",),
        permissions=frozenset(permissions),
    )


def _payload(dataset_count: int = 1) -> dict:
    return {
        "contract_version": "ANALYTICS_EXPORT_V1",
        "datasets": [
            {"dataset_id": dataset_id, "version_no": 2}
            for dataset_id in range(1, dataset_count + 1)
        ],
        "filters": {
            "lot_ids": ["LOT-1"],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": ["PASS", "FAIL"],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        "parameters": ["VTH"],
        "export_scope": "FILTERED_RESULT",
        "export_format": "CSV",
        "template_code": "PARAMETER_DETAIL",
        "template_version": "v1",
        "rule_context": {
            "spec_versions": [],
            "bin_mapping_versions": [],
            "evaluation_rule_versions": [],
        },
        "chart_config": {
            "correlation_min_abs": 0.25,
            "show_spec_overlay": True,
        },
        "display_config": {"section": "parameter", "page": 1, "page_size": 50},
        "artifact_ttl_hours": 24,
        "idempotency_key": "analytics-export-0001",
        "reason": "Export the reviewed parameter detail",
    }


def _record(dataset_count: int = 1) -> AnalyticsExportRecord:
    return AnalyticsExportRecord(
        export_job_id=81,
        requested_by=7,
        contract_version="ANALYTICS_EXPORT_V1",
        worker_contract_version="ANALYTICS_EXPORT_WORKER_V1",
        generation_mode="QUEUED_WORKER",
        status="QUEUED",
        export_scope="FILTERED_RESULT",
        export_format="CSV",
        template_code="PARAMETER_DETAIL",
        template_version="v1",
        datasets=tuple(
            AnalyticsExportDatasetRecord(
                dataset_version_id=100 + dataset_id,
                dataset_id=dataset_id,
                version_no=2,
                ordinal_no=dataset_id,
                test_stage="FT",
            )
            for dataset_id in range(1, dataset_count + 1)
        ),
        filters={
            "lot_ids": ["LOT-1"],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": ["FAIL", "PASS"],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        parameters=("VTH",),
        filter_hash="a" * 64,
        context_hash="b" * 64,
        rule_context=SavedAnalysisRuleContext(),
        chart_config={"correlation_min_abs": 0.25, "show_spec_overlay": True},
        display_config={"section": "parameter", "page": 1, "page_size": 50},
        presentation_hash="d" * 64,
        artifact_ttl_hours=24,
        page=None,
        page_size=None,
        idempotency_key="analytics-export-0001",
        request_reason_sha256="c" * 64,
        requested_at_utc="2026-08-31T01:00:00",
        started_at_utc=None,
        finished_at_utc=None,
        exported_row_count=None,
        row_version="0000000000000001",
        idempotent_replay=False,
    )


class _DatasetService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def assert_dataset_access(self, dataset_id, principal, *, version_no=None):
        self.calls.append((dataset_id, int(version_no), principal.user_id))
        if dataset_id == 99:
            raise DomainError("DATASET_SCOPE_DENIED", "scope denied", 403)


class _ExportService:
    def __init__(self) -> None:
        self.record = _record()

    def create(self, request, principal):
        self.record = _record(len(request.datasets))
        return self.record

    def list_page(self, principal, *, page, page_size):
        return AnalyticsExportPage((self.record,), 1, page, page_size)

    def get(self, export_job_id, principal):
        return replace(self.record, export_job_id=export_job_id)

    def download_metadata(self, export_job_id, principal):
        return AnalyticsExportDownloadMetadata(
            export_job_id=export_job_id,
            job_status="QUEUED",
            availability="PENDING_GENERATION",
            download_enabled=False,
            reason_code="ANALYTICS_EXPORT_WORKER_REQUIRED",
            artifacts=(),
        )

    def resolve_download(self, export_job_id, export_artifact_id, principal):
        assert export_job_id == 81
        assert export_artifact_id == 501
        return AnalyticsExportDownloadTarget(
            path=Path(__file__),
            file_name="analytics-export-81.csv",
            mime_type="text/csv; charset=utf-8",
        )

    def cancel(self, export_job_id, request, principal):
        assert request.confirmation == "CANCEL"
        return replace(
            self.record,
            export_job_id=export_job_id,
            status="CANCELLED",
            row_version="0000000000000002",
        )


def _client(principal: Principal) -> tuple[TestClient, _DatasetService]:
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.state.analytics_export_service = _ExportService()
    datasets = _DatasetService()
    app.state.dataset_service = datasets
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(router, prefix="/api/v1/analytics")
    return TestClient(app), datasets


def test_create_requires_both_permissions_and_authorizes_all_eight_versions() -> None:
    denied_export, _ = _client(_principal(export=False))
    denied_read, _ = _client(_principal(read=False))
    assert (
        denied_export.post("/api/v1/analytics/exports", json=_payload()).status_code
        == 403
    )
    assert (
        denied_read.post("/api/v1/analytics/exports", json=_payload()).status_code
        == 403
    )

    client, datasets = _client(_principal())
    response = client.post("/api/v1/analytics/exports", json=_payload(8))
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert response.json()["generation_mode"] == "QUEUED_WORKER"
    assert response.json()["chart_config"]["correlation_min_abs"] == 0.25
    assert response.json()["presentation_hash"] == "d" * 64
    assert [item[:2] for item in datasets.calls] == [
        (dataset_id, 2) for dataset_id in range(1, 9)
    ]


def test_list_contract_exposes_page_local_integrity_isolation_metadata() -> None:
    client, _ = _client(_principal())

    response = client.get("/api/v1/analytics/exports?page=1&page_size=20")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["integrity_blocked_job_ids"] == []
    assert response.json()["integrity_blocked_count"] == 0


def test_request_contract_rejects_client_paths_sql_formulas_and_unsafe_scope() -> None:
    client, _ = _client(_principal())
    for unexpected in ("storage_uri", "sql", "formula", "output_path"):
        payload = _payload()
        payload[unexpected] = "SELECT * FROM test.measurement"
        response = client.post("/api/v1/analytics/exports", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    path_template = _payload()
    path_template["template_code"] = "../../report"
    assert (
        client.post("/api/v1/analytics/exports", json=path_template).status_code == 422
    )

    unsafe_format = _payload()
    unsafe_format["export_format"] = "PDF"
    assert (
        client.post("/api/v1/analytics/exports", json=unsafe_format).status_code == 422
    )

    unknown_template = _payload()
    unknown_template["template_code"] = "CLIENT_DEFINED_TEMPLATE"
    assert (
        client.post("/api/v1/analytics/exports", json=unknown_template).status_code
        == 422
    )

    unknown_version = _payload()
    unknown_version["template_version"] = "v2"
    assert (
        client.post("/api/v1/analytics/exports", json=unknown_version).status_code
        == 422
    )

    incompatible_scope = _payload()
    incompatible_scope["template_code"] = "ANALYTICS_OVERVIEW"
    assert (
        client.post("/api/v1/analytics/exports", json=incompatible_scope).status_code
        == 422
    )

    incompatible_format = _payload()
    incompatible_format.update(
        {
            "template_code": "ANALYTICS_OVERVIEW",
            "export_scope": "REPORT",
            "export_format": "BIN_TXT",
        }
    )
    assert (
        client.post("/api/v1/analytics/exports", json=incompatible_format).status_code
        == 422
    )

    current_page = _payload()
    current_page["export_scope"] = "CURRENT_PAGE"
    assert (
        client.post("/api/v1/analytics/exports", json=current_page).status_code == 422
    )

    oversized_presentation = _payload()
    oversized_presentation["chart_config"] = {"title": "x" * 4_001}
    response = client.post("/api/v1/analytics/exports", json=oversized_presentation)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_current_page_requires_and_accepts_exact_frozen_detail_state() -> None:
    client, _ = _client(_principal())
    payload = _payload()
    payload.update({"export_scope": "CURRENT_PAGE", "page": 2, "page_size": 25})
    payload["display_config"] = {
        "section": "detail",
        "page": 2,
        "page_size": 25,
        "focus_dataset_id": 1,
    }
    payload["chart_config"]["analysis_view_state"] = {
        "contract_version": "ANALYSIS_VIEW_STATE_V1",
        "components": {
            "detail": {
                "view": "LONG",
                "sortBy": "RESULT",
                "sortDirection": "DESC",
            }
        },
    }

    assert client.post("/api/v1/analytics/exports", json=payload).status_code == 202

    stale_page = {**payload, "page": 3}
    assert client.post("/api/v1/analytics/exports", json=stale_page).status_code == 422
    invalid_sort = {
        **payload,
        "chart_config": {
            **payload["chart_config"],
            "analysis_view_state": {
                "contract_version": "ANALYSIS_VIEW_STATE_V1",
                "components": {
                    "detail": {
                        "view": "LONG",
                        "sortBy": "CLIENT_SQL_EXPRESSION",
                        "sortDirection": "DESC",
                    }
                },
            },
        },
    }
    assert (
        client.post("/api/v1/analytics/exports", json=invalid_sort).status_code == 422
    )


def test_current_page_detail_filters_are_closed_typed_and_v1_optional() -> None:
    client, _ = _client(_principal())
    payload = _payload()
    payload.update({"export_scope": "CURRENT_PAGE", "page": 1, "page_size": 25})
    payload["display_config"] = {
        "section": "detail",
        "page": 1,
        "page_size": 25,
        "focus_dataset_id": 1,
    }
    detail = {
        "view": "WIDE",
        "sortBy": "UNIT_SEQUENCE",
        "sortDirection": "ASC",
        "evaluation_filter": {
            "evaluation_type": "PAT",
            "evaluation_results": ["FAIL", "NOT_EVALUATED"],
            "rule_code": "PAT_ROBUST_IQR",
            "rule_version": "V2",
        },
        "measurement_filter": {
            "parameter": "VTH",
            "lower_bound": -1.5,
            "upper_bound": 2.5,
            "lower_inclusive": False,
            "upper_inclusive": True,
        },
    }
    payload["chart_config"]["analysis_view_state"] = {
        "contract_version": "ANALYSIS_VIEW_STATE_V1",
        "components": {"detail": detail},
    }

    assert client.post("/api/v1/analytics/exports", json=payload).status_code == 202

    # ANALYSIS_VIEW_STATE_V1 remains backward compatible when both filters are absent.
    legacy = json.loads(json.dumps(payload))
    legacy_detail = legacy["chart_config"]["analysis_view_state"]["components"][
        "detail"
    ]
    legacy_detail.pop("evaluation_filter")
    legacy_detail.pop("measurement_filter")
    assert client.post("/api/v1/analytics/exports", json=legacy).status_code == 202

    invalid_variants = []
    arbitrary_predicate = json.loads(json.dumps(payload))
    arbitrary_predicate["chart_config"]["analysis_view_state"]["components"]["detail"][
        "where_expression"
    ] = "1=1; DROP TABLE test.measurement"
    invalid_variants.append(arbitrary_predicate)

    unknown_evaluation = json.loads(json.dumps(payload))
    unknown_evaluation["chart_config"]["analysis_view_state"]["components"]["detail"][
        "evaluation_filter"
    ]["evaluation_type"] = "CLIENT_EXPRESSION"
    invalid_variants.append(unknown_evaluation)

    invalid_result = json.loads(json.dumps(payload))
    invalid_result["chart_config"]["analysis_view_state"]["components"]["detail"][
        "evaluation_filter"
    ]["evaluation_results"] = ["FAIL OR 1=1"]
    invalid_variants.append(invalid_result)

    partial_rule = json.loads(json.dumps(payload))
    partial_rule["chart_config"]["analysis_view_state"]["components"]["detail"][
        "evaluation_filter"
    ].pop("rule_version")
    invalid_variants.append(partial_rule)

    arbitrary_measurement = json.loads(json.dumps(payload))
    arbitrary_measurement["chart_config"]["analysis_view_state"]["components"][
        "detail"
    ]["measurement_filter"]["predicate"] = "value_numeric > 0"
    invalid_variants.append(arbitrary_measurement)

    for invalid in invalid_variants:
        assert client.post("/api/v1/analytics/exports", json=invalid).status_code == 422


def test_report_request_requires_matching_versioned_analysis_reconstruction() -> None:
    client, _ = _client(_principal())
    payload = _payload()
    payload.update(
        {
            "template_code": "ANALYTICS_OVERVIEW",
            "export_scope": "REPORT",
            "export_format": "CSV",
        }
    )

    assert client.post("/api/v1/analytics/exports", json=payload).status_code == 422

    payload["chart_config"]["analysis"] = {
        "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
        "section": "PARAMETER_ANALYSIS",
        "parameter_analysis": {
            "parameters": ["VTH"],
            "analyses": ["DESCRIPTIVE"],
        },
    }
    assert client.post("/api/v1/analytics/exports", json=payload).status_code == 422

    payload["chart_config"]["analysis"] = {
        "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
        "section": "OVERVIEW",
        "overview": {"evaluations": []},
    }
    assert client.post("/api/v1/analytics/exports", json=payload).status_code == 202


def test_registered_template_format_matrix_is_explicit_and_fail_closed() -> None:
    report_templates = (
        "ANALYTICS_OVERVIEW",
        "PARAMETER_ANALYSIS",
        "PARAMETER_RELATIONSHIP",
        "SPATIAL_ANALYSIS",
        "FT_QUALITY",
        "WAFER_SUMMARY",
    )
    for template in report_templates:
        for export_format in ("CSV", "XLSX", "HTML", "PDF", "PNG"):
            resolve_analytics_export_template(template, "v1", "REPORT", export_format)
        with pytest.raises(ValueError):
            resolve_analytics_export_template(template, "v1", "REPORT", "BIN_TXT")

    for template in ("ANALYTICS_DETAIL", "PARAMETER_DETAIL"):
        for export_format in ("CSV", "XLSX", "BIN_TXT"):
            resolve_analytics_export_template(
                template, "v1", "FILTERED_RESULT", export_format
            )
        with pytest.raises(ValueError):
            resolve_analytics_export_template(template, "v1", "REPORT", "PDF")


def test_status_download_metadata_and_cancel_are_path_free_and_fail_closed() -> None:
    client, _ = _client(_principal())
    status_response = client.get("/api/v1/analytics/exports/81")
    metadata = client.get("/api/v1/analytics/exports/81/download-metadata")
    cancelled = client.post(
        "/api/v1/analytics/exports/81/cancel",
        json={
            "confirmation": "CANCEL",
            "expected_row_version": "0000000000000001",
            "reason": "Cancel before the Worker claims it",
        },
    )

    assert status_response.status_code == 200
    assert metadata.status_code == 200
    assert metadata.json() == {
        "export_job_id": 81,
        "job_status": "QUEUED",
        "availability": "PENDING_GENERATION",
        "download_enabled": False,
        "reason_code": "ANALYTICS_EXPORT_WORKER_REQUIRED",
        "artifacts": [],
    }
    assert "storage_uri" not in metadata.text
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_download_handler_streams_authorized_target_without_exposing_storage_path() -> (
    None
):
    client, _ = _client(_principal())
    response = client.get("/api/v1/analytics/exports/81/artifacts/501/download")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "analytics-export-81.csv" in response.headers["content-disposition"]
    assert str(Path(__file__).parent) not in response.headers["content-disposition"]


def test_main_wires_analytics_export_service_and_router(monkeypatch) -> None:
    from app import main as main_module
    from app.core.config import get_settings
    from app.infrastructure.sql_analytics_export_service import (
        SqlAnalyticsExportService,
    )

    get_settings.cache_clear()
    monkeypatch.setenv("TMS_DATABASE_URL", "configured-for-constructor-test")
    monkeypatch.setenv("TMS_JOB_REPOSITORY", "memory")
    fake_engine = object()
    monkeypatch.setattr(main_module, "get_engine", lambda: fake_engine)
    try:
        application = main_module.create_app()
    finally:
        get_settings.cache_clear()

    assert isinstance(
        application.state.analytics_export_service, SqlAnalyticsExportService
    )
    assert any(
        getattr(route, "path", None) == "/api/v1/analytics/exports"
        for route in application.routes
    )
