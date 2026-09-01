from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.existing_cleaner_runner import CleanerArtifact

FINALIZE_CONTEXT_NOT_FOUND = "FINALIZE_CONTEXT_NOT_FOUND"
FINALIZE_JOB_BATCH_MISMATCH = "FINALIZE_JOB_BATCH_MISMATCH"
FINALIZE_JOB_NOT_RUNNING = "FINALIZE_JOB_NOT_RUNNING"
FINALIZE_BATCH_NOT_PROCESSING = "FINALIZE_BATCH_NOT_PROCESSING"
FINALIZE_PROTOCOL_REQUIRED = "FINALIZE_PROTOCOL_REQUIRED"
FINALIZE_LEASE_MISMATCH = "FINALIZE_LEASE_MISMATCH"
FINALIZE_LEASE_EXPIRED = "FINALIZE_LEASE_EXPIRED"
FINALIZE_INPUT_MANIFEST_INVALID = "FINALIZE_INPUT_MANIFEST_INVALID"
FINALIZE_FINGERPRINT_MISMATCH = "FINALIZE_FINGERPRINT_MISMATCH"
FINALIZE_INTENT_STATE_INVALID = "FINALIZE_INTENT_STATE_INVALID"


class InitialImportStageError(ValueError):
    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        super().__init__(f"{error_code}: {detail}")


@dataclass(frozen=True, slots=True)
class ExistingStagedImport:
    processing_run_id: int
    dataset_id: int
    dataset_version_id: int
    dataset_version_no: int
    spec_set_id: int | None
    unit_count: int
    measurement_count: int


@dataclass(frozen=True, slots=True)
class AtomicStagePreparation:
    context: Mapping[str, Any]
    source_file_id: int
    import_batch_file_ids: tuple[int, ...]
    input_manifest_sha256: str
    input_manifest_json: str
    existing: ExistingStagedImport | None


def _normalized_lease_token(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise InitialImportStageError(
            FINALIZE_LEASE_MISMATCH, "lease_token is not a valid UUID"
        ) from exc


def _cleaner_manifest(artifacts: tuple[CleanerArtifact, ...]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for artifact in artifacts:
        role = artifact.role.strip()
        sha256 = artifact.sha256.strip().lower()
        if (
            not role
            or artifact.size_bytes < 0
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise InitialImportStageError(
                FINALIZE_INPUT_MANIFEST_INVALID,
                "Cleaner artifact role, size or sha256 is invalid",
            )
        normalized.append(
            {
                "role": role,
                "size_bytes": int(artifact.size_bytes),
                "sha256": sha256,
            }
        )
    if not normalized:
        raise InitialImportStageError(
            FINALIZE_INPUT_MANIFEST_INVALID, "Cleaner artifact manifest is empty"
        )
    return sorted(
        normalized,
        key=lambda item: (
            str(item["role"]).casefold(),
            str(item["sha256"]),
            int(item["size_bytes"]),
        ),
    )


def prepare_atomic_stage(
    connection: Connection,
    *,
    job_id: int,
    import_batch_id: int,
    lease_token: str,
    artifacts: tuple[CleanerArtifact, ...],
) -> AtomicStagePreparation:
    """Lock and verify the leased job, then build its stable input fingerprint."""

    normalized_lease = _normalized_lease_token(lease_token)
    context = (
        connection.execute(
            text(
                "SELECT b.import_batch_id,b.owner_user_id,b.business_domain,b.test_stage,b.factory_code,"
                "b.access_scope,b.data_domain_id,b.source_definition_id,"
                "b.status AS batch_status,j.import_batch_id AS job_import_batch_id,"
                "j.status AS job_status,j.cleaner_release_id,j.finalize_protocol,j.lease_token,"
                "j.lease_expires_at_utc,j.attempt_count,cr.output_contract_version,"
                "CASE WHEN j.lease_expires_at_utc>SYSUTCDATETIME() THEN 1 ELSE 0 END "
                "AS lease_is_live "
                "FROM ingestion.processing_job j "
                "JOIN ingestion.import_batch b "
                "ON b.import_batch_id=j.import_batch_id "
                "JOIN ingestion.cleaner_release cr ON cr.cleaner_release_id=j.cleaner_release_id "
                "WHERE j.job_id=:job"
            ),
            {"job": job_id},
        )
        .mappings()
        .one_or_none()
    )
    if context is None:
        raise InitialImportStageError(
            FINALIZE_CONTEXT_NOT_FOUND, "initial import job context was not found"
        )
    if int(context["job_import_batch_id"]) != import_batch_id:
        raise InitialImportStageError(
            FINALIZE_JOB_BATCH_MISMATCH,
            "job does not belong to the requested import batch",
        )
    if context["job_status"] != "RUNNING":
        raise InitialImportStageError(
            FINALIZE_JOB_NOT_RUNNING, "initial import job must be RUNNING"
        )
    if context["batch_status"] != "PROCESSING":
        raise InitialImportStageError(
            FINALIZE_BATCH_NOT_PROCESSING, "import batch must be PROCESSING"
        )
    if context["finalize_protocol"] != "ATOMIC_V1":
        raise InitialImportStageError(
            FINALIZE_PROTOCOL_REQUIRED, "job does not use ATOMIC_V1"
        )
    if not 1 <= int(context["attempt_count"]) <= 20:
        raise InitialImportStageError(
            FINALIZE_INTENT_STATE_INVALID, "job attempt_count is outside 1..20"
        )
    if _normalized_lease_token(context["lease_token"]) != normalized_lease:
        raise InitialImportStageError(
            FINALIZE_LEASE_MISMATCH, "lease_token does not own the RUNNING job"
        )
    if not bool(context["lease_is_live"]):
        raise InitialImportStageError(
            FINALIZE_LEASE_EXPIRED,
            "lease_token is no longer live for the RUNNING job",
        )

    input_rows = (
        connection.execute(
            text(
                "SELECT ibf.import_batch_file_id,ibf.ordinal_no,ibf.file_role,"
                "ibf.receipt_id,r.source_file_id,s.sha256 "
                "FROM ingestion.import_batch_file ibf WITH (HOLDLOCK) "
                "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
                "WHERE ibf.import_batch_id=:batch "
                "ORDER BY ibf.ordinal_no,ibf.import_batch_file_id"
            ),
            {"batch": import_batch_id},
        )
        .mappings()
        .all()
    )
    if not input_rows:
        raise InitialImportStageError(
            FINALIZE_INPUT_MANIFEST_INVALID, "import batch has no input files"
        )
    batch_files: list[dict[str, object]] = []
    for row in input_rows:
        sha256 = str(row["sha256"] or "").strip().lower()
        if len(sha256) != 64 or any(
            character not in "0123456789abcdef" for character in sha256
        ):
            raise InitialImportStageError(
                FINALIZE_INPUT_MANIFEST_INVALID,
                "import batch contains a source file without a valid sha256",
            )
        batch_files.append(
            {
                "import_batch_file_id": int(row["import_batch_file_id"]),
                "ordinal_no": int(row["ordinal_no"]),
                "file_role": str(row["file_role"]),
                "receipt_id": int(row["receipt_id"]),
                "source_file_id": int(row["source_file_id"]),
                "sha256": sha256,
            }
        )
    manifest = {
        "schema_version": "INITIAL_IMPORT_INPUT_MANIFEST_V1",
        "import_batch_id": import_batch_id,
        "batch_files": batch_files,
        "cleaner_artifacts": _cleaner_manifest(artifacts),
    }
    manifest_json = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    fingerprint = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

    existing_row = (
        connection.execute(
            text(
                "SELECT i.status,i.input_manifest_sha256,i.processing_run_id,"
                "i.dataset_version_id,dv.dataset_id,dv.version_no,dv.spec_set_id,"
                "dv.unit_count,dv.measurement_count,dv.status AS version_status,"
                "dv.is_current,pr.status AS run_status "
                "FROM ingestion.initial_import_finalize_intent i WITH (UPDLOCK,HOLDLOCK) "
                "JOIN dataset.dataset_version dv ON dv.dataset_version_id=i.dataset_version_id "
                "JOIN ingestion.processing_run pr ON pr.processing_run_id=i.processing_run_id "
                "WHERE i.job_id=:job"
            ),
            {"job": job_id},
        )
        .mappings()
        .one_or_none()
    )
    existing = None
    if existing_row is not None:
        if str(existing_row["input_manifest_sha256"]).lower() != fingerprint:
            raise InitialImportStageError(
                FINALIZE_FINGERPRINT_MISMATCH,
                "existing staged write has a different input fingerprint",
            )
        if (
            existing_row["status"] != "STAGED"
            or existing_row["version_status"] != "DRAFT"
            or bool(existing_row["is_current"])
            or existing_row["run_status"] != "READY"
        ):
            raise InitialImportStageError(
                FINALIZE_INTENT_STATE_INVALID,
                "existing intent is not a reusable STAGED write",
            )
        connection.execute(
            text(
                "UPDATE ingestion.initial_import_finalize_intent "
                "SET staged_attempt_count=CASE WHEN staged_attempt_count<:attempt "
                "THEN :attempt ELSE staged_attempt_count END "
                "WHERE job_id=:job AND status='STAGED'"
            ),
            {"job": job_id, "attempt": int(context["attempt_count"])},
        )
        existing = ExistingStagedImport(
            processing_run_id=int(existing_row["processing_run_id"]),
            dataset_id=int(existing_row["dataset_id"]),
            dataset_version_id=int(existing_row["dataset_version_id"]),
            dataset_version_no=int(existing_row["version_no"]),
            spec_set_id=(
                int(existing_row["spec_set_id"])
                if existing_row["spec_set_id"] is not None
                else None
            ),
            unit_count=int(existing_row["unit_count"]),
            measurement_count=int(existing_row["measurement_count"]),
        )
    return AtomicStagePreparation(
        context=context,
        source_file_id=int(batch_files[0]["source_file_id"]),
        import_batch_file_ids=tuple(
            int(item["import_batch_file_id"]) for item in batch_files
        ),
        input_manifest_sha256=fingerprint,
        input_manifest_json=manifest_json,
        existing=existing,
    )


def insert_draft_dataset_version(
    connection: Connection,
    *,
    dataset_id: int,
    import_batch_id: int,
    unit_count: int,
    measurement_count: int,
    spec_set_id: int | None,
    metadata_json: str,
) -> tuple[int, int]:
    version_no = int(
        connection.execute(
            text(
                "SELECT ISNULL(MAX(version_no),0)+1 FROM dataset.dataset_version "
                "WITH (UPDLOCK,HOLDLOCK) WHERE dataset_id=:dataset"
            ),
            {"dataset": dataset_id},
        ).scalar_one()
    )
    version_id = int(
        connection.execute(
            text(
                "INSERT dataset.dataset_version(dataset_id,version_no,input_batch_id,"
                "canonical_model_version,status,is_current,row_count,unit_count,"
                "measurement_count,published_by,published_at_utc,"
                "supersedes_dataset_version_id,spec_set_id,metadata_json) "
                "OUTPUT INSERTED.dataset_version_id "
                "VALUES(:dataset,:version,:batch,'1.0','DRAFT',0,:units,:units,"
                ":measurements,NULL,NULL,NULL,:spec,:metadata)"
            ),
            {
                "dataset": dataset_id,
                "version": version_no,
                "batch": import_batch_id,
                "units": unit_count,
                "measurements": measurement_count,
                "spec": spec_set_id,
                "metadata": metadata_json,
            },
        ).scalar_one()
    )
    return version_id, version_no


def record_atomic_stage(
    connection: Connection,
    *,
    job_id: int,
    import_batch_id: int,
    processing_run_id: int,
    dataset_version_id: int,
    preparation: AtomicStagePreparation,
) -> None:
    fenced = connection.execute(
        text(
            "UPDATE j SET heartbeat_at_utc=heartbeat_at_utc "
            "FROM ingestion.processing_job j WITH (UPDLOCK,HOLDLOCK) "
            "JOIN ingestion.import_batch b WITH (UPDLOCK,HOLDLOCK) "
            "ON b.import_batch_id=j.import_batch_id "
            "WHERE j.job_id=:job AND j.import_batch_id=:batch "
            "AND j.status='RUNNING' AND j.finalize_protocol='ATOMIC_V1' "
            "AND j.lease_token=CONVERT(uniqueidentifier,:lease) "
            "AND j.lease_expires_at_utc>SYSUTCDATETIME() "
            "AND b.status='PROCESSING'"
        ),
        {
            "job": job_id,
            "batch": import_batch_id,
            "lease": str(preparation.context["lease_token"]),
        },
    )
    if fenced.rowcount != 1:
        raise InitialImportStageError(
            FINALIZE_LEASE_EXPIRED,
            "lease or Batch state changed before the staged write could commit",
        )
    connection.execute(
        text(
            "INSERT dataset.dataset_version_run(dataset_version_id,processing_run_id,"
            "run_role,ordinal_no) VALUES(:version,:processing,'PRIMARY',1)"
        ),
        {"version": dataset_version_id, "processing": processing_run_id},
    )
    connection.execute(
        text(
            "INSERT ingestion.processing_run_input_file(processing_run_id,"
            "import_batch_file_id,lineage_basis) "
            "VALUES(:processing,:batch_file,'WRITER_VERIFIED')"
        ),
        [
            {"processing": processing_run_id, "batch_file": batch_file_id}
            for batch_file_id in preparation.import_batch_file_ids
        ],
    )
    connection.execute(
        text(
            "UPDATE ingestion.processing_artifact SET processing_run_id=:processing "
            "WHERE job_id=:job"
        ),
        {"processing": processing_run_id, "job": job_id},
    )
    connection.execute(
        text(
            "INSERT ingestion.initial_import_finalize_intent(job_id,import_batch_id,"
            "processing_run_id,dataset_version_id,input_manifest_sha256,"
            "input_manifest_json,status,staged_attempt_count,staged_at_utc) "
            "VALUES(:job,:batch,:processing,:version,:fingerprint,:manifest,'STAGED',:attempt,"
            "SYSUTCDATETIME())"
        ),
        {
            "job": job_id,
            "batch": import_batch_id,
            "processing": processing_run_id,
            "version": dataset_version_id,
            "fingerprint": preparation.input_manifest_sha256,
            "manifest": preparation.input_manifest_json,
            "attempt": int(preparation.context["attempt_count"]),
        },
    )
