"""Create v0.6 current-published analytics views."""

from migration_helpers import irreversible_downgrade, run_sql_file

revision = "20260820_0004"
down_revision = "20260820_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0004_analytics_current_views_v0_6.sql")


def downgrade() -> None:
    irreversible_downgrade()
