"""Client refinement stage and explicit manual per-item tax calculation.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE requeststatus ADD VALUE IF NOT EXISTS 'READY_FOR_REFINEMENT'")
    for tax_type in ["PPH26", "PPH4_2"]:
        op.execute(f"ALTER TYPE indonesiantaxtype ADD VALUE IF NOT EXISTS '{tax_type}'")
    op.add_column("requests", sa.Column("refinement_submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tax_assignments", sa.Column("tax_base", sa.Numeric(18, 4), nullable=True))
    op.add_column(
        "tax_assignments", sa.Column("base_is_manual", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("tax_assignments", sa.Column("tax_effect", sa.String(10), nullable=False, server_default="ADD"))
    op.add_column("tax_assignments", sa.Column("rate_profile", sa.String(80), nullable=True))
    op.alter_column("tax_assignments", "tax_rate", type_=sa.Numeric(7, 6), existing_type=sa.Numeric(5, 4))


def downgrade():
    for name in ["rate_profile", "tax_effect", "base_is_manual", "tax_base"]:
        op.drop_column("tax_assignments", name)
    op.alter_column("tax_assignments", "tax_rate", type_=sa.Numeric(5, 4), existing_type=sa.Numeric(7, 6))
    op.drop_column("requests", "refinement_submitted_at")
    # PostgreSQL enum values are retained to avoid deleting historical states.
