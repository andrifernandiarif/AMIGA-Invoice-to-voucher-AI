"""Company numbering, upload metadata and account signatories.

Revision ID: 0009
Revises: 0008
"""

import uuid
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("clients", sa.Column("client_code", sa.String(40), nullable=True))
    op.add_column("clients", sa.Column("voucher_sequence", sa.Integer(), nullable=False, server_default="0"))
    op.create_unique_constraint("uq_clients_client_code", "clients", ["client_code"])
    for name, type_ in [("full_name", sa.String(300)), ("job_title", sa.String(200)), ("signature_data", sa.Text())]:
        op.add_column("users", sa.Column(name, type_, nullable=True))
    for name, type_ in [
        ("voucher_purpose", sa.String(1000)),
        ("approvers", sa.JSON()),
        ("voucher_number", sa.String(100)),
        ("voucher_created_at", sa.DateTime(timezone=True)),
        ("submitter_snapshot", sa.JSON()),
    ]:
        op.add_column("requests", sa.Column(name, type_, nullable=True))
    op.create_unique_constraint("uq_requests_voucher_number", "requests", ["voucher_number"])
    # Stable, unique fallback codes for existing companies. New companies can
    # supply a shorter business code explicitly; no guessing duplicate acronyms.
    from app.services.voucher_identity import default_client_code

    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT client_id, client_name FROM clients")).mappings():
        connection.execute(
            sa.text("UPDATE clients SET client_code=:code WHERE client_id=:id"),
            {"code": default_client_code(row["client_name"], row["client_id"]), "id": row["client_id"]},
        )
    # Count vouchers, not PDF versions. Old stored PDFs remain untouched.
    op.execute("""WITH generated AS (
        SELECT r.request_id, r.client_id, min(v.generated_at) AS made_at
        FROM requests r JOIN voucher_pdfs v ON v.request_id=r.request_id
        GROUP BY r.request_id, r.client_id
    ), numbered AS (
        SELECT *, row_number() OVER (PARTITION BY client_id ORDER BY made_at, request_id) AS seq FROM generated
    ) UPDATE requests r SET voucher_number=c.client_code || '-' || n.seq,
        voucher_created_at=n.made_at
        FROM numbered n JOIN clients c ON c.client_id=n.client_id WHERE r.request_id=n.request_id""")
    op.execute("""UPDATE clients c SET voucher_sequence=(
        SELECT count(*) FROM requests r WHERE r.client_id=c.client_id AND r.voucher_number IS NOT NULL)""")
    op.execute("""UPDATE requests r SET voucher_purpose=h.voucher_purpose
        FROM drafts d JOIN header_fields h ON h.draft_id=d.draft_id
        WHERE d.request_id=r.request_id AND h.voucher_purpose IS NOT NULL""")
    # Do not invent missing personal names, professional roles or approvers.
    content = (Path(__file__).resolve().parents[2] / "app/templates/voucher_default.html").read_text()
    op.execute("UPDATE voucher_templates SET is_active=false WHERE is_active=true")
    connection.execute(
        sa.text("""INSERT INTO voucher_templates
        (template_id,version,template_content,is_active,created_by,created_at)
        VALUES (:id,'v3.0-id',:content,true,NULL,now())"""),
        {"id": uuid.uuid4(), "content": content},
    )


def downgrade():
    op.execute("DELETE FROM voucher_templates WHERE version='v3.0-id'")
    op.execute("UPDATE voucher_templates SET is_active=true WHERE version='v2.0-id'")
    op.drop_constraint("uq_requests_voucher_number", "requests", type_="unique")
    for column in ["submitter_snapshot", "voucher_created_at", "voucher_number", "approvers", "voucher_purpose"]:
        op.drop_column("requests", column)
    for column in ["signature_data", "job_title", "full_name"]:
        op.drop_column("users", column)
    op.drop_constraint("uq_clients_client_code", "clients", type_="unique")
    op.drop_column("clients", "voucher_sequence")
    op.drop_column("clients", "client_code")
