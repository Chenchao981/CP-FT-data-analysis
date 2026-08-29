from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sql() -> str:
    return (
        ROOT
        / "db"
        / "alembic"
        / "sql"
        / "0015_atomic_initial_import_finalize_sql2014.sql"
    ).read_text(encoding="utf-8-sig")


def test_atomic_finalize_revision_extends_0014() -> None:
    wrapper = (
        ROOT
        / "db"
        / "alembic"
        / "versions"
        / "sql2014_0015_atomic_initial_import_finalize.py"
    ).read_text(encoding="utf-8-sig")
    assert 'revision = "sql2014_0015"' in wrapper
    assert 'down_revision = "sql2014_0014"' in wrapper
    assert 'run_sql_file("0015_atomic_initial_import_finalize_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_atomic_finalize_protocol_is_opt_in_and_preserves_legacy_jobs() -> None:
    sql = _sql()
    assert "finalize_protocol varchar(16) NOT NULL" in sql
    assert "DEFAULT('LEGACY')" in sql
    assert "WITH VALUES" in sql
    assert "finalize_protocol IN('LEGACY','ATOMIC_V1')" in sql
    assert "job_type<>'INITIAL_IMPORT' AND finalize_protocol='LEGACY'" in sql
    assert "CK_processing_job_finalize_protocol" in sql
    assert "drain active INITIAL_IMPORT jobs before enabling ATOMIC_V1" in sql


def test_processing_run_input_file_has_minimal_verified_lineage_contract() -> None:
    sql = _sql()
    assert "CREATE TABLE ingestion.processing_run_input_file" in sql
    assert "processing_run_id bigint NOT NULL" in sql
    assert "import_batch_file_id bigint NOT NULL" in sql
    assert "lineage_basis varchar(32) NOT NULL" in sql
    assert "lineage_basis IN('WRITER_VERIFIED','LEGACY_BATCH_MEMBERSHIP')" in sql
    assert "PK_processing_run_input_file" in sql
    assert "FK_processing_run_input_file_run" in sql
    assert "FK_processing_run_input_file_batch_file" in sql
    assert "IX_processing_run_input_file_batch_file" in sql
    assert "ON ingestion.processing_run_input_file(import_batch_file_id,processing_run_id)" in sql
    assert "INSERT INTO ingestion.processing_run_input_file" not in sql


def test_finalize_intent_has_recovery_evidence_and_terminal_invariants() -> None:
    sql = _sql()
    assert "CREATE TABLE ingestion.initial_import_finalize_intent" in sql
    assert "PK_initial_import_finalize_intent PRIMARY KEY CLUSTERED(job_id)" in sql
    assert "input_manifest_sha256 char(64) NOT NULL" in sql
    assert "input_manifest_json nvarchar(max) NOT NULL" in sql
    assert "status IN('STAGED','FINALIZED','ABORTED')" in sql
    assert "staged_attempt_count BETWEEN 1 AND 20" in sql
    assert "finalized_lease_token uniqueidentifier NULL" in sql
    assert "row_version rowversion NOT NULL" in sql
    assert "UQ_initial_import_finalize_run" in sql
    assert "UQ_initial_import_finalize_version" in sql
    assert "CK_initial_import_finalize_terminal" in sql
    assert "IX_initial_import_finalize_recovery" in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql


def test_atomic_finalize_sql_fails_closed_on_incompatible_partial_schema() -> None:
    sql = _sql()
    assert "sql2014_0015 blocked: ingestion.processing_job is missing." in sql
    assert "has an incompatible definition" in sql
    assert "has an incompatible column contract" in sql
    assert "constraints are incomplete" in sql
