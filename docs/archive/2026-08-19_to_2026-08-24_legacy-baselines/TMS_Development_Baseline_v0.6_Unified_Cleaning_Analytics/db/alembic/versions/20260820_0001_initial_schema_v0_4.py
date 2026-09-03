"""Initial v0.4 canonical and governance schema."""

from migration_helpers import irreversible_downgrade, run_sql_file

revision = "20260820_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_initial_schema_v0_4.sql")


def downgrade() -> None:
    irreversible_downgrade()
