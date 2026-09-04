from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.quick_analysis import (
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
    QuickAnalysisStatus,
)
from app.infrastructure.sql_quick_analysis_service import (
    SESSION_SELECT,
    SqlQuickAnalysisService,
    _to_session,
)
from sqlalchemy.exc import DBAPIError


class _EmptyResult:
    def mappings(self):
        return self

    def all(self) -> list:
        return []

    def one_or_none(self):
        return None


class _CapturingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        return _EmptyResult()


class _CapturingEngine:
    def __init__(self) -> None:
        self.connection = _CapturingConnection()

    def connect(self) -> _CapturingConnection:
        return self.connection


class _MappedResult:
    def __init__(self, value) -> None:
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _ExecutionConnection:
    def __init__(
        self,
        session: dict[str, object],
        active_grant: int | None,
        *,
        owner_status: str | None = "ACTIVE",
    ) -> None:
        self.session = session
        self.active_grant = active_grant
        self.owner_status = owner_status
        self.calls: list[str] = []

    def execute(self, statement, _parameters):
        sql = str(statement)
        self.calls.append(sql)
        if "FROM workspace.analysis_session" in sql:
            return _MappedResult(self.session)
        if "FROM iam.app_user" in sql:
            return _MappedResult(self.owner_status)
        return _MappedResult(self.active_grant)


class _CreateConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if sql.startswith("INSERT workspace.analysis_session"):
            return _MappedResult(41)
        return _MappedResult(self.row)


class _CreateEngine:
    def __init__(self, row: dict[str, object]) -> None:
        self.connection = _CreateConnection(row)

    def begin(self):
        return self.connection


def test_database_quick_session_row_maps_summary_and_effective_status() -> None:
    row = {
        "analysis_session_id": 7,
        "owner_user_id": 2,
        "owner_login": "analyst",
        "owner_name": "分析员",
        "access_scope": "DOMAIN",
        "data_domain_id": 7,
        "data_domain_code": "JIEQUN_FT",
        "analysis_type": "QUICK_PAT",
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "source_root_code": "JIEQUN_SHARED",
        "source_relative_path": "520data",
        "source_manifest_mode": "PATH_SIZE_MTIME_V1",
        "source_manifest_sha256": "a" * 64,
        "source_file_count": 520,
        "source_total_bytes": 3_041_085_645,
        "retention_mode": "RESULT_ONLY",
        "cleaner_release_id": 21,
        "reserved_bytes": 1_600_000_000,
        "cleanup_status": "RETAINED",
        "effective_status": "SUCCESS",
        "job_id": 40,
        "job_status": "SUCCESS",
        "parameter_count": 23,
        "record_count": 6_813_800,
        "summary_json": '{"elapsed_seconds":153.906}',
        "result_file_name": "PAT_001.xlsx",
        "result_size_bytes": 7759,
        "effective_error_code": None,
        "effective_error_message": None,
        "expires_at_utc": datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        "created_at_utc": datetime(2026, 8, 26, 1, 0, tzinfo=UTC),
        "started_at_utc": datetime(2026, 8, 26, 1, 1, tzinfo=UTC),
        "finished_at_utc": datetime(2026, 8, 26, 1, 4, tzinfo=UTC),
    }
    session = _to_session(row)
    assert session.status == QuickAnalysisStatus.SUCCESS
    assert session.summary == {"elapsed_seconds": 153.906}
    assert session.record_count == 6_813_800
    assert session.reserved_bytes == 1_600_000_000
    assert session.cleanup_status == "RETAINED"
    assert session.created_at_utc.tzinfo == UTC


def test_domain_session_create_locks_grant_and_reads_back_before_commit() -> None:
    row = {
        "analysis_session_id": 41,
        "owner_user_id": 8,
        "owner_login": "owner",
        "owner_name": "Owner",
        "access_scope": "DOMAIN",
        "data_domain_id": 7,
        "data_domain_code": "JIEQUN_FT",
        "analysis_type": "QUICK_PAT",
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "source_root_code": "JIEQUN_SHARED",
        "source_relative_path": "520data",
        "source_manifest_mode": "PATH_SIZE_MTIME_V1",
        "source_manifest_sha256": "a" * 64,
        "source_file_count": 520,
        "source_total_bytes": 3_041_085_645,
        "retention_mode": "RESULT_ONLY",
        "cleaner_release_id": 21,
        "reserved_bytes": 1_600_000_000,
        "cleanup_status": "RETAINED",
        "effective_status": "QUEUED",
        "job_id": None,
        "job_status": None,
        "parameter_count": None,
        "record_count": None,
        "summary_json": None,
        "result_file_name": None,
        "result_size_bytes": None,
        "effective_error_code": None,
        "effective_error_message": None,
        "expires_at_utc": datetime(2026, 9, 8, tzinfo=UTC),
        "created_at_utc": datetime(2026, 9, 1, tzinfo=UTC),
        "started_at_utc": None,
        "finished_at_utc": None,
    }
    engine = _CreateEngine(row)
    service = SqlQuickAnalysisService(engine)  # type: ignore[arg-type]
    principal = Principal(8, "owner", "Owner", (), frozenset({"ANALYSIS_RUN"}))

    created = service.create(
        principal,
        NewQuickAnalysisSession(
            analysis_type="QUICK_PAT",
            test_stage="FT",
            factory_code="JIEQUN",
            source_root_code="JIEQUN_SHARED",
            source_relative_path="520data",
            source_manifest_mode="PATH_SIZE_MTIME_V1",
            source_manifest_json="{}",
            source_manifest_sha256="a" * 64,
            source_file_count=520,
            source_total_bytes=3_041_085_645,
            retention_mode="RESULT_ONLY",
            cleaner_release_id=21,
            expires_at_utc=datetime(2026, 9, 8, tzinfo=UTC),
            access_scope="DOMAIN",
            data_domain_id=7,
            data_domain_code="JIEQUN_FT",
            reserved_bytes=1_600_000_000,
        ),
    )

    assert created.analysis_session_id == 41
    assert len(engine.connection.calls) == 2
    insert_sql = engine.connection.calls[0][0]
    assert "iam.data_domain d WITH (UPDLOCK,HOLDLOCK)" in insert_sql
    assert "iam.data_domain_grant g WITH (UPDLOCK,HOLDLOCK)" in insert_sql
    assert "g.expires_at_utc>SYSUTCDATETIME()" in insert_sql
    assert "WHERE s.analysis_session_id=:session" in engine.connection.calls[1][0]


def test_database_quick_session_normalizes_legacy_local_receipt_summary() -> None:
    row = {
        "analysis_session_id": 7,
        "owner_user_id": 2,
        "owner_login": "analyst",
        "owner_name": "分析员",
        "access_scope": "PERSONAL",
        "data_domain_id": None,
        "data_domain_code": None,
        "analysis_type": "QUICK_PAT",
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "source_root_code": "LOCAL_AGENT",
        "source_relative_path": "520data",
        "source_manifest_mode": "LOCAL_PATH_SIZE_MTIME_V1",
        "source_manifest_sha256": "a" * 64,
        "source_file_count": 520,
        "source_total_bytes": 3_041_085_645,
        "retention_mode": "RESULT_ONLY",
        "cleaner_release_id": 21,
        "reserved_bytes": 64 * 1024 * 1024,
        "cleanup_status": "RETAINED",
        "effective_status": "SUCCESS",
        "job_id": 40,
        "job_status": "SUCCESS",
        "parameter_count": 23,
        "record_count": 6_813_800,
        "summary_json": (
            '{"tool_code":"JIEQUN_FT_QUICK_PAT_EXISTING",'
            '"summary":{"parameter_count":23,"record_count":6813800,'
            '"elapsed_seconds":127.745}}'
        ),
        "result_file_name": "PAT_001.xlsx",
        "result_size_bytes": 7759,
        "effective_error_code": None,
        "effective_error_message": None,
        "expires_at_utc": datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        "created_at_utc": datetime(2026, 8, 26, 1, 0, tzinfo=UTC),
        "started_at_utc": datetime(2026, 8, 26, 1, 1, tzinfo=UTC),
        "finished_at_utc": datetime(2026, 8, 26, 1, 4, tzinfo=UTC),
    }

    session = _to_session(row)

    assert session.summary is not None
    assert session.summary["elapsed_seconds"] == 127.745
    assert session.summary["parameter_count"] == 23
    assert session.summary["record_count"] == 6_813_800


def test_sql_quick_personal_scope_never_grants_system_admin_owner_bypass() -> None:
    engine = _CapturingEngine()
    service = SqlQuickAnalysisService(engine)  # type: ignore[arg-type]
    admin = Principal(
        99,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"ANALYSIS_RUN"}),
    )

    assert service.list_for_principal(admin) == ()
    list_sql, list_parameters = engine.connection.calls[-1]
    assert "s.access_scope='PERSONAL'" in list_sql
    assert "access_grant.status='ACTIVE'" in list_sql
    assert "access_grant.expires_at_utc>SYSUTCDATETIME()" in list_sql
    assert list_parameters["user_id"] == 99
    assert list_parameters["has_data_break_glass"] is False

    with pytest.raises(DomainError) as metadata_error:
        service.get_for_principal(1, admin)
    assert metadata_error.value.code == "QUICK_ANALYSIS_NOT_FOUND"
    metadata_sql, metadata_parameters = engine.connection.calls[-1]
    assert "AND (:is_admin=1 OR (s.access_scope='PERSONAL'" in metadata_sql
    assert metadata_parameters["user_id"] == 99

    with pytest.raises(DomainError) as artifact_error:
        service.result_artifact(1, admin)
    assert artifact_error.value.code == "QUICK_RESULT_NOT_FOUND"
    artifact_sql, artifact_parameters = engine.connection.calls[-1]
    assert "AND (:is_admin=1 OR (s.access_scope='PERSONAL'" in artifact_sql
    assert artifact_parameters["user_id"] == 99


def test_success_artifacts_are_registered_as_persistent_result_history() -> None:
    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.responses = iter(
                (
                    _MappedResult(
                        {
                            "analysis_session_id": 41,
                            "owner_user_id": 8,
                            "access_scope": "PERSONAL",
                            "data_domain_id": None,
                            "status": "RUNNING",
                            "expires_at_utc": datetime(2026, 9, 2, tzinfo=UTC),
                        }
                    ),
                    _MappedResult("ACTIVE"),
                    _MappedResult(1),
                    _MappedResult(None),
                    _MappedResult(None),
                )
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, statement, parameters):
            self.calls.append((str(statement), parameters))
            return next(self.responses)

    class Engine:
        def __init__(self) -> None:
            self.connection = Connection()

        def begin(self):
            return self.connection

    engine = Engine()
    service = SqlQuickAnalysisService(engine)  # type: ignore[arg-type]
    service.record_success(
        41,
        91,
        parameter_count=1,
        record_count=10,
        summary={"elapsed_seconds": 1.2},
        artifacts=(
            # The file is not read here; registration uses already verified metadata.
            QuickAnalysisArtifact(
                "pat_report", r"F:\workspace\91\PAT.xlsx", 20, "a" * 64
            ),
        ),
    )

    artifact_sql, artifact_parameters = engine.connection.calls[3]
    assert ":sha,0,NULL)" in artifact_sql
    assert "expires" not in artifact_parameters
    assert "SUCCESS' AND s.expires_at_utc" not in SESSION_SELECT


def test_worker_execution_rejects_domain_session_when_owner_account_is_disabled() -> (
    None
):
    connection = _ExecutionConnection(
        {
            "analysis_session_id": 41,
            "owner_user_id": 8,
            "access_scope": "DOMAIN",
            "data_domain_id": 7,
            "status": "QUEUED",
            "expires_at_utc": datetime(2026, 9, 2, tzinfo=UTC),
        },
        active_grant=1,
        owner_status="DISABLED",
    )

    with pytest.raises(DomainError) as error:
        SqlQuickAnalysisService._locked_execution_session(connection, 41)

    assert error.value.code == "QUICK_ANALYSIS_REQUESTER_INACTIVE"
    authorization_sql = connection.calls[-1]
    assert "FROM iam.app_user WITH (UPDLOCK,HOLDLOCK)" in authorization_sql


def test_worker_execution_rejects_personal_session_when_owner_account_is_disabled() -> (
    None
):
    connection = _ExecutionConnection(
        {
            "analysis_session_id": 42,
            "owner_user_id": 8,
            "access_scope": "PERSONAL",
            "data_domain_id": None,
            "status": "QUEUED",
            "expires_at_utc": datetime(2026, 9, 2, tzinfo=UTC),
        },
        active_grant=None,
        owner_status="DISABLED",
    )

    with pytest.raises(DomainError) as error:
        SqlQuickAnalysisService._locked_execution_session(connection, 42)

    assert error.value.code == "QUICK_ANALYSIS_REQUESTER_INACTIVE"
    assert "FROM iam.app_user WITH (UPDLOCK,HOLDLOCK)" in connection.calls[-1]


def test_quick_session_page_retries_one_sql_server_deadlock(monkeypatch) -> None:
    class Result:
        def scalar_one(self):
            return 0

        def mappings(self):
            return self

        def all(self):
            return []

    class Connection:
        def __init__(self, engine):
            self.engine = engine

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            self.engine.execute_calls += 1
            if self.engine.execute_calls == 1:
                raise DBAPIError(
                    "SELECT",
                    {},
                    RuntimeError("SQL Server deadlock victim (1205)"),
                    False,
                )
            return Result()

    class Engine:
        def __init__(self):
            self.execute_calls = 0
            self.connect_calls = 0

        def connect(self):
            self.connect_calls += 1
            return Connection(self)

    engine = Engine()
    monkeypatch.setattr(
        "app.infrastructure.sql_quick_analysis_service.time.sleep",
        lambda _delay: None,
    )
    service = SqlQuickAnalysisService(engine)  # type: ignore[arg-type]
    principal = Principal(8, "owner", "Owner", (), frozenset())

    page = service.list_page_for_principal(principal, page=1, page_size=20)

    assert page.total == 0
    assert page.items == ()
    assert engine.connect_calls == 2
