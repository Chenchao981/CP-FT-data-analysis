"""Add covering indexes for formal analytics read paths."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from migration_helpers import run_sql_file

revision = "sql2014_0023"
down_revision = "sql2014_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0023_analytics_performance_indexes_sql2014.sql")


def downgrade() -> None:
    run_sql_file("0023_analytics_performance_indexes_down_sql2014.sql")
