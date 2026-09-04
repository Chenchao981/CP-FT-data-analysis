"""Enable result-only cleaning, chart and SBL/SYL personal tasks."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from migration_helpers import run_sql_file

revision = "sql2014_0026"
down_revision = "sql2014_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0026_personal_tools_sql2014.sql")


def downgrade() -> None:
    run_sql_file("0026_personal_tools_down_sql2014.sql")
