from __future__ import annotations

import json
import logging
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import get_settings

_SENSITIVE_ENVIRONMENT_NAMES = (
    "TMS_JWT_SECRET",
    "TMS_HEALTH_BEARER_TOKEN",
)
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:password|pwd|secret|token|jwt)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(\bPWD\s*=\s*)[^;\s]+"),
    re.compile(r"(?i)(://[^/@:\s]+:)[^@/\s]+(@)"),
)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    for environment_name in _SENSITIVE_ENVIRONMENT_NAMES:
        secret = os.getenv(environment_name, "")
        if len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(r"\1[REDACTED]\2", text)
        else:
            text = pattern.sub(r"\1[REDACTED]", text)
    return text


def prune_expired_rotated_logs(
    log_dir: Path,
    *,
    process_name: str,
    retention_days: int,
    now: datetime | None = None,
) -> tuple[Path, ...]:
    """Remove only expired numeric rotations for one sanitized process log."""

    observed_at = now or datetime.now(UTC)
    cutoff = observed_at - timedelta(days=retention_days)
    safe_pattern = re.compile(rf"^{re.escape(process_name)}\.jsonl\.\d+$")
    removed: list[Path] = []
    for candidate in log_dir.iterdir():
        if not safe_pattern.fullmatch(candidate.name):
            continue
        metadata = os.lstat(candidate)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            continue
        modified = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
        if modified >= cutoff:
            continue
        candidate.unlink()
        removed.append(candidate)
    return tuple(removed)


class RetentionRotatingFileHandler(RotatingFileHandler):
    def __init__(
        self,
        filename: Path,
        *,
        max_bytes: int,
        backup_count: int,
        retention_days: int,
        process_name: str,
    ) -> None:
        super().__init__(
            filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        self._tms_log_dir = filename.parent
        self._tms_process_name = process_name
        self._tms_retention_days = retention_days

    def doRollover(self) -> None:
        super().doRollover()
        prune_expired_rotated_logs(
            self._tms_log_dir,
            process_name=self._tms_process_name,
            retention_days=self._tms_retention_days,
        )


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
            "message": redact_sensitive_text(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_sensitive_text(
                self.formatException(record.exc_info)
            )
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
    prune_expired_rotated_logs(
        log_dir,
        process_name=process_name,
        retention_days=settings.log_retention_days,
    )
    log_path = log_dir / f"{process_name}.jsonl"
    if any(
        getattr(handler, "_tms_log_path", None) == str(log_path)
        for handler in root.handlers
    ):
        return

    file_handler = RetentionRotatingFileHandler(
        log_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        retention_days=settings.log_retention_days,
        process_name=process_name,
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
