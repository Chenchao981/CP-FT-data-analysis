from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "g0" / "verify_v12_duplicate_upload_to_current_sql_e2e.py"


def test_full_chain_verifier_covers_upload_queue_finalize_and_current_dataset() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "client.post(" in source
    assert '"/api/v1/production/ft/uploads"' in source
    assert "TestClient(application)" in source
    assert "application.dependency_overrides[current_principal]" in source
    assert "ThreadPoolExecutor(max_workers=2)" in source
    assert "DatabaseJobWorker(" in source
    assert "worker.run_once" in source
    assert "minimal synthetic Canonical identity" in source
    assert "Real Cleaner parsing" in source
    assert "worker_batch_info(" in source
    assert "Worker Receipt snapshot failed its SHA contract" in source
    assert "worker_mark_processing(" in source
    assert "finalize_initial_import(" in source
    assert "pr.job_id AS run_job_id" in source
    assert "processing_run_input_file rif" in source
    assert "rif.import_batch_file_id=ibf.import_batch_file_id" in source
    assert "digest = hashlib.sha256()" in source
    assert 'path.open("rb")' in source
    assert 'row["run_status"] != "PUBLISHED"' in source
    assert 'row["version_status"] != "PUBLISHED"' in source
    assert 'not bool(row["run_current"])' in source
    assert 'not bool(row["version_current"])' in source


def test_full_chain_verifier_has_exact_database_and_filesystem_cleanup_guards() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'EXPECTED_DATABASE = "TMS_G0_DEV"' in source
    assert 'EXPECTED_SCHEMA_REVISION = "sql2014_0027"' in source
    assert "INITIAL_IMPORT queue must be idle" in source
    assert "expected_owner_ids" in source
    assert "refused unsafe upload-root cleanup" in source
    assert "resolved.parent != expected_parent" in source
    assert "resolved.name != expected_name" in source
    assert "shutil.rmtree(resolved)" in source
    assert "counts_restored=true fixture_rows=0 active_queue=0" in source
