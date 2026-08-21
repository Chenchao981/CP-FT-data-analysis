from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    log_level: str
    job_repository: str
    auth_required: bool
    jwt_secret: str
    access_token_minutes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    job_repository = os.getenv("TMS_JOB_REPOSITORY", "memory").lower()
    if job_repository not in {"memory", "sql"}:
        raise RuntimeError("TMS_JOB_REPOSITORY must be memory or sql")
    return Settings(
        environment=os.getenv("TMS_ENV", "development"),
        log_level=os.getenv("TMS_LOG_LEVEL", "INFO").upper(),
        job_repository=job_repository,
        auth_required=os.getenv("TMS_AUTH_REQUIRED", "false").lower()
        in {"1", "true", "yes", "on"},
        jwt_secret=os.getenv("TMS_JWT_SECRET", "tms-local-development-only"),
        access_token_minutes=int(os.getenv("TMS_ACCESS_TOKEN_MINUTES", "480")),
    )
