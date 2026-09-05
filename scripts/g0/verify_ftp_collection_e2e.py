"""Use a local read-only FTP server and the managed Workers on TMS_G0_DEV.

Keeps the new acceptance source (paused), import and audit records. Copies an
existing approved input without changing it. Never uses an enterprise FTP password.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import shutil
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.testclient import TestClient
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer
from sqlalchemy import text
from app.api.dependencies import current_principal
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.ftp_credentials import store_ftp_credential
from app.infrastructure.sql_auth_service import SqlAuthService
from app.main import create_app


def checked(response, status=200):
    if response.status_code != status:
        error = response.json().get("error", {})
        raise RuntimeError(f"API {response.status_code}: {error.get('code')}: {error.get('message')}")
    return response.json()


def sample(engine):
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT TOP(100) r.metadata_json,s.canonical_storage_uri AS storage_uri,s.file_size FROM ingestion.import_batch b "
            "JOIN ingestion.source_file_receipt r ON r.import_batch_id=b.import_batch_id "
            "JOIN ingestion.source_file s ON s.source_file_id=r.source_file_id "
            "WHERE b.factory_code=N'riyuexin' AND b.test_stage='FT' AND b.status='PROCESSED' "
            "AND r.original_file_name LIKE N'%.xlsx' AND s.file_size>0 AND s.file_size<10485760 "
            "ORDER BY s.file_size,b.import_batch_id DESC"
        )).mappings().all()
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        path = Path(metadata.get("receipt_storage_uri") or row["storage_uri"])
        if path.is_file() and path.suffix.lower() == ".xlsx":
            return path
    raise RuntimeError("No retained, successfully processed Riyuexin XLSX is available")


def verify(output: Path):
    identity = check_database()
    if identity["database"] != "TMS_G0_DEV" or identity["database_server"] != "WIN-0I8N01REB5K" or identity["schema_revision"] != "sql2014_0029":
        raise RuntimeError("This acceptance is restricted to the verified development database/server")
    ready = json.loads((ROOT / "artifacts/runtime/local-test/ftp-worker.ready.json").read_text(encoding="utf-8-sig"))
    if any(ready[key] != identity[key] for key in ("database", "database_server", "schema_revision")):
        raise RuntimeError("Managed FTP collector must be ready on the same database")
    engine = get_engine()
    principal = SqlAuthService(engine).principal_for_development()
    app = create_app()
    app.dependency_overrides[current_principal] = lambda: principal
    original = sample(engine)
    with original.open("rb") as stream:
        original_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    reference = "FTP_ACCEPTANCE_" + uuid4().hex[:16].upper()
    remote = ROOT / "artifacts/runtime/ftp-acceptance" / reference
    remote.mkdir(parents=True, exist_ok=False)
    target = remote / original.parent.name / original.name
    target.parent.mkdir()
    shutil.copy2(original, target)
    # Age only the isolated copy; two database observations must still span 30 seconds.
    import os
    old = time.time() - 600
    os.utime(target, (old, old))
    password = uuid4().hex
    store_ftp_credential(reference, "acceptance-collector", password)
    authorizer = DummyAuthorizer()
    authorizer.add_user("acceptance-collector", password, str(remote), perm="elr")
    class Handler(FTPHandler):
        pass
    Handler.authorizer = authorizer
    logging.getLogger("pyftpdlib").setLevel(logging.ERROR)
    loop = IOLoop()
    server = FTPServer(("127.0.0.1", 0), Handler, ioloop=loop)
    thread = threading.Thread(target=server.serve_forever, kwargs=dict(timeout=0.05, blocking=True, handle_exit=False), daemon=True)
    thread.start()
    source_id = None
    evidence = dict(verification="RUNNING", database=identity, input_sha256=original_sha, input_bytes=target.stat().st_size,
                    managed_worker_id=ready["worker_id"], authentication="EXISTING_PRINCIPAL_TESTCLIENT", events=[])
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    started = time.monotonic()
    try:
        with TestClient(app) as client:
            options = checked(client.get("/api/v1/ftp-sources/options"))
            domain = next(item for item in options["domains"] if item["test_stage"] == "FT" and item["factory_code"] in {None, "RIYUEXIN"})
            release = next(item for item in options["releases"] if item["test_stage"] == "FT" and item["factory_code"] == "RIYUEXIN")
            payload = dict(source_code=reference, source_name="开发验收 FTP（本地只读）", protocol="FTP", host="127.0.0.1",
                port=server.socket.getsockname()[1], remote_root="/", credential_ref=reference, test_stage="FT", factory_code="RIYUEXIN",
                data_domain_id=domain["data_domain_id"], cleaner_release_id=release["cleaner_release_id"], package_mode="SINGLE_FILE",
                allowed_suffixes=[".xlsx"], stable_seconds=30, interval_seconds=30)
            created = checked(client.post("/api/v1/ftp-sources", json=payload), 201)
            source_id = created["source_definition_id"]
            assert not created["active"]
            evidence.update(source_id=source_id, source_code=reference, cleaner_release_id=release["cleaner_release_id"])
            checked(client.post(f"/api/v1/ftp-sources/{source_id}/connection-check"))
            checked(client.patch(f"/api/v1/ftp-sources/{source_id}/state", json=dict(active=True)))
            save()
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                page = checked(client.get(f"/api/v1/ftp-sources/{source_id}/packages"))
                if page["items"]:
                    item = page["items"][0]
                    state = (item["status"], item["job_status"])
                    if not evidence["events"] or evidence["events"][-1]["state"] != list(state):
                        evidence["events"].append(dict(state=list(state), elapsed_seconds=round(time.monotonic() - started, 3)))
                        save()
                    if item["status"] in {"FAILED", "CHANGED"} or item["job_status"] in {"FAILED", "NEEDS_INPUT", "CANCELLED"}:
                        raise RuntimeError(f"Acceptance task failed: {state}, job={item['job_id']}, code={item.get('error_code')}")
                    if item["job_status"] == "SUCCESS":
                        evidence.update(job_id=item["job_id"], import_batch_id=item["import_batch_id"])
                        break
                time.sleep(1)
            else:
                raise RuntimeError("Timed out waiting for the managed collection/cleaning Workers")
            with engine.connect() as connection:
                batch = connection.execute(text("SELECT access_scope,data_domain_id,source_definition_id,status FROM ingestion.import_batch WHERE import_batch_id=:id"), dict(id=evidence["import_batch_id"])).mappings().one()
                assert batch["access_scope"] == "DOMAIN" and batch["data_domain_id"] == domain["data_domain_id"] and batch["source_definition_id"] == source_id and batch["status"] == "PROCESSED"
                dataset_count = connection.execute(text("SELECT COUNT(*) FROM dataset.dataset WHERE source_definition_id=:id"), dict(id=source_id)).scalar_one()
                assert dataset_count >= 1
                evidence["formal_dataset_count"] = dataset_count

            def wait_for_scan():
                with engine.connect() as connection:
                    previous = connection.execute(text("SELECT MAX(collection_run_id) FROM ingestion.ftp_collection_run WHERE source_definition_id=:id"), dict(id=source_id)).scalar_one()
                checked(client.post(f"/api/v1/ftp-sources/{source_id}/scan"), 202)
                limit = time.monotonic() + 60
                while time.monotonic() < limit:
                    with engine.connect() as connection:
                        current = connection.execute(text("SELECT TOP(1) collection_run_id,status,worker_id FROM ingestion.ftp_collection_run WHERE source_definition_id=:id ORDER BY collection_run_id DESC"), dict(id=source_id)).mappings().one()
                    if current["collection_run_id"] > previous and current["status"] != "RUNNING":
                        assert current["status"] == "SUCCESS" and current["worker_id"] == ready["worker_id"]
                        return
                    time.sleep(0.5)
                raise RuntimeError("Additional collection scan timed out")

            wait_for_scan()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT COUNT(*) FROM ingestion.import_batch WHERE source_definition_id=:id"), dict(id=source_id)).scalar_one() == 1
                assert connection.execute(text("SELECT COUNT(*) FROM ingestion.ftp_package WHERE source_definition_id=:id AND job_id=:job"), dict(id=source_id, job=evidence["job_id"])).scalar_one() == 1
            evidence["duplicate_scan_no_new_import"] = True
            with target.open("ab") as stream:
                stream.write(b"acceptance-only-change")
            wait_for_scan()
            changed = checked(client.get(f"/api/v1/ftp-sources/{source_id}/packages"))["items"][0]
            assert changed["status"] == "CHANGED" and changed["job_id"] == evidence["job_id"]
            with original.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == original_sha
            evidence.update(verification="PASS", original_input_unchanged=True, changed_source_not_reimported=True,
                            elapsed_seconds=round(time.monotonic() - started, 3))
            save()
    except BaseException:
        evidence["verification"] = "FAILED"
        save()
        raise
    finally:
        if source_id is not None:
            app.state.ftp_source_service.control(principal, source_id, active=False)
        server.close_all()
        loop.close()
        thread.join(timeout=3)
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
        api.CredDeleteW(f"NCE_PYMS:FTP:{reference}", 1, 0)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.output)
    print(json.dumps(dict(verification=result["verification"], source_id=result["source_id"], job_id=result["job_id"])))
