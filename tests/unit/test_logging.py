from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging


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
