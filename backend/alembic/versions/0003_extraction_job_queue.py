"""Extraction job queue table

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Pre-declare enum with create_type=False so SQLAlchemy does not attempt to
# CREATE it implicitly during op.create_table column processing.
_extraction_job_status = postgresql.ENUM(
    "PENDING", "IN_PROGRESS", "DONE", "FAILED",
    name="extraction_job_status",
    create_type=False,
)


def upgrade() -> None:
    # ── Create enum type ──────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE extraction_job_status AS ENUM (
                'PENDING', 'IN_PROGRESS', 'DONE', 'FAILED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ── extraction_jobs ───────────────────────────────────────────────────────
    op.create_table(
        "extraction_jobs",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requests.request_id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _extraction_job_status,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
    )

    # ── Composite index on (status, created_at) to support efficient polling ──
    op.create_index(
        "ix_extraction_jobs_status_created_at",
        "extraction_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_jobs_status_created_at", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    op.execute("DROP TYPE IF EXISTS extraction_job_status")
