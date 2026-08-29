from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from app.infrastructure.formal_artifact_files import (
    FormalOrphanCandidate,
    FormalOrphanRootCleaner,
    OversizedFormalOrphanRoot,
    UnsafeFormalArtifactPath,
)


@dataclass(frozen=True, slots=True)
class FormalOrphanCleanupResult:
    directory_name: str
    job_id: int | None
    cleanup_status: str
    physical_status: str
    discovered_bytes: int
    discovered_file_count: int
    discovered_entry_count: int
    reason_code: str


class SqlFormalOrphanCleanupService:
    """Audited fail-closed sweep for unregistered formal Job roots."""

    _FORMAL_TYPES = frozenset(
        {"INITIAL_IMPORT", "EXPORT_LATEST", "REPROCESS_UPDATE"}
    )
    _TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED"})

    def __init__(self, engine: Engine, cleaner: FormalOrphanRootCleaner) -> None:
        self._engine = engine
        self._cleaner = cleaner

    def run(
        self,
        *,
        limit: int = 100,
        dry_run: bool = True,
        now: datetime | None = None,
        retention: timedelta = timedelta(days=7),
    ) -> tuple[FormalOrphanCleanupResult, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if retention <= timedelta(0) or retention > timedelta(days=3650):
            raise ValueError("retention must be between 0 and 3650 days")
        observed_at = _naive_utc(now or datetime.now(UTC))
        cutoff = observed_at - retention
        results: list[FormalOrphanCleanupResult] = []
        for candidate in self._cleaner.candidates(limit=limit):
            result = self._evaluate_candidate(
                candidate,
                observed_at=observed_at,
                cutoff=cutoff,
                dry_run=dry_run,
            )
            results.append(result)
        return tuple(results)

    def _evaluate_candidate(
        self,
        candidate: FormalOrphanCandidate,
        *,
        observed_at: datetime,
        cutoff: datetime,
        dry_run: bool,
    ) -> FormalOrphanCleanupResult:
        if candidate.issue_code is not None or candidate.job_id is None:
            result = self._result(
                candidate,
                cleanup_status="BLOCKED",
                physical_status="BLOCKED",
                reason_code=candidate.issue_code or "JOB_DIRECTORY_ID_INVALID",
            )
            self._audit(result, before={"directory_name": candidate.directory_name})
            return result

        state = self._job_state(candidate.job_id, lock=False)
        reason = self._ineligible_reason(state, observed_at=observed_at, cutoff=cutoff)
        before = self._safe_before(candidate, state, observed_at, cutoff)
        if reason is not None:
            result = self._result(
                candidate,
                cleanup_status="INELIGIBLE",
                physical_status="RETAINED",
                reason_code=reason,
            )
            self._audit(result, before=before)
            return result

        inspected = self._inspect(candidate)
        if isinstance(inspected, FormalOrphanCleanupResult):
            self._audit(inspected, before=before)
            return inspected
        if inspected.physical_status == "MISSING":
            result = self._result(
                candidate,
                cleanup_status="MISSING",
                physical_status="MISSING",
                reason_code="ORPHAN_ROOT_MISSING",
            )
            self._audit(result, before=before)
            return result
        if dry_run:
            result = self._result(
                candidate,
                cleanup_status="DRY_RUN",
                physical_status="PRESENT",
                reason_code="ELIGIBLE_ORPHAN_ROOT",
                discovered_bytes=inspected.discovered_bytes,
                discovered_files=inspected.discovered_file_count,
                discovered_entries=inspected.discovered_entry_count,
            )
            self._audit(result, before=before)
            return result

        started = self._result(
            candidate,
            cleanup_status="DELETE_STARTED",
            physical_status="PRESENT",
            reason_code="ELIGIBLE_ORPHAN_ROOT",
            discovered_bytes=inspected.discovered_bytes,
            discovered_files=inspected.discovered_file_count,
            discovered_entries=inspected.discovered_entry_count,
        )
        self._audit(started, before=before)
        return self._delete_with_recheck(
            candidate,
            observed_at=observed_at,
            cutoff=cutoff,
            before=before,
        )

    def _delete_with_recheck(
        self,
        candidate: FormalOrphanCandidate,
        *,
        observed_at: datetime,
        cutoff: datetime,
        before: dict[str, Any],
    ) -> FormalOrphanCleanupResult:
        assert candidate.job_id is not None
        with self._engine.begin() as connection:
            state = self._job_state(candidate.job_id, lock=True, connection=connection)
            reason = self._ineligible_reason(
                state,
                observed_at=observed_at,
                cutoff=cutoff,
            )
            if reason is not None:
                result = self._result(
                    candidate,
                    cleanup_status="INELIGIBLE",
                    physical_status="RETAINED",
                    reason_code=f"STATE_CHANGED_{reason}",
                )
                self._audit(result, before=before, connection=connection)
                return result
            try:
                outcome = self._cleaner.cleanup_job(candidate.job_id, dry_run=False)
                status = "MISSING" if outcome.physical_status == "MISSING" else "DELETED"
                result = self._result(
                    candidate,
                    cleanup_status=status,
                    physical_status=outcome.physical_status,
                    reason_code=(
                        "ORPHAN_ROOT_MISSING"
                        if status == "MISSING"
                        else "ORPHAN_ROOT_DELETED"
                    ),
                    discovered_bytes=outcome.discovered_bytes,
                    discovered_files=outcome.discovered_file_count,
                    discovered_entries=outcome.discovered_entry_count,
                )
            except OversizedFormalOrphanRoot:
                result = self._result(
                    candidate,
                    cleanup_status="BLOCKED",
                    physical_status="BLOCKED",
                    reason_code="ORPHAN_ROOT_OVERSIZED",
                )
            except UnsafeFormalArtifactPath:
                result = self._result(
                    candidate,
                    cleanup_status="BLOCKED",
                    physical_status="BLOCKED",
                    reason_code="ORPHAN_ROOT_PATH_UNSAFE",
                )
            except OSError:
                result = self._result(
                    candidate,
                    cleanup_status="ERROR",
                    physical_status="ERROR",
                    reason_code="ORPHAN_ROOT_IO_ERROR",
                )
            self._audit(result, before=before, connection=connection)
            return result

    def _inspect(self, candidate: FormalOrphanCandidate):
        assert candidate.job_id is not None
        try:
            return self._cleaner.inspect_job(candidate.job_id)
        except OversizedFormalOrphanRoot:
            return self._result(
                candidate,
                cleanup_status="BLOCKED",
                physical_status="BLOCKED",
                reason_code="ORPHAN_ROOT_OVERSIZED",
            )
        except UnsafeFormalArtifactPath:
            return self._result(
                candidate,
                cleanup_status="BLOCKED",
                physical_status="BLOCKED",
                reason_code="ORPHAN_ROOT_PATH_UNSAFE",
            )
        except OSError:
            return self._result(
                candidate,
                cleanup_status="ERROR",
                physical_status="ERROR",
                reason_code="ORPHAN_ROOT_IO_ERROR",
            )

    def _job_state(
        self,
        job_id: int,
        *,
        lock: bool,
        connection=None,
    ) -> Mapping[str, Any] | None:
        lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        artifact_lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        statement = text(
            "SELECT j.job_id,j.job_type,j.status,j.finished_at_utc,"
            "j.lease_token,j.lease_owner,j.lease_expires_at_utc,"
            "(SELECT COUNT_BIG(*) FROM ingestion.processing_artifact p"
            f"{artifact_lock_hint} "
            "WHERE p.job_id=j.job_id AND p.temporary_flag=0) "
            "AS permanent_artifact_count,"
            "(SELECT COUNT_BIG(*) FROM ingestion.processing_artifact t"
            f"{artifact_lock_hint} "
            "WHERE t.job_id=j.job_id AND t.temporary_flag=1 "
            "AND t.physical_status NOT IN('DELETED','MISSING')) "
            "AS active_temporary_artifact_count "
            f"FROM ingestion.processing_job j{lock_hint} WHERE j.job_id=:job"
        )
        if connection is not None:
            return (
                connection.execute(statement, {"job": job_id})
                .mappings()
                .one_or_none()
            )
        with self._engine.connect() as opened:
            return (
                opened.execute(statement, {"job": job_id})
                .mappings()
                .one_or_none()
            )

    def _ineligible_reason(
        self,
        state: Mapping[str, Any] | None,
        *,
        observed_at: datetime,
        cutoff: datetime,
    ) -> str | None:
        if state is None:
            return "JOB_NOT_FOUND"
        if str(state["job_type"]) not in self._FORMAL_TYPES:
            return "JOB_TYPE_NOT_FORMAL"
        if str(state["status"]) not in self._TERMINAL_STATUSES:
            return "JOB_NOT_TERMINAL"
        finished = state["finished_at_utc"]
        if finished is None:
            return "JOB_FINISH_TIME_MISSING"
        if _naive_utc(finished) > cutoff:
            return "ORPHAN_RETENTION_ACTIVE"
        lease_present = state["lease_token"] is not None or bool(
            str(state["lease_owner"] or "").strip()
        )
        lease_expires = state["lease_expires_at_utc"]
        if lease_present and (
            lease_expires is None or _naive_utc(lease_expires) > observed_at
        ):
            return "JOB_LEASE_ACTIVE"
        if int(state["permanent_artifact_count"]) != 0:
            return "PERMANENT_ARTIFACT_PRESENT"
        if int(state["active_temporary_artifact_count"]) != 0:
            return "REGISTERED_TEMPORARY_ARTIFACT_ACTIVE"
        return None

    @staticmethod
    def _safe_before(
        candidate: FormalOrphanCandidate,
        state: Mapping[str, Any] | None,
        observed_at: datetime,
        cutoff: datetime,
    ) -> dict[str, Any]:
        lease_present = (
            False
            if state is None
            else state["lease_token"] is not None
            or bool(str(state["lease_owner"] or "").strip())
        )
        lease_expires = None if state is None else state["lease_expires_at_utc"]
        active_lease = lease_present and (
            lease_expires is None or _naive_utc(lease_expires) > observed_at
        )
        return {
            "directory_name": candidate.directory_name,
            "job_found": state is not None,
            "job_type": None if state is None else str(state["job_type"]),
            "job_status": None if state is None else str(state["status"]),
            "finished_at_utc": (
                None
                if state is None or state["finished_at_utc"] is None
                else _naive_utc(state["finished_at_utc"]).isoformat()
            ),
            "active_lease": active_lease,
            "permanent_artifact_count": (
                None if state is None else int(state["permanent_artifact_count"])
            ),
            "active_temporary_artifact_count": (
                None
                if state is None
                else int(state["active_temporary_artifact_count"])
            ),
            "observed_at_utc": observed_at.isoformat(),
            "retention_cutoff_utc": cutoff.isoformat(),
        }

    @staticmethod
    def _result(
        candidate: FormalOrphanCandidate,
        *,
        cleanup_status: str,
        physical_status: str,
        reason_code: str,
        discovered_bytes: int = 0,
        discovered_files: int = 0,
        discovered_entries: int = 0,
    ) -> FormalOrphanCleanupResult:
        return FormalOrphanCleanupResult(
            candidate.directory_name,
            candidate.job_id,
            cleanup_status,
            physical_status,
            discovered_bytes,
            discovered_files,
            discovered_entries,
            reason_code,
        )

    def _audit(
        self,
        result: FormalOrphanCleanupResult,
        *,
        before: dict[str, Any],
        connection=None,
    ) -> None:
        parameters = {
            "actor": f"formal-orphan-cleanup:{socket.gethostname()}"[:128],
            "entity_id": (
                str(result.job_id)
                if result.job_id is not None
                else result.directory_name[:128]
            ),
            "before_json": json.dumps(
                before,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "after_json": json.dumps(
                asdict(result),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "reason": "Formal orphan Job root retention policy evaluation",
            "correlation_id": str(uuid4()),
        }
        statement = text(
            "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
            "before_json,after_json,reason,correlation_id,actor_user_id) VALUES("
            ":actor,'FORMAL_ORPHAN_ROOT_SWEEP','ingestion.processing_job',"
            ":entity_id,:before_json,:after_json,:reason,:correlation_id,NULL)"
        )
        if connection is not None:
            connection.execute(statement, parameters)
            return
        with self._engine.begin() as opened:
            opened.execute(statement, parameters)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
