"""Opt-in Neon/pgvector integration test. All synthetic writes roll back.

RUN_PGVECTOR_INTEGRATION=1 .venv/bin/pytest -q tests/test_vector_store_integration.py
"""

import os
import uuid
import pytest
from sqlalchemy import text
from app.dependencies import AsyncSessionLocal
from app.services.chunker import TextChunk
from app.services.vector_store import upsert_chunks, similarity_search

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PGVECTOR_INTEGRATION") != "1", reason="Requires configured PostgreSQL with migration 0010."
)


async def test_real_pgvector_idempotency_cosine_ranking_and_request_filter():
    async with AsyncSessionLocal() as db:
        try:
            rid = await db.scalar(text("SELECT request_id FROM requests ORDER BY created_at LIMIT 1"))
            if not rid:
                pytest.skip("Seed a request before running the live integration test.")
            chunks = [
                TextChunk("INTEGRATION_HEADER", "Vendor name and total amount", {"vendor_name": "Vector test"}),
                TextChunk("INTEGRATION_PAYMENT", "Bank details", {}),
            ]
            first = [1.0] + [0.0] * 1023
            second = [0.0, 1.0] + [0.0] * 1022
            await upsert_chunks(rid, chunks, [first, second], db)
            ids = list(
                (
                    await db.execute(
                        text(
                            "SELECT chunk_id FROM chunk_embeddings WHERE request_id=:id AND chunk_type LIKE 'INTEGRATION_%' ORDER BY chunk_type"
                        ),
                        {"id": rid},
                    )
                ).scalars()
            )
            await upsert_chunks(rid, chunks, [first, second], db)
            again = list(
                (
                    await db.execute(
                        text(
                            "SELECT chunk_id FROM chunk_embeddings WHERE request_id=:id AND chunk_type LIKE 'INTEGRATION_%' ORDER BY chunk_type"
                        ),
                        {"id": rid},
                    )
                ).scalars()
            )
            assert ids == again and len(ids) == 2
            result = await similarity_search(first, 8, db, filter_request_id=rid)
            assert result[0].chunk_type == "INTEGRATION_HEADER"
            assert all(row.request_id == rid for row in result)
            assert result[0].chunk_metadata["vendor_name"] == "Vector test"
            assert len(result[0].embedding) == 1024
            assert (await similarity_search(second, 8, db, filter_request_id=rid))[
                0
            ].chunk_type == "INTEGRATION_PAYMENT"
            assert await similarity_search(first, 8, db, filter_request_id=uuid.uuid4()) == []
            assert await similarity_search(first, 8, db, user_id=uuid.uuid4()) == []
        finally:
            await db.rollback()
