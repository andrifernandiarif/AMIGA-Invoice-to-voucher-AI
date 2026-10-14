"""Editable Indonesian voucher header fields and optional line item dates.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for name, length in [
        ("voucher_number", 100),
        ("voucher_name", 500),
        ("voucher_purpose", 1000),
        ("voucher_period", 200),
    ]:
        op.add_column("header_fields", sa.Column(name, sa.String(length), nullable=True))
    op.add_column("line_items", sa.Column("item_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("line_items", "item_date")
    for name in ["voucher_period", "voucher_purpose", "voucher_name", "voucher_number"]:
        op.drop_column("header_fields", name)
