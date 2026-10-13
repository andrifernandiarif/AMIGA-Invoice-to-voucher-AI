"""Evidence files, tax assignments, voucher templates

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New enum: Indonesian tax types ────────────────────────────────────────
    # PostgreSQL does not support CREATE TYPE IF NOT EXISTS; use a DO block instead.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE indonesiantaxtype AS ENUM ('PPH21','PPH22','PPH23','PPH25','PPN','NO_TAX');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ── evidence_files ────────────────────────────────────────────────────────
    op.create_table(
        "evidence_files",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("requests.request_id"), nullable=False),
        sa.Column("file_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("upload_order", sa.Integer, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_evidence_files_request_id", "evidence_files", ["request_id"])

    # ── tax_assignments ────────────────────────────────────────────────────────
    _indonesiantaxtype = postgresql.ENUM(
        "PPH21", "PPH22", "PPH23", "PPH25", "PPN", "NO_TAX",
        name="indonesiantaxtype", create_type=False,
    )
    op.create_table(
        "tax_assignments",
        sa.Column("tax_assignment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("line_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("line_items.line_item_id"), nullable=False, unique=True),
        sa.Column("tax_type", _indonesiantaxtype, nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=False),
    )

    # ── voucher_templates ─────────────────────────────────────────────────────
    op.create_table(
        "voucher_templates",
        sa.Column("template_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(100), nullable=False, unique=True),
        sa.Column("template_content", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # Partial unique index: at most one active template at a time
    op.execute(
        "CREATE UNIQUE INDEX uq_one_active_template "
        "ON voucher_templates (is_active) WHERE is_active = TRUE"
    )

    # ── Update voucher_pdfs: add template_version + template_hash columns ─────
    op.add_column(
        "voucher_pdfs",
        sa.Column("template_version", sa.String(100), nullable=True),
    )
    op.add_column(
        "voucher_pdfs",
        sa.Column("template_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voucher_pdfs", "template_hash")
    op.drop_column("voucher_pdfs", "template_version")
    op.execute("DROP INDEX IF EXISTS uq_one_active_template")
    op.drop_table("voucher_templates")
    op.drop_table("tax_assignments")
    op.execute("DROP INDEX IF EXISTS ix_evidence_files_request_id")
    op.drop_table("evidence_files")
    op.execute("DROP TYPE IF EXISTS indonesiantaxtype")
