"""Verify the actual local report Worker; keep generated test reports for review.

Uses an existing authorized principal in an in-process API client. This is not
browser-login evidence and never changes authentication on the running API.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies import current_principal
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_auth_service import SqlAuthService
from app.main import create_app


def snapshot(engine) -> dict:
    with engine.connect() as connection:
        counts = {
            name: int(connection.execute(text(f"SELECT COUNT_BIG(*) FROM {name}")).scalar_one())
            for name in ("test.test_run", "test.cp_die", "test.ft_device", "test.cp_measurement", "test.ft_measurement")
        }
        versions = [tuple(row) for row in connection.execute(text(
            "SELECT dataset_version_id,dataset_id,version_no,status,is_current "
            "FROM dataset.dataset_version ORDER BY dataset_version_id"
        ))]
    return dict(counts=counts, versions_sha256=hashlib.sha256(json.dumps(versions).encode()).hexdigest())


def response_json(response, expected=200):
    if response.status_code != expected:
        error = response.json().get("error", {})
        raise RuntimeError(f"API {response.status_code}: {error.get('code', 'UNKNOWN')}: {error.get('message', '')}")
    return response.json()


def verify(output: Path) -> dict:
    database = check_database()
    if database["database"] != "TMS_G0_DEV" or database["schema_revision"] != "sql2014_0028":
        raise RuntimeError("Managed report acceptance requires TMS_G0_DEV / sql2014_0028")
    state = json.loads((ROOT / "artifacts/runtime/local-test/processes.json").read_text(encoding="utf-8-sig"))
    ready = json.loads((ROOT / "artifacts/runtime/local-test/export-worker.ready.json").read_text(encoding="utf-8-sig"))
    record = next(item for item in state["processes"] if item["role"] == "export-worker")
    if state["status"] != "RUNNING" or ready["status"] != "READY" or ready["pid"] != record["process_id"]:
        raise RuntimeError("A managed report Worker must already be ready")
    if any(ready[key] != database[key] for key in ("database", "schema_revision", "database_server")):
        raise RuntimeError("Report Worker/database identity mismatch")
    engine = get_engine()
    principal = SqlAuthService(engine).principal_for_development()
    application = create_app()
    application.dependency_overrides[current_principal] = lambda: principal
    before = snapshot(engine)
    evidence = dict(verification="RUNNING", authentication="EXISTING_PRINCIPAL_TESTCLIENT", database=database,
                    worker_id=ready["worker_id"], before=before, reports=[])
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    with TestClient(application) as client:
        for stage in ("CP", "FT"):
            with engine.connect() as connection:
                target = connection.execute(text(
                    "SELECT TOP(1) d.dataset_id,dv.version_no FROM dataset.dataset d "
                    "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                    "WHERE d.test_stage=:stage AND dv.status='PUBLISHED' AND dv.is_current=1 "
                    "ORDER BY dv.dataset_version_id DESC"
                ), dict(stage=stage)).mappings().one()
            context = dict(datasets=[dict(target)], filters={}, parameters=[])
            shell = response_json(client.post("/api/v1/analytics/context", json=context))
            rules = {key: shell["rule_context"][key] for key in ("spec_versions", "bin_mapping_versions", "evaluation_rule_versions")}
            for fmt in ("CSV", "XLSX", "HTML", "PDF"):
                is_report = fmt in {"HTML", "PDF"}
                chart = {"analysis": {"contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1", "section": "OVERVIEW", "overview": {"evaluations": []}}} if is_report else {
                    "analysis_view_state": {"contract_version": "ANALYSIS_VIEW_STATE_V1", "components": {"detail": {"view": "WIDE", "sortBy": "UNIT_SEQUENCE", "sortDirection": "ASC"}}}
                }
                payload = context | dict(
                    contract_version="ANALYTICS_EXPORT_V1", export_scope="REPORT" if is_report else "CURRENT_PAGE",
                    export_format=fmt, template_code="ANALYTICS_OVERVIEW" if is_report else "ANALYTICS_DETAIL", template_version="v1",
                    rule_context=rules, chart_config=chart,
                    display_config=dict(section="overview" if is_report else "detail", page=1, page_size=10, focus_dataset_id=target["dataset_id"]),
                    artifact_ttl_hours=24, idempotency_key=f"managed-report-acceptance-{uuid4()}", reason="Verify managed report generation and authenticated download",
                )
                if not is_report:
                    payload.update(page=1, page_size=10)
                started = time.perf_counter()
                created = response_json(client.post("/api/v1/analytics/exports", json=payload), 202)
                job_id = created["export_job_id"]
                item = dict(stage=stage, dataset=dict(target), format=fmt, job_id=job_id, status=created["status"])
                evidence["reports"].append(item)
                save()
                replay = response_json(client.post("/api/v1/analytics/exports", json=payload), 202)
                assert replay["export_job_id"] == job_id and replay["idempotent_replay"]
                deadline = time.monotonic() + 60
                while True:
                    metadata = response_json(client.get(f"/api/v1/analytics/exports/{job_id}/download-metadata"))
                    if metadata["job_status"] not in {"QUEUED", "RUNNING"} or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                item["status"] = metadata["job_status"]
                save()
                assert metadata["job_status"] == "SUCCESS" and metadata["download_enabled"], metadata["reason_code"]
                artifact = metadata["artifacts"][0]
                downloaded = client.get(f"/api/v1/analytics/exports/{job_id}/artifacts/{artifact['export_artifact_id']}/download")
                assert downloaded.status_code == 200
                content = downloaded.content
                assert len(content) == artifact["file_size"]
                assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
                if fmt == "CSV":
                    assert len(list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))) == 11
                elif fmt == "XLSX":
                    from openpyxl import load_workbook
                    book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                    try:
                        assert len(list(book["Data"].values)) == 11
                    finally:
                        book.close()
                elif fmt == "PDF":
                    assert content.startswith(b"%PDF-")
                else:
                    assert b"<html" in content.lower()
                with engine.connect() as connection:
                    lease_owner = connection.execute(text("SELECT lease_owner FROM delivery.export_job WHERE export_job_id=:job"), dict(job=job_id)).scalar_one()
                item.update(elapsed_seconds=round(time.perf_counter()-started, 3), bytes=len(content), sha256=artifact["sha256"], lease_owner=lease_owner)
                # Completion may clear the lease; it may never name another active worker.
                assert lease_owner in (None, ready["worker_id"])
                save()
    after = snapshot(engine)
    assert before == after, "Formal facts or Current versions changed during report verification"
    evidence.update(after=after, verification="PASS")
    save()
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.output)
    print(json.dumps(dict(verification=result["verification"], reports=len(result["reports"])), ensure_ascii=False))
