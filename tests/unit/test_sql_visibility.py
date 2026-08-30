from __future__ import annotations

from app.domain.auth import ALL_PERMISSIONS, Principal
from app.domain.m2_queries import DatasetSummary
from app.infrastructure.sql_enrichment_service import SqlFieldEnrichmentService
from app.infrastructure.sql_input_request_service import (
    SqlProcessingInputRequestService,
)
from app.infrastructure.sql_m2_query_service import SqlM2QueryService
from app.infrastructure.sql_management_service import (
    _CURRENT_SCOPE_CTE,
    _FAILED_JOB_SQL,
)
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.infrastructure.sql_visibility import (
    batch_owner_scope_sql,
    batch_read_scope_sql,
    batch_write_scope_sql,
    current_dataset_read_scope_sql,
    formal_result_read_scope_sql,
    visibility_parameters,
)

OWNER = Principal(
    7,
    "owner",
    "Owner",
    ("ENGINEER",),
    frozenset({"DATASET_READ", "TASK_CREATE"}),
)
ADMIN = Principal(
    1,
    "admin",
    "Admin",
    ("SYSTEM_ADMIN",),
    ALL_PERMISSIONS,
)


def test_visibility_contract_separates_domain_read_from_owner_write() -> None:
    owner_scope = batch_owner_scope_sql(batch_alias="b")
    read_scope = batch_read_scope_sql(batch_alias="b")
    write_scope = batch_write_scope_sql(batch_alias="b")

    assert "b.owner_user_id=:user_id" in owner_scope
    assert "PRODUCTION" not in owner_scope
    assert "b.owner_user_id=:user_id" in read_scope
    assert "b.business_domain='PRODUCTION'" in read_scope
    assert "b.owner_user_id=:user_id" in write_scope
    assert "PRODUCTION" not in write_scope
    assert visibility_parameters(OWNER) == {"user_id": 7, "is_admin": False}
    assert visibility_parameters(ADMIN) == {"user_id": 1, "is_admin": True}


def test_cross_owner_dataset_and_result_reads_require_current_published_production() -> (
    None
):
    dataset_scope = current_dataset_read_scope_sql()
    result_scope = formal_result_read_scope_sql()

    assert "b.business_domain='PRODUCTION'" in dataset_scope
    assert "dv.status='PUBLISHED'" in dataset_scope
    assert "dv.is_current=1" in dataset_scope
    assert "b.business_domain='PRODUCTION'" in result_scope
    assert "result_d.dataset_id=s.dataset_id" in result_scope
    assert "result_dv.version_no=s.dataset_version_no" in result_scope
    assert "result_dv.status='PUBLISHED'" in result_scope
    assert "result_dv.is_current=1" in result_scope


def test_management_scope_filters_engineering_before_canonical_joins() -> None:
    normalized = " ".join(_CURRENT_SCOPE_CTE.split())

    assert "d.owner_user_id=:user_id" in normalized
    assert "b.business_domain='PRODUCTION'" in normalized
    assert normalized.index("b.business_domain='PRODUCTION'") < normalized.index(
        "), scoped_units AS"
    )
    assert "b.owner_user_id=:user_id" in _FAILED_JOB_SQL
    assert "b.business_domain='PRODUCTION'" in _FAILED_JOB_SQL


def test_stage_input_and_enrichment_write_scopes_never_inherit_production_read() -> (
    None
):
    assert "PRODUCTION" in SqlStageDataService._scope("READ")
    assert "PRODUCTION" not in SqlStageDataService._scope("WRITE")
    assert "PRODUCTION" not in SqlProcessingInputRequestService._scope("READ")
    assert SqlProcessingInputRequestService._scope(
        "READ"
    ) == SqlProcessingInputRequestService._scope("WRITE")
    assert "PRODUCTION" not in SqlProcessingInputRequestService._scope("WRITE")
    assert "PRODUCTION" in SqlFieldEnrichmentService._access_scope("READ")
    assert "PRODUCTION" not in SqlFieldEnrichmentService._access_scope("WRITE")


def test_duplicate_receipt_uses_its_own_snapshot_path() -> None:
    item = SqlStageDataService._batch_file(
        {
            "receipt_id": 9,
            "source_file_id": 3,
            "original_file_name": "same.xlsx",
            "metadata_json": '{"receipt_storage_uri":"F:/managed/second/same.xlsx"}',
            "is_duplicate_receipt": 1,
            "canonical_storage_uri": "F:/managed/first/same.xlsx",
            "expected_sha256": "a" * 64,
        }
    )

    assert item.storage_uri == "F:/managed/second/same.xlsx"
    assert item.is_duplicate_receipt is True


def test_cross_owner_production_job_only_offers_safe_read_action() -> None:
    job = {
        "source_file_count": 1,
        "import_batch_id": 41,
        "batch_status": "PROCESSED",
        "status": "SUCCESS",
        "job_type": "INITIAL_IMPORT",
    }
    dataset = DatasetSummary(201, 301, 1, "PUBLISHED", True)

    cross_owner = SqlM2QueryService._available_actions(
        OWNER, job, dataset, can_manage=False
    )
    owner = SqlM2QueryService._available_actions(OWNER, job, dataset, can_manage=True)

    assert [item.code for item in cross_owner] == ["VIEW_RESULT"]
    assert [item.code for item in owner] == [
        "DOWNLOAD_SOURCE",
        "VIEW_RESULT",
        "REPROCESS_BATCH",
    ]


def test_cross_owner_job_hides_draft_or_historical_publish_chain() -> None:
    draft = {"version_status": "DRAFT", "is_current": False}
    historical = {"version_status": "PUBLISHED", "is_current": False}
    current = {"version_status": "PUBLISHED", "is_current": True}

    assert SqlM2QueryService._visible_publish_chain(draft, can_manage=False) is None
    assert (
        SqlM2QueryService._visible_publish_chain(historical, can_manage=False) is None
    )
    assert (
        SqlM2QueryService._visible_publish_chain(current, can_manage=False) is current
    )
    assert SqlM2QueryService._visible_publish_chain(draft, can_manage=True) is draft
