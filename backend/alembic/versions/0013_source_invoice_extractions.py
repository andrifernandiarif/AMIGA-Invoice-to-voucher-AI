"""Retain source invoice values for combined vouchers."""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("drafts", sa.Column("source_invoices", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("drafts", "source_invoices")
