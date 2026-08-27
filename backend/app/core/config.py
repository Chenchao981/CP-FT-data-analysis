from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _boolean_environment(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off"
    )


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    log_level: str
    log_dir: str | None
    log_max_bytes: int
    log_backup_count: int
    process_name: str
    job_repository: str
    auth_required: bool
    jwt_secret: str
    access_token_minutes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    job_repository = os.getenv("TMS_JOB_REPOSITORY", "memory").lower()
    if job_repository not in {"memory", "sql"}:
        raise RuntimeError("TMS_JOB_REPOSITORY must be memory or sql")
    log_max_bytes = int(os.getenv("TMS_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    if log_max_bytes < 1024:
        raise RuntimeError("TMS_LOG_MAX_BYTES must be at least 1024")
    log_backup_count = int(os.getenv("TMS_LOG_BACKUP_COUNT", "10"))
    if log_backup_count < 1 or log_backup_count > 100:
        raise RuntimeError("TMS_LOG_BACKUP_COUNT must be between 1 and 100")
    log_dir = os.getenv("TMS_LOG_DIR", "").strip() or None
    process_name = os.getenv("TMS_PROCESS_NAME", "tms").strip() or "tms"
    return Settings(
        environment=os.getenv("TMS_ENV", "development"),
        log_level=os.getenv("TMS_LOG_LEVEL", "INFO").upper(),
        log_dir=log_dir,
        log_max_bytes=log_max_bytes,
        log_backup_count=log_backup_count,
        process_name=process_name,
        job_repository=job_repository,
        auth_required=_boolean_environment("TMS_AUTH_REQUIRED", default=True),
        jwt_secret=os.getenv("TMS_JWT_SECRET", "tms-local-development-only"),
        access_token_minutes=int(os.getenv("TMS_ACCESS_TOKEN_MINUTES", "480")),
    )
