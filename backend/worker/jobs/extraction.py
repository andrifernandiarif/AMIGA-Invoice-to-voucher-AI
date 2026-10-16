# backend/worker/jobs/extraction.py
"""
RQ job: extract_invoice
Fetches all evidence files for a Request from S3, submits them to the
ExtractionProvider (mock or Bedrock), persists the Draft + extracted fields,
marks the draft ready for manual review, and logs audit events.

Idempotency: if a Draft already exists for the request_id, the job skips
Draft creation and goes straight to manual review.
Retries: 3 attempts with exponential backoff (30s, 60s, 120s).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import create_database_engine
from app.models.audit import AuditEvent, AuditEventType
from app.models.draft import Draft, ExtractionStatus
from app.models.request import InvoiceRequest, RequestStatus
from app.providers import get_extraction_provider
from app.schemas.extraction import ExtractionResult

logger = logging.getLogger(__name__)

# RQ retry config (set on the enqueue side, but documented here)
RETRY_INTERVALS = [30, 60, 120]  # seconds

_engine = None
_session_factory = None


def _get_session_factory() -> async_sessionmaker:
    global _engine, _session_factory
    if _session_factory is None:
        _engine = create_database_engine(null_pool=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


@dataclass
class ExtractionJob:
    request_id: uuid.UUID  # UUID of the Request to process
    file_key: str  # S3 key of the primary invoice file
    invoice_page_count: int  # page count (0 if unknown at upload time)


async def _run_extraction(job: ExtractionJob) -> None:
    """Async core of the extraction job — called by the sync RQ entry point."""
    request_id = job.request_id if isinstance(job.request_id, uuid.UUID) else uuid.UUID(str(job.request_id))
    provider = get_extraction_provider()
    SessionLocal = _get_session_factory()
    start_time = datetime.now(UTC)

    async with SessionLocal() as db:
        # ── Idempotency check ──────────────────────────────────────────────────
        existing_draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
        if existing_draft and existing_draft.extraction_status == ExtractionStatus.COMPLETE:
            logger.info("Draft already exists for %s — skipping to manual review", request_id)
            await _mark_ready_for_manual_review(db, request_id, existing_draft)
            await db.commit()
            return

        from app.services.extraction_worker import _download_evidence_files, _load_evidence_sources

        sources = await _load_evidence_sources(db, request_id)
        await db.commit()
        evidence_files = await _download_evidence_files(sources)

        # ── Call extraction provider ───────────────────────────────────────────
        result: ExtractionResult = await provider.extract(evidence_files, request_id)

        from app.services.extraction_worker import _persist_extraction
        from app.services.telemetry_service import TelemetryService

        await TelemetryService.record_document_timings(db, request_id, result.document_timings)
        if not result.header or not any(item.line_total is not None for item in result.line_items):
            raise ValueError("Extraction returned no usable invoice values.")
        if existing_draft is None:
            draft, *_ = await _persist_extraction(db, request_id, result)
        else:
            draft = existing_draft
        await _record_telemetry(db, str(request_id), int((datetime.now(UTC) - start_time).total_seconds() * 1000))
        await _mark_ready_for_manual_review(db, request_id, draft)
        await db.commit()


async def _mark_ready_for_manual_review(db: AsyncSession, request_id: uuid.UUID, draft: Draft) -> None:
    """Run all validators and set the request status accordingly."""
    from app.services.workflow import finish_extraction

    req = await db.scalar(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id).with_for_update())
    if req.status in (RequestStatus.AMIGA_REVIEWED, RequestStatus.SENT_TO_CLIENT, RequestStatus.REJECTED):
        return
    previous = req.status.value
    await finish_extraction(db, req)
    req.updated_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            event_id=uuid.uuid4(),
            request_id=request_id,
            event_type=AuditEventType.STATUS_CHANGED,
            actor_id=None,
            payload={"from_status": previous, "to_status": req.status.value},
            occurred_at=datetime.now(UTC),
        )
    )


async def _record_telemetry(db: AsyncSession, subject_id: str, duration_ms: int) -> None:
    """Persist a TelemetryRecord for this extraction job."""
    from app.models.telemetry import MetricType, TelemetryRecord

    # Breach threshold: 30s (multi-file SLA per Req 4.5)
    threshold = 30_000
    db.add(
        TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.EXTRACTION_DURATION,
            subject_id=subject_id,
            duration_ms=duration_ms,
            page_count=None,
            is_breach=duration_ms > threshold,
            breach_threshold_ms=threshold,
            measured_at=datetime.now(UTC),
        )
    )


def extract_invoice(job: ExtractionJob) -> None:
    """
    RQ entry point (synchronous).
    Runs the async extraction logic in a new event loop.
    """
    try:
        asyncio.run(_run_extraction(job))
    except Exception as exc:
        logger.exception("extract_invoice failed for request %s: %s", job.request_id, exc)
        # Mark request as EXCEPTION + log PROCESSING_FAILED
        asyncio.run(_mark_processing_failed(str(job.request_id), str(exc)))
        raise  # Re-raise so RQ registers the failure and retries


async def _mark_processing_failed(request_id_str: str, error: str) -> None:
    request_id = uuid.UUID(str(request_id_str))
    SessionLocal = _get_session_factory()
    async with SessionLocal() as db:
        from sqlalchemy import update

        await db.execute(
            update(InvoiceRequest)
            .where(InvoiceRequest.request_id == request_id)
            .values(status=RequestStatus.EXCEPTION, updated_at=datetime.now(UTC))
        )
        db.add(
            AuditEvent(
                event_id=uuid.uuid4(),
                request_id=request_id,
                event_type=AuditEventType.PROCESSING_FAILED,
                actor_id=None,
                payload={"error": error},
                occurred_at=datetime.now(UTC),
            )
        )
        await db.commit()
