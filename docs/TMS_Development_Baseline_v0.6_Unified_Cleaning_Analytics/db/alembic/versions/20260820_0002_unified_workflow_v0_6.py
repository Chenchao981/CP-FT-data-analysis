"""Add v0.6 IAM, input set, dataset, evaluation, analysis and export models."""

from migration_helpers import irreversible_downgrade, run_sql_file

revision = "20260820_0002"
down_revision = "20260820_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_unified_workflow_v0_6.sql")


def downgrade() -> None:
    irreversible_downgrade()
