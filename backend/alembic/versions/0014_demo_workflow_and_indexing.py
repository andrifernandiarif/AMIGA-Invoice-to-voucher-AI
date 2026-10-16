"""Separate work ownership, consolidate administrators and add a durable indexing outbox."""

import uuid
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("requests", sa.Column("workflow_stage", sa.String(20), nullable=True))
    op.execute("""UPDATE requests r SET workflow_stage=CASE
        WHEN status='READY_FOR_REVIEW' OR (status='EXCEPTION' AND refinement_submitted_at IS NOT NULL)
            THEN 'FINANCE_REVIEW'
        WHEN status='READY_FOR_REFINEMENT' OR (status='EXCEPTION' AND EXISTS (
            SELECT 1 FROM drafts d JOIN line_items l ON l.draft_id=d.draft_id
            WHERE d.request_id=r.request_id AND l.line_total IS NOT NULL)) THEN 'REFINEMENT'
        ELSE NULL END WHERE status IN ('READY_FOR_REVIEW','READY_FOR_REFINEMENT','EXCEPTION')""")
    op.execute("""UPDATE requests SET flags=ARRAY(
        SELECT DISTINCT issue->>'flag_type' FROM jsonb_array_elements(validation_issues::jsonb) AS issue
        WHERE issue->>'flag_type' IN ('MISSING_EVIDENCE','CALC_DISCREPANCY','POSSIBLE_DUPLICATE'))
        WHERE workflow_stage IS NOT NULL AND jsonb_array_length(validation_issues::jsonb)>0""")
    op.execute("UPDATE requests SET status='EXCEPTION' WHERE workflow_stage IS NOT NULL AND cardinality(flags)>0")
    op.execute(
        "UPDATE users SET role='SYSTEM_ADMIN', token_version=token_version+1, updated_at=now() WHERE role='USER_ADMIN'"
    )
    op.create_table(
        "indexing_jobs",
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.request_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(200)),
    )
    op.create_index("ix_indexing_jobs_status", "indexing_jobs", ["status"])
    op.execute("""INSERT INTO indexing_jobs (request_id,status,revision,attempts,available_at)
        SELECT r.request_id,'PENDING',1,0,now() FROM requests r
        WHERE r.status IN ('AMIGA_REVIEWED','SENT_TO_CLIENT') AND
        (SELECT count(*) FROM chunk_embeddings c WHERE c.request_id=r.request_id) < 4
        ON CONFLICT (request_id) DO NOTHING""")
    conn = op.get_bind()
    active = (
        conn.execute(sa.text("SELECT version,created_by FROM voucher_templates WHERE is_active=true"))
        .mappings()
        .first()
    )
    bundled = {"v1.0", "v1.0-id", "v2.0-id", "v3.0-id", "v4.0-authorization"}
    activate = active is None or (active["version"] in bundled and active["created_by"] is None)
    if activate:
        op.execute("UPDATE voucher_templates SET is_active=false WHERE is_active=true")
    conn.execute(
        sa.text("""INSERT INTO voucher_templates
        (template_id,version,template_content,is_active,created_at)
        VALUES (:id,'v5.0-corporate',:content,:active,now())"""),
        {
            "id": uuid.uuid4(),
            "active": activate,
            "content": (Path(__file__).resolve().parents[2] / "app/templates/voucher_default.html").read_text(),
        },
    )
    # New enum values cannot be used before their ALTER TYPE transaction commits.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE metrictype ADD VALUE IF NOT EXISTS 'EXTRACTION_DOCUMENT_DURATION'")
        op.execute("ALTER TYPE metrictype ADD VALUE IF NOT EXISTS 'VOUCHER_AVAILABILITY_DURATION'")


def downgrade():
    # Keep migrated account identities, template versions and telemetry history.
    op.drop_table("indexing_jobs")
    op.drop_column("requests", "workflow_stage")
