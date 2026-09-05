from __future__ import annotations

"""Verify duplicate HTTP uploads through SQL Worker lease and atomic Current publish.

The verifier stages a minimal synthetic Canonical identity after the Worker claims each
Job. Real Cleaner parsing and Canonical row writing remain covered by the existing
factory Route A regression; this script isolates duplicate-upload identity semantics.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.dependencies import current_principal
from app.domain.auth import Principal
from app.domain.jobs import Job, JobStatus, JobType
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.main import create_app
from app.workers.route_a_worker import DatabaseJobWorker

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0029"
_COUNTED_TABLES = (
    "ingestion.import_batch",
    "ingestion.source_file",
    "ingestion.source_file_receipt",
    "ingestion.import_batch_file",
    "ingestion.processing_job",
    "ingestion.processing_run",
    "ingestion.processing_run_input_file",
    "ingestion.initial_import_finalize_intent",
    "ingestion.processing_result_summary",
    "ingestion.processing_artifact",
    "dataset.dataset",
    "dataset.dataset_version",
    "dataset.dataset_version_run",
    "governance.audit_log",
)


def _assert_database_identity(identity: Mapping[str, str]) -> None:
    database = identity.get("database")
    revision = identity.get("schema_revision")
    if database != EXPECTED_DATABASE or revision != EXPECTED_SCHEMA_REVISION:
        raise RuntimeError(
            "full-chain E2E is restricted to "
            f"{EXPECTED_DATABASE}/{EXPECTED_SCHEMA_REVISION}; "
            f"got {database}/{revision}"
        )


def _counts(connection: Connection) -> dict[str, int]:
    return {
        table: int(
            connection.execute(text(f"SELECT COUNT_BIG(*) FROM {table}")).scalar_one()
        )
        for table in _COUNTED_TABLES
    }


def _active_principals(connection: Connection) -> tuple[Principal, Principal]:
    rows = (
        connection.execute(
            text(
                "SELECT TOP (2) u.user_id,u.login_name,u.display_name,"
                "u.department_code FROM iam.app_user u "
                "WHERE u.status='ACTIVE' ORDER BY u.user_id"
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 2:
        raise RuntimeError("two active application users are required")
    principals = tuple(
        Principal(
            user_id=int(row["user_id"]),
            login_name=str(row["login_name"]),
            display_name=str(row["display_name"]),
            roles=(),
            permissions=frozenset({"TASK_CREATE", "DATASET_READ"}),
            department_code=row["department_code"],
        )
        for row in rows
    )
    return principals  # type: ignore[return-value]


def _upload_http_once(
    *,
    application: Any,
    barrier: Barrier,
    principal: Principal,
    file_name: str,
    payload: bytes,
    remark: str,
) -> dict[str, Any]:
    barrier.wait(timeout=15)
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/production/ft/uploads",
            headers={"X-V12-Test-User": str(principal.user_id)},
            files={
                "files": (
                    file_name,
                    payload,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"factory_code": "riyuexin", "remark": remark},
        )
    if response.status_code != 201:
        raise RuntimeError(
            f"HTTP upload failed: status={response.status_code} body={response.text[:1000]}"
        )
    return dict(response.json())


def _stage_claimed_job(
    engine: Engine,
    *,
    job: Job,
    format_profile_id: int,
    token: str,
) -> dict[str, Any]:
    if job.import_batch_id is None or not job.lease_token:
        raise RuntimeError("claimed INITIAL_IMPORT job is incomplete")
    stage_service = SqlStageDataService(engine)
    worker_batch = stage_service.worker_batch_info(job.import_batch_id)
    if len(worker_batch.files) != 1:
        raise RuntimeError("Worker did not resolve exactly one Receipt input")
    worker_input = Path(worker_batch.files[0].storage_uri).resolve()
    if not worker_input.is_file():
        raise RuntimeError("Worker resolved a missing Receipt snapshot")
    worker_digest = hashlib.sha256()
    with worker_input.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            worker_digest.update(chunk)
    if worker_digest.hexdigest() != worker_batch.files[0].expected_sha256:
        raise RuntimeError("Worker Receipt snapshot failed its SHA contract")
    stage_service.worker_mark_processing(
        job.import_batch_id,
        job.job_id,
        job.lease_token,
    )
    with engine.begin() as connection:
        batch = (
            connection.execute(
                text(
                    "SELECT owner_user_id FROM ingestion.import_batch "
                    "WHERE import_batch_id=:batch AND business_domain='PRODUCTION' "
                    "AND test_stage='FT' AND factory_code='riyuexin'"
                ),
                {"batch": job.import_batch_id},
            )
            .mappings()
            .one()
        )
        input_row = (
            connection.execute(
                text(
                    "SELECT TOP (1) ibf.import_batch_file_id,r.source_file_id "
                    "FROM ingestion.import_batch_file ibf "
                    "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                    "WHERE ibf.import_batch_id=:batch AND ibf.required_flag=1 "
                    "ORDER BY ibf.ordinal_no"
                ),
                {"batch": job.import_batch_id},
            )
            .mappings()
            .one()
        )
        summary = {
            "data_name": f"V12 duplicate full chain {token}",
            "product_name": None,
            "lot_id": f"V12-{token[:12]}",
            "wafer_count": None,
            "factory_code": "RIYUEXIN",
            "output_uri": f"verification://v12-route-a/{token}/{job.job_id}",
            "test_item_count": 1,
            "unit_count": 1,
            "pass_count": 1,
            "yield_rate": 1.0,
            "data_type": "FT",
            "artifacts": [],
        }
        run_id = int(
            connection.execute(
                text(
                    "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
                    "parser_version,canonical_model_version,status,is_current,row_count_input,"
                    "unit_count_output,measurement_count_output,started_at_utc,finished_at_utc,"
                    "metadata_json) OUTPUT INSERTED.processing_run_id VALUES("
                    ":job,:source,:profile,'v12-route-a-e2e','1.0','READY',0,1,1,1,"
                    "SYSUTCDATETIME(),SYSUTCDATETIME(),:metadata)"
                ),
                {
                    "job": job.job_id,
                    "source": int(input_row["source_file_id"]),
                    "profile": format_profile_id,
                    "metadata": json.dumps(
                        {"atomic_finalize_summary": summary}, ensure_ascii=False
                    ),
                },
            ).scalar_one()
        )
        dataset_id = int(
            connection.execute(
                text(
                    "INSERT dataset.dataset(dataset_code,dataset_name,dataset_type,"
                    "test_stage,owner_user_id) OUTPUT INSERTED.dataset_id VALUES("
                    ":code,:name,'FT_DETAIL','FT',:owner)"
                ),
                {
                    "code": f"V12-ROUTE-A-{token}-{job.job_id}",
                    "name": f"V12 duplicate full chain {token} {job.job_id}",
                    "owner": int(batch["owner_user_id"]),
                },
            ).scalar_one()
        )
        version_id = int(
            connection.execute(
                text(
                    "INSERT dataset.dataset_version(dataset_id,version_no,input_batch_id,"
                    "canonical_model_version,status,is_current,row_count,unit_count,"
                    "measurement_count) OUTPUT INSERTED.dataset_version_id VALUES("
                    ":dataset,1,:batch,'1.0','DRAFT',0,1,1,1)"
                ),
                {"dataset": dataset_id, "batch": job.import_batch_id},
            ).scalar_one()
        )
        connection.execute(
            text(
                "INSERT dataset.dataset_version_run(dataset_version_id,processing_run_id,"
                "run_role,ordinal_no) VALUES(:version,:run,'PRIMARY',1)"
            ),
            {"version": version_id, "run": run_id},
        )
        connection.execute(
            text(
                "INSERT ingestion.processing_run_input_file(processing_run_id,"
                "import_batch_file_id,lineage_basis) VALUES(:run,:file,'WRITER_VERIFIED')"
            ),
            {"run": run_id, "file": int(input_row["import_batch_file_id"])},
        )
        manifest = json.dumps(
            {
                "verification": "v12-duplicate-full-chain",
                "token": token,
                "job_id": job.job_id,
            },
            sort_keys=True,
        )
        connection.execute(
            text(
                "INSERT ingestion.initial_import_finalize_intent(job_id,import_batch_id,"
                "processing_run_id,dataset_version_id,input_manifest_sha256,"
                "input_manifest_json,status,staged_attempt_count,staged_at_utc) VALUES("
                ":job,:batch,:run,:version,:sha,:manifest,'STAGED',1,SYSUTCDATETIME())"
            ),
            {
                "job": job.job_id,
                "batch": job.import_batch_id,
                "run": run_id,
                "version": version_id,
                "sha": hashlib.sha256(manifest.encode()).hexdigest(),
                "manifest": manifest,
            },
        )
    return {
        "job": job,
        "run_id": run_id,
        "dataset_id": dataset_id,
        "version_id": version_id,
        "summary": summary,
        "worker_input": worker_input,
    }


def _finalize_one(engine: Engine, staged: Mapping[str, Any]) -> Job:
    job = staged["job"]
    result = SqlJobService(engine).finalize_initial_import(
        job_id=job.job_id,
        lease_token=job.lease_token,
        processing_run_id=int(staged["run_id"]),
        dataset_version_id=int(staged["version_id"]),
        summary=dict(staged["summary"]),
    )
    if result.status.value != "SUCCESS":
        raise RuntimeError("atomic finalizer did not complete the uploaded job")
    return result


def _process_claimed_job(
    engine: Engine,
    *,
    job: Job,
    format_profile_id: int,
    token: str,
) -> Job:
    staged = _stage_claimed_job(
        engine,
        job=job,
        format_profile_id=format_profile_id,
        token=token,
    )
    return _finalize_one(engine, staged)


def _full_chain_rows(
    connection: Connection, batch_ids: Sequence[int]
) -> list[Mapping[str, Any]]:
    return list(
        connection.execute(
            text(
                "SELECT b.import_batch_id,b.owner_user_id,b.status AS batch_status,"
                "b.business_domain,b.test_stage,b.factory_code,j.job_id,"
                "j.status AS job_status,r.receipt_id,r.is_duplicate_receipt,"
                "r.metadata_json,s.source_file_id,s.sha256,s.file_size,"
                "ibf.import_batch_file_id,pr.processing_run_id,pr.job_id AS run_job_id,"
                "pr.status AS run_status,pr.is_current AS run_current,d.dataset_id,"
                "d.owner_user_id AS dataset_owner_user_id,dv.dataset_version_id,"
                "dv.status AS version_status,dv.is_current AS version_current "
                "FROM ingestion.import_batch b "
                "JOIN ingestion.processing_job j ON j.import_batch_id=b.import_batch_id "
                "JOIN ingestion.import_batch_file ibf ON ibf.import_batch_id=b.import_batch_id "
                "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                "JOIN dataset.dataset_version dv ON dv.input_batch_id=b.import_batch_id "
                "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                "JOIN dataset.dataset_version_run dvr "
                "ON dvr.dataset_version_id=dv.dataset_version_id "
                "JOIN ingestion.processing_run pr "
                "ON pr.processing_run_id=dvr.processing_run_id "
                "JOIN ingestion.processing_run_input_file rif "
                "ON rif.processing_run_id=pr.processing_run_id "
                "AND rif.import_batch_file_id=ibf.import_batch_file_id "
                "WHERE b.import_batch_id IN(:first,:second) ORDER BY b.import_batch_id"
            ),
            {"first": batch_ids[0], "second": batch_ids[1]},
        )
        .mappings()
        .all()
    )


def _assert_full_chain(
    rows: Sequence[Mapping[str, Any]],
    *,
    principal_ids: set[int],
    expected_sha256: str,
    expected_size: int,
    upload_root: Path,
) -> tuple[Path, Path]:
    if len(rows) != 2:
        raise RuntimeError(f"expected two complete lineage rows, got {len(rows)}")
    identity_columns = (
        "import_batch_id",
        "job_id",
        "receipt_id",
        "processing_run_id",
        "dataset_id",
        "dataset_version_id",
    )
    for column in identity_columns:
        if len({int(row[column]) for row in rows}) != 2:
            raise RuntimeError(f"full-chain identity was reused: {column}")
    if len({int(row["source_file_id"]) for row in rows}) != 1:
        raise RuntimeError("same SHA did not converge on exactly one Source")
    if {str(row["sha256"]) for row in rows} != {expected_sha256}:
        raise RuntimeError("Source SHA does not match uploaded bytes")
    if {int(row["file_size"]) for row in rows} != {expected_size}:
        raise RuntimeError("Source size does not match uploaded bytes")
    if any(int(row["run_job_id"]) != int(row["job_id"]) for row in rows):
        raise RuntimeError("Processing Run is linked to the wrong upload Job")
    if sorted(bool(row["is_duplicate_receipt"]) for row in rows) != [False, True]:
        raise RuntimeError("Receipt duplicate flags are not first/subsequent")
    if {int(row["owner_user_id"]) for row in rows} != principal_ids:
        raise RuntimeError("the two production Batches did not retain their uploaders")
    if any(
        int(row["dataset_owner_user_id"]) != int(row["owner_user_id"]) for row in rows
    ):
        raise RuntimeError("Dataset owner does not match its upload Batch owner")
    for row in rows:
        if (
            row["business_domain"] != "PRODUCTION"
            or row["test_stage"] != "FT"
            or row["factory_code"] != "riyuexin"
            or row["batch_status"] != "PROCESSED"
            or row["job_status"] != "SUCCESS"
            or row["run_status"] != "PUBLISHED"
            or not bool(row["run_current"])
            or row["version_status"] != "PUBLISHED"
            or not bool(row["version_current"])
        ):
            raise RuntimeError(
                "one repeated production analysis is not Current+PUBLISHED"
            )
    paths = tuple(
        Path(json.loads(row["metadata_json"])["receipt_storage_uri"]).resolve()
        for row in rows
    )
    if len(set(paths)) != 2 or len({path.parent for path in paths}) != 2:
        raise RuntimeError("repeated uploads did not retain isolated snapshots")
    if any(not path.is_file() or upload_root not in path.parents for path in paths):
        raise RuntimeError("one receipt snapshot is missing or outside the test root")
    for path in paths:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise RuntimeError("one isolated Receipt snapshot changed on disk")
    return paths  # type: ignore[return-value]


def _cleanup_database(
    engine: Engine,
    *,
    token: str,
    sha256: str,
    expected_owner_ids: set[int],
) -> dict[str, int]:
    deleted: dict[str, int] = {}
    remark = f"v1.2 route-a duplicate full-chain {token}"
    with engine.begin() as connection:
        batches = (
            connection.execute(
                text(
                    "SELECT import_batch_id,owner_user_id,business_domain,test_stage,"
                    "factory_code,remark FROM ingestion.import_batch WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE remark=:remark"
                ),
                {"remark": remark},
            )
            .mappings()
            .all()
        )
        if len(batches) > 2:
            raise RuntimeError("cleanup guard found too many token-matched Batches")
        for row in batches:
            if (
                int(row["owner_user_id"]) not in expected_owner_ids
                or row["business_domain"] != "PRODUCTION"
                or row["test_stage"] != "FT"
                or row["factory_code"] != "riyuexin"
                or row["remark"] != remark
            ):
                raise RuntimeError("cleanup guard rejected a non-fixture Batch")
        batch_ids = [int(row["import_batch_id"]) for row in batches]
        if not batch_ids:
            connection.execute(
                text(
                    "DELETE s FROM ingestion.source_file s WHERE s.sha256=:sha "
                    "AND NOT EXISTS(SELECT 1 FROM ingestion.source_file_receipt r "
                    "WHERE r.source_file_id=s.source_file_id)"
                ),
                {"sha": sha256},
            )
            return deleted
        parameters = {
            f"batch_{index}": batch_id for index, batch_id in enumerate(batch_ids)
        }
        placeholders = ",".join(f":{name}" for name in parameters)
        source_rows = connection.execute(
            text(
                "SELECT DISTINCT s.source_file_id,s.sha256 "
                "FROM ingestion.source_file_receipt r "
                "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                f"WHERE r.import_batch_id IN({placeholders})"
            ),
            parameters,
        ).mappings()
        sources = list(source_rows)
        if len(sources) > 1 or any(str(row["sha256"]) != sha256 for row in sources):
            raise RuntimeError("cleanup guard found a non-fixture Source")
        job_ids = [
            int(value)
            for value in connection.execute(
                text(
                    "SELECT job_id FROM ingestion.processing_job "
                    f"WHERE import_batch_id IN({placeholders})"
                ),
                parameters,
            ).scalars()
        ]
        dataset_ids = [
            int(value)
            for value in connection.execute(
                text(
                    "SELECT DISTINCT dataset_id FROM dataset.dataset_version "
                    f"WHERE input_batch_id IN({placeholders})"
                ),
                parameters,
            ).scalars()
        ]
        job_parameters = {f"job_{index}": value for index, value in enumerate(job_ids)}
        job_placeholders = ",".join(f":{name}" for name in job_parameters)
        dataset_parameters = {
            f"dataset_{index}": value for index, value in enumerate(dataset_ids)
        }
        dataset_placeholders = ",".join(f":{name}" for name in dataset_parameters)
        if job_ids:
            deleted["audit"] = int(
                connection.execute(
                    text(
                        "DELETE FROM governance.audit_log WHERE correlation_id IN("
                        + ",".join(
                            f":correlation_{index}" for index in range(len(job_ids))
                        )
                        + ")"
                    ),
                    {
                        f"correlation_{index}": f"job:{job_id}"
                        for index, job_id in enumerate(job_ids)
                    },
                ).rowcount
            )
            for table in (
                "ingestion.processing_result_summary",
                "ingestion.initial_import_finalize_intent",
                "ingestion.processing_artifact",
            ):
                deleted[table] = int(
                    connection.execute(
                        text(
                            f"DELETE FROM {table} WHERE job_id IN({job_placeholders})"
                        ),
                        job_parameters,
                    ).rowcount
                )
            deleted["run_inputs"] = int(
                connection.execute(
                    text(
                        "DELETE rif FROM ingestion.processing_run_input_file rif "
                        "JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=rif.processing_run_id "
                        f"WHERE pr.job_id IN({job_placeholders})"
                    ),
                    job_parameters,
                ).rowcount
            )
        if dataset_ids:
            deleted["version_runs"] = int(
                connection.execute(
                    text(
                        "DELETE dvr FROM dataset.dataset_version_run dvr "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_version_id=dvr.dataset_version_id "
                        f"WHERE dv.dataset_id IN({dataset_placeholders})"
                    ),
                    dataset_parameters,
                ).rowcount
            )
            deleted["versions"] = int(
                connection.execute(
                    text(
                        "DELETE FROM dataset.dataset_version "
                        f"WHERE dataset_id IN({dataset_placeholders})"
                    ),
                    dataset_parameters,
                ).rowcount
            )
            deleted["datasets"] = int(
                connection.execute(
                    text(
                        f"DELETE FROM dataset.dataset WHERE dataset_id IN({dataset_placeholders})"
                    ),
                    dataset_parameters,
                ).rowcount
            )
        if job_ids:
            deleted["runs"] = int(
                connection.execute(
                    text(
                        f"DELETE FROM ingestion.processing_run WHERE job_id IN({job_placeholders})"
                    ),
                    job_parameters,
                ).rowcount
            )
            deleted["jobs"] = int(
                connection.execute(
                    text(
                        f"DELETE FROM ingestion.processing_job WHERE job_id IN({job_placeholders})"
                    ),
                    job_parameters,
                ).rowcount
            )
        deleted["batch_files"] = int(
            connection.execute(
                text(
                    f"DELETE FROM ingestion.import_batch_file WHERE import_batch_id IN({placeholders})"
                ),
                parameters,
            ).rowcount
        )
        deleted["receipts"] = int(
            connection.execute(
                text(
                    f"DELETE FROM ingestion.source_file_receipt WHERE import_batch_id IN({placeholders})"
                ),
                parameters,
            ).rowcount
        )
        deleted["batches"] = int(
            connection.execute(
                text(
                    f"DELETE FROM ingestion.import_batch WHERE import_batch_id IN({placeholders})"
                ),
                parameters,
            ).rowcount
        )
        deleted["sources"] = int(
            connection.execute(
                text(
                    "DELETE s FROM ingestion.source_file s WHERE s.sha256=:sha "
                    "AND NOT EXISTS(SELECT 1 FROM ingestion.source_file_receipt r "
                    "WHERE r.source_file_id=s.source_file_id)"
                ),
                {"sha": sha256},
            ).rowcount
        )
    return deleted


def _fixture_rows(connection: Connection, token: str, sha256: str) -> int:
    remark = f"v1.2 route-a duplicate full-chain {token}"
    return sum(
        (
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.import_batch WHERE remark=:remark"
                    ),
                    {"remark": remark},
                ).scalar_one()
            ),
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.source_file WHERE sha256=:sha"
                    ),
                    {"sha": sha256},
                ).scalar_one()
            ),
            int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset WHERE dataset_code LIKE :code"
                    ),
                    {"code": f"V12-ROUTE-A-{token}-%"},
                ).scalar_one()
            ),
        )
    )


def _safe_remove_upload_root(upload_root: Path, token: str) -> None:
    resolved = upload_root.resolve()
    expected_parent = Path(tempfile.gettempdir()).resolve()
    expected_name = f"tms-v12-route-a-{token}"
    if (
        resolved.parent != expected_parent
        or resolved.name != expected_name
        or not re.fullmatch(r"[0-9a-f]{32}", token)
    ):
        raise RuntimeError("refused unsafe upload-root cleanup")
    if resolved.exists():
        shutil.rmtree(resolved)
    if resolved.exists():
        raise RuntimeError("upload-root cleanup did not remove the exact fixture root")


def main() -> None:
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    _assert_database_identity(identity)
    engine = get_engine()
    token = uuid4().hex
    payload = f"v12-route-a-duplicate:{token}".encode()
    sha256 = hashlib.sha256(payload).hexdigest()
    file_name = f"v12-duplicate-{token}.xlsx"
    remark = f"v1.2 route-a duplicate full-chain {token}"
    upload_root = Path(tempfile.gettempdir()).resolve() / f"tms-v12-route-a-{token}"
    upload_root.mkdir(parents=False, exist_ok=False)
    previous_upload_root = os.environ.get("TMS_UPLOAD_ROOT")
    os.environ["TMS_UPLOAD_ROOT"] = str(upload_root)
    failure: BaseException | None = None
    full_chain_verified = False
    isolated_paths: tuple[Path, Path] | None = None
    principals: tuple[Principal, Principal] | None = None
    baseline: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    try:
        registry = SqlCleanerRegistry(engine)
        release = registry.latest_released("FT", "RIYUEXIN")
        with engine.connect() as connection:
            queue_busy = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
                        "WHERE job_type='INITIAL_IMPORT' AND status IN('QUEUED','RUNNING')"
                    )
                ).scalar_one()
            )
            if queue_busy:
                raise RuntimeError("INITIAL_IMPORT queue must be idle before this E2E")
            principals = _active_principals(connection)
            baseline = _counts(connection)
            if _fixture_rows(connection, token, sha256):
                raise RuntimeError("random fixture already exists")
        principal_by_id = {principal.user_id: principal for principal in principals}
        application = create_app()

        def test_principal(request: Request) -> Principal:
            raw_user_id = request.headers.get("X-V12-Test-User", "")
            try:
                return principal_by_id[int(raw_user_id)]
            except (KeyError, ValueError) as exc:
                raise RuntimeError(
                    "HTTP E2E request is missing its exact test user"
                ) from exc

        application.dependency_overrides[current_principal] = test_principal
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _upload_http_once,
                    application=application,
                    barrier=barrier,
                    principal=principal,
                    file_name=file_name,
                    payload=payload,
                    remark=remark,
                )
                for principal in principals
            ]
            responses = [future.result(timeout=30) for future in futures]
        batch_ids = [int(item["import_batch_id"]) for item in responses]
        job_ids = {int(item["job_id"]) for item in responses}
        if len(set(batch_ids)) != 2 or len(job_ids) != 2:
            raise RuntimeError("upload API reused a Batch or Job")
        queue = SqlJobService(engine)

        def handler(job: Job) -> Job:
            return _process_claimed_job(
                engine,
                job=job,
                format_profile_id=release.format_profile_id,
                token=token,
            )

        workers = [
            DatabaseJobWorker(
                queue,
                {JobType.INITIAL_IMPORT: handler},
                worker_id=f"v12-route-a-e2e-{index}",
                lease_for=timedelta(minutes=10),
                heartbeat_every=timedelta(minutes=1),
            )
            for index in range(2)
        ]
        completed = [worker.run_once() for worker in workers]
        if any(job is None or job.status != JobStatus.SUCCESS for job in completed):
            raise RuntimeError("SQL Worker did not complete both repeated uploads")
        if {job.job_id for job in completed if job is not None} != job_ids:
            raise RuntimeError("SQL Worker claimed a pre-existing or unexpected job")
        with engine.connect() as connection:
            rows = _full_chain_rows(connection, batch_ids)
            isolated_paths = _assert_full_chain(
                rows,
                principal_ids={principal.user_id for principal in principals},
                expected_sha256=sha256,
                expected_size=len(payload),
                upload_root=upload_root,
            )
        full_chain_verified = True
    except BaseException as exc:
        failure = exc
    finally:
        try:
            cleanup_counts = _cleanup_database(
                engine,
                token=token,
                sha256=sha256,
                expected_owner_ids=(
                    {principal.user_id for principal in principals}
                    if principals is not None
                    else set()
                ),
            )
            _safe_remove_upload_root(upload_root, token)
        except BaseException as cleanup_exc:
            if failure is not None:
                raise RuntimeError(
                    "full-chain check and exact cleanup both failed"
                ) from failure
            raise cleanup_exc
        finally:
            if previous_upload_root is None:
                os.environ.pop("TMS_UPLOAD_ROOT", None)
            else:
                os.environ["TMS_UPLOAD_ROOT"] = previous_upload_root

    with engine.connect() as connection:
        after = _counts(connection)
        leaked = _fixture_rows(connection, token, sha256)
        queue_busy_after = int(
            connection.execute(
                text(
                    "SELECT COUNT_BIG(*) FROM ingestion.processing_job "
                    "WHERE job_type='INITIAL_IMPORT' AND status IN('QUEUED','RUNNING')"
                )
            ).scalar_one()
        )
    if baseline != after or leaked or queue_busy_after:
        cleanup_error = RuntimeError(
            "full-chain cleanup did not restore the database: "
            f"count_drift={baseline != after}, fixture_rows={leaked}, "
            f"active_queue={queue_busy_after}"
        )
        if failure is not None:
            raise cleanup_error from failure
        raise cleanup_error
    if failure is not None:
        raise failure
    if not full_chain_verified or isolated_paths is None:
        raise RuntimeError("full-chain E2E produced no acceptance evidence")
    exact_minimum = {
        "batches": 2,
        "receipts": 2,
        "jobs": 2,
        "runs": 2,
        "datasets": 2,
        "versions": 2,
        "version_runs": 2,
        "sources": 1,
    }
    if any(cleanup_counts.get(name) != count for name, count in exact_minimum.items()):
        raise RuntimeError(f"unexpected exact cleanup counts: {cleanup_counts}")
    print(
        "v12_duplicate_upload_to_current=PASS http_uploads=2 sql_workers=2 "
        "users=2 sources=1 "
        "batches=2 receipts=2 jobs=2 runs=2 datasets=2 current_published=2"
    )
    print(
        "v12_duplicate_upload_filesystem=PASS snapshots=2 isolated_paths=true "
        "exact_root_cleanup=true"
    )
    print(
        "v12_duplicate_upload_cleanup=PASS database=TMS_G0_DEV schema=sql2014_0029 "
        "counts_restored=true fixture_rows=0 active_queue=0"
    )


if __name__ == "__main__":
    main()
