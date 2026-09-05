from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.core.security import issue_access_token
from app.domain.jobs import JobStatus, JobType
from app.infrastructure.database import get_engine
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_auth_service import SqlAuthService
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_quick_analysis_service import SqlQuickAnalysisService
from app.main import create_app
from app.workers.route_a_worker import DatabaseJobWorker, QuickPatHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Quick PAT through API, SQL queue, Worker, and download"
    )
    parser.add_argument("--expected-database", default="TMS_G0_DEV")
    parser.add_argument("--source-root", default="JIEQUN_FT_SHARED")
    parser.add_argument("--relative-path", default="520data")
    return parser.parse_args()


def _fact_counts(connection) -> dict[str, int]:
    return {
        "test_run": int(
            connection.execute(
                text("SELECT COUNT_BIG(*) FROM test.test_run")
            ).scalar_one()
        ),
        "unit_result": int(
            connection.execute(
                text("SELECT COUNT_BIG(*) FROM test.unit_result")
            ).scalar_one()
        ),
        "measurement": int(
            connection.execute(
                text("SELECT COUNT_BIG(*) FROM test.measurement")
            ).scalar_one()
        ),
    }


def _principal_ids(connection) -> tuple[int, int]:
    admin_id = connection.execute(
        text(
            "SELECT TOP (1) u.user_id FROM iam.app_user u "
            "JOIN iam.user_role ur ON ur.user_id=u.user_id "
            "JOIN iam.role r ON r.role_id=ur.role_id "
            "WHERE u.status='ACTIVE' AND r.role_code='SYSTEM_ADMIN' "
            "ORDER BY u.user_id"
        )
    ).scalar_one_or_none()
    analyst_id = connection.execute(
        text(
            "SELECT TOP (1) u.user_id FROM iam.app_user u "
            "JOIN iam.user_role ur ON ur.user_id=u.user_id "
            "JOIN iam.role r ON r.role_id=ur.role_id AND r.active=1 "
            "JOIN iam.role_permission rp ON rp.role_id=r.role_id "
            "JOIN iam.permission p ON p.permission_id=rp.permission_id "
            "WHERE u.status='ACTIVE' AND p.permission_code='ANALYSIS_RUN' "
            "AND NOT EXISTS(SELECT 1 FROM iam.user_role ux "
            "JOIN iam.role rx ON rx.role_id=ux.role_id "
            "WHERE ux.user_id=u.user_id AND rx.role_code='SYSTEM_ADMIN') "
            "ORDER BY u.user_id"
        )
    ).scalar_one_or_none()
    if admin_id is None:
        raise RuntimeError("an active SYSTEM_ADMIN is required")
    if analyst_id is None:
        raise RuntimeError("an active non-admin ANALYSIS_RUN user is required")
    return int(admin_id), int(analyst_id)


def _headers(auth: SqlAuthService, user_id: int) -> tuple[dict[str, str], str]:
    token, jti, expires = issue_access_token(user_id)
    auth.create_session(
        user_id,
        jti,
        expires,
        client_ip="127.0.0.1",
        user_agent="quick-pat-sql-e2e",
    )
    return {"Authorization": f"Bearer {token}"}, jti


def main() -> None:
    args = parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    if os.getenv("TMS_JOB_REPOSITORY", "memory").strip().lower() != "sql":
        raise RuntimeError("TMS_JOB_REPOSITORY=sql is required")
    if os.getenv("TMS_AUTH_REQUIRED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("TMS_AUTH_REQUIRED=true is required")

    source_catalog = SourceCatalog.from_environment()
    manifest = source_catalog.build_manifest(args.source_root, args.relative_path)
    engine = get_engine()
    with engine.connect() as connection:
        database = str(connection.execute(text("SELECT DB_NAME()")).scalar_one())
        revision = str(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        )
        before = _fact_counts(connection)
        admin_id, analyst_id = _principal_ids(connection)
    if database != args.expected_database:
        raise RuntimeError(
            f"refusing E2E against unexpected database {database!r}; "
            f"expected {args.expected_database!r}"
        )
    if revision != "sql2014_0029":
        raise RuntimeError(f"sql2014_0029 is required, database is {revision}")

    auth = SqlAuthService(engine)
    admin = auth.principal_for_user(admin_id)
    analyst = auth.principal_for_user(analyst_id)
    if "ANALYSIS_RUN" not in admin.permissions:
        raise RuntimeError("SYSTEM_ADMIN lacks ANALYSIS_RUN")
    if "ANALYSIS_RUN" not in analyst.permissions or "SYSTEM_ADMIN" in analyst.roles:
        raise RuntimeError("non-admin analyst principal is invalid")

    admin_headers, admin_jti = _headers(auth, admin_id)
    analyst_jti: str | None = None
    started = time.perf_counter()
    session_id: int | None = None
    job_id: int | None = None
    try:
        app = create_app()
        with TestClient(app) as client:
            roots = client.get(
                "/api/v1/quick-analysis/source-roots", headers=admin_headers
            )
            assert roots.status_code == 200, roots.text
            root_codes = {item["code"] for item in roots.json()}
            assert args.source_root in root_codes, root_codes

            browse = client.get(
                f"/api/v1/quick-analysis/source-roots/{args.source_root}/directories",
                headers=admin_headers,
                params={"relative_path": "."},
            )
            assert browse.status_code == 200, browse.text

            created = client.post(
                "/api/v1/quick-analysis/pat",
                headers=admin_headers,
                json={
                    "source_root_code": args.source_root,
                    "source_relative_path": args.relative_path,
                },
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            session_id = int(payload["analysis_session_id"])
            job_id = int(payload["job_id"])
            assert payload["status"] == "QUEUED", payload
            assert int(payload["source_file_count"]) == manifest.file_count
            assert int(payload["source_total_bytes"]) == manifest.total_bytes
            assert payload["source_manifest_sha256"] == manifest.sha256

            queue = SqlJobService(engine)
            quick = SqlQuickAnalysisService(engine)
            worker = DatabaseJobWorker(
                queue,
                {
                    JobType.QUICK_PAT: QuickPatHandler(
                        SqlCleanerRegistry(engine), quick, source_catalog
                    )
                },
                worker_id=f"{socket.gethostname()}-quick-pat-sql-e2e",
                lease_for=timedelta(minutes=5),
                heartbeat_every=timedelta(seconds=30),
            )
            completed = worker.run_once()
            if completed is None:
                raise RuntimeError("SQL Worker did not claim a QUICK_PAT job")
            if completed.job_id != job_id:
                raise RuntimeError(
                    f"SQL Worker claimed job {completed.job_id}, expected {job_id}"
                )
            if completed.status != JobStatus.SUCCESS:
                raise RuntimeError(
                    f"QUICK_PAT job failed: {completed.error_code} "
                    f"{completed.error_message}"
                )

            detail = client.get(
                f"/api/v1/quick-analysis/sessions/{session_id}",
                headers=admin_headers,
            )
            assert detail.status_code == 200, detail.text
            result = detail.json()
            assert result["status"] == "SUCCESS", result
            assert int(result["parameter_count"]) == 23, result
            assert int(result["record_count"]) == 6_813_800, result

            download = client.get(
                f"/api/v1/quick-analysis/sessions/{session_id}/download",
                headers=admin_headers,
            )
            assert download.status_code == 200, download.text
            assert download.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            download_sha = hashlib.sha256(download.content).hexdigest()

            analyst_headers, analyst_jti = _headers(auth, analyst_id)
            hidden_detail = client.get(
                f"/api/v1/quick-analysis/sessions/{session_id}",
                headers=analyst_headers,
            )
            assert hidden_detail.status_code == 404, hidden_detail.text
            hidden_download = client.get(
                f"/api/v1/quick-analysis/sessions/{session_id}/download",
                headers=analyst_headers,
            )
            assert hidden_download.status_code == 404, hidden_download.text
            analyst_list = client.get(
                "/api/v1/quick-analysis/sessions", headers=analyst_headers
            )
            assert analyst_list.status_code == 200, analyst_list.text
            assert session_id not in {
                int(item["analysis_session_id"]) for item in analyst_list.json()
            }

            try:
                queue.get_for_principal(job_id, analyst)
            except DomainError as exc:
                assert exc.code == "JOB_NOT_FOUND" and exc.status_code == 404
            else:
                raise AssertionError("non-owner analyst accessed another user's job")

        with engine.connect() as connection:
            after = _fact_counts(connection)
            artifact_rows = (
                connection.execute(
                    text(
                        "SELECT artifact_role,file_size,sha256 FROM "
                        "ingestion.processing_artifact WHERE job_id=:job "
                        "ORDER BY artifact_role"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .all()
            )
            job_input = (
                connection.execute(
                    text(
                        "SELECT source_file_id,import_batch_id,analysis_session_id,status "
                        "FROM ingestion.processing_job WHERE job_id=:job"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .one()
            )
        if after != before:
            raise AssertionError(
                f"formal test facts changed: before={before}, after={after}"
            )
        artifact_roles = {str(row["artifact_role"]) for row in artifact_rows}
        if artifact_roles != {"pat_report", "pat_summary", "source_manifest"}:
            raise AssertionError(f"unexpected artifact roles: {artifact_roles}")
        report_row = next(
            row for row in artifact_rows if row["artifact_role"] == "pat_report"
        )
        if str(report_row["sha256"]) != download_sha:
            raise AssertionError("download SHA does not match registered PAT artifact")
        if (
            job_input["source_file_id"] is not None
            or job_input["import_batch_id"] is not None
            or int(job_input["analysis_session_id"]) != session_id
            or str(job_input["status"]) != "SUCCESS"
        ):
            raise AssertionError(f"invalid QUICK_PAT job input: {dict(job_input)}")

        print(
            json.dumps(
                {
                    "verification": "PASS",
                    "database": database,
                    "revision": revision,
                    "analysis_session_id": session_id,
                    "job_id": job_id,
                    "source_root_code": args.source_root,
                    "source_relative_path": manifest.selected_relative_path,
                    "source_file_count": manifest.file_count,
                    "source_total_bytes": manifest.total_bytes,
                    "manifest_sha256": manifest.sha256,
                    "parameter_count": int(result["parameter_count"]),
                    "record_count": int(result["record_count"]),
                    "result_file_name": result["result_file_name"],
                    "result_size_bytes": int(result["result_size_bytes"]),
                    "result_sha256": download_sha,
                    "artifact_roles": sorted(artifact_roles),
                    "owner_isolation": "API_DETAIL_DOWNLOAD_LIST_AND_JOB_404_PASS",
                    "formal_fact_counts_before": before,
                    "formal_fact_counts_after": after,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if analyst_jti is not None:
            auth.revoke_session(analyst_jti)
        auth.revoke_session(admin_jti)


if __name__ == "__main__":
    main()
