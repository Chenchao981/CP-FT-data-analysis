"""Separate CP/FT run identity and source specification from opaque metadata."""

import json
import sys
from pathlib import Path

from alembic import op
from sqlalchemy import text

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from migration_helpers import irreversible_downgrade, run_sql_file

revision = "sql2014_0027"
down_revision = "sql2014_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # JSON is decoded in Python: SQL Server 2014 has no JSON_VALUE/OPENJSON.
    # Keep this revision self-contained, independent of future Writer changes.
    rows = (
        connection.execute(
            text(
                "SELECT run_id,test_stage,metadata_json FROM test.test_run "
                "WHERE test_stage IN('CP','FT') ORDER BY run_id"
            )
        )
        .mappings()
        .all()
    )
    prepared = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        if not isinstance(metadata, dict):
            raise ValueError(f"Run {row['run_id']} has invalid metadata")  # noqa: TRY004
        stage = row["test_stage"]
        if stage == "CP":
            columns = {"raw_wafer_id": 64, "source_group": 128, "source_lot_run": 128}
            source = metadata
        else:
            identity = metadata.get("source_identity")
            if identity is None:
                identity = {}
            if not isinstance(identity, dict):
                raise ValueError("Invalid FT source identity")
            for key in ("source_id", "source_file"):
                if identity.get(key) is not None and identity[key] != metadata.get(key):
                    raise ValueError(f"Run {row['run_id']} has conflicting {key}")
            source = {
                **identity,
                "source_id": metadata.get("source_id"),
                "source_file": metadata.get("source_file"),
            }
            columns = {
                "source_id": 256,
                "source_file": 1024,
                "manufacturing_lot": 128,
                "test_tag": 128,
                "test_file_name": 128,
                "source_segment": 128,
                "source_format": 64,
                "metadata_lot": 128,
            }
            if source["source_id"] is None:
                raise ValueError(
                    f"Run {row['run_id']} lacks explicit FT source identity"
                )
        values = {"run_id": row["run_id"]}
        for key, limit in columns.items():
            value = source.get(key)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-16-le")) // 2 > limit
            ):
                raise ValueError(f"Run {row['run_id']} has invalid {key}")
            values[key] = value
        spec_id = metadata.get("spec_set_id")
        if spec_id is not None and (type(spec_id) is not int or spec_id <= 0):
            raise ValueError("Invalid source specification identity")
        values["source_spec_set_id"] = spec_id
        prepared.append((stage, values))
    run_sql_file("0027_stage_run_details_sql2014.sql")
    for stage, values in prepared:
        table = "test.cp_run_detail" if stage == "CP" else "test.ft_run_detail"
        connection.execute(
            text(
                f"INSERT {table}({','.join(values)}) VALUES({','.join(':' + key for key in values)})"
            ),
            values,
        )


def downgrade() -> None:
    irreversible_downgrade()
