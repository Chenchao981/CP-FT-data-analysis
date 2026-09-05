from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def is_stop_requested(stop_file: Path | None) -> bool:
    return stop_file is not None and stop_file.is_file()


def write_ready_file(
    ready_file: Path | None,
    worker_id: str,
    database_metadata: dict[str, str],
) -> None:
    if ready_file is None:
        return
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = ready_file.with_name(f"{ready_file.name}.{os.getpid()}.tmp")
    payload = {
        "status": "READY",
        "pid": os.getpid(),
        "worker_id": worker_id,
        "ready_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database_metadata["database"],
        "schema_revision": database_metadata["schema_revision"],
        "database_server": database_metadata["database_server"],
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, ready_file)


def remove_ready_file(ready_file: Path | None) -> None:
    if ready_file is not None:
        ready_file.unlink(missing_ok=True)


def validate_database_identity(
    database_metadata: dict[str, str],
    *,
    expected_database: str | None,
    expected_schema_revision: str | None,
    expected_database_server: str | None,
) -> None:
    expected = {
        "database": expected_database,
        "schema_revision": expected_schema_revision,
        "database_server": expected_database_server,
    }
    supplied = [value is not None for value in expected.values()]
    if any(supplied) and not all(supplied):
        raise RuntimeError("all expected database identity fields must be supplied together")
    mismatches = [
        field
        for field, value in expected.items()
        if value is not None and database_metadata.get(field) != value
    ]
    if mismatches:
        actual = "/".join(database_metadata.get(field, "") for field in expected)
        wanted = "/".join(value or "" for value in expected.values())
        raise RuntimeError(
            f"Worker database identity rejected {actual}; expected {wanted}"
        )
