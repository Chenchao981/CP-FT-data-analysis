from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import CreateJobRequest, Job, JobStatus, JobType, TriggerType
from app.infrastructure.sql_job_service import (
    SqlJobService,
    _assert_idempotent_job_scope,
    _external_idempotency_key,
    _lease_tokens_equal,
    _mark_exhausted_initial_import_batches_failed,
    _raise_initial_import_batch_state_conflict,
    _to_job,
)

from scripts.g0.revalidate_sqlserver_enterprise import version_tuple


def test_database_job_row_maps_to_domain() -> None:
    row = {
        "job_id": 1,
        "source_file_id": None,
        "import_batch_id": 7,
        "analysis_session_id": None,
        "cleaner_release_id": 2,
        "job_type": "PARSE",
        "trigger_type": "MANUAL",
        "requested_by": "tester",
        "requested_by_user_id": 1,
        "reason": None,
        "status": "RUNNING",
        "requested_at_utc": datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC),
        "started_at_utc": datetime(2026, 8, 20, 1, 2, 4, tzinfo=UTC),
        "finished_at_utc": None,
        "error_code": None,
        "error_message": None,
        "idempotency_key": "test-job-0001",
        "not_before_utc": datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC),
        "lease_token": "11111111-1111-1111-1111-111111111111",
        "lease_owner": "worker-1",
        "lease_expires_at_utc": datetime(2026, 8, 20, 1, 7, 3, tzinfo=UTC),
        "heartbeat_at_utc": datetime(2026, 8, 20, 1, 2, 4, tzinfo=UTC),
        "attempt_count": 1,
        "max_attempts": 3,
    }
    job = _to_job(row)
    assert job.status == JobStatus.RUNNING
    assert job.requested_at_utc.utcoffset().total_seconds() == 0


def test_sql_server_build_comparison_input() -> None:
    assert version_tuple("12.0.6024.0") == (12, 0, 6024, 0)


def _principal(user_id: int) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name=f"User {user_id}",
        roles=(),
        permissions=frozenset(),
    )


def _job_request(principal: Principal, *, batch_id: int = 7) -> CreateJobRequest:
    return CreateJobRequest(
        import_batch_id=batch_id,
        cleaner_release_id=2,
        job_type=JobType.PARSE,
        trigger_type=TriggerType.API,
        requested_by=principal.login_name,
        requested_by_user_id=principal.user_id,
        reason="idempotency scope test",
        idempotency_key=_external_idempotency_key(principal.user_id, "client-key-0001"),
    )


def _existing_job(request: CreateJobRequest) -> Job:
    return Job(
        job_id=91,
        source_file_id=request.source_file_id,
        import_batch_id=request.import_batch_id,
        analysis_session_id=request.analysis_session_id,
        cleaner_release_id=request.cleaner_release_id,
        job_type=request.job_type,
        trigger_type=request.trigger_type,
        requested_by=request.requested_by,
        requested_by_user_id=request.requested_by_user_id,
        reason=request.reason,
        status=JobStatus.QUEUED,
        requested_at_utc=datetime.now(UTC),
        idempotency_key=request.idempotency_key,
        max_attempts=request.max_attempts,
    )


def test_external_idempotency_keys_are_namespaced_per_user() -> None:
    assert _external_idempotency_key(1, "shared-client-key") != (
        _external_idempotency_key(2, "shared-client-key")
    )


def test_worker_lease_token_comparison_accepts_sql_server_uuid_casing() -> None:
    assert _lease_tokens_equal(
        "11111111-AAAA-BBBB-CCCC-222222222222",
        "11111111-aaaa-bbbb-cccc-222222222222",
    )
    assert not _lease_tokens_equal(None, "11111111-aaaa-bbbb-cccc-222222222222")


def test_idempotent_job_return_requires_same_user_and_exact_scope() -> None:
    owner = _principal(1)
    other_user = _principal(2)
    request = _job_request(owner)
    existing = _existing_job(request)

    _assert_idempotent_job_scope(existing, request, owner)

    with pytest.raises(DomainError) as cross_user:
        _assert_idempotent_job_scope(existing, _job_request(other_user), other_user)
    assert cross_user.value.code == "JOB_IDEMPOTENCY_SCOPE_CONFLICT"

    with pytest.raises(DomainError) as changed_input:
        _assert_idempotent_job_scope(existing, _job_request(owner, batch_id=8), owner)
    assert changed_input.value.code == "JOB_IDEMPOTENCY_SCOPE_CONFLICT"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("NEEDS_INPUT", "LOT_INPUT_RESOLUTION_REQUIRED"),
        ("QUEUED", "BATCH_ALREADY_ACTIVE"),
        ("PROCESSING", "BATCH_ALREADY_ACTIVE"),
        ("CANCELLED", "BATCH_QUEUE_STATE_CONFLICT"),
    ],
)
def test_initial_import_batch_state_conflicts_are_explicit(
    status: str, code: str
) -> None:
    with pytest.raises(DomainError) as exc_info:
        _raise_initial_import_batch_state_conflict(status)
    assert exc_info.value.code == code


class _SqlResult:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]


class _AtomicInitialImportConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append((sql, parameters))
        if "SELECT status,owner_user_id" in sql:
            return _SqlResult(rows=[{"status": "PROCESSED", "owner_user_id": 1}])
        if "UPDATE ingestion.import_batch SET status='QUEUED'" in sql:
            return _SqlResult(rowcount=1)
        if "WHERE idempotency_key=:key" in sql:
            return _SqlResult()
        if "INSERT ingestion.processing_job" in sql:
            now = datetime.now(UTC)
            return _SqlResult(
                rows=[
                    {
                        "job_id": 91,
                        "source_file_id": None,
                        "import_batch_id": 7,
                        "analysis_session_id": None,
                        "cleaner_release_id": 2,
                        "job_type": "INITIAL_IMPORT",
                        "trigger_type": "MANUAL",
                        "requested_by": "user-1",
                        "requested_by_user_id": 1,
                        "reason": "atomic reprocess",
                        "status": "QUEUED",
                        "requested_at_utc": now,
                        "started_at_utc": None,
                        "finished_at_utc": None,
                        "error_code": None,
                        "error_message": None,
                        "idempotency_key": "reprocess:7:atomic",
                        "not_before_utc": now,
                        "lease_token": None,
                        "lease_owner": None,
                        "lease_expires_at_utc": None,
                        "heartbeat_at_utc": None,
                        "attempt_count": 0,
                        "max_attempts": 3,
                        "parent_job_id": None,
                    }
                ]
            )
        raise AssertionError(sql)


class _AtomicEngine:
    def __init__(self, connection: _AtomicInitialImportConnection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


def test_initial_import_job_and_batch_queue_are_written_in_one_transaction() -> None:
    principal = _principal(1)
    request = CreateJobRequest(
        import_batch_id=7,
        cleaner_release_id=2,
        job_type=JobType.INITIAL_IMPORT,
        trigger_type=TriggerType.MANUAL,
        requested_by="untrusted",
        requested_by_user_id=999,
        reason="atomic reprocess",
        idempotency_key="reprocess:7:atomic",
    )
    connection = _AtomicInitialImportConnection()
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    job = service.create_initial_import_for_batch(
        request,
        principal,
        allowed_batch_statuses=("PROCESSED", "FAILED"),
    )

    assert job.job_id == 91
    assert job.requested_by == principal.login_name
    statements = [sql for sql, _parameters in connection.statements]
    assert "WITH (UPDLOCK,HOLDLOCK)" in statements[0]
    assert "status='QUEUED'" in statements[1]
    assert "INSERT ingestion.processing_job" in statements[-1]


class _BatchFailureConnection:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        self.updates.append((str(statement), parameters or {}))
        return _SqlResult(rowcount=1)


def test_max_attempt_cleanup_fails_only_linked_active_initial_import_batches() -> None:
    connection = _BatchFailureConnection()
    now = datetime.now(UTC).replace(tzinfo=None)

    _mark_exhausted_initial_import_batches_failed(
        connection,  # type: ignore[arg-type]
        [
            {"job_type": "INITIAL_IMPORT", "import_batch_id": 7},
            {"job_type": "INITIAL_IMPORT", "import_batch_id": 7},
            {"job_type": "QUICK_PAT", "import_batch_id": 8},
            {"job_type": "INITIAL_IMPORT", "import_batch_id": None},
        ],
        now,
    )

    assert len(connection.updates) == 1
    sql, parameters = connection.updates[0]
    assert "b.status IN('QUEUED','PROCESSING')" in sql
    assert "active_job.status IN('QUEUED','RUNNING')" in sql
    assert parameters == {"now": now, "batch": 7}
