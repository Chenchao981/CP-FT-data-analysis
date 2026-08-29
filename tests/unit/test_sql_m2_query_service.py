from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import ALL_PERMISSIONS, Principal
from app.domain.m2_queries import M2PageFilters
from app.infrastructure.sql_m2_query_service import (
    _CURRENT_DATA_READ_GRANT,
    _CURRENT_DATASET_COLUMNS,
    _CURRENT_DATASET_FROM,
    _JOB_DETAILS_SQL,
    _JOB_LINKS_SQL,
    _JOB_PUBLISH_CHAIN_SQL,
    SqlM2QueryService,
)


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one_or_none(self):
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        assert self._scalar is not None
        return self._scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        if not self._results:
            raise AssertionError(f"unexpected SQL: {statement}")
        return self._results.pop(0)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


OWNER = Principal(
    user_id=7,
    login_name="owner",
    display_name="Owner",
    roles=("ENGINEER",),
    permissions=frozenset({"DATASET_READ", "TASK_CREATE"}),
)
ADMIN = Principal(
    user_id=1,
    login_name="admin",
    display_name="Admin",
    roles=("SYSTEM_ADMIN",),
    permissions=ALL_PERMISSIONS,
)
MANAGER = Principal(
    user_id=23,
    login_name="manager",
    display_name="Manager",
    roles=("MANAGER_VIEWER",),
    permissions=frozenset({"DATASET_READ"}),
)


def _service(results: list[_Result]) -> tuple[SqlM2QueryService, _Connection]:
    connection = _Connection(results)
    return SqlM2QueryService(_Engine(connection)), connection  # type: ignore[arg-type]


def test_upload_page_uses_owner_scope_offset_fetch_and_ignores_validated_fields() -> None:
    service, connection = _service(
        [
            _Result(scalar=12),
            _Result(
                rows=[
                    {
                        "import_batch_id": 41,
                        "ordinal_no": 2,
                        "receipt_id": 81,
                        "source_file_id": 91,
                        "original_file_name": "sample.xlsx",
                        "file_size": 120,
                        "factory_code": "RIYUEXIN",
                        "started_at_utc": datetime(2026, 8, 29, 1),
                        "completed_at_utc": None,
                        "login_name": "owner",
                        "display_name": "Owner",
                        "status": "QUEUED",
                        "latest_job_id": 101,
                        "error_code": "CLEANER_FAILED",
                        "error_message": r"C:\secret\input.xlsx failed",
                        "queue_age_seconds": 90,
                    }
                ]
            ),
        ]
    )
    filters = M2PageFilters(
        page=2,
        page_size=5,
        factory_code="RIYUEXIN",
        status="QUEUED",
        product_name="validated but ignored",
        lot_id="LOT-IGNORED",
        from_utc=datetime(2026, 8, 1),
    )

    page = service.list_uploads_page(OWNER, "PRODUCTION", "FT", filters)

    assert page.total == 12
    assert page.page == 2
    assert page.items[0].queue_age_seconds == 90
    assert page.items[0].error_message == "处理失败；详细日志仅供管理员在受控主机查看。"
    count_sql, count_parameters = connection.calls[0]
    page_sql, page_parameters = connection.calls[1]
    assert "COUNT_BIG(*)" in count_sql
    assert "(:is_admin=1 OR b.owner_user_id=:user_id)" in page_sql
    assert "DATEDIFF(second,j.not_before_utc,SYSUTCDATETIME())" in page_sql
    assert "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in page_sql
    assert "iam.data_scope_grant" not in count_sql
    assert "iam.data_scope_grant" not in page_sql
    assert ":product_name" not in page_sql
    assert ":lot_id" not in page_sql
    assert count_parameters["is_admin"] is False
    assert page_parameters["offset"] == 5
    assert page_parameters["page_size"] == 5
    assert "product_name" not in page_parameters
    assert "lot_id" not in page_parameters


def test_result_page_returns_job_id_nullable_metrics_and_no_storage_fields() -> None:
    service, connection = _service(
        [
            _Result(scalar=1),
            _Result(
                rows=[
                    {
                        "result_summary_id": 8,
                        "import_batch_id": 41,
                        "job_id": 101,
                        "data_name": "FT result",
                        "product_name": "NCE%_1",
                        "lot_id": "LOT-1",
                        "wafer_count": None,
                        "factory_code": "RIYUEXIN",
                        "test_item_count": 20,
                        "unit_count": 100,
                        "pass_count": None,
                        "yield_rate": None,
                        "status": "PROCESSED",
                        "data_type": "FT",
                        "dataset_id": 201,
                        "dataset_version_no": 1,
                        "created_at_utc": datetime(2026, 8, 29, 1, 10),
                    }
                ]
            ),
        ]
    )
    filters = M2PageFilters(
        page=1,
        page_size=20,
        product_name="NCE%_1",
        lot_id="LOT-1",
    )

    page = service.list_results_page(MANAGER, "PRODUCTION", "FT", filters)

    item = page.items[0]
    assert item.job_id == 101
    assert item.pass_count is None
    assert item.yield_rate is None
    count_sql, _count_parameters = connection.calls[0]
    sql, parameters = connection.calls[1]
    assert "s.job_id" in sql
    assert "s.output_uri" not in sql
    assert "s.artifact_manifest_json" not in sql
    assert "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in sql
    assert parameters["product_name"] == r"%NCE\%\_1%"
    assert parameters["lot_id"] == "%LOT-1%"
    assert parameters["is_admin"] is False
    assert parameters["user_id"] == 23
    for scoped_sql in (count_sql, sql):
        assert "iam.data_scope_grant" in scoped_sql
        assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in scoped_sql
        assert "scope_g.expires_at_utc>SYSUTCDATETIME()" in scoped_sql


def test_current_catalog_only_returns_owner_visible_published_current_versions() -> None:
    service, connection = _service(
        [
            _Result(scalar=1),
            _Result(
                rows=[
                    {
                        "dataset_id": 201,
                        "dataset_version_id": 301,
                        "version_no": 2,
                        "import_batch_id": 41,
                        "job_id": 101,
                        "processing_run_id": 501,
                        "product_name": "NCE-1",
                        "lot_id": "LOT-1",
                        "factory_code": "RIYUEXIN",
                        "business_domain": "PRODUCTION",
                        "test_stage": "FT",
                        "status": "PUBLISHED",
                        "unit_count": 100,
                        "pass_count": None,
                        "yield_rate": None,
                        "source_file_count": 1,
                        "processed_at_utc": datetime(2026, 8, 29, 1, 10),
                    }
                ]
            ),
        ]
    )
    filters = M2PageFilters(
        page=3,
        page_size=10,
        business_domain="PRODUCTION",
        test_stage="FT",
        factory_code="RIYUEXIN",
        status="PUBLISHED",
        product_name="NCE-1",
        lot_id="LOT-1",
        to_utc=datetime(2026, 8, 30),
    )

    page = service.list_current_datasets(OWNER, filters)

    assert page.items[0].dataset_version_id == 301
    assert page.items[0].pass_count is None
    count_sql, _count_parameters = connection.calls[0]
    page_sql, parameters = connection.calls[1]
    for sql in (count_sql, page_sql):
        assert "dv.status='PUBLISHED'" in sql
        assert "dv.is_current=1" in sql
        assert ":is_admin=1 OR d.owner_user_id=:user_id" in sql
        assert "iam.data_scope_grant" in sql
        assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in sql
        assert "scope_g.expires_at_utc>SYSUTCDATETIME()" in sql
    assert "ingestion.processing_run_input_file" in page_sql
    assert "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in page_sql
    assert "COALESCE(pr.finished_at_utc,dv.published_at_utc)" in page_sql
    assert parameters["offset"] == 20
    assert parameters["user_id"] == 7
    assert parameters["is_admin"] is False


def test_job_details_returns_safe_trace_chain_timeline_and_actions() -> None:
    main_row = {
        "job_id": 101,
        "source_file_id": None,
        "import_batch_id": 41,
        "analysis_session_id": None,
        "cleaner_release_id": 11,
        "job_type": "INITIAL_IMPORT",
        "lifecycle_action_type": "REPROCESS_UPDATE",
        "trigger_type": "SYSTEM",
        "requested_by": "owner",
        "reason": r"retry C:\secret\input.xlsx",
        "status": "SUCCESS",
        "requested_at_utc": datetime(2026, 8, 29, 1),
        "started_at_utc": datetime(2026, 8, 29, 1, 1),
        "finished_at_utc": datetime(2026, 8, 29, 1, 10),
        "error_code": None,
        "error_message": "token=secret",
        "not_before_utc": datetime(2026, 8, 29, 1),
        "heartbeat_at_utc": datetime(2026, 8, 29, 1, 9),
        "lease_expires_at_utc": None,
        "attempt_count": 1,
        "max_attempts": 3,
        "parent_job_id": 100,
        "finalize_protocol": "ATOMIC_V1",
        "queue_age_seconds": None,
        "source_file_count": 2,
        "cleaner_code": "FT_CLEANER",
        "cleaner_version": "1.2.3",
        "code_checksum": "a" * 64,
        "batch_name": "LOT-1",
        "business_domain": "PRODUCTION",
        "test_stage": "FT",
        "factory_code": "RIYUEXIN",
        "batch_status": "PROCESSED",
    }
    link_base = {
        "job_type": "INITIAL_IMPORT",
        "import_batch_id": 41,
        "requested_at_utc": datetime(2026, 8, 29, 0, 30),
        "started_at_utc": None,
        "finished_at_utc": None,
        "error_code": None,
        "error_message": None,
        "attempt_count": 1,
        "max_attempts": 3,
    }
    chain = {
        "intent_status": "FINALIZED",
        "processing_run_id": 501,
        "run_status": "PUBLISHED",
        "run_started_at_utc": datetime(2026, 8, 29, 1, 1),
        "run_finished_at_utc": datetime(2026, 8, 29, 1, 10),
        "dataset_id": 201,
        "dataset_version_id": 301,
        "version_no": 1,
        "version_status": "PUBLISHED",
        "is_current": True,
        "staged_at_utc": datetime(2026, 8, 29, 1, 8),
        "finalized_at_utc": datetime(2026, 8, 29, 1, 9),
        "aborted_at_utc": None,
    }
    service, connection = _service(
        [
            _Result(rows=[main_row]),
            _Result(
                rows=[
                    link_base
                    | {
                        "job_id": 100,
                        "parent_job_id": None,
                        "status": "NEEDS_INPUT",
                    },
                    link_base
                    | {
                        "job_id": 102,
                        "parent_job_id": 101,
                        "status": "QUEUED",
                    },
                ]
            ),
            _Result(rows=[chain]),
            _Result(
                rows=[
                    {
                        "source_file_id": 71,
                        "ordinal_no": 1,
                        "original_file_name": "lot-a.xlsx",
                        "file_size": 1234,
                        "sha256": "b" * 64,
                        "lineage_basis": "WRITER_VERIFIED",
                    },
                    {
                        "source_file_id": 72,
                        "ordinal_no": 2,
                        "original_file_name": "lot-b.xlsx",
                        "file_size": 2345,
                        "sha256": "c" * 64,
                        "lineage_basis": "WRITER_VERIFIED",
                    },
                ]
            ),
        ]
    )

    details = service.get_job_details(ADMIN, 101)

    assert details.job.job_id == 101
    assert details.job.lifecycle_action_type == "REPROCESS_UPDATE"
    assert details.job.reason == "处理失败；详细日志仅供管理员在受控主机查看。"
    assert details.job.error_message == "处理失败；详细日志仅供管理员在受控主机查看。"
    assert details.parent is not None and details.parent.job_id == 100
    assert details.children[0].job_id == 102
    assert details.release is not None
    assert details.release.content_sha256 == "a" * 64
    assert details.batch is not None and details.batch.source_file_count == 2
    assert details.intent is not None and details.intent.status == "FINALIZED"
    assert details.run is not None and details.run.processing_run_id == 501
    assert details.dataset is not None and details.dataset.is_current is True
    assert [source.source_file_id for source in details.sources] == [71, 72]
    assert details.sources[0].lineage_basis == "WRITER_VERIFIED"
    assert [event.event_code for event in details.timeline] == [
        "JOB_QUEUED",
        "JOB_STARTED",
        "RUN_STARTED",
        "PUBLISH_STAGED",
        "PUBLISH_FINALIZED",
        "RUN_FINISHED",
        "JOB_FINISHED",
    ]
    assert [action.code for action in details.actions] == [
        "DOWNLOAD_SOURCE",
        "VIEW_RESULT",
        "REPROCESS_BATCH",
    ]
    main_sql, main_parameters = connection.calls[0]
    links_sql, links_parameters = connection.calls[1]
    sources_sql, sources_parameters = connection.calls[3]
    assert "j.lease_token" not in main_sql
    assert "j.idempotency_key" not in main_sql
    assert "storage_uri" not in main_sql
    assert "artifact_uri" not in main_sql
    assert ":is_admin=1 OR b.owner_user_id=:user_id" in main_sql
    assert ":is_admin=1 OR b.owner_user_id=:user_id" in links_sql
    assert "lifecycle_job_target lt" in main_sql
    assert "lifecycle_job_target lt" in links_sql
    for sql in (main_sql, links_sql):
        assert "j.import_batch_id IS NOT NULL" in sql
        assert "iam.data_scope_grant" in sql
        assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in sql
        assert "scope_g.permission_mode='READ'" in sql
        assert "scope_g.expires_at_utc>SYSUTCDATETIME()" in sql
    assert main_parameters == {"user_id": 1, "is_admin": True, "job_id": 101}
    assert links_parameters["parent_job_id"] == 100
    assert "processing_run_input_file" in sources_sql
    assert "storage_uri" not in sources_sql
    assert sources_parameters == {"processing_run_id": 501}


def test_job_details_hides_existence_when_owner_scope_does_not_match() -> None:
    service, connection = _service([_Result(rows=[])])

    with pytest.raises(DomainError) as exc_info:
        service.get_job_details(OWNER, 999)

    assert exc_info.value.code == "JOB_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert len(connection.calls) == 1


def test_global_current_data_grant_is_narrow_and_expiry_aware() -> None:
    grant = _CURRENT_DATA_READ_GRANT

    assert "scope_ur.user_id=:user_id" in grant
    assert "scope_r.active=1" in grant
    assert "scope_g.role_id=scope_ur.role_id" in grant
    assert "scope_g.user_id IS NULL" in grant
    assert "scope_g.scope_type='GLOBAL'" in grant
    assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in grant
    assert "scope_g.permission_mode='READ'" in grant
    assert "scope_g.expires_at_utc IS NULL" in grant
    assert "scope_g.expires_at_utc>SYSUTCDATETIME()" in grant
    assert "scope_g.scope_key=N'*'" not in grant
    for job_sql in (_JOB_DETAILS_SQL, _JOB_LINKS_SQL):
        assert "j.import_batch_id IS NOT NULL" in job_sql
        assert grant in job_sql


def test_m2_read_queries_keep_sql_server_2014_compatible_constructs() -> None:
    sql = "\n".join(
        (
            _CURRENT_DATASET_COLUMNS,
            _CURRENT_DATASET_FROM,
            _JOB_DETAILS_SQL,
            _JOB_LINKS_SQL,
            _JOB_PUBLISH_CHAIN_SQL,
        )
    ).upper()

    assert "TOP (1)" in sql
    assert "OUTER APPLY" in sql
    for unsupported in (
        "STRING_AGG(",
        "OPENJSON(",
        "JSON_VALUE(",
        "AT TIME ZONE",
        "DROP TABLE IF EXISTS",
    ):
        assert unsupported not in sql
