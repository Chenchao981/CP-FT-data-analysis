from __future__ import annotations

import argparse
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_analytics_export_worker as entry


def args_for(tmp_path: Path, **changes):
    values = dict(
        once=False, poll_seconds=0.1, worker_id="test-export", lease_seconds=300,
        heartbeat_seconds=30, stop_file=tmp_path / "export.stop",
        ready_file=tmp_path / "export.ready.json", expected_database="TMS_G0_DEV",
        expected_schema_revision="sql2014_0029", expected_database_server="SQL-TEST",
    )
    return argparse.Namespace(**(values | changes))


def test_export_worker_finishes_current_report_before_stopping(tmp_path):
    args = args_for(tmp_path)
    calls = []

    def process():
        calls.append("started")
        args.stop_file.write_text("stop", encoding="ascii")
        calls.append("completed")
        return SimpleNamespace(export_job_id=1)

    entry.run_loop(SimpleNamespace(run_once=process), args, logging.getLogger(__name__))
    assert calls == ["started", "completed"]


def test_export_worker_does_not_claim_after_stop_or_once(tmp_path):
    args = args_for(tmp_path, once=True)
    calls = []
    worker = SimpleNamespace(run_once=lambda: calls.append("claim"))
    entry.run_loop(worker, args, logging.getLogger(__name__))
    assert calls == ["claim"]
    args.stop_file.touch()
    entry.run_loop(worker, args, logging.getLogger(__name__))
    assert calls == ["claim"]


@pytest.mark.parametrize("field,value", [
    ("database", "PRODUCTION"), ("schema_revision", "sql2014_0027"),
    ("database_server", "OTHER-SERVER"),
])
def test_export_worker_rejects_wrong_database_before_claim_and_clears_stale_ready(
    tmp_path, monkeypatch, field, value,
):
    args = args_for(tmp_path)
    args.ready_file.write_text("stale", encoding="ascii")
    monkeypatch.setattr(entry, "parse_args", lambda: args)
    monkeypatch.setenv("TMS_DATABASE_URL", "test-only")
    metadata = dict(database="TMS_G0_DEV", schema_revision="sql2014_0029", database_server="SQL-TEST")
    metadata[field] = value
    monkeypatch.setattr(entry, "check_database", lambda: metadata)
    monkeypatch.setattr(entry, "get_engine", lambda: pytest.fail("must not claim or create a worker"))
    with pytest.raises(RuntimeError, match="database identity rejected"):
        entry.main()
    assert not args.ready_file.exists()


@pytest.mark.parametrize("fail", [False, True])
def test_export_worker_readiness_removed_after_completion_or_failure(tmp_path, monkeypatch, fail):
    args = args_for(tmp_path, once=True)
    monkeypatch.setattr(entry, "parse_args", lambda: args)
    monkeypatch.setenv("TMS_DATABASE_URL", "test-only")
    monkeypatch.setenv("TMS_ANALYTICS_EXPORT_ROOT", str(tmp_path / "exports"))
    monkeypatch.setattr(entry, "check_database", lambda: dict(
        database="TMS_G0_DEV", schema_revision="sql2014_0029", database_server="SQL-TEST",
    ))
    monkeypatch.setattr(entry, "get_engine", lambda: object())
    monkeypatch.setattr(entry, "SqlAnalyticsExportWorkerRepository", lambda *a, **kw: object())
    monkeypatch.setattr(entry, "AnalyticsExportRenderer", lambda *a, **kw: object())
    monkeypatch.setattr(entry, "SqlAnalyticsExportContentSource", lambda *a, **kw: object())
    monkeypatch.setattr(entry, "configure_logging", lambda: None)

    def process():
        assert args.ready_file.is_file()
        if fail:
            raise RuntimeError("queue unavailable")
        return None

    monkeypatch.setattr(entry, "AnalyticsExportWorker", lambda *a, **kw: SimpleNamespace(run_once=process))
    if fail:
        with pytest.raises(RuntimeError, match="queue unavailable"):
            entry.main()
    else:
        entry.main()
    assert not args.ready_file.exists()
