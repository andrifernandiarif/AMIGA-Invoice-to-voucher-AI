"""Organization authentication and advisory workflow metadata."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPER_ADMIN'")
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'USER_ADMIN'")
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("requests", sa.Column("validation_issues", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("requests", sa.Column("due_date", sa.Date(), nullable=True))


def downgrade():
    for column in ("due_date", "validation_issues"):
        op.drop_column("requests", column)
    for column in ("token_version", "must_change_password", "password_hash"):
        op.drop_column("users", column)
    # PostgreSQL enum values are intentionally retained for safe downgrade.
