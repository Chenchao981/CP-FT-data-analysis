"""One physical write target per stage, with globally stable reference IDs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, text

UNIT_COLUMNS = frozenset(
    {
        "run_id",
        "logical_unit_key",
        "attempt_no",
        "unit_sequence",
        "vendor_unit_id",
        "wafer_id",
        "x_coord",
        "y_coord",
        "site_no",
        "serial_no",
        "soft_bin",
        "hard_bin",
        "overall_result",
        "fail_test_no",
        "fail_test_name",
        "test_duration_ms",
        "source_row_no",
        "metadata_json",
        "created_at_utc",
    }
)
MEASUREMENT_COLUMNS = frozenset(
    {
        "unit_id",
        "test_item_id",
        "value_numeric",
        "value_text",
        "raw_value",
        "measurement_status",
        "tester_pass_flag",
        "source_column_index",
        "created_at_utc",
    }
)


def fact_table(stage: str, kind: str) -> str:
    tables = {
        ("CP", "unit"): "test.cp_die",
        ("FT", "unit"): "test.ft_device",
        ("CP", "measurement"): "test.cp_measurement",
        ("FT", "measurement"): "test.ft_measurement",
    }
    try:
        return tables[(stage, kind)]
    except KeyError as exc:
        raise ValueError("unsupported stage/fact kind") from exc


def _insert(
    connection: Connection, stage: str, kind: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[int, ...]:
    table = fact_table(stage, kind)
    if not rows:
        return ()
    if not connection.in_transaction():
        raise ValueError("stage facts require a caller-owned transaction")
    columns = tuple(rows[0])
    allowed = UNIT_COLUMNS if kind == "unit" else MEASUREMENT_COLUMNS
    required = (
        {"run_id", "logical_unit_key"}
        if kind == "unit"
        else {"unit_id", "test_item_id", "measurement_status"}
    )
    if not required <= set(columns) or not set(columns) <= allowed:
        raise ValueError("invalid stage fact columns")
    if (
        kind == "unit"
        and stage == "FT"
        and set(columns) & {"wafer_id", "x_coord", "y_coord"}
    ):
        raise ValueError("FT devices cannot carry wafer coordinates")
    if any(set(row) != set(columns) for row in rows):
        raise ValueError("inconsistent stage fact batch columns")
    id_column = "unit_id" if kind == "unit" else "measurement_id"
    # SQL Server allocates a unique range across concurrent Writers. Rollback gaps
    # are expected; no ID is recycled and no MAX(id)+1 allocation is used at runtime.
    first = int(
        connection.execute(
            text(
                "SET NOCOUNT ON; DECLARE @first sql_variant; "
                "EXEC sys.sp_sequence_get_range @sequence_name=:sequence,@range_size=:count,"
                "@range_first_value=@first OUTPUT; SET NOCOUNT OFF; SELECT CONVERT(bigint,@first);"
            ),
            {"sequence": f"test.{kind}_id_sequence", "count": len(rows)},
        ).scalar_one()
    )
    ids = tuple(range(first, first + len(rows)))
    identities = [{id_column: value, "test_stage": stage} for value in ids]
    connection.execute(
        text(
            f"INSERT test.{kind}_identity({id_column},test_stage) VALUES(:{id_column},:test_stage)"
        ),
        identities,
    )
    payloads = [{id_column: value, **row} for value, row in zip(ids, rows, strict=True)]
    all_columns = (id_column, *columns)
    connection.execute(
        text(
            f"INSERT {table}({','.join(all_columns)}) VALUES({','.join(':' + col for col in all_columns)})"
        ),
        payloads,
    )
    return ids


def insert_units(
    connection: Connection, stage: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[int, ...]:
    return _insert(connection, stage, "unit", rows)


def insert_measurements(
    connection: Connection, stage: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[int, ...]:
    return _insert(connection, stage, "measurement", rows)
