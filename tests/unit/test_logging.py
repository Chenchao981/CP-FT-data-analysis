from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import (
    configure_logging,
    prune_expired_rotated_logs,
    redact_sensitive_text,
)


def _remove_tms_handlers() -> None:
    handlers: set[logging.Handler] = set()
    for logger in (
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
    ):
        for handler in list(logger.handlers):
            if getattr(handler, "_tms_handler", False):
                logger.removeHandler(handler)
                handlers.add(handler)
    for handler in handlers:
        handler.close()


def test_configure_logging_writes_rotating_process_jsonl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _remove_tms_handlers()
    monkeypatch.setenv("TMS_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TMS_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("TMS_LOG_BACKUP_COUNT", "2")
    monkeypatch.setenv("TMS_PROCESS_NAME", "worker/test")
    get_settings.cache_clear()

    try:
        configure_logging()
        logger = logging.getLogger("tests.rotation")
        for index in range(40):
            logger.info("rotation-record-%s-%s", index, "x" * 100)
        for handler in logging.getLogger().handlers:
            handler.flush()

        current_log = tmp_path / "worker_test.jsonl"
        assert current_log.is_file()
        assert (tmp_path / "worker_test.jsonl.1").is_file()
        payload = json.loads(current_log.read_text(encoding="utf-8").splitlines()[-1])
        assert payload["process"] == "worker/test"
        assert payload["logger"] == "tests.rotation"
        assert isinstance(payload["pid"], int)
    finally:
        _remove_tms_handlers()
        get_settings.cache_clear()


def test_log_redaction_masks_runtime_secrets_and_passwords(monkeypatch) -> None:
    monkeypatch.setenv("TMS_JWT_SECRET", "a-production-secret-value")
    monkeypatch.setenv("TMS_HEALTH_BEARER_TOKEN", "health-token-value")

    text = redact_sensitive_text(
        "jwt=a-production-secret-value Authorization: Bearer health-token-value "
        "PWD=database-password mssql://user:password@server/db"
    )

    assert "a-production-secret-value" not in text
    assert "health-token-value" not in text
    assert "database-password" not in text
    assert "user:password@" not in text
    assert text.count("[REDACTED]") >= 4


def test_log_retention_only_removes_expired_numeric_rotations(tmp_path: Path) -> None:
    old_rotation = tmp_path / "worker.jsonl.9"
    current_rotation = tmp_path / "worker.jsonl.1"
    unrelated = tmp_path / "api.jsonl.9"
    suspicious = tmp_path / "worker.jsonl.secret"
    for path in (old_rotation, current_rotation, unrelated, suspicious):
        path.write_text("record\n", encoding="utf-8")
    observed_at = datetime(2026, 8, 29, tzinfo=UTC)
    old_timestamp = (observed_at - timedelta(days=31)).timestamp()
    current_timestamp = (observed_at - timedelta(days=1)).timestamp()
    os.utime(old_rotation, (old_timestamp, old_timestamp))
    os.utime(unrelated, (old_timestamp, old_timestamp))
    os.utime(suspicious, (old_timestamp, old_timestamp))
    os.utime(current_rotation, (current_timestamp, current_timestamp))

    removed = prune_expired_rotated_logs(
        tmp_path,
        process_name="worker",
        retention_days=30,
        now=observed_at,
    )

    assert removed == (old_rotation,)
    assert not old_rotation.exists()
    assert current_rotation.exists()
    assert unrelated.exists()
    assert suspicious.exists()
