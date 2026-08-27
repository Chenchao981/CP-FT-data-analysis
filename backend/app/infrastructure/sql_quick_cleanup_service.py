from __future__ import annotations

import json
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, text

from app.infrastructure.quick_artifact_cleanup import (
    QuickArtifactFileCleaner,
    UnsafeCleanupTarget,
)


@dataclass(frozen=True, slots=True)
class CleanupResult:
    analysis_session_id: int
    job_id: int
    cleanup_status: str
    physical_status: str
    registered_bytes: int
    discovered_bytes: int
    discovered_file_count: int
    message: str | None = None


class SqlQuickCleanupService:
    def __init__(self, engine: Engine, file_cleaner: QuickArtifactFileCleaner) -> None:
        self._engine = engine
        self._file_cleaner = file_cleaner

    def run_due(
        self,
        *,
        limit: int = 100,
        dry_run: bool = False,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=30),
    ) -> tuple[CleanupResult, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        cutoff = (now or datetime.now(UTC)).replace(tzinfo=None)
        stale_cutoff = cutoff - stale_after
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT TOP (:limit) s.analysis_session_id,j.job_id "
                        "FROM workspace.analysis_session s "
                        "JOIN ingestion.processing_job j "
                        "ON j.analysis_session_id=s.analysis_session_id "
                        "WHERE s.expires_at_utc<=:cutoff "
                        "AND (s.cleanup_status IN('RETAINED','ERROR') OR ("
                        "s.cleanup_status='CLEANING' AND "
                        "s.cleanup_attempted_at_utc<=:stale_cutoff)) "
                        "AND j.status IN('SUCCESS','FAILED','CANCELLED') "
                        "ORDER BY s.expires_at_utc,s.analysis_session_id"
                    ),
                    {
                        "limit": limit,
                        "cutoff": cutoff,
                        "stale_cutoff": stale_cutoff,
                    },
                )
                .mappings()
                .all()
            )
        results: list[CleanupResult] = []
        for row in rows:
            session_id = int(row["analysis_session_id"])
            job_id = int(row["job_id"])
            if dry_run:
                results.append(self._inspect(session_id, job_id))
                continue
            if not self._claim(session_id, cutoff, stale_cutoff):
                continue
            results.append(self._cleanup_claimed(session_id, job_id))
        return tuple(results)

    def _claim(
        self, session_id: int, cutoff: datetime, stale_cutoff: datetime
    ) -> bool:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET cleanup_status='CLEANING',"
                    "cleanup_attempt_count=cleanup_attempt_count+1,"
                    "cleanup_attempted_at_utc=SYSUTCDATETIME(),cleanup_error=NULL "
                    "WHERE analysis_session_id=:session AND expires_at_utc<=:cutoff "
                    "AND (cleanup_status IN('RETAINED','ERROR') OR ("
                    "cleanup_status='CLEANING' AND "
                    "cleanup_attempted_at_utc<=:stale_cutoff))"
                ),
                {
                    "session": session_id,
                    "cutoff": cutoff,
                    "stale_cutoff": stale_cutoff,
                },
            ).rowcount
        return updated == 1

    def _inspect(self, session_id: int, job_id: int) -> CleanupResult:
        artifacts = self._artifacts(job_id)
        outcome = self._file_cleaner.cleanup_job(
            job_id,
            tuple(str(item["storage_uri"]) for item in artifacts),
            dry_run=True,
        )
        return CleanupResult(
            session_id,
            job_id,
            "DRY_RUN",
            outcome.physical_status,
            sum(int(item["file_size"]) for item in artifacts),
            outcome.discovered_bytes,
            outcome.discovered_file_count,
        )

    def _cleanup_claimed(self, session_id: int, job_id: int) -> CleanupResult:
        artifacts = self._artifacts(job_id)
        registered_bytes = sum(int(item["file_size"]) for item in artifacts)
        before = {
            "job_id": job_id,
            "registered_bytes": registered_bytes,
            "artifacts": [
                {
                    "processing_artifact_id": int(item["processing_artifact_id"]),
                    "artifact_role": str(item["artifact_role"]),
                    "file_size": int(item["file_size"]),
                    "sha256": str(item["sha256"]),
                    "physical_status": str(item["physical_status"]),
                }
                for item in artifacts
            ],
        }
        try:
            outcome = self._file_cleaner.cleanup_job(
                job_id, tuple(str(item["storage_uri"]) for item in artifacts)
            )
            result = CleanupResult(
                session_id,
                job_id,
                "CLEANED",
                outcome.physical_status,
                registered_bytes,
                outcome.discovered_bytes,
                outcome.discovered_file_count,
            )
            self._record_success(result, before)
            return result
        except UnsafeCleanupTarget as exc:
            result = CleanupResult(
                session_id,
                job_id,
                "BLOCKED",
                "BLOCKED",
                registered_bytes,
                0,
                0,
                str(exc),
            )
            self._record_failure(result, before)
            return result
        except OSError as exc:
            result = CleanupResult(
                session_id,
                job_id,
                "ERROR",
                "ERROR",
                registered_bytes,
                0,
                0,
                str(exc),
            )
            self._record_failure(result, before)
            return result

    def _artifacts(self, job_id: int):
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    text(
                        "SELECT processing_artifact_id,artifact_role,storage_uri,"
                        "file_size,sha256,physical_status FROM "
                        "ingestion.processing_artifact WHERE job_id=:job "
                        "AND temporary_flag=1 ORDER BY processing_artifact_id"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .all()
            )

    def _record_success(self, result: CleanupResult, before: dict) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        artifact_status = (
            "MISSING" if result.physical_status == "MISSING" else "DELETED"
        )
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET "
                    "physical_status=:physical_status,"
                    "deletion_attempt_count=deletion_attempt_count+1,"
                    "deletion_attempted_at_utc=:now,deleted_at_utc=:deleted_at,"
                    "deletion_error=NULL WHERE job_id=:job AND temporary_flag=1"
                ),
                {
                    "physical_status": artifact_status,
                    "now": now,
                    "deleted_at": now if artifact_status == "DELETED" else None,
                    "job": result.job_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET "
                    "status=CASE WHEN status='SUCCESS' THEN 'EXPIRED' ELSE status END,"
                    "cleanup_status='CLEANED',cleaned_at_utc=:now,cleanup_error=NULL,"
                    "reserved_bytes=0 "
                    "WHERE analysis_session_id=:session AND cleanup_status='CLEANING'"
                ),
                {"now": now, "session": result.analysis_session_id},
            )
            self._audit(connection, result, before)

    def _record_failure(self, result: CleanupResult, before: dict) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET "
                    "physical_status=:physical_status,"
                    "deletion_attempt_count=deletion_attempt_count+1,"
                    "deletion_attempted_at_utc=:now,deletion_error=:error "
                    "WHERE job_id=:job AND temporary_flag=1"
                ),
                {
                    "physical_status": result.physical_status,
                    "now": now,
                    "error": (result.message or "cleanup failed")[-2000:],
                    "job": result.job_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE workspace.analysis_session SET cleanup_status=:status,"
                    "cleanup_error=:error WHERE analysis_session_id=:session "
                    "AND cleanup_status='CLEANING'"
                ),
                {
                    "status": result.cleanup_status,
                    "error": (result.message or "cleanup failed")[-2000:],
                    "session": result.analysis_session_id,
                },
            )
            self._audit(connection, result, before)

    @staticmethod
    def _audit(connection, result: CleanupResult, before: dict) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                "before_json,after_json,reason,correlation_id,actor_user_id) VALUES("
                ":actor,'QUICK_ARTIFACT_CLEANUP','workspace.analysis_session',"
                ":entity_id,:before_json,:after_json,:reason,:correlation_id,NULL)"
            ),
            {
                "actor": f"quick-cleanup:{socket.gethostname()}"[:128],
                "entity_id": str(result.analysis_session_id),
                "before_json": json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                "after_json": json.dumps(
                    asdict(result), ensure_ascii=False, separators=(",", ":")
                ),
                "reason": "Quick Analysis TTL expired; remove temporary files",
                "correlation_id": str(uuid4()),
            },
        )
