"""Prepare all embeddings before an atomic request-scoped upsert."""

from datetime import UTC

from sqlalchemy import select

from app.models.request import InvoiceRequest
from app.services import chunker, embedding_service, vector_store


async def index_request(request_id, db):
    # Standalone indexing owns its transaction; approval uses the durable outbox.
    req = await db.scalar(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id))
    if req is None:
        raise ValueError("Request not found.")
    chunks = await chunker.chunk_request(request_id, db)
    version = req.updated_at
    await db.commit()  # Release the connection before embedding.
    embeddings = await embedding_service.embed_chunks([chunk.chunk_text for chunk in chunks])
    current = await db.scalar(
        select(InvoiceRequest)
        .where(InvoiceRequest.request_id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (current.updated_at.replace(tzinfo=UTC) if current.updated_at.tzinfo is None else current.updated_at) != (
        version.replace(tzinfo=UTC) if version.tzinfo is None else version
    ):
        raise ValueError("Request changed during indexing; retry with live values.")
    await vector_store.upsert_chunks(request_id, chunks, embeddings, db)
    return len(chunks)
