"""PostgreSQL outbox: claim briefly, embed without a connection, then publish atomically."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from app.models.indexing_job import IndexingJob
from app.models.request import InvoiceRequest
from app.services import chunker, embedding_service, vector_store

logger = logging.getLogger(__name__)


async def enqueue_indexing(db, request_id):
    # Callers hold the request lock, so approvals cannot enqueue competing rows.
    job = await db.get(IndexingJob, request_id)
    if job is None:
        job = IndexingJob(request_id=request_id, revision=0)
        db.add(job)
    job.revision += 1
    job.status, job.attempts = "PENDING", 0
    job.available_at = datetime.now(UTC)
    job.started_at, job.last_error = None, None
    await db.flush()


async def process_next(session_factory):
    now = datetime.now(UTC)
    async with session_factory() as db:
        job = await db.scalar(
            select(IndexingJob)
            .where(
                or_(
                    and_(IndexingJob.status == "PENDING", IndexingJob.available_at <= now),
                    and_(IndexingJob.status == "RUNNING", IndexingJob.started_at < now - timedelta(minutes=5)),
                )
            )
            .order_by(IndexingJob.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return False
        job.status, job.started_at = "RUNNING", now
        job.attempts += 1
        request_id, revision = job.request_id, job.revision
        await db.commit()
    try:
        async with session_factory() as db:
            req = await db.get(InvoiceRequest, request_id)
            if req is None:
                return True
            version = req.updated_at
            chunks = await chunker.chunk_request(request_id, db)
        # No transaction, locks or pooled connection during network/model calls.
        async with asyncio.timeout(120):
            vectors = await embedding_service.embed_chunks([c.chunk_text for c in chunks])
        async with session_factory() as db:
            req = await db.scalar(
                select(InvoiceRequest).where(InvoiceRequest.request_id == request_id).with_for_update()
            )
            job = await db.scalar(select(IndexingJob).where(IndexingJob.request_id == request_id).with_for_update())
            if job is None or job.revision != revision:
                return True
            if req.updated_at != version:
                job.status, job.available_at = "PENDING", datetime.now(UTC)
            else:
                await vector_store.upsert_chunks(request_id, chunks, vectors, db)
                job.status, job.last_error = "DONE", None
            await db.commit()
    except asyncio.CancelledError:
        # RUNNING lease survives shutdown and is recovered after restart.
        raise
    except Exception as exc:
        logger.warning("Indexing failed for %s (%s)", request_id, type(exc).__name__)
        async with session_factory() as db:
            job = await db.scalar(select(IndexingJob).where(IndexingJob.request_id == request_id).with_for_update())
            if job is not None and job.revision == revision:
                job.status = "PENDING" if job.attempts < 5 else "FAILED"
                job.available_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts))
                job.last_error = type(exc).__name__  # Never persist credentials/provider payloads.
                await db.commit()
    return True


async def indexing_worker_loop():
    from app.dependencies import AsyncSessionLocal

    while True:
        try:
            worked = await process_next(AsyncSessionLocal)
            if not worked:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Indexing queue unavailable (%s)", type(exc).__name__)
            await asyncio.sleep(5)
