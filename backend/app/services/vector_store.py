"""Bound-parameter pgvector SQL; caller controls the transaction."""

import json
import uuid
from sqlalchemy import text
from app.config import settings
from app.models.chunk_embedding import ChunkEmbedding
from app.services.embedding_service import validate_vector


def vector_literal(vector):
    return json.dumps(validate_vector(vector), allow_nan=False, separators=(",", ":"))


async def upsert_chunks(request_id, chunks, embeddings, db):
    if len(chunks) != len(embeddings) or not chunks:
        raise ValueError("Each non-empty chunk list needs exactly one embedding per chunk.")
    if len({chunk.chunk_type for chunk in chunks}) != len(chunks):
        raise ValueError("Chunk types must be unique within a request.")
    # Validate every vector before writing any rows.
    vectors = [vector_literal(vector) for vector in embeddings]
    for chunk, vector in zip(chunks, vectors):
        await db.execute(
            text("""
            INSERT INTO chunk_embeddings
                (chunk_id, request_id, chunk_type, chunk_text, metadata, embedding, embedding_model, updated_at)
            VALUES (:chunk_id, :request_id, :chunk_type, :chunk_text, CAST(:metadata AS jsonb),
                    CAST(:embedding AS vector), :model_id, now())
            ON CONFLICT (request_id, chunk_type) DO UPDATE SET
                chunk_text=EXCLUDED.chunk_text, metadata=EXCLUDED.metadata,
                embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model, updated_at=now()
        """),
            {
                "chunk_id": uuid.uuid4(),
                "request_id": request_id,
                "chunk_type": chunk.chunk_type,
                "chunk_text": chunk.chunk_text,
                "metadata": json.dumps(chunk.metadata),
                "embedding": vector,
                "model_id": settings.TITAN_EMBED_MODEL_ID,
            },
        )


async def similarity_search(query_vector, top_k, db, filter_request_id=None, *, user_id=None):
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50.")
    params = {"vector": vector_literal(query_vector), "top_k": top_k, "model_id": settings.TITAN_EMBED_MODEL_ID}
    conditions = ["ce.embedding_model = :model_id"]
    if filter_request_id is not None:
        conditions.append("ce.request_id = :request_id")
        params["request_id"] = filter_request_id
    if user_id is not None:
        # Authorization is applied before LIMIT, so inaccessible neighbors cannot
        # crowd authorized results out of the candidate set.
        conditions.append("""EXISTS (SELECT 1 FROM requests r JOIN client_assignments ca ON ca.client_id=r.client_id
            WHERE r.request_id=ce.request_id AND ca.user_id=:user_id AND ca.is_active=true)""")
        params["user_id"] = user_id
    sql = (
        """SELECT ce.chunk_id, ce.request_id, ce.chunk_type, ce.chunk_text, ce.metadata,
                    ce.embedding::text AS embedding_text, ce.embedding_model, ce.updated_at
             FROM chunk_embeddings ce WHERE """
        + " AND ".join(conditions)
        + """
             ORDER BY ce.embedding <=> CAST(:vector AS vector), ce.chunk_id LIMIT :top_k"""
    )
    result = await db.execute(text(sql), params)
    return [
        ChunkEmbedding(
            chunk_id=row["chunk_id"],
            request_id=row["request_id"],
            chunk_type=row["chunk_type"],
            chunk_text=row["chunk_text"],
            chunk_metadata=row["metadata"],
            embedding=json.loads(row["embedding_text"]),
            embedding_model=row["embedding_model"],
            updated_at=row["updated_at"],
        )
        for row in result.mappings()
    ]
