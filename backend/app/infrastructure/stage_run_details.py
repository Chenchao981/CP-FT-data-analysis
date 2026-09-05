from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection, text


def stage_detail_values(stage: str, raw_metadata: str | None) -> dict[str, Any]:
    """Promote only explicit Cleaner evidence; never derive manufacturing identity."""
    metadata = json.loads(raw_metadata or "{}")
    if not isinstance(metadata, dict):
        raise ValueError("run metadata must be an object")  # noqa: TRY004 - invalid JSON value
    if stage == "CP":
        fields = {"raw_wafer_id": 64, "source_group": 128, "source_lot_run": 128}
        source = metadata
    elif stage == "FT":
        identity = metadata.get("source_identity")
        if identity is None:
            identity = {}
        if not isinstance(identity, dict):
            raise ValueError("FT source identity must be an object")
        for key in ("source_id", "source_file"):
            if identity.get(key) is not None and identity[key] != metadata.get(key):
                raise ValueError(f"FT {key} conflicts with source identity")
        source = {
            **identity,
            "source_id": metadata.get("source_id"),
            "source_file": metadata.get("source_file"),
        }
        fields = {
            "source_id": 256,
            "source_file": 1024,
            "manufacturing_lot": 128,
            "test_tag": 128,
            "test_file_name": 128,
            "source_segment": 128,
            "source_format": 64,
            "metadata_lot": 128,
        }
    else:
        raise ValueError("stage details support only CP and FT")
    values: dict[str, Any] = {}
    for key, limit in fields.items():
        value = source.get(key)
        if value is not None and (
            not isinstance(value, str)
            or not value.strip()
            or len(value.encode("utf-16-le")) // 2 > limit
        ):
            raise ValueError(f"invalid stage field: {key}")
        values[key] = value
    if stage == "FT" and values["source_id"] is None:
        raise ValueError("FT source_id is required")
    spec_id = metadata.get("spec_set_id")
    if spec_id is not None and (type(spec_id) is not int or spec_id <= 0):
        raise ValueError("invalid source spec_set_id")
    values["source_spec_set_id"] = spec_id
    return values


def persist_stage_run_details(
    connection: Connection, *, processing_run_id: int
) -> None:
    """Run in the Writer transaction, before facts and Dataset publication."""
    rows = (
        connection.execute(
            text(
                "SELECT run_id,test_stage,metadata_json FROM test.test_run "
                "WHERE processing_run_id=:processing ORDER BY run_id"
            ),
            {"processing": processing_run_id},
        )
        .mappings()
        .all()
    )
    for row in rows:
        stage = str(row["test_stage"])
        values = stage_detail_values(stage, row["metadata_json"])
        values["run_id"] = int(row["run_id"])
        table = "test.cp_run_detail" if stage == "CP" else "test.ft_run_detail"
        columns = ",".join(values)
        placeholders = ",".join(f":{key}" for key in values)
        connection.execute(
            text(f"INSERT {table}({columns}) VALUES({placeholders})"), values
        )


def run_source_identity(row: Mapping[str, Any]) -> str:
    # A selected relational column is authoritative, including an explicit NULL.
    if "source_id" in row:
        return (
            str(row["source_id"]).strip() if row["source_id"] else f"RUN-{int(row['run_id'])}"
        )
    # Compatibility for pre-migration detached snapshots and test fixtures only.
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    explicit = metadata.get("source_id") if isinstance(metadata, dict) else None
    return str(explicit).strip() if explicit else f"RUN-{int(row['run_id'])}"
