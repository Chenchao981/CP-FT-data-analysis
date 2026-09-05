from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.infrastructure.database import get_engine
from app.infrastructure.quick_artifact_cleanup import QuickArtifactFileCleaner
from app.infrastructure.sql_quick_cleanup_service import SqlQuickCleanupService


def main() -> None:
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    work_root = Path(
        os.getenv("TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace")
    ).resolve()
    engine = get_engine()
    token = uuid4().hex
    session_id: int | None = None
    job_id: int | None = None
    audit_ids: list[int] = []
    job_root: Path | None = None
    before_facts: dict[str, int]
    try:
        with engine.begin() as connection:
            database = str(connection.execute(text("SELECT DB_NAME()")).scalar_one())
            revision = str(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            )
            if database != "TMS_G0_DEV" or revision != "sql2014_0029":
                raise RuntimeError(
                    f"expected TMS_G0_DEV/sql2014_0029, got {database}/{revision}"
                )
            owner_id = int(
                connection.execute(
                    text(
                        "SELECT TOP (1) u.user_id FROM iam.app_user u "
                        "JOIN iam.user_role ur ON ur.user_id=u.user_id "
                        "JOIN iam.role r ON r.role_id=ur.role_id "
                        "WHERE u.status='ACTIVE' AND r.role_code='SYSTEM_ADMIN' "
                        "ORDER BY u.user_id"
                    )
                ).scalar_one()
            )
            release_id = int(
                connection.execute(
                    text(
                        "SELECT TOP (1) cleaner_release_id FROM "
                        "ingestion.cleaner_release WHERE "
                        "cleaner_code='JIEQUN_FT_QUICK_PAT_EXISTING' "
                        "AND status='RELEASED' ORDER BY cleaner_release_id DESC"
                    )
                ).scalar_one()
            )
            before_facts = {
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
            expired = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
            session_id = int(
                connection.execute(
                    text(
                        "INSERT workspace.analysis_session("
                        "owner_user_id,analysis_type,test_stage,factory_code,"
                        "source_root_code,source_relative_path,source_manifest_mode,"
                        "source_manifest_json,source_manifest_sha256,source_file_count,"
                        "source_total_bytes,retention_mode,cleaner_release_id,status,"
                        "parameter_count,record_count,summary_json,expires_at_utc,"
                        "finished_at_utc,reserved_bytes) "
                        "OUTPUT INSERTED.analysis_session_id VALUES("
                        ":owner,'QUICK_PAT','FT',N'JIEQUN','CLEANUP_E2E',:path,"
                        "'PATH_SIZE_MTIME_V1',:manifest,:sha,1,"
                        "8,'RESULT_ONLY',:release,'SUCCESS',1,1,:summary,"
                        ":expired,SYSUTCDATETIME(),16)"
                    ),
                    {
                        "owner": owner_id,
                        "path": token,
                        "sha": hashlib.sha256(token.encode()).hexdigest(),
                        "manifest": json.dumps({"verification": True}),
                        "summary": json.dumps({"verification": True}),
                        "release": release_id,
                        "expired": expired,
                    },
                ).scalar_one()
            )
            job_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_job(source_file_id,import_batch_id,"
                        "analysis_session_id,cleaner_release_id,job_type,trigger_type,"
                        "requested_by,requested_by_user_id,status,reason,finished_at_utc,"
                        "idempotency_key,attempt_count,max_attempts) "
                        "OUTPUT INSERTED.job_id VALUES(NULL,NULL,:session,:release,"
                        "'QUICK_PAT','SYSTEM','cleanup-e2e',:owner,'SUCCESS',"
                        "N'Quick cleanup SQL E2E',SYSUTCDATETIME(),:key,1,3)"
                    ),
                    {
                        "session": session_id,
                        "release": release_id,
                        "owner": owner_id,
                        "key": f"cleanup-e2e:{token}",
                    },
                ).scalar_one()
            )

        job_root = work_root / str(job_id) / "attempt-1"
        job_root.mkdir(parents=True, exist_ok=False)
        files = {
            "pat_report": job_root / "PAT_E2E.xlsx",
            "pat_summary": job_root / "pat_summary.json",
            "source_manifest": job_root / "source_manifest.json",
        }
        files["pat_report"].write_bytes(b"synthetic-pat")
        files["pat_summary"].write_text('{"synthetic":true}', encoding="utf-8")
        files["source_manifest"].write_text('{"files":[]}', encoding="utf-8")
        with engine.begin() as connection:
            for role, path in files.items():
                content = path.read_bytes()
                connection.execute(
                    text(
                        "INSERT ingestion.processing_artifact(job_id,artifact_role,"
                        "file_name,storage_uri,file_size,sha256,temporary_flag,"
                        "expires_at_utc) VALUES(:job,:role,:name,:uri,:size,:sha,1,"
                        "DATEADD(minute,-1,SYSUTCDATETIME()))"
                    ),
                    {
                        "job": job_id,
                        "role": role,
                        "name": path.name,
                        "uri": str(path),
                        "size": len(content),
                        "sha": hashlib.sha256(content).hexdigest(),
                    },
                )
            connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET cleanup_status='CLEANING',"
                    "cleanup_attempted_at_utc=DATEADD(hour,-1,SYSUTCDATETIME()) "
                    "WHERE analysis_session_id=:session"
                ),
                {"session": session_id},
            )

        service = SqlQuickCleanupService(engine, QuickArtifactFileCleaner(work_root))
        preview = service.run_due(limit=100, dry_run=True)
        preview_item = next(
            item for item in preview if item.analysis_session_id == session_id
        )
        if preview_item.discovered_file_count != 3 or not job_root.is_dir():
            raise AssertionError(f"invalid cleanup preview: {preview_item}")

        completed = service.run_due(limit=100)
        result = next(
            item for item in completed if item.analysis_session_id == session_id
        )
        if result.cleanup_status != "CLEANED" or job_root.parent.exists():
            raise AssertionError(f"physical cleanup failed: {result}")

        with engine.connect() as connection:
            session = (
                connection.execute(
                    text(
                        "SELECT status,cleanup_status,cleanup_attempt_count,cleaned_at_utc "
                        "FROM workspace.analysis_session WHERE analysis_session_id=:session"
                    ),
                    {"session": session_id},
                )
                .mappings()
                .one()
            )
            artifact_states = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT physical_status FROM ingestion.processing_artifact "
                        "WHERE job_id=:job"
                    ),
                    {"job": job_id},
                ).all()
            }
            audits = (
                connection.execute(
                    text(
                        "SELECT audit_id,after_json FROM governance.audit_log "
                        "WHERE operation='QUICK_ARTIFACT_CLEANUP' "
                        "AND entity_type='workspace.analysis_session' AND entity_id=:entity"
                    ),
                    {"entity": str(session_id)},
                )
                .mappings()
                .all()
            )
            audit_ids = [int(row["audit_id"]) for row in audits]
            after_facts = {
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
        if (
            str(session["status"]) != "EXPIRED"
            or str(session["cleanup_status"]) != "CLEANED"
            or int(session["cleanup_attempt_count"]) != 1
            or session["cleaned_at_utc"] is None
            or artifact_states != {"DELETED"}
            or len(audits) != 1
            or json.loads(str(audits[0]["after_json"]))["cleanup_status"] != "CLEANED"
            or after_facts != before_facts
        ):
            raise AssertionError(
                f"cleanup audit mismatch: session={dict(session)}, "
                f"artifact_states={artifact_states}, audits={audits}, "
                f"before={before_facts}, after={after_facts}"
            )
        print(
            json.dumps(
                {
                    "verification": "PASS",
                    "database": "TMS_G0_DEV",
                    "revision": "sql2014_0029",
                    "dry_run_file_count": preview_item.discovered_file_count,
                    "stale_cleaning_recovery": "PASS",
                    "cleanup_status": result.cleanup_status,
                    "physical_status": result.physical_status,
                    "removed_bytes": result.discovered_bytes,
                    "artifact_states": sorted(artifact_states),
                    "audit_rows": len(audits),
                    "formal_fact_counts_before": before_facts,
                    "formal_fact_counts_after": after_facts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if job_root is not None and job_root.parent.exists():
            cleaner = QuickArtifactFileCleaner(work_root)
            if job_id is not None:
                cleaner.cleanup_job(job_id, (), dry_run=False)
        if session_id is not None:
            with engine.begin() as connection:
                if audit_ids:
                    placeholders = ",".join(
                        f":audit_{index}" for index in range(len(audit_ids))
                    )
                    connection.execute(
                        text(
                            f"DELETE governance.audit_log WHERE audit_id IN ({placeholders})"
                        ),
                        {
                            f"audit_{index}": audit_id
                            for index, audit_id in enumerate(audit_ids)
                        },
                    )
                if job_id is not None:
                    connection.execute(
                        text("DELETE ingestion.processing_artifact WHERE job_id=:job"),
                        {"job": job_id},
                    )
                    connection.execute(
                        text("DELETE ingestion.processing_job WHERE job_id=:job"),
                        {"job": job_id},
                    )
                connection.execute(
                    text(
                        "DELETE workspace.analysis_session "
                        "WHERE analysis_session_id=:session"
                    ),
                    {"session": session_id},
                )
        engine.dispose()
        print("integration_cleanup=PASS")


if __name__ == "__main__":
    main()
