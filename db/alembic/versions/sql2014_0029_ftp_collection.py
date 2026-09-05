"""Persist controlled FTP collection, checkpoints and atomic import receipts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migration_helpers import run_sql_file

revision = "sql2014_0029"
down_revision = "sql2014_0028"
branch_labels = None
depends_on = None


def upgrade():
    run_sql_file("0029_ftp_collection_sql2014.sql")


def downgrade():
    # Retain import/source provenance once collection has been used.
    run_sql_file("0029_ftp_collection_down_sql2014.sql")
