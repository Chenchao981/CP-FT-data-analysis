from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.domain.analytics import AnalyticsContextRequest, AnalyticsFilters
from app.domain.saved_analyses import (
    SavedAnalysisRuleContext,
    canonical_json,
    saved_analysis_hashes,
    validate_analysis_presentation_config,
)
from app.infrastructure.analytics_export_cleanup import AnalyticsExportFileCleaner
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy
from app.infrastructure.analytics_export_renderer import AnalyticsExportRenderer
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_analytics_export_cleanup import (
    SqlAnalyticsExportCleanupService,
)
from app.infrastructure.sql_analytics_export_content import (
    SqlAnalyticsExportContentSource,
)
from app.infrastructure.sql_analytics_export_worker import (
    SqlAnalyticsExportWorkerRepository,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create only synthetic delivery rows to verify Analytics Export stale "
            "recovery, fencing and DryRun/Execute TTL cleanup"
        )
    )
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def assert_lifecycle_write_target(database: dict[str, str]) -> None:
    if database.get("database") != "TMS_G0_DEV":
        raise RuntimeError("Analytics Export lifecycle E2E requires TMS_G0_DEV")
    if database.get("schema_revision") != "sql2014_0028":
        raise RuntimeError("Analytics Export lifecycle E2E requires sql2014_0028")


def main() -> None:
    args = parse_args()
    output_root = args.output_root.absolute()
    database = check_database()
    assert_lifecycle_write_target(database)
    engine = get_engine()
    policy = AnalyticsExportPathPolicy(output_root)
    old_token = str(uuid4())
    export_job_id: int | None = None
    artifact_path: Path | None = None
    try:
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT TOP (1) dv.dataset_version_id,dv.version_no,d.dataset_id,"
                        "d.test_stage,u.user_id FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                        "CROSS JOIN (SELECT TOP (1) user_id FROM iam.app_user "
                        "WHERE status='ACTIVE' ORDER BY user_id) u "
                        "WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                        "ORDER BY dv.dataset_version_id"
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise RuntimeError(
                "no active user and Current Published Dataset are available"
            )
        context = AnalyticsContextRequest(
            datasets=[
                {
                    "dataset_id": int(row["dataset_id"]),
                    "version_no": int(row["version_no"]),
                }
            ],
            filters=AnalyticsFilters(),
            parameters=[],
        )
        hashes = saved_analysis_hashes(context)
        chart_config = {
            "show_spec_overlay": True,
            "analysis_view_state": {
                "contract_version": "ANALYSIS_VIEW_STATE_V1",
                "components": {
                    "detail": {
                        "view": "WIDE",
                        "sortBy": "UNIT_SEQUENCE",
                        "sortDirection": "ASC",
                    }
                },
            },
        }
        display_config = {
            "section": "detail",
            "page": 1,
            "page_size": 1,
            "focus_dataset_id": int(row["dataset_id"]),
        }
        envelope = {
            "artifact_ttl_hours": 1,
            "chart_config": chart_config,
            "display_config": display_config,
            "filters": context.filters.model_dump(mode="json"),
            "page": 1,
            "page_size": 1,
            "parameters": [],
            "presentation_hash": validate_analysis_presentation_config(
                chart_config, display_config
            ),
            "request_reason_sha256": "e" * 64,
        }
        with engine.begin() as connection:
            export_job_id = int(
                connection.execute(
                    text(
                        "INSERT delivery.export_job(requested_by,dataset_version_id,"
                        "evaluation_run_id,export_scope,export_format,template_code,"
                        "template_version,filter_json,status,requested_at_utc,"
                        "started_at_utc,contract_version,filter_hash,context_hash,"
                        "rule_context_json,idempotency_key,attempt_count,max_attempts,"
                        "lease_token,lease_owner,lease_expires_at_utc,heartbeat_at_utc) "
                        "OUTPUT INSERTED.export_job_id VALUES(:requested_by,"
                        ":dataset_version_id,NULL,'CURRENT_PAGE','CSV',"
                        "'ANALYTICS_DETAIL','v1',:filter_json,'RUNNING',"
                        "SYSUTCDATETIME(),DATEADD(minute,-10,SYSUTCDATETIME()),"
                        "'ANALYTICS_EXPORT_V1',:filter_hash,:context_hash,"
                        ":rule_context_json,:idempotency_key,1,3,:lease_token,"
                        ":lease_owner,DATEADD(minute,-5,SYSUTCDATETIME()),"
                        "DATEADD(minute,-6,SYSUTCDATETIME()))"
                    ),
                    {
                        "requested_by": int(row["user_id"]),
                        "dataset_version_id": int(row["dataset_version_id"]),
                        "filter_json": canonical_json(envelope),
                        "filter_hash": hashes.filter_hash,
                        "context_hash": hashes.context_hash,
                        "rule_context_json": canonical_json(
                            SavedAnalysisRuleContext().model_dump(mode="json")
                        ),
                        "idempotency_key": f"analytics-export-lifecycle-e2e-{uuid4()}",
                        "lease_token": old_token,
                        "lease_owner": "analytics-export-e2e-crashed",
                    },
                ).scalar_one()
            )
            connection.execute(
                text(
                    "INSERT delivery.export_job_dataset(export_job_id,"
                    "dataset_version_id,ordinal_no) VALUES(:export_job_id,"
                    ":dataset_version_id,1)"
                ),
                {
                    "export_job_id": export_job_id,
                    "dataset_version_id": int(row["dataset_version_id"]),
                },
            )

        repository = SqlAnalyticsExportWorkerRepository(
            engine,
            policy,
            worker_id="analytics-export-e2e-recovery",
            lease_seconds=120,
        )
        work_item = repository.claim_next()
        if work_item is None or work_item.export_job_id != export_job_id:
            raise RuntimeError("stale Analytics Export was not recovered")
        if work_item.attempt_count != 2 or work_item.lease_token == old_token:
            raise RuntimeError("stale Analytics Export fencing token was not replaced")
        old_repository = SqlAnalyticsExportWorkerRepository(
            engine,
            policy,
            worker_id="analytics-export-e2e-crashed",
            lease_seconds=120,
        )
        try:
            old_repository.heartbeat(
                replace(
                    work_item,
                    lease_token=old_token,
                    lease_owner="analytics-export-e2e-crashed",
                )
            )
        except DomainError as exc:
            if exc.code != "ANALYTICS_EXPORT_WORKER_CLAIM_LOST":
                raise
        else:
            raise RuntimeError("old Worker fencing token unexpectedly remained valid")

        artifact = AnalyticsExportRenderer(
            policy,
            SqlAnalyticsExportContentSource(engine),
        ).render(work_item)
        artifact_path = artifact.path
        repository.complete(
            work_item,
            artifact,
            expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE delivery.export_artifact SET expires_at_utc="
                    "DATEADD(minute,-1,SYSUTCDATETIME()) "
                    "WHERE export_job_id=:export_job_id"
                ),
                {"export_job_id": export_job_id},
            )

        cleanup = SqlAnalyticsExportCleanupService(
            engine,
            AnalyticsExportFileCleaner(policy),
        )
        preview = cleanup.run_due(dry_run=True)
        if not any(item.export_job_id == export_job_id for item in preview):
            raise RuntimeError("expired Analytics Export was missing from DryRun")
        if not artifact.path.is_file():
            raise RuntimeError("DryRun mutated the Analytics Export Artifact")
        executed = cleanup.run_due(dry_run=False)
        result = next(
            (item for item in executed if item.export_job_id == export_job_id),
            None,
        )
        if result is None or result.physical_status != "DELETED":
            raise RuntimeError(
                "Analytics Export TTL Execute did not delete the exact root"
            )
        with engine.connect() as connection:
            state = (
                connection.execute(
                    text(
                        "SELECT j.status,a.physical_status,a.deleted_at_utc,"
                        "a.deletion_reason,a.file_size,a.sha256 "
                        "FROM delivery.export_job j JOIN delivery.export_artifact a "
                        "ON a.export_job_id=j.export_job_id "
                        "WHERE j.export_job_id=:export_job_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                .mappings()
                .one()
            )
        if (
            str(state["status"]) != "EXPIRED"
            or str(state["physical_status"]) != "DELETED"
            or state["deleted_at_utc"] is None
            or str(state["deletion_reason"]) != "TTL_EXPIRED"
        ):
            raise RuntimeError(
                "Analytics Export cleanup database state did not reconcile"
            )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "database": database["database"],
                    "schema_revision": database["schema_revision"],
                    "export_job_id": export_job_id,
                    "recovered_attempt_count": work_item.attempt_count,
                    "old_worker_fenced": True,
                    "dry_run_preserved_file": True,
                    "execute_physical_status": str(state["physical_status"]),
                    "execute_job_status": str(state["status"]),
                    "file_size": int(state["file_size"]),
                    "sha256": str(state["sha256"]),
                },
                ensure_ascii=False,
            )
        )
    finally:
        if export_job_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM governance.audit_log WHERE "
                        "entity_type='ANALYTICS_EXPORT' AND entity_id=:entity_id"
                    ),
                    {"entity_id": str(export_job_id)},
                )
                connection.execute(
                    text(
                        "DELETE FROM delivery.export_artifact "
                        "WHERE export_job_id=:export_job_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM delivery.export_job_dataset "
                        "WHERE export_job_id=:export_job_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM delivery.export_job "
                        "WHERE export_job_id=:export_job_id"
                    ),
                    {"export_job_id": export_job_id},
                )
        root = policy.job_root(export_job_id) if export_job_id is not None else None
        if root is not None and root.exists():
            paths = tuple(str(path) for path in root.iterdir())
            AnalyticsExportFileCleaner(policy).cleanup_job(export_job_id, paths)
        if artifact_path is not None and artifact_path.exists():
            raise RuntimeError("synthetic Analytics Export Artifact cleanup failed")


if __name__ == "__main__":
    main()
