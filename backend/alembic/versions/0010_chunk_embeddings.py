"""Enable pgvector and create Titan V2 retrieval chunks with cosine IVFFlat.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from pgvector.sqlalchemy import Vector

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id", UUID(as_uuid=True), sa.ForeignKey("requests.request_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chunk_type", sa.String(40), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "chunk_type", name="uq_chunk_embeddings_request_type"),
    )
    op.create_index("ix_chunk_embeddings_request_id", "chunk_embeddings", ["request_id"])
    # One list is intentional for the initially small corpus. Rebuild with more
    # lists after growth; creating many lists on an empty table harms recall.
    op.execute(
        "CREATE INDEX ix_chunk_embeddings_cosine ON chunk_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 1)"
    )


def downgrade():
    op.drop_table("chunk_embeddings")
    # Extension may be shared by other applications; retain it.
