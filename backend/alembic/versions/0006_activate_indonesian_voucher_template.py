"""Activate the Indonesian expense voucher layout for new generated PDFs.

Revision ID: 0006
Revises: 0005
"""

import uuid
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    template = (Path(__file__).resolve().parents[2] / "app/templates/voucher_default.html").read_text(encoding="utf-8")
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE voucher_templates SET is_active = false WHERE is_active = true"))
    connection.execute(
        sa.text("""
        INSERT INTO voucher_templates (template_id, version, template_content, is_active, created_by, created_at)
        VALUES (:template_id, 'v2.0-id', :content, true, NULL, now())
        ON CONFLICT (version) DO UPDATE SET is_active = true
    """),
        {"template_id": uuid.uuid4(), "content": template},
    )


def downgrade():
    op.execute("UPDATE voucher_templates SET is_active = false WHERE version = 'v2.0-id'")
    op.execute("""UPDATE voucher_templates SET is_active = true
                  WHERE template_id = (SELECT template_id FROM voucher_templates
                  WHERE version <> 'v2.0-id' ORDER BY created_at DESC LIMIT 1)""")
