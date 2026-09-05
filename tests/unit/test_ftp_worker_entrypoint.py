from types import SimpleNamespace

import pytest

from scripts import run_ftp_collection_worker as entry


def configure(tmp_path, monkeypatch, **metadata_changes):
    ready = tmp_path / "ftp.ready.json"
    ready.write_text("stale", encoding="ascii")
    monkeypatch.setattr(entry.sys, "argv", ["worker", "--once", "--worker-id", "ftp-test",
        "--ready-file", str(ready), "--expected-database", "TMS_G0_DEV",
        "--expected-schema-revision", "sql2014_0029", "--expected-database-server", "SQL-TEST"])
    metadata = dict(database="TMS_G0_DEV", schema_revision="sql2014_0029", database_server="SQL-TEST") | metadata_changes
    monkeypatch.setattr(entry, "check_database", lambda: metadata)
    monkeypatch.setattr(entry, "configure_logging", lambda: None)
    return ready


@pytest.mark.parametrize("field,value", [
    ("database", "PRODUCTION"), ("schema_revision", "sql2014_0028"), ("database_server", "OTHER"),
])
def test_rejects_wrong_identity_before_creating_worker(tmp_path, monkeypatch, field, value):
    ready = configure(tmp_path, monkeypatch, **{field: value})
    monkeypatch.setattr(entry, "get_engine", lambda: pytest.fail("must not create worker"))
    with pytest.raises(RuntimeError, match="database identity rejected"):
        entry.main()
    assert not ready.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_ready_removed_after_one_scan_or_failure(tmp_path, monkeypatch, fail):
    ready = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(entry, "get_engine", lambda: object())
    monkeypatch.setattr(entry, "_upload_root", lambda: tmp_path)
    calls = []

    def process():
        assert ready.is_file()
        calls.append("scan")
        if fail:
            raise RuntimeError("queue unavailable")
        return None

    monkeypatch.setattr(entry, "FtpCollectionWorker", lambda *a, **kw: SimpleNamespace(run_once=process))
    if fail:
        with pytest.raises(RuntimeError, match="queue unavailable"):
            entry.main()
    else:
        entry.main()
    assert calls == ["scan"] and not ready.exists()
