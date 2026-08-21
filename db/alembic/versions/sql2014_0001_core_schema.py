"""SQL Server 2014 core canonical schema."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from migration_helpers import irreversible_downgrade, run_sql_file


revision = "sql2014_0001"
down_revision = None
branch_labels = ("sqlserver2014",)
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_core_schema_sql2014.sql")


def downgrade() -> None:
    irreversible_downgrade()
