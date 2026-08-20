"""Seed v0.6 scopes, permissions, roles and data-quality rule codes."""

from migration_helpers import irreversible_downgrade, run_sql_file

revision = "20260820_0003"
down_revision = "20260820_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_seed_governance_v0_6.sql")


def downgrade() -> None:
    irreversible_downgrade()
