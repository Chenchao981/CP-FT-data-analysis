"""Add staged atomic initial-import finalization and verified input lineage."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from migration_helpers import irreversible_downgrade, run_sql_file

revision = "sql2014_0015"
down_revision = "sql2014_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0015_atomic_initial_import_finalize_sql2014.sql")


def downgrade() -> None:
    irreversible_downgrade()
