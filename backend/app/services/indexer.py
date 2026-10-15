"""Prepare all embeddings before an atomic request-scoped upsert."""

from sqlalchemy import select
from app.models.request import InvoiceRequest
from app.services import chunker, embedding_service, vector_store


async def index_request(request_id, db):
    # Serialize reindexing of this request without committing the caller's work.
    req = await db.scalar(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id).with_for_update())
    if req is None:
        raise ValueError("Request not found.")
    chunks = await chunker.chunk_request(request_id, db)
    embeddings = await embedding_service.embed_chunks([chunk.chunk_text for chunk in chunks])
    await vector_store.upsert_chunks(request_id, chunks, embeddings, db)
    return len(chunks)
