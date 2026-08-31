from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.domain.saved_analyses import canonical_json
from app.infrastructure.analytics_export_cleanup import AnalyticsExportFileCleaner
from app.infrastructure.analytics_export_files import UnsafeAnalyticsExportPath


@dataclass(frozen=True, slots=True)
class AnalyticsExportCleanupResult:
    export_job_id: int
    cleanup_status: str
    physical_status: str
    registered_bytes: int
    discovered_bytes: int
    discovered_file_count: int
    message: str | None = None


class SqlAnalyticsExportCleanupService:
    """TTL cleanup for managed Analytics Export Artifacts; DryRun is default."""

    def __init__(
        self, engine: Engine, file_cleaner: AnalyticsExportFileCleaner
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
    ) -> tuple[AnalyticsExportCleanupResult, ...]:
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
                        "SELECT TOP (:limit) j.export_job_id "
                        "FROM delivery.export_job j WHERE j.status='SUCCESS' "
                        "AND EXISTS(SELECT 1 FROM delivery.export_artifact a "
                        "WHERE a.export_job_id=j.export_job_id "
                        "AND a.expires_at_utc<=:cutoff AND ("
                        "a.physical_status IN('PRESENT','ERROR') OR ("
                        "a.physical_status='DELETING' AND "
                        "a.deletion_attempted_at_utc<=:stale_cutoff))) "
                        "AND NOT EXISTS(SELECT 1 FROM delivery.export_artifact b "
                        "WHERE b.export_job_id=j.export_job_id AND ("
                        "b.expires_at_utc IS NULL OR b.expires_at_utc>:cutoff OR "
                        "b.physical_status='BLOCKED' OR ("
                        "b.physical_status='DELETING' AND "
                        "b.deletion_attempted_at_utc>:stale_cutoff))) "
                        "ORDER BY j.export_job_id"
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
        results: list[AnalyticsExportCleanupResult] = []
        for row in rows:
            export_job_id = int(row["export_job_id"])
            if dry_run:
                results.append(self._inspect(export_job_id))
                continue
            claimed = self._claim(export_job_id, cutoff, stale_cutoff)
            if claimed is None:
                continue
            results.append(self._cleanup_claimed(export_job_id, claimed))
        return tuple(results)

    def _inspect(self, export_job_id: int) -> AnalyticsExportCleanupResult:
        artifacts = self._artifacts(export_job_id)
        if len(artifacts) != 1:
            return AnalyticsExportCleanupResult(
                export_job_id,
                "BLOCKED",
                "BLOCKED",
                sum(int(item["file_size"]) for item in artifacts),
                0,
                0,
                "analytics export must have exactly one registered Artifact",
            )
        registered_bytes = int(artifacts[0]["file_size"])
        try:
            outcome = self._file_cleaner.cleanup_job(
                export_job_id,
                (str(artifacts[0]["storage_uri"]),),
                dry_run=True,
            )
            return AnalyticsExportCleanupResult(
                export_job_id,
                "DRY_RUN",
                outcome.physical_status,
                registered_bytes,
                outcome.discovered_bytes,
                outcome.discovered_file_count,
            )
        except (UnsafeAnalyticsExportPath, OSError) as exc:
            blocked = isinstance(exc, UnsafeAnalyticsExportPath)
            return AnalyticsExportCleanupResult(
                export_job_id,
                "BLOCKED" if blocked else "ERROR",
                "BLOCKED" if blocked else "ERROR",
                registered_bytes,
                0,
                0,
                str(exc),
            )

    def _claim(
        self,
        export_job_id: int,
        cutoff: datetime,
        stale_cutoff: datetime,
    ) -> tuple[dict[str, Any], ...] | None:
        with self._engine.begin() as connection:
            status = connection.execute(
                text(
                    "SELECT status FROM delivery.export_job WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE export_job_id=:export_job_id"
                ),
                {"export_job_id": export_job_id},
            ).scalar_one_or_none()
            if str(status) != "SUCCESS":
                return None
            rows = tuple(
                dict(row)
                for row in (
                    connection.execute(
                        text(
                            "SELECT export_artifact_id,file_name,mime_type,storage_uri,"
                            "file_size,sha256,expires_at_utc,physical_status,"
                            "deletion_attempt_count,deletion_attempted_at_utc "
                            "FROM delivery.export_artifact WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE export_job_id=:export_job_id "
                            "ORDER BY export_artifact_id"
                        ),
                        {"export_job_id": export_job_id},
                    )
                    .mappings()
                    .all()
                )
            )
            if len(rows) != 1:
                return None
            row = rows[0]
            expires = row["expires_at_utc"]
            physical_status = str(row["physical_status"])
            if expires is None or expires > cutoff or physical_status == "BLOCKED":
                return None
            if physical_status == "DELETING" and (
                row["deletion_attempted_at_utc"] is None
                or row["deletion_attempted_at_utc"] > stale_cutoff
            ):
                return None
            if physical_status not in {"PRESENT", "ERROR", "DELETING"}:
                return None
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_artifact SET physical_status='DELETING',"
                    "deletion_attempt_count=deletion_attempt_count+1,"
                    "deletion_attempted_at_utc=:cutoff,deleted_at_utc=NULL,"
                    "deletion_reason=NULL WHERE export_artifact_id=:artifact_id "
                    "AND expires_at_utc<=:cutoff AND ("
                    "physical_status IN('PRESENT','ERROR') OR ("
                    "physical_status='DELETING' AND "
                    "deletion_attempted_at_utc<=:stale_cutoff))"
                ),
                {
                    "artifact_id": int(row["export_artifact_id"]),
                    "cutoff": cutoff,
                    "stale_cutoff": stale_cutoff,
                },
            ).rowcount
            if changed != 1:
                return None
            return rows

    def _cleanup_claimed(
        self,
        export_job_id: int,
        artifacts: tuple[dict[str, Any], ...],
    ) -> AnalyticsExportCleanupResult:
        artifact = artifacts[0]
        registered_bytes = int(artifact["file_size"])
        before = {
            "status": "SUCCESS",
            "artifact": {
                "export_artifact_id": int(artifact["export_artifact_id"]),
                "file_name": str(artifact["file_name"]),
                "mime_type": str(artifact["mime_type"]),
                "file_size": registered_bytes,
                "sha256": str(artifact["sha256"]),
                "expires_at_utc": self._json_datetime(artifact["expires_at_utc"]),
                "physical_status": str(artifact["physical_status"]),
                "deletion_attempt_count": int(artifact["deletion_attempt_count"]),
            },
        }
        try:
            outcome = self._file_cleaner.cleanup_job(
                export_job_id,
                (str(artifact["storage_uri"]),),
            )
            result = AnalyticsExportCleanupResult(
                export_job_id,
                "CLEANED",
                outcome.physical_status,
                registered_bytes,
                outcome.discovered_bytes,
                outcome.discovered_file_count,
            )
        except UnsafeAnalyticsExportPath as exc:
            result = AnalyticsExportCleanupResult(
                export_job_id,
                "BLOCKED",
                "BLOCKED",
                registered_bytes,
                0,
                0,
                str(exc),
            )
        except OSError as exc:
            result = AnalyticsExportCleanupResult(
                export_job_id,
                "ERROR",
                "ERROR",
                registered_bytes,
                0,
                0,
                str(exc),
            )
        self._record(result, before)
        return result

    def _artifacts(self, export_job_id: int) -> tuple[Mapping[str, Any], ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT export_artifact_id,file_name,mime_type,storage_uri,"
                        "file_size,sha256,expires_at_utc,physical_status,"
                        "deletion_attempt_count,deletion_attempted_at_utc "
                        "FROM delivery.export_artifact WHERE "
                        "export_job_id=:export_job_id AND physical_status NOT IN("
                        "'DELETED','MISSING') ORDER BY export_artifact_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                .mappings()
                .all()
            )
        return tuple(rows)

    def _record(
        self,
        result: AnalyticsExportCleanupResult,
        before: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        terminal = result.physical_status in {"DELETED", "MISSING"}
        if result.physical_status == "DELETED":
            reason = "TTL_EXPIRED"
        elif result.physical_status == "MISSING":
            reason = "TTL_EXPIRED_FILE_ALREADY_MISSING"
        else:
            reason = (
                f"ANALYTICS_EXPORT_CLEANUP_{result.physical_status}: "
                f"{result.message or 'physical cleanup failed'}"
            )[-1000:]
        with self._engine.begin() as connection:
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_artifact SET physical_status=:status,"
                    "deleted_at_utc=:deleted_at,deletion_reason=:reason "
                    "WHERE export_job_id=:export_job_id "
                    "AND physical_status='DELETING'"
                ),
                {
                    "status": result.physical_status,
                    "deleted_at": now if terminal else None,
                    "reason": reason,
                    "export_job_id": result.export_job_id,
                },
            ).rowcount
            if changed != 1:
                raise RuntimeError(
                    "analytics export cleanup fencing state changed before record"
                )
            if terminal:
                changed = connection.execute(
                    text(
                        "UPDATE delivery.export_job SET status='EXPIRED' "
                        "WHERE export_job_id=:export_job_id AND status='SUCCESS'"
                    ),
                    {"export_job_id": result.export_job_id},
                ).rowcount
                if changed != 1:
                    raise RuntimeError(
                        "analytics export changed before TTL expiration was recorded"
                    )
            connection.execute(
                text(
                    "INSERT governance.audit_log(actor,actor_user_id,operation,"
                    "entity_type,entity_id,before_json,after_json,reason,"
                    "correlation_id) VALUES('analytics-export-cleanup',NULL,"
                    "'ANALYTICS_EXPORT_TTL_CLEANUP','ANALYTICS_EXPORT',"
                    ":entity_id,:before_json,:after_json,:reason,:correlation_id)"
                ),
                {
                    "entity_id": str(result.export_job_id),
                    "before_json": canonical_json(before),
                    "after_json": canonical_json(
                        {
                            **asdict(result),
                            "job_status": "EXPIRED" if terminal else "SUCCESS",
                            "deletion_reason": reason,
                        }
                    ),
                    "reason": "Analytics Export Artifact TTL cleanup",
                    "correlation_id": str(uuid4()),
                },
            )

    @staticmethod
    def _json_datetime(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()
        return json.loads(json.dumps(str(value)))
