"""Add PERSONAL / DOMAIN data authorization and managed source definitions."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from migration_helpers import run_sql_file

revision = "sql2014_0024"
down_revision = "sql2014_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0024_personal_domain_access_sql2014.sql")


def downgrade() -> None:
    run_sql_file("0024_personal_domain_access_down_sql2014.sql")
