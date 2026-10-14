"""Give existing unapproved drafts the new client refinement step.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        WITH moved AS (
            UPDATE requests SET status='READY_FOR_REFINEMENT',updated_at=now()
            WHERE status='READY_FOR_REVIEW' AND refinement_submitted_at IS NULL
            RETURNING request_id
        )
        INSERT INTO audit_events(event_id,request_id,event_type,actor_id,payload,occurred_at)
        SELECT gen_random_uuid(),request_id,'STATUS_CHANGED',NULL,
        '{"from_status":"READY_FOR_REVIEW","to_status":"READY_FOR_REFINEMENT","reason":"Client refinement step enabled"}'::jsonb,now()
        FROM moved
    """)


def downgrade():
    op.execute("UPDATE requests SET status='READY_FOR_REVIEW' WHERE status='READY_FOR_REFINEMENT'")
