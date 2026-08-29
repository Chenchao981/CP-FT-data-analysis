from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from functools import lru_cache
from json import JSONDecodeError, loads
from pathlib import Path
from urllib.parse import unquote


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


def _repository_schema_head() -> str:
    versions = Path(__file__).resolve().parents[3] / "db" / "alembic" / "versions"
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in versions.glob("sql2014_*.py"):
        content = path.read_text(encoding="utf-8")
        revision = re.search(
            r'^revision\s*=\s*["\'](sql2014_\d+)["\']\s*$',
            content,
            re.MULTILINE,
        )
        if revision is None:
            raise RuntimeError(f"invalid SQL Server migration revision: {path.name}")
        revisions.add(revision.group(1))
        parent = re.search(
            r'^down_revision\s*=\s*["\'](sql2014_\d+)["\']\s*$',
            content,
            re.MULTILINE,
        )
        if parent is not None:
            parents.add(parent.group(1))
    heads = revisions - parents
    if len(heads) != 1:
        raise RuntimeError("the release must contain exactly one schema head")
    return heads.pop()


def _production_managed_roots() -> tuple[Path, ...]:
    paths: list[Path] = []
    for name in (
        "TMS_UPLOAD_ROOT",
        "TMS_WORK_ROOT",
        "TMS_QUICK_WORK_ROOT",
        "TMS_LOG_DIR",
    ):
        raw = os.getenv(name, "").strip()
        path = Path(raw)
        if not raw or not path.is_absolute() or not path.is_dir():
            raise RuntimeError(f"{name} must identify an existing absolute directory")
        paths.append(Path(os.path.abspath(path)))
    try:
        sources = loads(os.getenv("TMS_SOURCE_ROOTS_JSON", ""))
    except JSONDecodeError as exc:
        raise RuntimeError("TMS_SOURCE_ROOTS_JSON must be valid JSON") from exc
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("TMS_SOURCE_ROOTS_JSON must contain managed Source roots")
    for source in sources:
        if not isinstance(source, dict):
            raise TypeError("each managed Source root must be an object")
        raw = str(source.get("path", "")).strip()
        path = Path(raw)
        if not raw or not path.is_absolute() or not path.is_dir():
            raise RuntimeError(
                "each managed Source root must identify an existing absolute directory"
            )
        paths.append(Path(os.path.abspath(path)))
    for path in paths:
        cursor: Path | None = path
        while cursor is not None:
            attributes = getattr(cursor.lstat(), "st_file_attributes", 0)
            if cursor.is_symlink() or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise RuntimeError("managed roots must not contain reparse points")
            cursor = cursor.parent if cursor.parent != cursor else None
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            try:
                common = Path(os.path.commonpath((left, right)))
            except ValueError:
                continue
            if common in {left, right}:
                raise RuntimeError("managed roots must not overlap")
    return tuple(paths)


def _validate_production_environment(jwt_secret: str) -> None:
    if len(jwt_secret) < 48 or re.search(
        r"(?i)(change|replace|example|placeholder|development|local|secret)",
        jwt_secret,
    ):
        raise RuntimeError(
            "TMS_JWT_SECRET must be a non-placeholder secret of at least 48 characters in production"
        )
    database_url = os.getenv("TMS_DATABASE_URL", "").strip()
    identity = re.match(
        r"^mssql\+pyodbc://@(?P<server>[^/]+)/(?P<database>[^?]+)(?:\?|$)",
        database_url,
        re.IGNORECASE,
    )
    if (
        identity is None
        or re.search(r"(?i)(\bPWD\s*=|\bpassword\s*=|://[^/@:\s]+:[^@/\s]+@)", database_url)
        or not re.search(
            r"(?i)(trusted_connection=(?:yes|true)|integrated(?:\+|%20|\s)+security=(?:true|sspi))",
            database_url,
        )
    ):
        raise RuntimeError(
            "TMS_DATABASE_URL must use anonymous SQL Server Integrated Security without a password in production"
        )
    required = {
        name: os.getenv(name, "").strip()
        for name in (
            "TMS_EXPECTED_DATABASE",
            "TMS_EXPECTED_DATABASE_SERVER",
            "TMS_EXPECTED_SCHEMA_REVISION",
        )
    }
    if any(
        not value or re.search(r"(?i)(__|<|>|change|replace|example|placeholder)", value)
        for value in required.values()
    ):
        raise RuntimeError("production database identity fields are required")
    if unquote(identity.group("database")).casefold() != required[
        "TMS_EXPECTED_DATABASE"
    ].casefold():
        raise RuntimeError("TMS_DATABASE_URL database identity mismatch")
    if unquote(identity.group("server")).casefold() != required[
        "TMS_EXPECTED_DATABASE_SERVER"
    ].casefold():
        raise RuntimeError("TMS_DATABASE_URL server identity mismatch")
    if required["TMS_EXPECTED_SCHEMA_REVISION"] != _repository_schema_head():
        raise RuntimeError("TMS_EXPECTED_SCHEMA_REVISION must equal the release head")
    _production_managed_roots()


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    log_level: str
    log_dir: str | None
    log_max_bytes: int
    log_backup_count: int
    log_retention_days: int
    process_name: str
    job_repository: str
    auth_required: bool
    jwt_secret: str
    access_token_minutes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    environment = os.getenv("TMS_ENV", "development").strip().lower()
    job_repository = os.getenv("TMS_JOB_REPOSITORY", "memory").lower()
    if job_repository not in {"memory", "sql"}:
        raise RuntimeError("TMS_JOB_REPOSITORY must be memory or sql")
    log_max_bytes = int(os.getenv("TMS_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    if log_max_bytes < 1024:
        raise RuntimeError("TMS_LOG_MAX_BYTES must be at least 1024")
    log_backup_count = int(os.getenv("TMS_LOG_BACKUP_COUNT", "10"))
    if log_backup_count < 1 or log_backup_count > 100:
        raise RuntimeError("TMS_LOG_BACKUP_COUNT must be between 1 and 100")
    log_retention_days = int(os.getenv("TMS_LOG_RETENTION_DAYS", "30"))
    if log_retention_days < 1 or log_retention_days > 3650:
        raise RuntimeError("TMS_LOG_RETENTION_DAYS must be between 1 and 3650")
    log_dir = os.getenv("TMS_LOG_DIR", "").strip() or None
    process_name = os.getenv("TMS_PROCESS_NAME", "tms").strip() or "tms"
    auth_required = _boolean_environment("TMS_AUTH_REQUIRED", default=True)
    jwt_secret = os.getenv("TMS_JWT_SECRET", "tms-local-development-only")
    if environment in {"staging", "production"}:
        if not auth_required:
            raise RuntimeError(f"TMS_AUTH_REQUIRED must be true in {environment}")
        if job_repository != "sql":
            raise RuntimeError(f"TMS_JOB_REPOSITORY must be sql in {environment}")
        if jwt_secret == "tms-local-development-only" or len(jwt_secret) < 32:
            raise RuntimeError(
                f"TMS_JWT_SECRET must be an environment-specific secret of at least 32 characters in {environment}"
            )
        if environment == "production":
            _validate_production_environment(jwt_secret)
    return Settings(
        environment=environment,
        log_level=os.getenv("TMS_LOG_LEVEL", "INFO").upper(),
        log_dir=log_dir,
        log_max_bytes=log_max_bytes,
        log_backup_count=log_backup_count,
        log_retention_days=log_retention_days,
        process_name=process_name,
        job_repository=job_repository,
        auth_required=auth_required,
        jwt_secret=jwt_secret,
        access_token_minutes=int(os.getenv("TMS_ACCESS_TOKEN_MINUTES", "480")),
    )
