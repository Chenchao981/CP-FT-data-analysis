from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.stage_data import StoredUpload
from app.infrastructure.sql_stage_data_service import SqlStageDataService


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return 1 if self.rowcount else None


class _Connection:
    def __init__(self, rowcounts: tuple[int, ...]) -> None:
        self._rowcounts = iter(rowcounts)
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return _Result(next(self._rowcounts))


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


class _ScalarResult:
    def __init__(self, scalar=None) -> None:
        self.scalar = scalar

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.scalar


class _RegisterConnection:
    def __init__(self, results=(101, None, 201, 301, None)) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.results = iter(results)

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return _ScalarResult(next(self.results))


def test_worker_only_moves_a_queued_batch_to_processing() -> None:
    connection = _Connection((0, 0))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service.worker_mark_processing(7, 41, "11111111-1111-1111-1111-111111111111")

    assert exc_info.value.code == "BATCH_STATE_CONFLICT"
    assert "b.status='QUEUED'" in connection.statements[0]
    assert "finalize_protocol='ATOMIC_V1'" in connection.statements[0]
    assert (
        "lease_token=CONVERT(uniqueidentifier,:lease_token)" in connection.statements[0]
    )


def test_worker_processing_transition_is_idempotent_for_the_same_active_lease() -> None:
    connection = _Connection((0, 1))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    service.worker_mark_processing(7, 41, "11111111-1111-1111-1111-111111111111")

    assert "b.status='PROCESSING'" in connection.statements[1]


def test_legacy_mark_queued_cannot_regress_terminal_batches() -> None:
    connection = _Connection((1,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    service.mark_queued(7)

    assert "status='RECEIVED'" in connection.statements[0]
    assert "PROCESSED" not in connection.statements[0]
    assert "FAILED" not in connection.statements[0]


def test_worker_failure_only_changes_active_batch_states() -> None:
    connection = _Connection((1,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]

    service.mark_failed(7, 41, "validation failed", finish_job=False)

    assert "status IN('QUEUED','PROCESSING')" in connection.statements[0]


def test_registration_does_not_claim_a_false_detected_factory_format(
    tmp_path: Path,
) -> None:
    connection = _RegisterConnection()
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"sample")
    principal = Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        department_code=None,
        roles=("OPERATOR",),
        permissions=frozenset({"TASK_CREATE"}),
    )

    batch_id = service.register_upload(
        principal,
        "PRODUCTION",
        "FT",
        "riyuexin",
        (StoredUpload("sample.xlsx", source, 6, "a" * 64),),
        None,
    )

    assert batch_id == 101
    import_file_sql = next(
        statement
        for statement, _parameters in connection.calls
        if "INSERT ingestion.import_batch_file" in statement
    )
    source_lookup_sql = next(
        statement
        for statement, _parameters in connection.calls
        if "SELECT source_file_id FROM ingestion.source_file" in statement
    )
    assert "WITH (UPDLOCK,HOLDLOCK)" in source_lookup_sql
    assert "NULL,NULL" in import_file_sql
    assert "HUAHONG_DCP" not in import_file_sql


def test_catalog_registration_rechecks_grant_and_writes_domain_ownership(
    tmp_path: Path,
) -> None:
    connection = _RegisterConnection(
        (
            {
                "data_domain_id": 17,
                "domain_code": "RIYUEXIN_FT",
                "service_user_id": 900,
            },
            101,
            None,
            201,
            301,
            None,
        )
    )
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"sample")
    principal = Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        department_code=None,
        roles=("OPERATOR",),
        permissions=frozenset({"TASK_CREATE"}),
    )
    metadata = {
        "purpose": "FORMAL_IMPORT",
        "source_root_code": "RIYUEXIN_PRODUCTION",
        "data_domain_code": "RIYUEXIN_FT",
        "source_relative_path": "lot-a",
        "source_manifest_mode": "PATH_SIZE_MTIME_V1",
        "source_manifest_sha256": "b" * 64,
        "source_file_count": 1,
        "source_total_bytes": 6,
    }

    batch_id = service.register_upload(
        principal,
        "PRODUCTION",
        "FT",
        "riyuexin",
        (StoredUpload("sample.xlsx", source, 6, "a" * 64, metadata),),
        None,
        data_domain_id=17,
    )

    assert batch_id == 101
    binding_sql, binding_params = connection.calls[0]
    assert "iam.data_domain_grant" in binding_sql
    assert "g.expires_at_utc>SYSUTCDATETIME()" in binding_sql
    assert "WITH (UPDLOCK,HOLDLOCK)" in binding_sql
    assert "SYSTEM_INGESTION" in binding_sql
    assert binding_params == {
        "data_domain_id": 17,
        "actor_user_id": 7,
        "stage": "FT",
        "factory": "RIYUEXIN",
    }
    batch_sql, batch_params = connection.calls[1]
    assert ":access_scope,:data_domain_id,NULL" in batch_sql
    assert batch_params is not None
    assert batch_params["access_scope"] == "DOMAIN"
    assert batch_params["data_domain_id"] == 17
    assert batch_params["owner"] == 900


def test_catalog_registration_fails_closed_when_grant_was_revoked(
    tmp_path: Path,
) -> None:
    connection = _RegisterConnection((None,))
    service = SqlStageDataService(_Engine(connection))  # type: ignore[arg-type]
    source = tmp_path / "sample.xlsx"
    source.write_bytes(b"sample")
    principal = Principal(
        user_id=7,
        login_name="operator",
        display_name="操作员",
        department_code=None,
        roles=("OPERATOR",),
        permissions=frozenset({"TASK_CREATE"}),
    )
    metadata = {
        "purpose": "FORMAL_IMPORT",
        "source_root_code": "RIYUEXIN_PRODUCTION",
        "data_domain_code": "RIYUEXIN_FT",
    }

    with pytest.raises(DomainError) as exc_info:
        service.register_upload(
            principal,
            "PRODUCTION",
            "FT",
            "riyuexin",
            (StoredUpload("sample.xlsx", source, 6, "a" * 64, metadata),),
            None,
            data_domain_id=17,
        )

    assert exc_info.value.code == "SOURCE_ROOT_NOT_FOUND"
    assert len(connection.calls) == 1
