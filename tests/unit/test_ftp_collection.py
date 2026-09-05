from __future__ import annotations

import ctypes
import ftplib
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer

from app.core.errors import DomainError
from app.domain.ftp_sources import FtpSourceCreate, RemoteFile, RemotePackage
from app.infrastructure.ftp_credentials import read_ftp_credential, store_ftp_credential
from app.infrastructure.ftp_storage import download_package, ftp_connection, read_mlsd, scan_packages
from app.workers.ftp_collection_worker import FtpCollectionWorker


def config(**changes):
    return FtpSourceCreate(**(dict(source_code="FTP_TEST", source_name="测试 FTP", protocol="FTP", host="127.0.0.1", remote_root="/",
        credential_ref="FTP_TEST", test_stage="FT", factory_code="RIYUEXIN", data_domain_id=1, cleaner_release_id=1,
        package_mode="SINGLE_FILE", allowed_suffixes=[".xlsx"], stable_seconds=30, interval_seconds=30) | changes))


@pytest.mark.parametrize("changes", [
    dict(host="ftp://example.com"), dict(host="user@example.com"), dict(host="example.com\r\nDELE x"),
    dict(remote_root="../unsafe"), dict(remote_root="/a/../b"), dict(credential_ref="../SECRET"),
    dict(protocol="SFTP"), dict(factory_code="UNKNOWN"), dict(test_stage="CP"),
    dict(allowed_suffixes=[".csv"]), dict(allowed_suffixes=[".xlsx", ".xlsx"]),
    dict(package_mode="DIRECTORY"), dict(package_mode="DIRECTORY", ready_marker="../DONE"),
    dict(package_mode="DIRECTORY", ready_marker="done.xlsx"), dict(ready_marker="READY"),
    dict(stable_seconds=0), dict(password="must-not-be-a-config-field"),
])
def test_config_rejects_ambiguous_or_unsafe_sources(changes):
    with pytest.raises(ValidationError):
        config(**changes)


@pytest.fixture
def live_ftp(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "批次一").mkdir()
    source = remote / "批次一" / "结果.xlsx"
    source.write_bytes(b"approved-input-copy")
    old = (datetime.now(UTC) - timedelta(minutes=10)).timestamp()
    os.utime(source, (old, old))
    authorizer = DummyAuthorizer()
    password = uuid4().hex
    authorizer.add_user("test-collector", password, str(remote), perm="elr")
    class Handler(FTPHandler):
        pass
    Handler.authorizer = authorizer
    loop = IOLoop()
    server = FTPServer(("127.0.0.1", 0), Handler, ioloop=loop)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, kwargs=dict(timeout=0.05, blocking=True, handle_exit=False), daemon=True)
    thread.start()
    try:
        yield config(port=port), lambda reference: ("test-collector", password), remote, source
    finally:
        server.close_all()
        loop.close()
        thread.join(timeout=3)


def test_real_ftp_unicode_listing_streaming_hash_and_read_only_source(live_ftp, tmp_path):
    cfg, credential, remote, source = live_ftp
    original = source.read_bytes()
    with ftp_connection(cfg, credential) as ftp:
        packages = scan_packages(ftp, cfg)
        assert len(packages) == 1 and packages[0].path == "批次一/结果.xlsx"
        assert packages[0].old_enough(datetime.now(UTC), 30)
        target, stored, digest = download_package(ftp, cfg, packages[0], tmp_path / "uploads", domain_code="TEST")
        assert stored[0].path.read_bytes() == original
        assert stored[0].path.parent.name == "批次一" and len(digest) == 64
        assert scan_packages(ftp, cfg, selected_path=packages[0].path) == packages
        with pytest.raises(ftplib.error_perm):
            ftp.delete("批次一/结果.xlsx")
    assert source.read_bytes() == original


def test_directory_needs_completion_marker_and_preserves_package_identity(live_ftp):
    cfg, credential, remote, source = live_ftp
    cfg = cfg.model_copy(update=dict(package_mode="DIRECTORY", ready_marker="_READY"))
    with ftp_connection(cfg, credential) as ftp:
        incomplete = scan_packages(ftp, cfg)[0]
        assert incomplete.path == "批次一" and not incomplete.complete
        assert not incomplete.old_enough(datetime.now(UTC), 30)
        marker = remote / "批次一" / "_READY"
        marker.touch()
        old = (datetime.now(UTC) - timedelta(minutes=10)).timestamp()
        os.utime(marker, (old, old))
        complete = scan_packages(ftp, cfg)[0]
        assert complete.complete and complete.old_enough(datetime.now(UTC), 30)
        assert complete.fingerprint != incomplete.fingerprint
        assert len(complete.files) == 1


def test_authentication_failure_does_not_expose_password(live_ftp):
    cfg, _, _, _ = live_ftp
    secret = "synthetic-secret-should-never-be-reported"
    with pytest.raises(DomainError) as captured:
        with ftp_connection(cfg, lambda ref: ("test-collector", secret)):
            pytest.fail("authentication must fail")
    assert secret not in captured.value.message


@pytest.mark.parametrize("name", ["../escape.xlsx", "evil\\escape.xlsx", "CON.xlsx", "x.xlsx ", "bad\n.xlsx", "/absolute.xlsx"])
def test_remote_names_cannot_escape_local_snapshot(name):
    class Remote:
        def retrlines(self, command, callback):
            callback(f"type=file;size=3;modify=20260901000000; {name}")
    with pytest.raises(DomainError, match="FTP"):
        scan_packages(Remote(), config())


def test_scan_limits_are_applied_during_wire_read_and_case_collisions_fail():
    class Remote:
        def retrlines(self, command, callback):
            callback("type=file;size=3;modify=20260901000000; A.xlsx")
            callback("type=file;size=3;modify=20260901000000; a.xlsx")
            pytest.fail("reading must stop at the bound")
    with pytest.raises(DomainError) as bounded:
        read_mlsd(Remote(), ".", limit=1)
    assert bounded.value.code == "FTP_ENTRY_LIMIT"
    class Collision:
        def retrlines(self, command, callback):
            callback("type=file;size=3;modify=20260901000000; A.xlsx")
            callback("type=file;size=3;modify=20260901000000; a.xlsx")
    with pytest.raises(DomainError) as collision:
        scan_packages(Collision(), config())
    assert collision.value.code == "FTP_PATH_COLLISION"


def test_partial_download_is_removed_and_does_not_return_a_receipt(tmp_path):
    class Remote:
        def retrbinary(self, command, callback, **kwargs):
            callback(b"ab")
    package = RemotePackage("one.xlsx", (RemoteFile("one.xlsx", 3, "20260901000000"),))
    with pytest.raises(DomainError) as captured:
        download_package(Remote(), config(), package, tmp_path, domain_code="TEST")
    assert captured.value.code == "FTP_SOURCE_CHANGED"
    assert list((tmp_path / "engineering" / "ft").iterdir()) == []


def test_ftps_requires_verified_context_and_protected_data_channel(monkeypatch):
    calls = []
    class Tls:
        def __init__(self, *, context, **kwargs):
            assert context.check_hostname and context.verify_mode != 0
        def connect(self, *args): calls.append("connect")
        def login(self, *args): calls.append("login")
        def prot_p(self): calls.append("protected-data")
        def set_pasv(self, value): assert value
        def cwd(self, path): calls.append("root")
        def close(self): calls.append("close")
    monkeypatch.setattr(ftplib, "FTP_TLS", Tls)
    with ftp_connection(config(protocol="FTPS"), lambda ref: ("user", "synthetic-password")):
        pass
    assert calls == ["connect", "login", "protected-data", "root", "close"]


def test_worker_stop_does_not_claim_or_connect(tmp_path):
    class Repository:
        def claim(self, worker): pytest.fail("must not claim after stop")
    worker = FtpCollectionWorker(Repository(), lambda ref: pytest.fail("must not read credentials"), tmp_path, "worker", should_stop=lambda: True)
    assert worker.run_once() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager contract")
def test_windows_credential_roundtrip_uses_reference_only():
    ref = "FTP_TEST_" + uuid4().hex.upper()
    password = uuid4().hex
    try:
        store_ftp_credential(ref, "collector-test-user", password)
        assert read_ftp_credential(ref) == ("collector-test-user", password)
    finally:
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
        api.CredDeleteW(f"NCE_PYMS:FTP:{ref}", 1, 0)


@pytest.mark.parametrize("reference", ["../unsafe", "prefix:other-secret", "a", "FTP\nOTHER"])
def test_credentials_reject_uncontrolled_references(reference):
    with pytest.raises(DomainError) as captured:
        read_ftp_credential(reference)
    assert captured.value.code == "FTP_CREDENTIAL_REF_INVALID"
