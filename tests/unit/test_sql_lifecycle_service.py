from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.lifecycle import TemporaryArtifactInput
from app.infrastructure.formal_artifact_files import ManagedJobPathPolicy
from app.infrastructure.sql_lifecycle_service import SqlLifecycleService


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
        scalars: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self.rows = rows or []
        self.scalar = scalar
        self.scalar_rows = scalars or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def scalars(self):
        return self

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def all(self):
        return self.scalar_rows if self.scalar_rows else self.rows

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return next(self.results)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def begin(self):
        try:
            yield self.connection
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    @contextmanager
    def connect(self):
        yield self.connection


def _principal(*, user_id: int = 7, admin: bool = False) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"owner-{user_id}",
        display_name="Owner",
        roles=("SYSTEM_ADMIN",) if admin else ("DATA_OWNER",),
        permissions=frozenset({"EXPORT_DATA", "TASK_CREATE"}),
    )


def _target(*, owner: int = 7) -> dict[str, Any]:
    return {
        "dataset_id": 5,
        "owner_user_id": owner,
        "access_scope": "PERSONAL",
        "data_domain_id": None,
        "can_read": owner == 7,
        "test_stage": "FT",
        "lifecycle_status": "ACTIVE",
        "dataset_version_id": 6,
        "input_batch_id": 17,
        "batch_test_stage": "FT",
        "factory_code": "日月新",
        "batch_status": "PROCESSED",
        "input_file_count": 1,
    }


def _service(
    tmp_path: Path,
    results: list[_Result],
    *,
    fault_injector=None,
) -> tuple[SqlLifecycleService, _Engine, _Connection]:
    connection = _Connection(results)
    engine = _Engine(connection)
    service = SqlLifecycleService(
        engine,  # type: ignore[arg-type]
        ManagedJobPathPolicy((tmp_path / "work").absolute()),
        fault_injector=fault_injector,
    )
    return service, engine, connection


def test_create_export_locks_current_selects_latest_release_and_never_mutates_facts(
    tmp_path: Path,
) -> None:
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(),
            _Result(rows=[_target()]),
            _Result(scalars=[41]),
            _Result(scalar=9),
            _Result(rows=[{"job_id": 81, "status": "QUEUED"}]),
            _Result(),
            _Result(),
        ],
    )

    receipt = service.create_export(5, "export-request-001", _principal())

    assert receipt.job_id == 81
    assert receipt.action_type == "EXPORT_LATEST"
    assert receipt.cleaner_release_id == 9
    assert receipt.created is True
    assert engine.commits == 1
    sql = "\n".join(call[0] for call in connection.calls)
    assert "WITH (UPDLOCK,HOLDLOCK)" in sql
    assert "candidate.status='RELEASED'" in sql
    assert "candidate.format_profile_id=original_release.format_profile_id" in sql
    assert "candidate.input_contract_version=" in sql
    assert "original_release.input_contract_version" in sql
    insert_call = next(
        call for call in connection.calls if "INSERT ingestion.processing_job" in call[0]
    )
    assert insert_call[1] is not None
    assert insert_call[1]["job_type"] == "EXPORT_LATEST"
    assert insert_call[1]["batch"] == 17
    assert insert_call[1]["release"] == 9
    lowered = sql.lower()
    assert "update test." not in lowered
    assert "delete from test." not in lowered
    assert "update ingestion.import_batch" not in lowered
    assert "update dataset.dataset_version" not in lowered
    assert "update ingestion.source_file" not in lowered


def test_duplicate_export_returns_existing_job_without_new_insert(
    tmp_path: Path,
) -> None:
    existing = {
        "job_id": 81,
        "job_type": "EXPORT_LATEST",
        "status": "SUCCESS",
        "import_batch_id": 17,
        "cleaner_release_id": 9,
        "parent_job_id": 41,
        "idempotency_key": "placeholder",
        "dataset_id": 5,
        "target_dataset_version_id": 6,
        "action_type": "EXPORT_LATEST",
    }
    # The service-generated key is deterministic; capture it from a first dry service.
    first, _, first_connection = _service(
        tmp_path,
        [
            _Result(),
            _Result(rows=[_target()]),
            _Result(scalars=[41]),
            _Result(scalar=9),
            _Result(rows=[{"job_id": 81, "status": "QUEUED"}]),
            _Result(),
            _Result(),
        ],
    )
    created = first.create_export(5, "export-request-001", _principal())
    existing["idempotency_key"] = created.idempotency_key
    assert first_connection.calls
    service, _, connection = _service(
        tmp_path,
        [_Result(rows=[existing]), _Result(scalar=1)],
    )

    replay = service.create_export(5, "export-request-001", _principal())

    assert replay.job_id == 81
    assert replay.created is False
    assert all(
        "INSERT ingestion.processing_job" not in statement
        for statement, _parameters in connection.calls
    )


def test_dataset_owner_overreach_fails_before_job_creation(tmp_path: Path) -> None:
    service, engine, connection = _service(
        tmp_path, [_Result(), _Result(rows=[_target(owner=99)])]
    )

    with pytest.raises(DomainError) as exc_info:
        service.create_archive(
            5, "approved archive reason", "archive-request-001", _principal()
        )

    assert exc_info.value.code == "DATASET_SCOPE_DENIED"
    assert engine.rollbacks == 1
    assert all(
        "INSERT ingestion.processing_job" not in statement
        for statement, _parameters in connection.calls
    )


def test_system_admin_can_mutate_other_personal_dataset(tmp_path: Path) -> None:
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(),
            _Result(rows=[_target(owner=99)]),
            _Result(scalars=[41]),
            _Result(rows=[{"job_id": 82, "status": "QUEUED"}]),
            _Result(),
            _Result(),
        ],
    )

    receipt = service.create_archive(
        5,
        "approved archive reason",
        "archive-request-admin-support",
        _principal(admin=True),
    )

    assert receipt.job_id == 82
    assert receipt.action_type == "DELETE_TASK"
    assert engine.commits == 1
    target_query = connection.calls[1]
    assert target_query[1]["is_admin"] is True


def test_domain_grantee_can_export_without_mutating_dataset(tmp_path: Path) -> None:
    target = {
        **_target(owner=99),
        "access_scope": "DOMAIN",
        "data_domain_id": 12,
        "can_read": True,
    }
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(),
            _Result(rows=[target]),
            _Result(scalars=[41]),
            _Result(scalar=9),
            _Result(rows=[{"job_id": 81, "status": "QUEUED"}]),
            _Result(),
            _Result(),
        ],
    )

    receipt = service.create_export(5, "domain-export-request-001", _principal())

    assert receipt.job_id == 81
    assert engine.commits == 1
    sql = "\n".join(statement for statement, _ in connection.calls)
    assert "iam.data_domain_grant" in sql
    assert "business_domain='PRODUCTION'" not in sql


@pytest.mark.parametrize(
    ("action", "target_counts"),
    [
        (
            "archive",
            {
                "active_lifecycle_job_count": 1,
                "active_lifecycle_mutation_count": 1,
            },
        ),
        (
            "export",
            {
                "active_lifecycle_job_count": 1,
                "active_lifecycle_mutation_count": 1,
            },
        ),
    ],
)
def test_conflicting_lifecycle_actions_are_serialized_before_job_creation(
    tmp_path: Path,
    action: str,
    target_counts: dict[str, int],
) -> None:
    target = {**_target(), **target_counts}
    service, engine, connection = _service(
        tmp_path,
        [_Result(), _Result(rows=[target])],
    )

    with pytest.raises(DomainError) as exc_info:
        if action == "archive":
            service.create_archive(
                5,
                "approved archive reason",
                "archive-request-conflict",
                _principal(),
            )
        else:
            service.create_export(5, "export-request-conflict", _principal())

    assert exc_info.value.code == "LIFECYCLE_ACTION_IN_PROGRESS"
    assert engine.rollbacks == 1
    sql = "\n".join(statement for statement, _parameters in connection.calls)
    assert "active_lifecycle_job_count" in sql
    assert "active_lifecycle_mutation_count" in sql
    assert "INSERT ingestion.processing_job" not in sql


def test_reprocess_contract_requires_unique_parent_and_uses_independent_type(
    tmp_path: Path,
) -> None:
    service, _, connection = _service(
        tmp_path,
        [
            _Result(),
            _Result(rows=[_target()]),
            _Result(scalars=[41]),
            _Result(scalar=10),
            _Result(rowcount=1),
            _Result(rows=[{"job_id": 82, "status": "QUEUED"}]),
            _Result(),
            _Result(),
        ],
    )

    receipt = service.create_reprocess(
        5, "Cleaner release correction", "reprocess-request-001", _principal()
    )

    assert receipt.action_type == "REPROCESS_UPDATE"
    assert receipt.job_type == "INITIAL_IMPORT"
    assert receipt.parent_job_id == 41
    insert = next(
        call for call in connection.calls if "INSERT ingestion.processing_job" in call[0]
    )
    assert insert[1] is not None
    assert insert[1]["job_type"] == "INITIAL_IMPORT"
    assert insert[1]["parent"] == 41
    assert insert[1]["finalize_protocol"] == "ATOMIC_V1"
    target_insert = next(
        call
        for call in connection.calls
        if "INSERT ingestion.lifecycle_job_target" in call[0]
    )
    assert target_insert[1] is not None
    assert target_insert[1]["action"] == "REPROCESS_UPDATE"
    batch_cas = next(
        call
        for call in connection.calls
        if "UPDATE ingestion.import_batch SET status='QUEUED'" in call[0]
    )
    assert batch_cas[1] == {"batch": 17, "expected_status": "PROCESSED"}


def test_archive_fault_injection_rolls_back_version_update_and_never_deletes_facts(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    archive_row = {
        "job_id": 83,
        "job_type": "DELETE_TASK",
        "status": "RUNNING",
        "requested_by": "owner-7",
        "requested_by_user_id": 7,
        "lease_token": "11111111-1111-1111-1111-111111111111",
        "lease_expires_at_utc": now + timedelta(minutes=5),
        "action_type": "DELETE_TASK",
        "dataset_id": 5,
        "target_dataset_version_id": 6,
        "request_reason": "approved duplicate dataset",
        "lifecycle_status": "ACTIVE",
        "owner_user_id": 7,
        "archived_at_utc": None,
        "archived_by_user_id": None,
        "archive_reason": None,
        "version_status": "PUBLISHED",
        "is_current": True,
        "version_no": 1,
    }

    def inject(point: str) -> None:
        if point == "after_archive_version_update":
            raise RuntimeError("synthetic failure")

    service, engine, connection = _service(
        tmp_path,
        [
            _Result(rows=[archive_row]),
            _Result(
                rows=[
                    {
                        "processing_run_id": 61,
                        "status": "PUBLISHED",
                        "is_current": True,
                    }
                ]
            ),
            _Result(scalar=None),
            _Result(rowcount=1),
        ],
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        service.archive_dataset_leased(
            83, "11111111-1111-1111-1111-111111111111"
        )

    assert engine.rollbacks == 1
    sql = "\n".join(call[0].lower() for call in connection.calls)
    assert "update dataset.dataset_version" in sql
    assert "delete from" not in sql
    assert "update test." not in sql
    assert "update ingestion.import_batch" not in sql
    assert "update ingestion.source_file" not in sql


def test_archive_removes_version_and_runs_from_current_without_deleting_test_facts(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    archive_row = {
        "job_id": 83,
        "job_type": "DELETE_TASK",
        "status": "RUNNING",
        "requested_by": "owner-7",
        "requested_by_user_id": 7,
        "lease_token": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        "lease_expires_at_utc": now + timedelta(minutes=5),
        "action_type": "DELETE_TASK",
        "dataset_id": 5,
        "target_dataset_version_id": 6,
        "request_reason": "approved duplicate dataset",
        "lifecycle_status": "ACTIVE",
        "owner_user_id": 7,
        "archived_at_utc": None,
        "archived_by_user_id": None,
        "archive_reason": None,
        "version_status": "PUBLISHED",
        "is_current": True,
        "version_no": 1,
    }
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(rows=[archive_row]),
            _Result(
                rows=[
                    {
                        "processing_run_id": 61,
                        "status": "PUBLISHED",
                        "is_current": True,
                    }
                ]
            ),
            _Result(scalar=None),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(),
            _Result(rowcount=1),
            _Result(rowcount=1),
        ],
    )

    service.archive_dataset_leased(
        83, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    assert engine.commits == 1
    sql = "\n".join(call[0] for call in connection.calls)
    assert "UPDATE dataset.dataset_version SET status='ARCHIVED',is_current=0" in sql
    assert "UPDATE r SET status='SUPERSEDED',is_current=0" in sql
    assert "processing_result_summary SET status='ARCHIVED'" in sql
    assert "other_dataset.lifecycle_status='ACTIVE'" in sql
    current_views = (
        Path(__file__).resolve().parents[2]
        / "db"
        / "alembic"
        / "sql"
        / "0004_analytics_views_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    assert "WHERE dv.status='PUBLISHED' AND dv.is_current=1" in current_views
    lowered = sql.lower()
    assert "delete from test." not in lowered
    assert "update test." not in lowered
    assert "delete from ingestion.source_file" not in lowered
    assert "delete from ingestion.import_batch" not in lowered
    assert "sys.sp_getapplock" in sql


def test_archive_fails_closed_when_run_is_shared_by_another_active_current(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    archive_row = {
        "job_id": 83,
        "job_type": "DELETE_TASK",
        "status": "RUNNING",
        "requested_by": "owner-7",
        "requested_by_user_id": 7,
        "lease_token": "11111111-1111-1111-1111-111111111111",
        "lease_expires_at_utc": now + timedelta(minutes=5),
        "action_type": "DELETE_TASK",
        "dataset_id": 5,
        "target_dataset_version_id": 6,
        "request_reason": "approved duplicate dataset",
        "lifecycle_status": "ACTIVE",
        "owner_user_id": 7,
        "archived_at_utc": None,
        "archived_by_user_id": None,
        "archive_reason": None,
        "version_status": "PUBLISHED",
        "is_current": True,
    }
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(rows=[archive_row]),
            _Result(
                rows=[
                    {
                        "processing_run_id": 61,
                        "status": "PUBLISHED",
                        "is_current": True,
                    }
                ]
            ),
            _Result(scalar=61),
        ],
    )

    with pytest.raises(DomainError) as exc_info:
        service.archive_dataset_leased(
            83, "11111111-1111-1111-1111-111111111111"
        )

    assert exc_info.value.code == "ARCHIVE_RUN_SHARED_WITH_ACTIVE_CURRENT"
    assert engine.rollbacks == 1
    assert all(
        "UPDATE " not in statement.upper()
        for statement, _parameters in connection.calls
    )


def test_download_rejects_registered_path_outside_its_managed_job_root(
    tmp_path: Path,
) -> None:
    outside = (tmp_path / "source" / "raw.xlsx").absolute()
    outside.parent.mkdir()
    outside.write_bytes(b"raw-source-must-remain")
    row = {
        "processing_artifact_id": 3,
        "job_id": 81,
        "file_name": "raw.xlsx",
        "storage_uri": str(outside),
        "file_size": outside.stat().st_size,
        "sha256": "a" * 64,
        "temporary_flag": True,
        "expires_at_utc": datetime.now(UTC) + timedelta(hours=1),
        "physical_status": "PRESENT",
        "status": "SUCCESS",
        "job_type": "EXPORT_LATEST",
        "action_type": "EXPORT_LATEST",
    }
    service, _, connection = _service(
        tmp_path,
        [
            _Result(
                rows=[
                    {
                        "processing_artifact_id": 3,
                        "job_id": 81,
                        "status": "SUCCESS",
                        "job_type": "EXPORT_LATEST",
                        "action_type": "EXPORT_LATEST",
                    }
                ]
            ),
            _Result(rows=[row]),
        ],
    )

    with pytest.raises(DomainError) as exc_info:
        service.artifact_download(81, 3, _principal())

    assert exc_info.value.code == "EXPORT_ARTIFACT_PATH_INVALID"
    assert outside.read_bytes() == b"raw-source-must-remain"
    assert "storage_uri" not in connection.calls[0][0]
    assert "storage_uri" in connection.calls[1][0]
    assert "iam.data_domain_grant" in connection.calls[0][0]
    assert "iam.data_domain_grant" in connection.calls[1][0]


def test_download_hides_export_artifact_existence_after_access_is_revoked(
    tmp_path: Path,
) -> None:
    service, _, connection = _service(
        tmp_path,
        [_Result()],
    )

    with pytest.raises(DomainError) as hidden:
        service.artifact_download(81, 3, _principal())

    assert hidden.value.code == "EXPORT_ARTIFACT_NOT_FOUND"
    assert hidden.value.status_code == 404
    assert len(connection.calls) == 1
    assert "storage_uri" not in connection.calls[0][0]
    assert "iam.data_domain_grant" in connection.calls[0][0]


def test_export_status_returns_safe_artifact_discovery_without_paths_or_leases(
    tmp_path: Path,
) -> None:
    expires = datetime.now(UTC) + timedelta(hours=1)
    service, _, connection = _service(
        tmp_path,
        [
            _Result(
                rows=[
                    {
                        "job_id": 81,
                        "status": "SUCCESS",
                        "error_code": None,
                        "cleaner_release_id": 9,
                        "dataset_id": 5,
                        "target_dataset_version_id": 6,
                        "action_type": "EXPORT_LATEST",
                    }
                ]
            ),
            _Result(
                rows=[
                    {
                        "processing_artifact_id": 3,
                        "job_id": 81,
                        "artifact_role": "EXPORT",
                        "file_name": "latest.xlsx",
                        "file_size": 100,
                        "sha256": "a" * 64,
                        "expires_at_utc": expires,
                        "physical_status": "PRESENT",
                    }
                ]
            ),
        ],
    )

    result = service.export_status(81, _principal())

    assert result.availability == "READY"
    assert result.artifacts[0].processing_artifact_id == 3
    sql = "\n".join(statement for statement, _parameters in connection.calls)
    assert "storage_uri" not in sql
    assert "lease_token" not in sql
    assert "lease_owner" not in sql


def test_export_status_checks_owner_before_loading_artifacts(tmp_path: Path) -> None:
    service, _, connection = _service(
        tmp_path,
        [
            _Result()
        ],
    )

    with pytest.raises(DomainError) as exc_info:
        service.export_status(81, _principal())

    assert exc_info.value.code == "EXPORT_JOB_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert len(connection.calls) == 1


def test_export_status_exposes_safe_failure_code_without_infrastructure_details(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(
        tmp_path,
        [
            _Result(
                rows=[
                    {
                        "job_id": 81,
                        "status": "FAILED",
                        "error_code": "WORKER_EXECUTION_FAILED",
                        "cleaner_release_id": 9,
                        "dataset_id": 5,
                        "target_dataset_version_id": 6,
                        "action_type": "EXPORT_LATEST",
                    }
                ]
            ),
            _Result(rows=[]),
        ],
    )

    result = service.export_status(81, _principal())

    assert result.status == "FAILED"
    assert result.availability == "FAILED"
    assert result.error_code == "WORKER_EXECUTION_FAILED"
    assert result.artifacts == ()


def test_export_artifact_fault_injection_rolls_back_registration(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "work" / "81" / "attempt-1" / "latest.xlsx").absolute()
    output.parent.mkdir(parents=True)
    payload = b"latest-export"
    output.write_bytes(payload)

    def inject(point: str) -> None:
        if point == "after_export_artifact_insert":
            raise RuntimeError("synthetic artifact failure")

    service, engine, connection = _service(
        tmp_path,
        [_Result(scalar=1), _Result()],
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match="synthetic artifact failure"):
        service.record_export_artifacts(
            81,
            "11111111-1111-1111-1111-111111111111",
            (
                TemporaryArtifactInput(
                    role="EXPORT",
                    path=str(output),
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
            ),
            datetime.now(UTC) + timedelta(hours=1),
        )

    assert engine.rollbacks == 1
    assert any(
        "INSERT ingestion.processing_artifact" in statement
        for statement, _parameters in connection.calls
    )


def test_export_worker_context_rejects_inactive_requester_before_loading_inputs(
    tmp_path: Path,
) -> None:
    row = {
        "job_id": 81,
        "job_type": "EXPORT_LATEST",
        "import_batch_id": 17,
        "cleaner_release_id": 9,
        "action_type": "EXPORT_LATEST",
        "dataset_id": 5,
        "target_dataset_version_id": 6,
        "requested_by_user_id": 7,
        "request_reason": None,
        "test_stage": "FT",
        "lifecycle_status": "ACTIVE",
        "version_status": "PUBLISHED",
        "is_current": True,
        "can_execute": False,
        "batch_test_stage": "FT",
        "factory_code": "RIYUEXIN",
    }
    service, engine, connection = _service(tmp_path, [_Result(rows=[row])])

    with pytest.raises(DomainError) as denied:
        service.worker_context(
            81,
            "11111111-1111-1111-1111-111111111111",
            "EXPORT_LATEST",
        )

    assert denied.value.code == "LIFECYCLE_EXPORT_ACCESS_REVOKED"
    assert engine.rollbacks == 1
    assert len(connection.calls) == 1
    sql = connection.calls[0][0]
    assert "iam.app_user lifecycle_user WITH (UPDLOCK,HOLDLOCK)" in sql
    assert "lifecycle_user.status='ACTIVE'" in sql
    assert "iam.data_domain_grant" in sql
    assert "expires_at_utc>SYSUTCDATETIME()" in sql
    assert "lifecycle_domain.active=1" in sql


def test_requester_disabled_after_render_keeps_lifecycle_artifact_and_success_absent(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "work" / "81" / "attempt-1" / "latest.xlsx").absolute()
    output.parent.mkdir(parents=True)
    payload = b"latest-export"
    output.write_bytes(payload)
    service, engine, connection = _service(tmp_path, [_Result(scalar=0)])

    with pytest.raises(DomainError) as denied:
        service.record_export_artifacts(
            81,
            "11111111-1111-1111-1111-111111111111",
            (
                TemporaryArtifactInput(
                    role="EXPORT",
                    path=str(output),
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
            ),
            datetime.now(UTC) + timedelta(hours=1),
        )

    assert denied.value.code == "LIFECYCLE_EXPORT_ACCESS_REVOKED"
    assert engine.rollbacks == 1
    sql = "\n".join(statement for statement, _ in connection.calls)
    assert "iam.app_user lifecycle_user WITH (UPDLOCK,HOLDLOCK)" in sql
    assert "lifecycle_user.status='ACTIVE'" in sql
    assert "INSERT ingestion.processing_artifact" not in sql
    assert "status='SUCCESS'" not in sql


def test_export_artifacts_and_job_success_commit_atomically(tmp_path: Path) -> None:
    output = (tmp_path / "work" / "81" / "attempt-1" / "latest.xlsx").absolute()
    output.parent.mkdir(parents=True)
    payload = b"latest-export"
    output.write_bytes(payload)
    artifact_row = {
        "processing_artifact_id": 3,
        "job_id": 81,
        "artifact_role": "EXPORT",
        "file_name": "latest.xlsx",
        "file_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "expires_at_utc": datetime.now(UTC) + timedelta(hours=1),
        "physical_status": "PRESENT",
    }
    service, engine, connection = _service(
        tmp_path,
        [
            _Result(scalar=1),
            _Result(),
            _Result(rows=[artifact_row]),
            _Result(rowcount=1),
        ],
    )

    artifacts = service.record_export_artifacts(
        81,
        "11111111-1111-1111-1111-111111111111",
        (
            TemporaryArtifactInput(
                role="EXPORT",
                path=str(output),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
        ),
        datetime.now(UTC) + timedelta(hours=1),
    )

    assert artifacts[0].processing_artifact_id == 3
    assert engine.commits == 1
    sql = "\n".join(statement for statement, _parameters in connection.calls)
    assert "INSERT ingestion.processing_artifact" in sql
    assert "UPDATE ingestion.processing_job SET status='SUCCESS'" in sql
    assert "lease_token=CONVERT(uniqueidentifier,:lease)" in sql
    assert sql.count("iam.app_user lifecycle_user WITH (UPDLOCK,HOLDLOCK)") == 2
    assert sql.count("lifecycle_user.status='ACTIVE'") == 2


def test_export_terminal_fault_rolls_back_artifact_and_job_together(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "work" / "81" / "attempt-1" / "latest.xlsx").absolute()
    output.parent.mkdir(parents=True)
    payload = b"latest-export"
    output.write_bytes(payload)
    artifact_row = {
        "processing_artifact_id": 3,
        "job_id": 81,
        "artifact_role": "EXPORT",
        "file_name": "latest.xlsx",
        "file_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "expires_at_utc": datetime.now(UTC) + timedelta(hours=1),
        "physical_status": "PRESENT",
    }

    def inject(point: str) -> None:
        if point == "after_export_job_success":
            raise RuntimeError("synthetic terminal failure")

    service, engine, connection = _service(
        tmp_path,
        [
            _Result(scalar=1),
            _Result(),
            _Result(rows=[artifact_row]),
            _Result(rowcount=1),
        ],
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match="synthetic terminal failure"):
        service.record_export_artifacts(
            81,
            "11111111-1111-1111-1111-111111111111",
            (
                TemporaryArtifactInput(
                    role="EXPORT",
                    path=str(output),
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                ),
            ),
            datetime.now(UTC) + timedelta(hours=1),
        )

    assert engine.rollbacks == 1
    sql = "\n".join(statement for statement, _parameters in connection.calls)
    assert "INSERT ingestion.processing_artifact" in sql
    assert "UPDATE ingestion.processing_job SET status='SUCCESS'" in sql
