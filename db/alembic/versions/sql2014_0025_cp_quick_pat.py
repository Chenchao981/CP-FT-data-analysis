"""Allow CP sessions in the isolated Quick Analysis workspace."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from migration_helpers import run_sql_file

revision = "sql2014_0025"
down_revision = "sql2014_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0025_cp_quick_pat_sql2014.sql")


def downgrade() -> None:
    run_sql_file("0025_cp_quick_pat_down_sql2014.sql")
