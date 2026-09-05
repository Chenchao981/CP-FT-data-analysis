"""Verify FTP transactional and lease guards on an idle, exact development DB.

Creates one paused test source and observations. All simulated formal imports
roll back. Requires the local runtime stopped and no enabled FTP sources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.ftp_sources import FtpSourceCreate, RemoteFile, RemotePackage
from app.domain.stage_data import StoredUpload
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_auth_service import SqlAuthService
from app.infrastructure.sql_ftp_sources import SqlFtpSourceService
from app.infrastructure.sql_job_service import SqlJobService


def snapshot(engine):
    tables = ("ingestion.import_batch", "ingestion.source_file", "ingestion.source_file_receipt",
              "ingestion.import_batch_file", "ingestion.processing_job", "dataset.dataset",
              "test.test_run", "test.cp_die", "test.ft_device", "test.cp_measurement", "test.ft_measurement")
    with engine.connect() as connection:
        return {name: int(connection.execute(text(f"SELECT COUNT_BIG(*) FROM {name}")).scalar_one()) for name in tables}


def rejected(call, code):
    try:
        call()
    except DomainError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"Expected {code}")


def verify(output):
    identity = check_database()
    if identity["database"] != "TMS_G0_DEV" or identity["database_server"] != "WIN-0I8N01REB5K" or identity["schema_revision"] != "sql2014_0029":
        raise RuntimeError("SQL guard acceptance is restricted to the exact development DB")
    if (ROOT / "artifacts/runtime/local-test/processes.json").exists():
        raise RuntimeError("Stop the managed local runtime before this isolated guard acceptance")
    engine = get_engine()
    with engine.connect() as connection:
        if connection.execute(text("SELECT COUNT(*) FROM ingestion.source_definition s JOIN ingestion.ftp_collection_state c ON c.source_definition_id=s.source_definition_id WHERE s.active=1")).scalar_one():
            raise RuntimeError("Existing FTP sources must be paused before the guard acceptance")
    repository = SqlFtpSourceService(engine)
    admin = SqlAuthService(engine).principal_for_development()
    options = repository.options(admin)
    domain = next(row for row in options["domains"] if row["test_stage"] == "FT" and row["factory_code"] in {None, "RIYUEXIN"})
    release = next(row for row in options["releases"] if row["test_stage"] == "FT" and row["factory_code"] == "RIYUEXIN")
    code = "FTP_SQL_GUARD_" + uuid4().hex[:16].upper()
    config = FtpSourceCreate(source_code=code, source_name="开发 FTP 事务守卫验收（暂停）", protocol="FTP", host="127.0.0.1", port=1,
        remote_root="/", credential_ref=code, test_stage="FT", factory_code="RIYUEXIN", data_domain_id=domain["data_domain_id"],
        cleaner_release_id=release["cleaner_release_id"], package_mode="SINGLE_FILE", allowed_suffixes=[".xlsx"], stable_seconds=30)
    source_id = repository.create(admin, config)["source_definition_id"]
    before = snapshot(engine)
    evidence = dict(verification="RUNNING", database=identity, source_id=source_id, checks={})
    token = None
    try:
        repository.control(admin, source_id, active=True)
        claim = repository.claim("ftp-sql-guard-a")
        assert claim["source_id"] == source_id
        token = claim["token"]
        assert repository.claim("ftp-sql-guard-b") is None
        evidence["checks"]["exclusive_claim"] = True
        package = RemotePackage("guard.xlsx", (RemoteFile("guard.xlsx", 7, "20200101000000"),))
        assert not repository.observe(source_id, token, package, config)
        assert not repository.observe(source_id, token, package, config)
        # Age only this synthetic SQL observation; no external file is involved.
        with engine.begin() as connection:
            connection.execute(text("UPDATE ingestion.ftp_package SET first_observed_at_utc=DATEADD(second,-60,SYSUTCDATETIME()) WHERE source_definition_id=:id"), dict(id=source_id))
        assert repository.observe(source_id, token, package, config)
        evidence["checks"]["stable_observation_required"] = True
        local = ROOT / "artifacts/runtime/ftp-sql-guard" / code / "guard.xlsx"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"fixture")
        sha = hashlib.sha256(b"fixture").hexdigest()
        files = (StoredUpload("guard.xlsx", local, 7, sha, dict(source_root_code=code, purpose="FORMAL_IMPORT")),)

        def fail_after_receipts(service, connection, request):
            assert connection.execute(text("SELECT COUNT(*) FROM ingestion.source_file_receipt WHERE import_batch_id=:id"), dict(id=request.import_batch_id)).scalar_one() == 1
            raise RuntimeError("injected failure after source registration")

        with patch.object(SqlJobService, "_create_with_connection", fail_after_receipts):
            try:
                repository.submit(source_id, token, package, files, sha)
            except RuntimeError as exc:
                assert str(exc) == "injected failure after source registration"
            else:
                raise AssertionError("Failure injection did not run")
        assert snapshot(engine) == before
        assert repository.packages(admin, source_id)["items"][0]["job_id"] is None
        evidence["checks"]["batch_receipts_job_transaction_rollback"] = True
        rejected(lambda: repository.submit(source_id, str(uuid4()), package, files, sha), "FTP_LEASE_LOST")
        with engine.begin() as connection:
            connection.execute(text("UPDATE ingestion.ftp_collection_state SET lease_expires_at_utc=DATEADD(second,-1,SYSUTCDATETIME()),scan_requested=1 WHERE source_definition_id=:id"), dict(id=source_id))
        assert not repository.heartbeat(source_id, token)
        rejected(lambda: repository.submit(source_id, token, package, files, sha), "FTP_LEASE_LOST")
        successor = repository.claim("ftp-sql-guard-successor")
        assert successor["source_id"] == source_id and successor["token"] != token
        assert not repository.heartbeat(source_id, token)
        rejected(lambda: repository.submit(source_id, token, package, files, sha), "FTP_LEASE_LOST")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT status FROM ingestion.ftp_collection_run WHERE lease_token=:token"), dict(token=token)).scalar_one() == "INTERRUPTED"
        token = successor["token"]
        assert repository.heartbeat(source_id, token)
        evidence["checks"]["expired_and_replaced_lease_rejected"] = True
        repository.control(admin, source_id, active=False)
        rejected(lambda: repository.submit(source_id, token, package, files, sha), "FTP_LEASE_LOST")
        assert not repository.heartbeat(source_id, token)
        evidence["checks"]["pause_prevents_submission"] = True
        control_only = Principal(admin.user_id, admin.login_name, "control only", (), frozenset({"SOURCE_ADMIN"}))
        rejected(lambda: repository.packages(control_only, source_id), "FTP_SOURCE_NOT_FOUND")
        ungranted = Principal(-999, "ungranted", "ungranted", (), frozenset({"DATASET_READ"}))
        assert repository.list(ungranted) == []
        rejected(lambda: repository.packages(ungranted, source_id), "FTP_SOURCE_NOT_FOUND")
        evidence["checks"]["control_permission_does_not_grant_data_read"] = True
        after = snapshot(engine)
        assert before == after
        evidence.update(verification="PASS", before=before, after=after)
    finally:
        repository.control(admin, source_id, active=False)
        if token:
            repository.finish(source_id, token, config, discovered=1, submitted=0)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = verify(parser.parse_args().output)
    print(json.dumps(dict(verification=result["verification"], source_id=result["source_id"], checks=result["checks"])))
