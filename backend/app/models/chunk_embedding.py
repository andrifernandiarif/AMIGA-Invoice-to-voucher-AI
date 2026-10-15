"""Retrieval chunks. Titan V2 uses 1024 dimensions (1536 is Titan G1)."""

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base

EMBEDDING_DIMENSIONS = 1024


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("request_id", "chunk_type", name="uq_chunk_embeddings_request_type"),
        Index("ix_chunk_embeddings_request_id", "request_id"),
        Index(
            "ix_chunk_embeddings_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 1},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.request_id", ondelete="CASCADE"), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(40), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # SQLAlchemy reserves .metadata for the model registry.
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS).with_variant(JSON(), "sqlite"), nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
