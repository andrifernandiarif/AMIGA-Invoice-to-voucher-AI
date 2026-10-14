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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.audit import AuditEvent, AuditEventType
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineExtractionStatus, LineItem, PaymentDetails
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
        _engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
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

        from app.services.extraction_worker import _fetch_evidence_files

        evidence_files = await _fetch_evidence_files(db, request_id)

        # ── Call extraction provider ───────────────────────────────────────────
        result: ExtractionResult = await provider.extract(evidence_files, request_id)

        # ── Persist Draft ──────────────────────────────────────────────────────
        if existing_draft:
            draft = existing_draft
            draft.extraction_status = ExtractionStatus.COMPLETE
            draft.extracted_at = datetime.now(UTC)
        else:
            draft = Draft(
                draft_id=uuid.uuid4(),
                request_id=request_id,
                ai_label_visible=True,
                extraction_status=ExtractionStatus.COMPLETE,
                extracted_at=datetime.now(UTC),
            )
            db.add(draft)
            await db.flush()

        # ── Persist HeaderFields ───────────────────────────────────────────────
        h = result.header
        header = HeaderFields(
            header_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            vendor_name=h.vendor_name if h else None,
            invoice_number=h.invoice_number if h else None,
            invoice_date=h.invoice_date if h else None,
            currency=h.currency if h else None,
            subtotal=h.subtotal if h else None,
            total_amount=h.total_amount if h else None,
            tax_amount=h.tax_amount if h else None,
            fee_amount=h.fee_amount if h else None,
            rounding_adj=h.rounding_adj if h else None,
        )
        db.add(header)

        # ── Persist LineItems ──────────────────────────────────────────────────
        for li in result.line_items:
            partial_fields = [li.description, li.quantity, li.unit, li.unit_price, li.line_total]
            ext_status = (
                LineExtractionStatus.PARTIAL
                if any(f is None for f in partial_fields)
                else LineExtractionStatus.COMPLETE
            )
            db.add(
                LineItem(
                    line_item_id=uuid.uuid4(),
                    draft_id=draft.draft_id,
                    sequence_number=li.sequence_number,
                    item_date=li.item_date,
                    description=li.description,
                    quantity=li.quantity,
                    unit=li.unit,
                    unit_price=li.unit_price,
                    line_total=li.line_total,
                    extraction_status=ext_status,
                )
            )

        # ── Persist PaymentDetails ─────────────────────────────────────────────
        pd = result.payment_details
        db.add(
            PaymentDetails(
                payment_id=uuid.uuid4(),
                draft_id=draft.draft_id,
                bank_account_number=pd.bank_account_number if pd else None,
                bank_name=pd.bank_name if pd else None,
                payment_reference=pd.payment_reference if pd else None,
            )
        )
        await db.flush()

        # ── Telemetry ──────────────────────────────────────────────────────────
        end_time = datetime.now(UTC)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        await _record_telemetry(db, str(request_id), duration_ms)

        # ── Audit: extraction completed ────────────────────────────────────────
        db.add(
            AuditEvent(
                event_id=uuid.uuid4(),
                request_id=request_id,
                event_type=AuditEventType.STATUS_CHANGED,
                actor_id=None,
                payload={
                    "action": "EXTRACTION_COMPLETE",
                    "extraction_prompt_version": result.extraction_prompt_version,
                    "duration_ms": duration_ms,
                    "line_items_extracted": len(result.line_items),
                },
                occurred_at=end_time,
            )
        )

        # ── Run validation + transition status ─────────────────────────────────
        await _mark_ready_for_manual_review(db, request_id, draft)
        await db.commit()


async def _mark_ready_for_manual_review(db: AsyncSession, request_id: uuid.UUID, draft: Draft) -> None:
    """Run all validators and set the request status accordingly."""
    from sqlalchemy import update

    new_status = RequestStatus.READY_FOR_REFINEMENT
    await db.execute(
        update(InvoiceRequest)
        .where(InvoiceRequest.request_id == request_id)
        .values(
            status=new_status,
            flags=[],
            updated_at=datetime.now(UTC),
        )
    )
    db.add(
        AuditEvent(
            event_id=uuid.uuid4(),
            request_id=request_id,
            event_type=AuditEventType.STATUS_CHANGED,
            actor_id=None,
            payload={"from_status": "PROCESSING", "to_status": new_status.value},
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
