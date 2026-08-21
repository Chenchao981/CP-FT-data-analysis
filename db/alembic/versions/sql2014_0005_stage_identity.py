"""Make CP Product and FT Lot optional according to stage identity facts."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from migration_helpers import irreversible_downgrade, run_sql_file


revision = "sql2014_0005"
down_revision = "sql2014_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0005_stage_identity_sql2014.sql")


def downgrade() -> None:
    irreversible_downgrade()
