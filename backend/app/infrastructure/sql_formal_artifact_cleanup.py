from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.infrastructure.formal_artifact_files import (
    FormalArtifactFileCleaner,
    UnsafeFormalArtifactPath,
)


@dataclass(frozen=True, slots=True)
class FormalCleanupResult:
    job_id: int
    cleanup_status: str
    physical_status: str
    registered_bytes: int
    discovered_bytes: int
    discovered_file_count: int
    message: str | None = None


class SqlFormalArtifactCleanupService:
    """TTL cleanup for formal Job artifacts; dry-run is the default."""

    _FORMAL_TYPES = ("INITIAL_IMPORT", "EXPORT_LATEST", "REPROCESS_UPDATE")

    def __init__(
        self, engine: Engine, file_cleaner: FormalArtifactFileCleaner
    ) -> None:
        self._engine = engine
        self._file_cleaner = file_cleaner

    def run_due(
        self,
        *,
        limit: int = 100,
        dry_run: bool = True,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=30),
    ) -> tuple[FormalCleanupResult, ...]:
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
                        "SELECT TOP (:limit) j.job_id "
                        "FROM ingestion.processing_job j "
                        "WHERE j.job_type IN('INITIAL_IMPORT','EXPORT_LATEST',"
                        "'REPROCESS_UPDATE') "
                        "AND j.status IN('SUCCESS','FAILED','CANCELLED') "
                        "AND EXISTS(SELECT 1 FROM ingestion.processing_artifact a "
                        "WHERE a.job_id=j.job_id AND a.temporary_flag=1 "
                        "AND a.expires_at_utc<=:cutoff AND ("
                        "a.physical_status IN('PRESENT','ERROR') OR ("
                        "a.physical_status='DELETING' "
                        "AND a.deletion_attempted_at_utc<=:stale_cutoff))) "
                        "AND NOT EXISTS(SELECT 1 FROM ingestion.processing_artifact b "
                        "WHERE b.job_id=j.job_id AND (b.temporary_flag=0 OR ("
                        "b.temporary_flag=1 AND b.physical_status IN("
                        "'PRESENT','ERROR','BLOCKED','DELETING') AND ("
                        "b.expires_at_utc IS NULL OR b.expires_at_utc>:cutoff OR "
                        "b.physical_status='BLOCKED' OR ("
                        "b.physical_status='DELETING' AND "
                        "b.deletion_attempted_at_utc>:stale_cutoff))))) "
                        "ORDER BY j.job_id"
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
        results: list[FormalCleanupResult] = []
        for row in rows:
            job_id = int(row["job_id"])
            if dry_run:
                results.append(self._inspect(job_id))
                continue
            claimed = self._claim(job_id, cutoff, stale_cutoff)
            if claimed is None:
                continue
            results.append(self._cleanup_claimed(job_id, claimed))
        return tuple(results)

    def _inspect(self, job_id: int) -> FormalCleanupResult:
        artifacts = self._artifacts(job_id)
        registered_bytes = sum(int(item["file_size"]) for item in artifacts)
        try:
            outcome = self._file_cleaner.cleanup_job(
                job_id,
                tuple(str(item["storage_uri"]) for item in artifacts),
                dry_run=True,
            )
            return FormalCleanupResult(
                job_id,
                "DRY_RUN",
                outcome.physical_status,
                registered_bytes,
                outcome.discovered_bytes,
                outcome.discovered_file_count,
            )
        except (UnsafeFormalArtifactPath, OSError) as exc:
            return FormalCleanupResult(
                job_id,
                "BLOCKED" if isinstance(exc, UnsafeFormalArtifactPath) else "ERROR",
                "BLOCKED" if isinstance(exc, UnsafeFormalArtifactPath) else "ERROR",
                registered_bytes,
                0,
                0,
                str(exc),
            )

    def _claim(
        self, job_id: int, cutoff: datetime, stale_cutoff: datetime
    ) -> tuple[dict[str, Any], ...] | None:
        with self._engine.begin() as connection:
            job_type = connection.execute(
                text(
                    "SELECT job_type FROM ingestion.processing_job "
                    "WITH (UPDLOCK,HOLDLOCK) WHERE job_id=:job "
                    "AND status IN('SUCCESS','FAILED','CANCELLED')"
                ),
                {"job": job_id},
            ).scalar_one_or_none()
            if str(job_type) not in self._FORMAL_TYPES:
                return None
            rows = (
                connection.execute(
                    text(
                        "SELECT processing_artifact_id,artifact_role,storage_uri,"
                        "file_size,sha256,temporary_flag,expires_at_utc,"
                        "physical_status,deletion_attempted_at_utc "
                        "FROM ingestion.processing_artifact WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE job_id=:job ORDER BY processing_artifact_id"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .all()
            )
            active = [
                row
                for row in rows
                if str(row["physical_status"]) not in {"DELETED", "MISSING"}
            ]
            if not active or any(not bool(row["temporary_flag"]) for row in active):
                return None
            for row in active:
                status = str(row["physical_status"])
                expires = row["expires_at_utc"]
                if status == "BLOCKED" or expires is None or expires > cutoff:
                    return None
                if status == "DELETING" and (
                    row["deletion_attempted_at_utc"] is None
                    or row["deletion_attempted_at_utc"] > stale_cutoff
                ):
                    return None
                if status not in {"PRESENT", "ERROR", "DELETING"}:
                    return None
            updated = connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET "
                    "physical_status='DELETING',"
                    "deletion_attempt_count=deletion_attempt_count+1,"
                    "deletion_attempted_at_utc=:now,deletion_error=NULL "
                    "WHERE job_id=:job AND temporary_flag=1 "
                    "AND expires_at_utc<=:now AND ("
                    "physical_status IN('PRESENT','ERROR') OR ("
                    "physical_status='DELETING' "
                    "AND deletion_attempted_at_utc<=:stale_cutoff))"
                ),
                {"job": job_id, "now": cutoff, "stale_cutoff": stale_cutoff},
            )
            if updated.rowcount != len(active):
                return None
            return tuple(dict(row) for row in active)

    def _cleanup_claimed(
        self, job_id: int, artifacts: tuple[dict[str, Any], ...]
    ) -> FormalCleanupResult:
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
            result = FormalCleanupResult(
                job_id,
                "CLEANED",
                outcome.physical_status,
                registered_bytes,
                outcome.discovered_bytes,
                outcome.discovered_file_count,
            )
            self._record(result, before)
            return result
        except UnsafeFormalArtifactPath as exc:
            result = FormalCleanupResult(
                job_id, "BLOCKED", "BLOCKED", registered_bytes, 0, 0, str(exc)
            )
            self._record(result, before)
            return result
        except OSError as exc:
            result = FormalCleanupResult(
                job_id, "ERROR", "ERROR", registered_bytes, 0, 0, str(exc)
            )
            self._record(result, before)
            return result

    def _artifacts(self, job_id: int) -> tuple[Mapping[str, Any], ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT processing_artifact_id,artifact_role,storage_uri,"
                        "file_size,sha256,physical_status "
                        "FROM ingestion.processing_artifact WHERE job_id=:job "
                        "AND temporary_flag=1 AND physical_status NOT IN("
                        "'DELETED','MISSING') ORDER BY processing_artifact_id"
                    ),
                    {"job": job_id},
                )
                .mappings()
                .all()
            )
        return tuple(rows)

    def _record(
        self, result: FormalCleanupResult, before: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        target_status = (
            "MISSING" if result.physical_status == "MISSING" else result.physical_status
        )
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET "
                    "physical_status=:status,deleted_at_utc=:deleted_at,"
                    "deletion_error=:error WHERE job_id=:job "
                    "AND temporary_flag=1 AND physical_status='DELETING'"
                ),
                {
                    "status": target_status,
                    "deleted_at": now if target_status == "DELETED" else None,
                    "error": (
                        None
                        if target_status in {"DELETED", "MISSING"}
                        else (result.message or "formal cleanup failed")[-2000:]
                    ),
                    "job": result.job_id,
                },
            )
            connection.execute(
                text(
                    "INSERT governance.audit_log(actor,operation,entity_type,"
                    "entity_id,before_json,after_json,reason,correlation_id,"
                    "actor_user_id) VALUES('formal-artifact-cleanup',"
                    "'FORMAL_ARTIFACT_CLEANUP','ingestion.processing_job',"
                    ":entity_id,:before_json,:after_json,:reason,:correlation_id,NULL)"
                ),
                {
                    "entity_id": str(result.job_id),
                    "before_json": json.dumps(
                        before, ensure_ascii=False, separators=(",", ":")
                    ),
                    "after_json": json.dumps(
                        asdict(result), ensure_ascii=False, separators=(",", ":")
                    ),
                    "reason": "Temporary formal Artifact TTL expired",
                    "correlation_id": str(uuid4()),
                },
            )
