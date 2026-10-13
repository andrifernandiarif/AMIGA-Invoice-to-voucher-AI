"""Initial schema — all 12 entities

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Pre-declare enum types with create_type=False so SQLAlchemy never tries to
# CREATE them via op.create_table column processing — we manage DDL ourselves.
# ---------------------------------------------------------------------------
_userrole = postgresql.ENUM(
    "CLIENT_PIC", "INTAKE_SUPPORT", "FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN",
    name="userrole", create_type=False,
)
_requeststatus = postgresql.ENUM(
    "QUEUED", "PROCESSING", "READY_FOR_REVIEW", "EXCEPTION",
    "AMIGA_REVIEWED", "SENT_TO_CLIENT", "REJECTED",
    name="requeststatus", create_type=False,
)
_extractionstatus = postgresql.ENUM(
    "PENDING", "COMPLETE", "PARTIAL", "FAILED",
    name="extractionstatus", create_type=False,
)
_lineextractionstatus = postgresql.ENUM(
    "COMPLETE", "PARTIAL",
    name="lineextractionstatus", create_type=False,
)
_auditeventtype = postgresql.ENUM(
    "REQUEST_CREATED", "STATUS_CHANGED", "FLAG_APPLIED", "FLAG_DISMISSED",
    "FIELD_REVISED", "APPROVED", "REJECTED", "DUPLICATE_DISMISSED", "REALLOCATED",
    "VOUCHER_GENERATED", "VOUCHER_DOWNLOADED", "UNAUTHORIZED_APPROVAL_ATTEMPT",
    "USER_MODIFIED", "PROCESSING_FAILED",
    name="auditeventtype", create_type=False,
)
_metrictype = postgresql.ENUM(
    "EXTRACTION_DURATION", "PAGE_LOAD_DURATION", "PDF_GENERATION_DURATION",
    name="metrictype", create_type=False,
)


def upgrade() -> None:
    # --- Enum types (PostgreSQL does not support CREATE TYPE IF NOT EXISTS;
    #     use DO blocks to swallow duplicate_object errors idempotently) ---
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE userrole AS ENUM (
                'CLIENT_PIC','INTAKE_SUPPORT','FINANCE_REVIEWER',
                'SERVICE_LEAD','SYSTEM_ADMIN'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE requeststatus AS ENUM (
                'QUEUED','PROCESSING','READY_FOR_REVIEW','EXCEPTION',
                'AMIGA_REVIEWED','SENT_TO_CLIENT','REJECTED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE extractionstatus AS ENUM ('PENDING','COMPLETE','PARTIAL','FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE lineextractionstatus AS ENUM ('COMPLETE','PARTIAL');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE auditeventtype AS ENUM (
                'REQUEST_CREATED','STATUS_CHANGED','FLAG_APPLIED','FLAG_DISMISSED',
                'FIELD_REVISED','APPROVED','REJECTED','DUPLICATE_DISMISSED','REALLOCATED',
                'VOUCHER_GENERATED','VOUCHER_DOWNLOADED','UNAUTHORIZED_APPROVAL_ATTEMPT',
                'USER_MODIFIED','PROCESSING_FAILED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE metrictype AS ENUM (
                'EXTRACTION_DURATION','PAGE_LOAD_DURATION','PDF_GENERATION_DURATION'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # --- clients ---
    op.create_table(
        "clients",
        sa.Column("client_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_name", sa.String(300), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("role", _userrole, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    # --- client_assignments ---
    op.create_table(
        "client_assignments",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "client_id", name="uq_user_client"),
    )

    # --- requests ---
    op.create_table(
        "requests",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.client_id"), nullable=False),
        sa.Column("uploader_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("assigned_reviewer", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("request_type", sa.String(100), nullable=False),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("request_date", sa.Date, nullable=False),
        sa.Column("status", _requeststatus, nullable=False, server_default="QUEUED"),
        sa.Column("flags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("invoice_file_key", sa.String(500), nullable=False),
        sa.Column("invoice_page_count", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- drafts ---
    op.create_table(
        "drafts",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.request_id"), nullable=False, unique=True),
        sa.Column("ai_label_visible", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("extraction_status", _extractionstatus, nullable=False, server_default="PENDING"),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- header_fields ---
    op.create_table(
        "header_fields",
        sa.Column("header_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drafts.draft_id"), nullable=False, unique=True),
        sa.Column("vendor_name", sa.String(500), nullable=True),
        sa.Column("invoice_number", sa.String(200), nullable=True),
        sa.Column("invoice_date", sa.Date, nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("subtotal", sa.Numeric(18, 4), nullable=True),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("fee_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("rounding_adj", sa.Numeric(18, 4), nullable=True),
    )

    # --- line_items ---
    op.create_table(
        "line_items",
        sa.Column("line_item_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drafts.draft_id"), nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("line_total", sa.Numeric(18, 4), nullable=True),
        sa.Column("extraction_status", _lineextractionstatus, nullable=False, server_default="COMPLETE"),
    )

    # --- payment_details ---
    op.create_table(
        "payment_details",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drafts.draft_id"), nullable=False, unique=True),
        sa.Column("bank_account_number", sa.String(100), nullable=True),
        sa.Column("bank_name", sa.String(300), nullable=True),
        sa.Column("payment_reference", sa.String(300), nullable=True),
    )

    # --- field_revisions ---
    op.create_table(
        "field_revisions",
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.request_id"), nullable=False),
        sa.Column("field_path", sa.Text, nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("revised_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- audit_events ---
    op.create_table(
        "audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.request_id"), nullable=True),
        sa.Column("event_type", _auditeventtype, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- voucher_pdfs ---
    op.create_table(
        "voucher_pdfs",
        sa.Column("voucher_pdf_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.request_id"), nullable=False),
        sa.Column("file_key", sa.String(500), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("disclaimer_hash", sa.String(64), nullable=False),
    )

    # --- telemetry_records ---
    op.create_table(
        "telemetry_records",
        sa.Column("record_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("metric_type", _metrictype, nullable=False),
        sa.Column("subject_id", sa.String(500), nullable=False),
        sa.Column("duration_ms", sa.BigInteger, nullable=False),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("is_breach", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("breach_threshold_ms", sa.BigInteger, nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- Append-only protection: INSERT-only role grants + DDL triggers ---
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amiga_app') THEN
                CREATE ROLE amiga_app;
            END IF;
        END $$
    """)
    op.execute("REVOKE ALL ON audit_events FROM amiga_app")
    op.execute("GRANT SELECT, INSERT ON audit_events TO amiga_app")
    op.execute("REVOKE ALL ON field_revisions FROM amiga_app")
    op.execute("GRANT SELECT, INSERT ON field_revisions TO amiga_app")

    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Modifications to audit records are not permitted.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("CREATE TRIGGER no_update_audit_events BEFORE UPDATE ON audit_events FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()")
    op.execute("CREATE TRIGGER no_delete_audit_events BEFORE DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()")
    op.execute("CREATE TRIGGER no_update_field_revisions BEFORE UPDATE ON field_revisions FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()")
    op.execute("CREATE TRIGGER no_delete_field_revisions BEFORE DELETE ON field_revisions FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()")


def downgrade() -> None:
    for trigger, table in [
        ("no_delete_field_revisions", "field_revisions"),
        ("no_update_field_revisions", "field_revisions"),
        ("no_delete_audit_events", "audit_events"),
        ("no_update_audit_events", "audit_events"),
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")

    for tbl in [
        "telemetry_records", "voucher_pdfs", "audit_events", "field_revisions",
        "payment_details", "line_items", "header_fields", "drafts",
        "requests", "client_assignments", "users", "clients",
    ]:
        op.drop_table(tbl)

    for typ in ["metrictype", "auditeventtype", "lineextractionstatus", "extractionstatus", "requeststatus", "userrole"]:
        op.execute(f"DROP TYPE IF EXISTS {typ}")
