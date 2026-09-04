from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.jobs import (
    CreateJobRequest,
    Job,
    JobStatus,
    JobType,
    TransitionJobRequest,
    TriggerType,
)
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
        "finalize_protocol": "ATOMIC_V1",
    }
    job = _to_job(row)
    assert job.status == JobStatus.RUNNING
    assert job.finalize_protocol == "ATOMIC_V1"
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

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        row = self._rows[0]
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row


class _AtomicInitialImportConnection:
    def __init__(
        self,
        *,
        batch_row: dict[str, Any] | None = None,
        active_domain_grant: bool = True,
    ) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.batch_row = batch_row or {
            "status": "PROCESSED",
            "owner_user_id": 1,
            "access_scope": "PERSONAL",
            "data_domain_id": None,
            "source_channel": "WEB",
            "uploaded_by": "user-1",
        }
        self.active_domain_grant = active_domain_grant

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append((sql, parameters))
        if "SELECT status,owner_user_id" in sql:
            return _SqlResult(rows=[self.batch_row])
        if "SELECT TOP (1) 1 FROM iam.data_domain_grant g" in sql:
            return _SqlResult(rows=[{"allowed": 1}] if self.active_domain_grant else [])
        if "SELECT 1 FROM ingestion.import_batch b" in sql:
            return _SqlResult(rows=[{"allowed": 1}] if self.active_domain_grant else [])
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
                        "finalize_protocol": "ATOMIC_V1",
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
    assert connection.statements[-1][1]["finalize_protocol"] == "ATOMIC_V1"


def test_catalog_submitter_with_active_domain_grant_can_queue_received_batch() -> None:
    principal = _principal(7)
    request = CreateJobRequest(
        import_batch_id=17,
        cleaner_release_id=2,
        job_type=JobType.INITIAL_IMPORT,
        trigger_type=TriggerType.AUTO,
        requested_by="untrusted",
        requested_by_user_id=999,
        reason="catalog import",
        idempotency_key="initial-import:17",
    )
    connection = _AtomicInitialImportConnection(
        batch_row={
            "status": "RECEIVED",
            "owner_user_id": 900,
            "access_scope": "DOMAIN",
            "data_domain_id": 23,
            "source_channel": "SOURCE_CATALOG",
            "uploaded_by": principal.login_name,
        }
    )
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    job = service.create_initial_import_for_batch(
        request,
        principal,
        allowed_batch_statuses=("RECEIVED",),
    )

    assert job.job_id == 91
    grant_sql, grant_parameters = connection.statements[1]
    assert "iam.data_domain_grant" in grant_sql
    assert "WITH (UPDLOCK,HOLDLOCK)" in grant_sql
    assert "JOIN iam.data_domain d WITH (UPDLOCK,HOLDLOCK)" in grant_sql
    assert "d.active=1" in grant_sql
    assert "JOIN iam.app_user u WITH (UPDLOCK,HOLDLOCK)" in grant_sql
    assert "u.status='ACTIVE'" in grant_sql
    assert "g.expires_at_utc>SYSUTCDATETIME()" in grant_sql
    assert grant_parameters == {"user_id": 7, "data_domain_id": 23}
    assert "status='QUEUED'" in connection.statements[2][0]


def test_catalog_submitter_cannot_queue_after_domain_grant_revocation() -> None:
    principal = _principal(7)
    request = CreateJobRequest(
        import_batch_id=17,
        cleaner_release_id=2,
        job_type=JobType.INITIAL_IMPORT,
        trigger_type=TriggerType.AUTO,
        requested_by="untrusted",
        requested_by_user_id=999,
        reason="catalog import",
        idempotency_key="initial-import:17",
    )
    connection = _AtomicInitialImportConnection(
        batch_row={
            "status": "RECEIVED",
            "owner_user_id": 900,
            "access_scope": "DOMAIN",
            "data_domain_id": 23,
            "source_channel": "SOURCE_CATALOG",
            "uploaded_by": principal.login_name,
        },
        active_domain_grant=False,
    )
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        service.create_initial_import_for_batch(
            request,
            principal,
            allowed_batch_statuses=("RECEIVED",),
        )

    assert error.value.code == "BATCH_NOT_FOUND"
    assert all("status='QUEUED'" not in sql for sql, _ in connection.statements)


def test_system_admin_can_queue_another_users_personal_batch() -> None:
    principal = Principal(
        1,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"TASK_CREATE"}),
    )
    request = CreateJobRequest(
        import_batch_id=17,
        cleaner_release_id=2,
        job_type=JobType.INITIAL_IMPORT,
        trigger_type=TriggerType.MANUAL,
        requested_by=principal.login_name,
        requested_by_user_id=principal.user_id,
        reason="cross-owner attempt",
        idempotency_key="initial-import:17:cross-owner",
    )
    connection = _AtomicInitialImportConnection(
        batch_row={
            "status": "PROCESSED",
            "owner_user_id": 2,
            "access_scope": "PERSONAL",
            "data_domain_id": None,
            "source_channel": "WEB",
            "uploaded_by": "user-2",
        }
    )
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    job = service.create_initial_import_for_batch(
        request,
        principal,
        allowed_batch_statuses=("PROCESSED", "FAILED"),
    )

    assert job.job_id == 91
    assert any("status='QUEUED'" in sql for sql, _ in connection.statements)


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


_LEASE_TOKEN = "11111111-aaaa-bbbb-cccc-222222222222"


def _atomic_job_row(
    *,
    status: str = "RUNNING",
    lease_token: str | None = _LEASE_TOKEN,
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    return {
        "job_id": 91,
        "source_file_id": None,
        "import_batch_id": 7,
        "analysis_session_id": None,
        "cleaner_release_id": 2,
        "job_type": "INITIAL_IMPORT",
        "trigger_type": "MANUAL",
        "requested_by": "user-1",
        "requested_by_user_id": 1,
        "reason": "atomic finalize test",
        "status": status,
        "requested_at_utc": now,
        "started_at_utc": now,
        "finished_at_utc": now if status in {"SUCCESS", "FAILED"} else None,
        "error_code": "CLEANER_FAILED" if status == "FAILED" else None,
        "error_message": "failed" if status == "FAILED" else None,
        "idempotency_key": "atomic-finalize-test",
        "not_before_utc": now,
        "lease_token": lease_token,
        "lease_owner": "route-a-worker-1" if lease_token else None,
        "lease_expires_at_utc": now + timedelta(minutes=5) if lease_token else None,
        "heartbeat_at_utc": now,
        "attempt_count": 1,
        "max_attempts": 3,
        "parent_job_id": None,
        "finalize_protocol": "ATOMIC_V1",
    }


class _JobAccessConnection:
    def __init__(self, *, business_domain: str, can_manage: bool = False) -> None:
        self.business_domain = business_domain
        self.can_manage = can_manage
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if "AS can_manage,b.business_domain" in sql:
            if not self.can_manage and self.business_domain != "PRODUCTION":
                return _SqlResult()
            return _SqlResult(
                rows=[
                    {
                        "can_manage": int(self.can_manage),
                        "business_domain": self.business_domain,
                    }
                ]
            )
        if "SELECT TOP (1) 1 FROM ingestion.processing_job j" in sql:
            return (
                _SqlResult(rows=[{"allowed": 1}]) if self.can_manage else _SqlResult()
            )
        if "FROM ingestion.processing_job WHERE job_id=:job_id" in sql:
            row = {
                **_atomic_job_row(),
                "source_file_id": 31,
                "reason": "operator-only diagnostic",
                "error_message": "private worker detail",
            }
            return _SqlResult(rows=[row])
        raise AssertionError(sql)


class _JobAccessEngine:
    def __init__(self, connection: _JobAccessConnection) -> None:
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


class _QuickCreateDeniedEngine:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, parameters=None):
        del parameters
        self.statements.append(str(statement))
        return _SqlResult()


def test_domain_member_can_read_redacted_domain_job() -> None:
    connection = _JobAccessConnection(business_domain="PRODUCTION")
    service = SqlJobService(_JobAccessEngine(connection))  # type: ignore[arg-type]

    job = service.get_for_principal(91, _principal(2))

    assert job.job_id == 91
    assert job.status == JobStatus.RUNNING
    assert job.import_batch_id == 7
    assert job.source_file_id is None
    assert job.requested_by_user_id is None
    assert job.reason is None
    assert job.error_message is None
    assert job.idempotency_key is None
    assert job.lease_token is None
    assert job.lease_owner is None
    assert "b.access_scope='DOMAIN'" in connection.statements[0]
    assert "iam.data_domain_grant" in connection.statements[0]
    assert "business_domain='PRODUCTION'" not in connection.statements[0]


def test_non_member_cannot_read_domain_job() -> None:
    service = SqlJobService(  # type: ignore[arg-type]
        _JobAccessEngine(_JobAccessConnection(business_domain="ENGINEERING"))
    )

    with pytest.raises(DomainError) as error:
        service.get_for_principal(91, _principal(2))

    assert error.value.code == "JOB_NOT_FOUND"


def test_non_owner_production_reader_cannot_transition_job() -> None:
    connection = _JobAccessConnection(business_domain="PRODUCTION")
    service = SqlJobService(_JobAccessEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        service.transition_for_principal(
            91,
            TransitionJobRequest(target_status=JobStatus.CANCELLED),
            _principal(2),
        )

    assert error.value.code == "JOB_NOT_FOUND"
    assert "b.access_scope='PERSONAL'" in connection.statements[0]
    assert "access_grant.data_domain_id=ws.data_domain_id" in connection.statements[0]


def test_system_admin_cannot_create_quick_job_without_session_access() -> None:
    engine = _QuickCreateDeniedEngine()
    service = SqlJobService(engine)  # type: ignore[arg-type]
    admin = Principal(
        99,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"SYSTEM_OPERATE"}),
    )
    request = CreateJobRequest(
        analysis_session_id=77,
        cleaner_release_id=2,
        job_type=JobType.QUICK_PAT,
        trigger_type=TriggerType.MANUAL,
        requested_by=admin.login_name,
        requested_by_user_id=admin.user_id,
        reason="must not bypass Quick ACL",
    )

    with pytest.raises(DomainError) as error:
        service.create_for_principal(request, admin)

    assert error.value.code == "JOB_INPUT_NOT_FOUND"
    assert len(engine.statements) == 1
    assert (
        "workspace.analysis_session ws WITH (UPDLOCK,HOLDLOCK)" in engine.statements[0]
    )
    assert "ws.owner_user_id=:user_id" in engine.statements[0]
    assert "access_grant WITH (UPDLOCK,HOLDLOCK)" in engine.statements[0]
    assert "access_domain WITH (UPDLOCK,HOLDLOCK)" in engine.statements[0]
    assert "access_grant.status='ACTIVE'" in engine.statements[0]
    assert "expires_at_utc>SYSUTCDATETIME()" in engine.statements[0]


def test_system_admin_cannot_create_job_for_another_users_personal_batch() -> None:
    engine = _QuickCreateDeniedEngine()
    service = SqlJobService(engine)  # type: ignore[arg-type]
    admin = Principal(
        99,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"TASK_CREATE", "SYSTEM_OPERATE"}),
    )
    request = CreateJobRequest(
        import_batch_id=77,
        cleaner_release_id=2,
        job_type=JobType.REPROCESS,
        trigger_type=TriggerType.MANUAL,
        requested_by=admin.login_name,
        requested_by_user_id=admin.user_id,
        reason="must not bypass PERSONAL ACL",
    )

    with pytest.raises(DomainError) as error:
        service.create_for_principal(request, admin)

    assert error.value.code == "JOB_INPUT_NOT_FOUND"
    assert len(engine.statements) == 1
    assert "b.access_scope='PERSONAL'" in engine.statements[0]
    assert "b.owner_user_id=:user_id" in engine.statements[0]
    assert "access_grant.status='ACTIVE'" in engine.statements[0]
    assert "access_domain.active=1" in engine.statements[0]
    assert "job_requester.status='ACTIVE'" in engine.statements[0]


def test_domain_member_job_creation_locks_current_authorization() -> None:
    principal = _principal(7)
    request = CreateJobRequest(
        import_batch_id=17,
        cleaner_release_id=2,
        job_type=JobType.REPROCESS,
        trigger_type=TriggerType.MANUAL,
        requested_by=principal.login_name,
        requested_by_user_id=principal.user_id,
        reason="domain analysis",
    )
    connection = _AtomicInitialImportConnection(active_domain_grant=True)
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    job = service.create_for_principal(request, principal)

    assert job.job_id == 91
    authorization_sql = connection.statements[0][0]
    assert "job_requester.status='ACTIVE'" in authorization_sql
    assert "access_grant WITH (UPDLOCK,HOLDLOCK)" in authorization_sql
    assert "access_domain WITH (UPDLOCK,HOLDLOCK)" in authorization_sql
    assert "access_grant.expires_at_utc>SYSUTCDATETIME()" in authorization_sql
    assert authorization_sql.count("(") == authorization_sql.count(")")


def test_domain_member_job_creation_rejects_revoked_authorization() -> None:
    principal = _principal(7)
    request = CreateJobRequest(
        import_batch_id=17,
        cleaner_release_id=2,
        job_type=JobType.REPROCESS,
        trigger_type=TriggerType.MANUAL,
        requested_by=principal.login_name,
        requested_by_user_id=principal.user_id,
        reason="revoked domain analysis",
    )
    connection = _AtomicInitialImportConnection(active_domain_grant=False)
    service = SqlJobService(_AtomicEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as error:
        service.create_for_principal(request, principal)

    assert error.value.code == "JOB_INPUT_NOT_FOUND"
    assert all("INSERT ingestion.processing_job" not in sql for sql, _ in connection.statements)


def _staged_intent_row() -> dict[str, Any]:
    return {
        "job_id": 91,
        "import_batch_id": 7,
        "processing_run_id": 101,
        "dataset_version_id": 205,
        "status": "STAGED",
        "finalized_lease_token": None,
        "dataset_id": 41,
        "version_no": 5,
        "input_batch_id": 7,
        "version_status": "DRAFT",
        "is_current": False,
        "unit_count": 20,
        "measurement_count": 200,
        "spec_set_id": None,
        "run_job_id": 91,
        "run_status": "READY",
        "run_is_current": False,
        "source_file_id": 31,
        "unit_count_output": 20,
        "measurement_count_output": 200,
        "lifecycle_action_type": None,
        "lifecycle_dataset_id": None,
        "lifecycle_target_version_id": None,
        "lifecycle_dataset_status": None,
        "lifecycle_target_version_status": None,
        "lifecycle_target_is_current": None,
        "lifecycle_target_batch_id": None,
    }


class _AtomicFinalizeConnection:
    def __init__(
        self,
        *,
        job_row: dict[str, Any] | None = None,
        intent_row: dict[str, Any] | None = None,
        links: dict[str, int] | None = None,
        previous_version_id: int | None = 204,
        previous_run_id: int | None = 100,
        previous_run_has_other_current: bool = False,
        zero_rowcount_contains: str | None = None,
    ) -> None:
        self.job_row = job_row or _atomic_job_row()
        self.intent_row = intent_row or _staged_intent_row()
        self.links = links or {
            "batch_file_count": 3,
            "lineage_count": 3,
            "wrong_batch_count": 0,
            "unverified_lineage_count": 0,
            "version_run_count": 1,
        }
        self.previous_version_id = previous_version_id
        self.previous_run_id = previous_run_id
        self.previous_run_has_other_current = previous_run_has_other_current
        self.zero_rowcount_contains = zero_rowcount_contains
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        parameters = parameters or {}
        self.statements.append((sql, parameters))
        if (
            "SELECT job_id" in sql
            and "FROM ingestion.processing_job" in sql
            and "WHERE job_id=:job_id" in sql
        ):
            return _SqlResult(rows=[self.job_row])
        if sql.startswith("SELECT i.job_id"):
            return _SqlResult(rows=[self.intent_row])
        if sql.startswith(
            "SELECT import_batch_id,processing_run_id,dataset_version_id,status"
        ):
            simple_intent = {
                key: self.intent_row[key]
                for key in (
                    "import_batch_id",
                    "processing_run_id",
                    "dataset_version_id",
                    "status",
                )
            }
            return _SqlResult(rows=[simple_intent])
        if sql.startswith("SELECT b.status,b.owner_user_id,b.access_scope"):
            return _SqlResult(
                rows=[
                    {
                        "status": "PROCESSING",
                        "owner_user_id": 1,
                        "access_scope": "PERSONAL",
                        "data_domain_id": None,
                        "source_definition_id": None,
                        "dataset_access_scope": "PERSONAL",
                        "dataset_data_domain_id": None,
                        "dataset_source_definition_id": None,
                    }
                ]
            )
        if sql.startswith("SELECT (SELECT COUNT(*)"):
            return _SqlResult(rows=[self.links])
        if sql.startswith("SELECT dataset_version_id FROM dataset.dataset_version"):
            rows = (
                [{"dataset_version_id": self.previous_version_id}]
                if self.previous_version_id is not None
                else []
            )
            return _SqlResult(rows=rows)
        if sql.startswith("SELECT pr.processing_run_id,pr.status,pr.is_current"):
            rows = (
                [
                    {
                        "processing_run_id": self.previous_run_id,
                        "status": "PUBLISHED",
                        "is_current": True,
                        "has_other_current": self.previous_run_has_other_current,
                    }
                ]
                if self.previous_run_id is not None
                else []
            )
            return _SqlResult(rows=rows)
        if (
            sql.startswith("UPDATE ingestion.processing_job")
            and "SET status=:status" in sql
        ):
            failed = _atomic_job_row(status="FAILED", lease_token=None)
            return _SqlResult(rows=[failed], rowcount=1)
        if sql.startswith("UPDATE ingestion.processing_job SET status='SUCCESS'"):
            succeeded = _atomic_job_row(status="SUCCESS", lease_token=None)
            return _SqlResult(rows=[succeeded], rowcount=1)
        if sql.startswith("UPDATE ingestion.processing_result_summary SET data_name"):
            return _SqlResult(rowcount=0)
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'"):
            return _SqlResult(rowcount=0 if self.previous_run_has_other_current else 1)
        if sql.startswith(("UPDATE ", "INSERT ")):
            if self.zero_rowcount_contains and self.zero_rowcount_contains in sql:
                return _SqlResult(rowcount=0)
            return _SqlResult(rowcount=1)
        raise AssertionError(f"unhandled SQL in atomic test fake: {sql}")


class _TransactionalFakeEngine:
    def __init__(self, connection: _AtomicFinalizeConnection) -> None:
        self.connection = connection
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    @contextmanager
    def begin(self):
        self.begin_count += 1
        try:
            yield self.connection
        except BaseException:
            self.rollback_count += 1
            raise
        else:
            self.commit_count += 1


def _atomic_summary() -> dict[str, Any]:
    return {
        "data_name": "NCE CP LOT-001",
        "product_name": "NCE-TEST",
        "lot_id": "LOT-001",
        "wafer_count": 2,
        "factory_code": "HUAHONG",
        "output_uri": "tms://formal/job-91",
        "test_item_count": 10,
        "unit_count": 20,
        "pass_count": 18,
        "yield_rate": 0.9,
        "data_type": "CP",
        "artifacts": [{"name": "manifest.json"}],
        # These untrusted values must never override the staged Intent lineage.
        "dataset_id": 999,
        "dataset_version_no": 999,
    }


def test_finish_leased_rejects_atomic_initial_import_success() -> None:
    connection = _AtomicFinalizeConnection()
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.finish_leased(91, _LEASE_TOKEN, JobStatus.SUCCESS)

    assert exc_info.value.code == "ATOMIC_FINALIZE_REQUIRED"
    assert engine.begin_count == 1
    assert engine.rollback_count == 1
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.statements)


def test_finish_leased_failure_aborts_staged_atomic_import_in_one_transaction() -> None:
    connection = _AtomicFinalizeConnection()
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    job = service.finish_leased(
        91,
        _LEASE_TOKEN,
        JobStatus.FAILED,
        error_code="CLEANER_FAILED",
        error_message="cleaner failed after staging",
    )

    assert job.status == JobStatus.FAILED
    assert engine.begin_count == 1
    assert engine.commit_count == 1
    assert engine.rollback_count == 0
    statements = [sql for sql, _ in connection.statements]
    assert any(
        "UPDATE ingestion.processing_run SET status='FAILED'" in sql
        and "status='READY'" in sql
        for sql in statements
    )
    assert any(
        "UPDATE dataset.dataset_version SET status='ARCHIVED'" in sql
        and "status='DRAFT'" in sql
        for sql in statements
    )
    assert any(
        "initial_import_finalize_intent SET status='ABORTED'" in sql
        and "status='STAGED'" in sql
        for sql in statements
    )
    assert any(
        "import_batch SET status='FAILED'" in sql
        and "status IN('QUEUED','PROCESSING')" in sql
        for sql in statements
    )


def test_atomic_abort_rejects_run_state_drift_and_rolls_back_job_failure() -> None:
    connection = _AtomicFinalizeConnection(
        zero_rowcount_contains="processing_run SET status='FAILED'"
    )
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.finish_leased(
            91,
            _LEASE_TOKEN,
            JobStatus.FAILED,
            error_code="CLEANER_FAILED",
            error_message="synthetic drift",
        )

    assert exc_info.value.code == "ATOMIC_ABORT_RUN_STATE_CONFLICT"
    assert engine.commit_count == 0
    assert engine.rollback_count == 1


def test_finalize_initial_import_publishes_complete_lineage_atomically() -> None:
    connection = _AtomicFinalizeConnection()
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    job = service.finalize_initial_import(
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )

    assert job.status == JobStatus.SUCCESS
    assert job.finalize_protocol == "ATOMIC_V1"
    assert engine.begin_count == 1
    assert engine.commit_count == 1
    assert engine.rollback_count == 0
    statements = [sql for sql, _ in connection.statements]
    assert any(
        "FROM ingestion.processing_job WITH (UPDLOCK,HOLDLOCK)" in sql
        for sql in statements
    )
    assert any("sys.sp_getapplock" in sql for sql in statements)
    assert any(
        "FROM ingestion.initial_import_finalize_intent i WITH (UPDLOCK,HOLDLOCK)" in sql
        and "JOIN dataset.dataset_version dv WITH (UPDLOCK,HOLDLOCK)" in sql
        and "JOIN ingestion.processing_run pr WITH (UPDLOCK,HOLDLOCK)" in sql
        for sql in statements
    )
    assert any(
        "FROM ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK)" in sql
        and "JOIN dataset.dataset d ON d.dataset_id=:dataset" in sql
        for sql in statements
    )
    lineage_sql, lineage_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if "AS batch_file_count" in sql
    )
    assert "processing_run_input_file" in lineage_sql
    assert "dataset.dataset_version_run" in lineage_sql
    assert lineage_parameters == {"batch": 7, "run": 101, "version": 205}
    assert any("SET status='SUPERSEDED',is_current=0" in sql for sql in statements)
    assert any(
        "SET status='PUBLISHED',is_current=1" in sql and "status='DRAFT'" in sql
        for sql in statements
    )
    assert any(
        "processing_run SET status='PUBLISHED'" in sql
        and "is_current=1" in sql
        and "status='READY'" in sql
        for sql in statements
    )
    assert any(
        "UPDATE pr SET pr.status='SUPERSEDED'" in sql and "pr.is_current=0" in sql
        for sql in statements
    )
    previous_run_sql, previous_run_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if sql.startswith("SELECT pr.processing_run_id,pr.status,pr.is_current")
    )
    assert "dataset.dataset_version_run" in previous_run_sql
    assert "dvr.dataset_version_id=:previous" in previous_run_sql
    assert "source_file_id" not in previous_run_sql
    assert previous_run_parameters == {"previous": 204}
    assert not any(
        "target.source_file_id=prior.source_file_id" in sql
        or "WHERE source_file_id=:source" in sql
        for sql in statements
    )
    result_parameters = next(
        parameters
        for sql, parameters in connection.statements
        if sql.startswith("UPDATE ingestion.processing_result_summary SET data_name")
    )
    assert result_parameters["dataset_id"] == 41
    assert result_parameters["dataset_version_no"] == 5
    assert any("import_batch SET status='PROCESSED'" in sql for sql in statements)
    assert any("processing_job SET status='SUCCESS'" in sql for sql in statements)
    assert any(
        "initial_import_finalize_intent SET status='FINALIZED'" in sql
        for sql in statements
    )


def test_finalize_same_source_in_another_dataset_does_not_supersede_it() -> None:
    connection = _AtomicFinalizeConnection(
        previous_version_id=None,
        # A Source-global lookup would have found this other Dataset's Run.
        previous_run_id=100,
    )
    engine = _TransactionalFakeEngine(connection)

    job = SqlJobService(engine).finalize_initial_import(  # type: ignore[arg-type]
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )

    assert job.status == JobStatus.SUCCESS
    statements = [sql for sql, _ in connection.statements]
    assert not any(
        sql.startswith("SELECT pr.processing_run_id,pr.status,pr.is_current")
        for sql in statements
    )
    assert not any("UPDATE pr SET pr.status='SUPERSEDED'" in sql for sql in statements)
    run_publish_parameters = next(
        parameters
        for sql, parameters in connection.statements
        if "processing_run SET status='PUBLISHED'" in sql
    )
    assert run_publish_parameters["previous_run"] is None


def test_finalize_same_dataset_reprocess_supersedes_only_previous_version_runs() -> (
    None
):
    connection = _AtomicFinalizeConnection(
        previous_version_id=204,
        previous_run_id=100,
    )
    engine = _TransactionalFakeEngine(connection)

    SqlJobService(engine).finalize_initial_import(  # type: ignore[arg-type]
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )

    demotion_sql, parameters = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'")
    )
    assert "dvr.dataset_version_id=:previous" in demotion_sql
    assert "source_file_id" not in demotion_sql
    assert parameters == {"previous": 204}
    run_publish_parameters = next(
        parameters
        for sql, parameters in connection.statements
        if "processing_run SET status='PUBLISHED'" in sql
    )
    assert run_publish_parameters["previous_run"] == 100


def test_finalize_keeps_a_previous_run_current_when_another_dataset_still_uses_it() -> (
    None
):
    connection = _AtomicFinalizeConnection(
        previous_version_id=204,
        previous_run_id=100,
        previous_run_has_other_current=True,
    )
    engine = _TransactionalFakeEngine(connection)

    job = SqlJobService(engine).finalize_initial_import(  # type: ignore[arg-type]
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )

    assert job.status == JobStatus.SUCCESS
    demotion_sql = next(
        sql
        for sql, _ in connection.statements
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'")
    )
    assert "NOT EXISTS" in demotion_sql
    assert "other_dv.status='PUBLISHED'" in demotion_sql


def test_reprocess_finalize_locks_and_validates_lifecycle_target() -> None:
    lifecycle_intent = {
        **_staged_intent_row(),
        "lifecycle_action_type": "REPROCESS_UPDATE",
        "lifecycle_dataset_id": 41,
        "lifecycle_target_version_id": 204,
        "lifecycle_dataset_status": "ACTIVE",
        "lifecycle_target_version_status": "PUBLISHED",
        "lifecycle_target_is_current": True,
        "lifecycle_target_batch_id": 7,
    }
    connection = _AtomicFinalizeConnection(intent_row=lifecycle_intent)
    engine = _TransactionalFakeEngine(connection)

    result = SqlJobService(engine).finalize_initial_import(  # type: ignore[arg-type]
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )

    assert result.status == JobStatus.SUCCESS
    intent_sql = next(
        sql for sql, _ in connection.statements if sql.startswith("SELECT i.job_id")
    )
    assert "lifecycle_job_target lt WITH (UPDLOCK,HOLDLOCK)" in intent_sql
    assert "dataset.dataset ld WITH (UPDLOCK,HOLDLOCK)" in intent_sql
    assert "dataset.dataset_version ldv WITH (UPDLOCK,HOLDLOCK)" in intent_sql


@pytest.mark.parametrize(
    "overrides",
    [
        {"lifecycle_dataset_status": "ARCHIVED"},
        {"lifecycle_target_is_current": False},
        {"lifecycle_dataset_id": 99},
        {"lifecycle_target_batch_id": 99},
    ],
)
def test_reprocess_finalize_rejects_archived_replaced_or_wrong_canonical_target(
    overrides: dict[str, Any],
) -> None:
    lifecycle_intent = {
        **_staged_intent_row(),
        "lifecycle_action_type": "REPROCESS_UPDATE",
        "lifecycle_dataset_id": 41,
        "lifecycle_target_version_id": 204,
        "lifecycle_dataset_status": "ACTIVE",
        "lifecycle_target_version_status": "PUBLISHED",
        "lifecycle_target_is_current": True,
        "lifecycle_target_batch_id": 7,
        **overrides,
    }
    connection = _AtomicFinalizeConnection(intent_row=lifecycle_intent)
    engine = _TransactionalFakeEngine(connection)

    with pytest.raises(DomainError) as exc_info:
        SqlJobService(engine).finalize_initial_import(  # type: ignore[arg-type]
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=101,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )

    assert exc_info.value.code == "LIFECYCLE_TARGET_DRIFTED"
    assert engine.rollback_count == 1
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.statements)


def test_finalize_initial_import_wrong_lease_fails_closed() -> None:
    connection = _AtomicFinalizeConnection(
        job_row=_atomic_job_row(lease_token="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    )
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.finalize_initial_import(
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=101,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )

    assert exc_info.value.code == "JOB_LEASE_LOST"
    assert engine.rollback_count == 1
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.statements)


def test_finalize_idempotency_requires_same_lease_run_and_version() -> None:
    finalized_intent = {
        **_staged_intent_row(),
        "status": "FINALIZED",
        "finalized_lease_token": _LEASE_TOKEN,
        "version_status": "PUBLISHED",
        "is_current": True,
        "run_status": "PUBLISHED",
    }
    connection = _AtomicFinalizeConnection(
        job_row=_atomic_job_row(status="SUCCESS", lease_token=None),
        intent_row=finalized_intent,
    )
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    repeated = service.finalize_initial_import(
        job_id=91,
        lease_token=_LEASE_TOKEN,
        processing_run_id=101,
        dataset_version_id=205,
        summary=_atomic_summary(),
    )
    assert repeated.status == JobStatus.SUCCESS

    with pytest.raises(DomainError) as exc_info:
        service.finalize_initial_import(
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=999,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )
    assert exc_info.value.code == "JOB_LEASE_LOST"


def test_finalize_initial_import_incomplete_lineage_fails_closed() -> None:
    connection = _AtomicFinalizeConnection(
        links={
            "batch_file_count": 3,
            "lineage_count": 2,
            "wrong_batch_count": 0,
            "unverified_lineage_count": 0,
            "version_run_count": 1,
        }
    )
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.finalize_initial_import(
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=101,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )

    assert exc_info.value.code == "ATOMIC_LINEAGE_INCOMPLETE"
    assert engine.rollback_count == 1
    assert not any(sql.startswith("UPDATE ") for sql, _ in connection.statements)


def test_finalize_initial_import_rejects_unverified_legacy_lineage() -> None:
    connection = _AtomicFinalizeConnection(
        links={
            "batch_file_count": 3,
            "lineage_count": 3,
            "wrong_batch_count": 0,
            "unverified_lineage_count": 1,
            "version_run_count": 1,
        }
    )
    engine = _TransactionalFakeEngine(connection)
    service = SqlJobService(engine)  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.finalize_initial_import(
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=101,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )

    assert exc_info.value.code == "ATOMIC_LINEAGE_INCOMPLETE"
    assert engine.rollback_count == 1


class _InjectedFinalizeFault(RuntimeError):
    pass


@pytest.mark.parametrize(
    "fault_point",
    [
        "after_previous_current_superseded",
        "after_new_version_published",
        "after_result_persisted",
        "after_batch_completed",
        "after_job_completed",
        "after_intent_finalized",
    ],
)
def test_finalize_faults_propagate_and_rollback_transaction(fault_point: str) -> None:
    connection = _AtomicFinalizeConnection()
    engine = _TransactionalFakeEngine(connection)

    def inject(point: str) -> None:
        if point == fault_point:
            raise _InjectedFinalizeFault(point)

    service = SqlJobService(engine, fault_injector=inject)  # type: ignore[arg-type]

    with pytest.raises(_InjectedFinalizeFault, match=fault_point):
        service.finalize_initial_import(
            job_id=91,
            lease_token=_LEASE_TOKEN,
            processing_run_id=101,
            dataset_version_id=205,
            summary=_atomic_summary(),
        )

    assert engine.begin_count == 1
    assert engine.commit_count == 0
    assert engine.rollback_count == 1
