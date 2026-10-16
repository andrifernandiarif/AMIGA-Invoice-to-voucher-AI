"""Register the AMIGA logo voucher layout; retain content, custom templates and PDFs."""
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    current = conn.execute(sa.text(
        "SELECT template_id,version,created_by FROM voucher_templates WHERE is_active=true"
    )).mappings().first()
    bundled = {"v1.0", "v1.0-id", "v2.0-id", "v3.0-id", "v4.0-authorization", "v5.0-corporate"}
    activate = current is None or (current["created_by"] is None and current["version"] in bundled)
    if activate and current:
        conn.execute(sa.text("UPDATE voucher_templates SET is_active=false WHERE template_id=:id"),
                     {"id": current["template_id"]})
    conn.execute(sa.text("""INSERT INTO voucher_templates
        (template_id,version,template_content,is_active,created_by,created_at)
        VALUES (:id,'v5.1-branded',:content,:active,NULL,now())"""),
        {"id": uuid.uuid4(), "active": activate,
         "content": (Path(__file__).resolve().parents[2]/"app/templates/voucher_default.html").read_text()})


def downgrade():
    # Retain versioned templates and historical PDFs.
    pass
