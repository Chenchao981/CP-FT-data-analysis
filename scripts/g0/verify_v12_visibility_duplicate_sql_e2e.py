from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetStage,
    DatasetType,
    PublishDatasetVersionRequest,
)
from app.domain.stage_data import BatchInfo, StoredUpload
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_stage_data_service import SqlStageDataService

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0023"

_COUNTED_TABLES = (
    "iam.app_user",
    "mdm.supplier",
    "mdm.product",
    "ingestion.parser_profile",
    "ingestion.import_batch",
    "ingestion.source_file",
    "ingestion.source_file_receipt",
    "ingestion.import_batch_file",
    "ingestion.processing_job",
    "ingestion.processing_run",
    "dataset.dataset",
    "dataset.dataset_version",
    "dataset.dataset_version_run",
    "test.test_run",
    "test.unit_result",
)


class _RollbackEngine:
    """Bind every application service call to one caller-owned transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        yield self._connection

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        yield self._connection


@dataclass(frozen=True, slots=True)
class _ReferenceIds:
    supplier_id: int
    product_id: int
    parser_profile_id: int


@dataclass(frozen=True, slots=True)
class _RunIds:
    job_id: int
    processing_run_id: int
    test_run_id: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify v1.2 Dataset-scoped Current, visibility, and duplicate "
            "Receipt behavior in TMS_G0_DEV; every fixture row is rolled back"
        )
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="include the random fixture token in PASS output",
    )
    return parser.parse_args()


def _assert_database_identity(identity: Mapping[str, str]) -> None:
    database = identity.get("database")
    revision = identity.get("schema_revision")
    if database != EXPECTED_DATABASE or revision != EXPECTED_SCHEMA_REVISION:
        raise RuntimeError(
            "rollback E2E is restricted to "
            f"{EXPECTED_DATABASE}/{EXPECTED_SCHEMA_REVISION}; "
            f"got {database}/{revision}"
        )


def _assert_schema_contract(connection: Connection) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT name,is_unique,has_filter FROM sys.indexes "
                "WHERE object_id=OBJECT_ID(N'ingestion.processing_run') "
                "AND name IN(N'UX_processing_run_current',"
                "N'IX_processing_run_source_state')"
            )
        )
        .mappings()
        .all()
    )
    indexes = {
        str(row["name"]): (bool(row["is_unique"]), bool(row["has_filter"]))
        for row in rows
    }
    if "UX_processing_run_current" in indexes:
        raise RuntimeError("obsolete source-global Current unique index still exists")
    if indexes.get("IX_processing_run_source_state") != (False, False):
        raise RuntimeError("sql2014_0020 non-unique source-state index is missing")


def _table_counts(connection: Connection) -> dict[str, int]:
    return {
        table: int(
            connection.execute(text(f"SELECT COUNT_BIG(*) FROM {table}")).scalar_one()
        )
        for table in _COUNTED_TABLES
    }


def _fixture_leak_count(connection: Connection, *, token: str, sha256: str) -> int:
    statements: Sequence[tuple[str, dict[str, object]]] = (
        (
            "SELECT COUNT_BIG(*) FROM iam.app_user WHERE login_name LIKE :prefix",
            {"prefix": f"v12-{token}-%"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM mdm.supplier WHERE supplier_code=:code",
            {"code": f"V12-{token}"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM mdm.product WHERE product_code=:code",
            {"code": f"V12-{token}"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM ingestion.parser_profile WHERE format_code=:code",
            {"code": f"V12_{token}"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM ingestion.source_file WHERE sha256=:sha",
            {"sha": sha256},
        ),
        (
            "SELECT COUNT_BIG(*) FROM ingestion.import_batch "
            "WHERE batch_name LIKE :pattern",
            {"pattern": f"%{token}%"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM dataset.dataset WHERE dataset_code LIKE :prefix",
            {"prefix": f"V12-{token}-%"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
            "WHERE reason LIKE :pattern",
            {"pattern": f"%{token}%"},
        ),
        (
            "SELECT COUNT_BIG(*) FROM test.test_run WHERE lot_id=:lot",
            {"lot": f"LOT-{token}"},
        ),
    )
    return sum(
        int(connection.execute(text(sql), parameters).scalar_one())
        for sql, parameters in statements
    )


def _create_principal(connection: Connection, token: str, suffix: str) -> Principal:
    login = f"v12-{token}-{suffix}"
    user_id = int(
        connection.execute(
            text(
                "INSERT iam.app_user(login_name,display_name,identity_provider,"
                "external_subject,status) OUTPUT INSERTED.user_id "
                "VALUES(:login,:display,'AD',:subject,'ACTIVE')"
            ),
            {
                "login": login,
                "display": f"v1.2 rollback {suffix}",
                "subject": f"v12-e2e:{token}:{suffix}",
            },
        ).scalar_one()
    )
    return Principal(
        user_id=user_id,
        login_name=login,
        display_name=f"v1.2 rollback {suffix}",
        roles=(),
        permissions=frozenset({"DATASET_READ", "TASK_CREATE"}),
    )


def _create_reference_data(connection: Connection, token: str) -> _ReferenceIds:
    supplier_id = int(
        connection.execute(
            text(
                "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type) "
                "OUTPUT INSERTED.supplier_id VALUES(:code,:name,'OTHER')"
            ),
            {"code": f"V12-{token}", "name": f"v1.2 rollback {token}"},
        ).scalar_one()
    )
    product_id = int(
        connection.execute(
            text(
                "INSERT mdm.product(product_code,product_name) "
                "OUTPUT INSERTED.product_id VALUES(:code,:name)"
            ),
            {"code": f"V12-{token}", "name": f"v1.2 rollback {token}"},
        ).scalar_one()
    )
    parser_profile_id = int(
        connection.execute(
            text(
                "INSERT ingestion.parser_profile(format_code,supplier_id,test_stage,"
                "parser_name,parser_version,canonical_model_version,active,is_default) "
                "OUTPUT INSERTED.parser_profile_id "
                "VALUES(:format,:supplier,'CP',:name,'1.0','1.0',1,0)"
            ),
            {
                "format": f"V12_{token}",
                "supplier": supplier_id,
                "name": f"v1.2 rollback parser {token}",
            },
        ).scalar_one()
    )
    return _ReferenceIds(supplier_id, product_id, parser_profile_id)


def _create_ready_run(
    connection: Connection,
    *,
    token: str,
    ordinal: int,
    principal: Principal,
    batch_id: int,
    source_file_id: int,
    references: _ReferenceIds,
) -> _RunIds:
    job_id = int(
        connection.execute(
            text(
                "INSERT ingestion.processing_job(job_type,trigger_type,"
                "requested_by,status,import_batch_id,reason,metadata_json,"
                "requested_by_user_id,idempotency_key,finished_at_utc) "
                "OUTPUT INSERTED.job_id VALUES('OTHER','SYSTEM',:login,"
                "'SUCCESS',:batch,:reason,:metadata,:owner,:key,SYSUTCDATETIME())"
            ),
            {
                "login": principal.login_name,
                "batch": batch_id,
                "reason": f"v1.2 rollback fixture {token} run {ordinal}",
                "metadata": json.dumps(
                    {"verification": "v12-visibility-duplicate", "ordinal": ordinal},
                    separators=(",", ":"),
                ),
                "owner": principal.user_id,
                "key": f"v12:{token}:run:{ordinal}",
            },
        ).scalar_one()
    )
    processing_run_id = int(
        connection.execute(
            text(
                "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
                "parser_version,canonical_model_version,status,is_current,row_count_input,"
                "unit_count_output,measurement_count_output,finished_at_utc,metadata_json) "
                "OUTPUT INSERTED.processing_run_id VALUES(:job,:source,:parser,'1.0',"
                "'1.0','READY',0,1,1,0,SYSUTCDATETIME(),:metadata)"
            ),
            {
                "job": job_id,
                "source": source_file_id,
                "parser": references.parser_profile_id,
                "metadata": json.dumps(
                    {"verification": "v12-visibility-duplicate", "ordinal": ordinal},
                    separators=(",", ":"),
                ),
            },
        ).scalar_one()
    )
    test_run_id = int(
        connection.execute(
            text(
                "INSERT test.test_run(processing_run_id,supplier_id,product_id,"
                "test_stage,lot_id,wafer_id,metadata_json) OUTPUT INSERTED.run_id "
                "VALUES(:run,:supplier,:product,'CP',:lot,:wafer,:metadata)"
            ),
            {
                "run": processing_run_id,
                "supplier": references.supplier_id,
                "product": references.product_id,
                "lot": f"LOT-{token}",
                "wafer": f"W{ordinal}",
                "metadata": json.dumps(
                    {"verification": "v12-visibility-duplicate"},
                    separators=(",", ":"),
                ),
            },
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT test.unit_result(run_id,logical_unit_key,unit_sequence,wafer_id,"
            "x_coord,y_coord,soft_bin,overall_result,metadata_json) "
            "VALUES(:run,:key,1,:wafer,:x,:y,'1','PASS',:metadata)"
        ),
        {
            "run": test_run_id,
            "key": f"V12-{token}-{ordinal}",
            "wafer": f"W{ordinal}",
            "x": ordinal,
            "y": ordinal,
            "metadata": json.dumps(
                {"verification": "v12-visibility-duplicate"},
                separators=(",", ":"),
            ),
        },
    )
    return _RunIds(job_id, processing_run_id, test_run_id)


def _assert_receipts(
    first: BatchInfo,
    second: BatchInfo,
    *,
    first_path: Path,
    second_path: Path,
) -> int:
    if len(first.files) != 1 or len(second.files) != 1:
        raise RuntimeError(
            "duplicate upload fixture did not create one Receipt per Batch"
        )
    first_file = first.files[0]
    second_file = second.files[0]
    if first.import_batch_id == second.import_batch_id:
        raise RuntimeError("duplicate upload reused an Import Batch")
    if first_file.receipt_id == second_file.receipt_id:
        raise RuntimeError("duplicate upload reused a Receipt")
    if first_file.source_file_id != second_file.source_file_id:
        raise RuntimeError("identical SHA did not resolve to the same immutable Source")
    if first_file.storage_uri != str(first_path):
        raise RuntimeError("first Receipt lost its uploader-specific storage path")
    if second_file.storage_uri != str(second_path):
        raise RuntimeError("second Receipt lost its uploader-specific storage path")
    if first_file.is_duplicate_receipt or not second_file.is_duplicate_receipt:
        raise RuntimeError(
            "duplicate Receipt flags do not match first/subsequent receipt order"
        )
    return first_file.source_file_id


def _dataset_run_rows(
    connection: Connection, dataset_ids: Sequence[int]
) -> list[Mapping[str, Any]]:
    placeholders = ",".join(f":dataset_{index}" for index in range(len(dataset_ids)))
    parameters = {
        f"dataset_{index}": dataset_id for index, dataset_id in enumerate(dataset_ids)
    }
    return list(
        connection.execute(
            text(
                "SELECT d.dataset_id,dv.dataset_version_id,dv.version_no,"
                "dv.status AS version_status,dv.is_current AS version_current,"
                "dv.supersedes_dataset_version_id,pr.processing_run_id,"
                "pr.source_file_id,pr.status AS run_status,pr.is_current AS run_current "
                "FROM dataset.dataset d JOIN dataset.dataset_version dv "
                "ON dv.dataset_id=d.dataset_id JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN ingestion.processing_run pr "
                "ON pr.processing_run_id=dvr.processing_run_id "
                f"WHERE d.dataset_id IN ({placeholders}) "
                "ORDER BY d.dataset_id,dv.version_no,pr.processing_run_id"
            ),
            parameters,
        )
        .mappings()
        .all()
    )


def _assert_dataset_run_state(
    rows: Sequence[Mapping[str, Any]],
    expected: Mapping[
        tuple[int, int], tuple[int, str, bool, int, str, bool, int | None]
    ],
) -> None:
    actual = {
        (int(row["dataset_id"]), int(row["version_no"])): (
            int(row["dataset_version_id"]),
            str(row["version_status"]),
            bool(row["version_current"]),
            int(row["processing_run_id"]),
            str(row["run_status"]),
            bool(row["run_current"]),
            int(row["supersedes_dataset_version_id"])
            if row["supersedes_dataset_version_id"] is not None
            else None,
        )
        for row in rows
    }
    if actual != dict(expected):
        raise RuntimeError(
            "Dataset/Run Current state mismatch: "
            f"expected={dict(expected)!r}, actual={actual!r}"
        )


def _expect_dataset_denied(
    service: SqlDatasetService,
    dataset_id: int,
    principal: Principal,
    mode: str,
    *,
    version_no: int | None = None,
) -> None:
    try:
        service.assert_dataset_access(
            dataset_id,
            principal,
            mode,
            version_no=version_no,
        )
    except DomainError as exc:
        if exc.code != "DATASET_ACCESS_DENIED" or exc.status_code != 403:
            raise RuntimeError(f"unexpected visibility rejection: {exc.code}") from exc
        return
    raise RuntimeError(f"non-owner unexpectedly received Dataset {mode} access")


def _run_fixture(connection: Connection, token: str, sha256: str) -> dict[str, int]:
    _assert_schema_contract(connection)
    bound_engine = _RollbackEngine(connection)
    stage_service = SqlStageDataService(bound_engine)  # type: ignore[arg-type]
    dataset_service = SqlDatasetService(bound_engine)  # type: ignore[arg-type]

    owner_a = _create_principal(connection, token, "owner-a")
    owner_b = _create_principal(connection, token, "owner-b")
    viewer = _create_principal(connection, token, "viewer")
    references = _create_reference_data(connection, token)

    first_path = ROOT / "data" / "work" / "v12-e2e" / token / "owner-a.csv"
    second_path = ROOT / "data" / "work" / "v12-e2e" / token / "owner-b.csv"
    first_upload = StoredUpload(
        original_name=f"owner-a-{token}.csv",
        path=first_path,
        size_bytes=128,
        sha256=sha256,
    )
    second_upload = StoredUpload(
        original_name=f"owner-b-{token}.csv",
        path=second_path,
        size_bytes=128,
        sha256=sha256,
    )
    engineering_batch = stage_service.register_upload(
        owner_a,
        "ENGINEERING",
        "CP",
        "V12-E2E",
        (first_upload,),
        f"rollback-only {token}",
    )
    production_batch = stage_service.register_upload(
        owner_b,
        "PRODUCTION",
        "CP",
        "V12-E2E",
        (second_upload,),
        f"rollback-only {token}",
    )
    first_info = stage_service.get_batch_info(
        owner_a, "ENGINEERING", "CP", engineering_batch
    )
    second_info = stage_service.get_batch_info(
        owner_b, "PRODUCTION", "CP", production_batch
    )
    if first_info is None or second_info is None:
        raise RuntimeError("owners could not read their newly registered Batches")
    source_file_id = _assert_receipts(
        first_info,
        second_info,
        first_path=first_path,
        second_path=second_path,
    )

    run_a1 = _create_ready_run(
        connection,
        token=token,
        ordinal=1,
        principal=owner_a,
        batch_id=engineering_batch,
        source_file_id=source_file_id,
        references=references,
    )
    run_b1 = _create_ready_run(
        connection,
        token=token,
        ordinal=2,
        principal=owner_b,
        batch_id=production_batch,
        source_file_id=source_file_id,
        references=references,
    )
    connection.execute(
        text(
            "UPDATE ingestion.import_batch SET status='PROCESSED',"
            "completed_at_utc=SYSUTCDATETIME() "
            "WHERE import_batch_id IN(:first,:second)"
        ),
        {"first": engineering_batch, "second": production_batch},
    )

    engineering_dataset = dataset_service.create_dataset(
        CreateDatasetRequest(
            dataset_code=f"V12-{token.upper()}-ENG",
            dataset_name=f"v1.2 Engineering rollback {token}",
            dataset_type=DatasetType.CP_DETAIL,
            test_stage=DatasetStage.CP,
            supplier_id=references.supplier_id,
            product_id=references.product_id,
            owner_user_id=owner_a.user_id,
        )
    )
    production_dataset = dataset_service.create_dataset(
        CreateDatasetRequest(
            dataset_code=f"V12-{token.upper()}-PROD",
            dataset_name=f"v1.2 Production rollback {token}",
            dataset_type=DatasetType.CP_DETAIL,
            test_stage=DatasetStage.CP,
            supplier_id=references.supplier_id,
            product_id=references.product_id,
            owner_user_id=owner_b.user_id,
        )
    )
    engineering_v1 = dataset_service.create_version(
        engineering_dataset.dataset_id,
        CreateDatasetVersionRequest(
            input_batch_id=engineering_batch,
            processing_run_ids=[run_a1.processing_run_id],
        ),
    )
    production_v1 = dataset_service.create_version(
        production_dataset.dataset_id,
        CreateDatasetVersionRequest(
            input_batch_id=production_batch,
            processing_run_ids=[run_b1.processing_run_id],
        ),
    )
    dataset_service.publish(
        engineering_dataset.dataset_id,
        engineering_v1.version_no,
        PublishDatasetVersionRequest(published_by=owner_a.user_id),
    )
    dataset_service.publish(
        production_dataset.dataset_id,
        production_v1.version_no,
        PublishDatasetVersionRequest(published_by=owner_b.user_id),
    )
    _assert_dataset_run_state(
        _dataset_run_rows(
            connection,
            (engineering_dataset.dataset_id, production_dataset.dataset_id),
        ),
        {
            (engineering_dataset.dataset_id, 1): (
                engineering_v1.dataset_version_id,
                "PUBLISHED",
                True,
                run_a1.processing_run_id,
                "PUBLISHED",
                True,
                None,
            ),
            (production_dataset.dataset_id, 1): (
                production_v1.dataset_version_id,
                "PUBLISHED",
                True,
                run_b1.processing_run_id,
                "PUBLISHED",
                True,
                None,
            ),
        },
    )

    run_a2 = _create_ready_run(
        connection,
        token=token,
        ordinal=3,
        principal=owner_a,
        batch_id=engineering_batch,
        source_file_id=source_file_id,
        references=references,
    )
    engineering_v2 = dataset_service.create_version(
        engineering_dataset.dataset_id,
        CreateDatasetVersionRequest(
            input_batch_id=engineering_batch,
            processing_run_ids=[run_a2.processing_run_id],
        ),
    )
    dataset_service.publish(
        engineering_dataset.dataset_id,
        engineering_v2.version_no,
        PublishDatasetVersionRequest(published_by=owner_a.user_id),
    )
    final_rows = _dataset_run_rows(
        connection,
        (engineering_dataset.dataset_id, production_dataset.dataset_id),
    )
    _assert_dataset_run_state(
        final_rows,
        {
            (engineering_dataset.dataset_id, 1): (
                engineering_v1.dataset_version_id,
                "SUPERSEDED",
                False,
                run_a1.processing_run_id,
                "SUPERSEDED",
                False,
                None,
            ),
            (engineering_dataset.dataset_id, 2): (
                engineering_v2.dataset_version_id,
                "PUBLISHED",
                True,
                run_a2.processing_run_id,
                "PUBLISHED",
                True,
                engineering_v1.dataset_version_id,
            ),
            (production_dataset.dataset_id, 1): (
                production_v1.dataset_version_id,
                "PUBLISHED",
                True,
                run_b1.processing_run_id,
                "PUBLISHED",
                True,
                None,
            ),
        },
    )
    if {int(row["source_file_id"]) for row in final_rows} != {source_file_id}:
        raise RuntimeError("Dataset versions no longer share the immutable Source")
    current_source_runs = sum(
        bool(row["version_current"]) and bool(row["run_current"]) for row in final_rows
    )
    if current_source_runs != 2:
        raise RuntimeError("same Source does not have two Dataset-scoped Current Runs")

    viewer_datasets = {
        item.dataset_id for item in dataset_service.list_datasets(viewer)
    }
    if production_dataset.dataset_id not in viewer_datasets:
        raise RuntimeError("Production Current Dataset is not visible to a non-owner")
    if engineering_dataset.dataset_id in viewer_datasets:
        raise RuntimeError("Engineering Dataset leaked into a non-owner catalog")
    _expect_dataset_denied(
        dataset_service,
        engineering_dataset.dataset_id,
        viewer,
        "READ",
        version_no=engineering_v2.version_no,
    )
    dataset_service.assert_dataset_access(
        production_dataset.dataset_id,
        viewer,
        "READ",
        version_no=production_v1.version_no,
    )
    production_summary = dataset_service.get_summary(
        production_dataset.dataset_id,
        production_v1.version_no,
        viewer,
    )
    if (
        not production_summary.is_current
        or production_summary.version_status != "PUBLISHED"
    ):
        raise RuntimeError("non-owner did not receive Production Current summary")
    _expect_dataset_denied(
        dataset_service,
        production_dataset.dataset_id,
        viewer,
        "WRITE",
    )
    if (
        stage_service.get_batch_info(
            viewer,
            "PRODUCTION",
            "CP",
            production_batch,
            access_mode="WRITE",
        )
        is not None
    ):
        raise RuntimeError(
            "Production non-owner unexpectedly received Batch management"
        )
    viewer_production_rows = {
        row.import_batch_id: row
        for row in stage_service.list_uploads(viewer, "PRODUCTION", "CP")
    }
    visible_production = viewer_production_rows.get(production_batch)
    if visible_production is None or visible_production.can_manage:
        raise RuntimeError("Production upload visibility/manage flags are incorrect")
    if visible_production.can_download_source:
        raise RuntimeError(
            "Production non-owner unexpectedly received raw Source access"
        )
    viewer_engineering_ids = {
        row.import_batch_id
        for row in stage_service.list_uploads(viewer, "ENGINEERING", "CP")
    }
    if engineering_batch in viewer_engineering_ids:
        raise RuntimeError("Engineering upload metadata leaked to a non-owner")

    return {
        "source_file_id": source_file_id,
        "engineering_batch_id": engineering_batch,
        "production_batch_id": production_batch,
        "engineering_dataset_id": engineering_dataset.dataset_id,
        "production_dataset_id": production_dataset.dataset_id,
        "current_source_runs": current_source_runs,
    }


def main() -> None:
    args = _parse_args()
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    _assert_database_identity(identity)
    engine = get_engine()
    token = uuid4().hex
    sha256 = hashlib.sha256(f"v12-rollback:{token}".encode()).hexdigest()
    result: dict[str, int] | None = None
    failure: BaseException | None = None

    with engine.connect() as connection:
        baseline = _table_counts(connection)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            result = _run_fixture(connection, token, sha256)
        except BaseException as exc:  # rollback and leak-check before propagating
            failure = exc
        finally:
            transaction.rollback()

    with engine.connect() as connection:
        after = _table_counts(connection)
        leaked = _fixture_leak_count(connection, token=token, sha256=sha256)
    if baseline != after or leaked:
        rollback_error = RuntimeError(
            "rollback did not restore database rows: "
            f"count_drift={baseline != after}, fixture_rows={leaked}"
        )
        if failure is not None:
            raise rollback_error from failure
        raise rollback_error
    if failure is not None:
        raise failure
    if result is None:
        raise RuntimeError("rollback E2E produced no verification result")

    token_output = f" token={token}" if args.show_token else ""
    print(
        "v12_dataset_scoped_current=PASS "
        f"same_source_current_runs={result['current_source_runs']}"
        f"{token_output}"
    )
    print(
        "v12_visibility=PASS engineering_owner_only=true "
        "production_current_shared=true production_manage_owner_only=true"
    )
    print(
        "v12_duplicate_receipts=PASS independent_batches=true "
        "independent_receipts=true receipt_paths_independent=true"
    )
    print(
        "v12_rollback=PASS database=TMS_G0_DEV schema=sql2014_0023 "
        "database_rows_restored=true durable_fixture_rows=0"
    )


if __name__ == "__main__":
    main()
