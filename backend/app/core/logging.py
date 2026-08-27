from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    def __init__(self, process_name: str) -> None:
        super().__init__()
        self.process_name = process_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "process": self.process_name,
            "pid": os.getpid(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    formatter = JsonFormatter(settings.process_name)

    if not any(
        getattr(handler, "_tms_handler_kind", None) == "stream"
        for handler in root.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler._tms_handler = True  # type: ignore[attr-defined]
        stream_handler._tms_handler_kind = "stream"  # type: ignore[attr-defined]
        root.addHandler(stream_handler)

    if settings.log_dir is None:
        return

    process_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", settings.process_name).strip("._")
    if not process_name:
        process_name = "tms"
    log_dir = Path(settings.log_dir).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{process_name}.jsonl"
    if any(
        getattr(handler, "_tms_log_path", None) == str(log_path)
        for handler in root.handlers
    ):
        return

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(formatter)
    file_handler._tms_handler = True  # type: ignore[attr-defined]
    file_handler._tms_handler_kind = "file"  # type: ignore[attr-defined]
    file_handler._tms_log_path = str(log_path)  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    # Uvicorn owns these two non-propagating loggers. Reusing the same handler
    # keeps API lifecycle/access records in the same rotating process log.
    for logger_name in ("uvicorn", "uvicorn.access"):
        named_logger = logging.getLogger(logger_name)
        if file_handler not in named_logger.handlers:
            named_logger.addHandler(file_handler)
