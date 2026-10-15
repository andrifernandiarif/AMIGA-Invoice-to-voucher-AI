"""python -m scripts.backfill_embeddings [--retry-partial]."""

import argparse
import asyncio
from sqlalchemy import func, select
from app.dependencies import AsyncSessionLocal, engine
from app.models.chunk_embedding import ChunkEmbedding
from app.models.request import InvoiceRequest, RequestStatus
from app.services.indexer import index_request


async def backfill(retry_partial=False):
    async with AsyncSessionLocal() as db:
        count = (
            select(func.count())
            .select_from(ChunkEmbedding)
            .where(ChunkEmbedding.request_id == InvoiceRequest.request_id)
            .correlate(InvoiceRequest)
            .scalar_subquery()
        )
        ids = list(
            (
                await db.scalars(
                    select(InvoiceRequest.request_id)
                    .where(
                        InvoiceRequest.status.in_([RequestStatus.AMIGA_REVIEWED, RequestStatus.SENT_TO_CLIENT]),
                        count < 4 if retry_partial else count == 0,
                    )
                    .order_by(InvoiceRequest.created_at)
                )
            ).all()
        )
    indexed = 0
    failed = 0
    for i, request_id in enumerate(ids):
        try:
            async with AsyncSessionLocal() as db:
                await index_request(request_id, db)
                await db.commit()
            indexed += 1
        except Exception as exc:
            failed += 1
            print(f"Failed request {request_id}: {type(exc).__name__}", flush=True)
        print(f"Indexed {indexed} of {len(ids)} requests (failed: {failed})", flush=True)
        if i + 1 < len(ids):
            await asyncio.sleep(0.1)
    if not ids:
        print("Indexed 0 of 0 requests (nothing to backfill).")
    return indexed, failed


async def main(retry_partial=False):
    try:
        _, failed = await backfill(retry_partial)
        return 1 if failed else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retry-partial", action="store_true", help="Also repair requests with fewer than four chunks."
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().retry_partial)))
