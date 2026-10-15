"""Activate the voucher template with a fixed client authorization notice."""

import uuid
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE voucher_templates SET is_active=false WHERE is_active=true"))
    conn.execute(
        sa.text(
            "INSERT INTO voucher_templates (template_id, version, template_content, is_active, created_at) VALUES (:id, :version, :content, true, now())"
        ),
        {
            "id": uuid.uuid4(),
            "version": "v4.0-authorization",
            "content": (Path(__file__).resolve().parents[2] / "app/templates/voucher_default.html").read_text(),
        },
    )


def downgrade():
    # Keep templates and generated PDFs as historical records.
    pass
