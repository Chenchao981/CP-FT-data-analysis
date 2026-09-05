from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_lot_input_migration_is_sql2014_safe_and_keeps_test_run_nullable() -> None:
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0014_lot_input_resolution_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    assert "'NEEDS_INPUT'" in sql
    assert "CREATE TABLE ingestion.processing_input_request" in sql
    assert "field_code='LOT_ID'" in sql
    assert "status IN('OPEN','RESOLVED')" in sql
    assert "CK_import_batch_status" in sql
    assert "resolved_enrichment_id bigint NULL" in sql
    assert "resolved_by bigint NULL" in sql
    assert "CK_processing_input_request_resolution" in sql
    assert "UX_processing_input_request_open" in sql
    assert (
        "ON ingestion.processing_input_request(import_batch_id,receipt_id,field_code)"
        in sql
    )
    assert "FK_job_parent" in sql
    assert "ALTER COLUMN lot_id" not in sql
    assert "ISJSON" not in sql
    assert "OPENJSON" not in sql


def test_existing_release_bootstrap_requires_current_atomic_schema() -> None:
    script = (
        ROOT / "scripts" / "g0" / "bootstrap_existing_cleaner_releases.py"
    ).read_text(encoding="utf-8-sig")
    assert 'revision != "sql2014_0027"' in script
    assert "Cleaner version checksum collision" in script
    assert "Cleaner Release rows are immutable" in script
    assert "UPDATE ingestion.cleaner_release SET" not in script
