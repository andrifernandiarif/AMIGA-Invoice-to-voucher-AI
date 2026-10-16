# backend/app/services/extraction_worker.py
"""
Asyncio background worker that polls the extraction_jobs table and
processes pending jobs without any Redis/RQ dependency.

Lifecycle:
  PENDING  → claims job (IN_PROGRESS), sets Request to PROCESSING
  success  → job DONE, Request to READY_FOR_REFINEMENT for Client PIC edits
  failure  → retry_count + 1; if retry_count < 3 reset to PENDING, else FAILED + Request EXCEPTION
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import rollback_safely
from app.dependencies import AsyncSessionLocal
from app.models.audit import AuditEventType
from app.models.draft import Draft, ExtractionStatus
from app.models.evidence_file import EvidenceFile
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.models.invoice_fields import (
    HeaderFields,
    LineExtractionStatus,
    LineItem,
    PaymentDetails,
)
from app.models.request import InvoiceRequest, RequestStatus
from app.providers import get_extraction_provider
from app.repositories.request_repository import request_repository
from app.schemas.extraction import ExtractionResult
from app.services.audit_service import log_event
from app.services.extraction_status import MAX_RETRIES
from app.services.telemetry_service import TelemetryService
from app.storage import file_store

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2


async def _claim_pending_job(db: AsyncSession) -> ExtractionJob | None:
    """
    Atomically claim a single PENDING job using SELECT FOR UPDATE SKIP LOCKED.
    Returns the claimed job (now IN_PROGRESS) or None if the queue is empty.
    """
    stmt = (
        select(ExtractionJob)
        .where(ExtractionJob.status == ExtractionJobStatus.PENDING)
        .order_by(ExtractionJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        return None

    now = datetime.now(UTC)
    await db.execute(
        update(ExtractionJob)
        .where(ExtractionJob.job_id == job.job_id)
        .values(status=ExtractionJobStatus.IN_PROGRESS, started_at=now)
    )
    await db.flush()

    # Transition the Request to PROCESSING
    await request_repository.update_status(db, job.request_id, RequestStatus.PROCESSING)
    await log_event(
        db,
        AuditEventType.STATUS_CHANGED,
        payload={"from": "QUEUED", "to": "PROCESSING", "job_id": str(job.job_id)},
        request_id=job.request_id,
        actor_id=None,
    )

    await db.commit()
    return job


async def _persist_extraction(
    db: AsyncSession,
    request_id: uuid.UUID,
    result: ExtractionResult,
) -> tuple[Draft, HeaderFields | None, list[LineItem], PaymentDetails | None]:
    """
    Persist extracted fields into drafts / header_fields / line_items /
    payment_details. Returns the persisted draft objects.
    """
    now = datetime.now(UTC)

    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE if result.header else ExtractionStatus.PARTIAL,
        extracted_at=now,
        source_invoices=[invoice.model_dump(mode="json") for invoice in result.source_invoices],
    )
    db.add(draft)
    await db.flush()

    header_orm: HeaderFields | None = None
    if result.header:
        h = result.header
        header_orm = HeaderFields(
            header_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            voucher_name=h.voucher_name if h else None,
            vendor_name=h.vendor_name,
            invoice_number=h.invoice_number,
            invoice_date=h.invoice_date,
            currency=h.currency,
            subtotal=h.subtotal,
            total_amount=h.total_amount,
            tax_amount=h.tax_amount,
            fee_amount=h.fee_amount,
            rounding_adj=h.rounding_adj,
        )
        db.add(header_orm)
        await db.flush()

    line_item_orms: list[LineItem] = []
    for li in result.line_items:
        status = (
            LineExtractionStatus.COMPLETE
            if (li.description and li.quantity and li.unit_price and li.line_total)
            else LineExtractionStatus.PARTIAL
        )
        line_orm = LineItem(
            line_item_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            sequence_number=li.sequence_number,
            item_date=li.item_date,
            description=li.description,
            quantity=li.quantity,
            unit=li.unit,
            unit_price=li.unit_price,
            line_total=li.line_total,
            extraction_status=status,
        )
        db.add(line_orm)
        await db.flush()
        line_item_orms.append(line_orm)

    payment_orm: PaymentDetails | None = None
    if result.payment_details:
        pd = result.payment_details
        payment_orm = PaymentDetails(
            payment_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            bank_account_number=pd.bank_account_number,
            bank_name=pd.bank_name,
            payment_reference=pd.payment_reference,
        )
        db.add(payment_orm)
        await db.flush()

    await db.flush()
    return draft, header_orm, line_item_orms, payment_orm


async def _load_evidence_sources(db: AsyncSession, request_id: uuid.UUID) -> list[tuple[str, str]]:
    """Read storage metadata only; callers can release the connection before S3."""
    stmt = select(EvidenceFile).where(EvidenceFile.request_id == request_id).order_by(EvidenceFile.upload_order)
    evidence = list((await db.scalars(stmt)).all())
    if evidence:
        return [(ef.file_key, ef.content_type) for ef in evidence]
    req = await request_repository.get_by_id(db, request_id)
    if req is None:
        raise ValueError(f"Request {request_id} not found")
    key_lower = req.invoice_file_key.lower()
    content_type = (
        "application/pdf"
        if key_lower.endswith(".pdf")
        else ("image/png" if key_lower.endswith(".png") else "image/jpeg")
    )
    return [(req.invoice_file_key, content_type)]


async def _download_evidence_files(sources: list[tuple[str, str]]) -> list[tuple[bytes, str]]:
    return [(await file_store.download(key), content_type) for key, content_type in sources]


async def _fetch_evidence_files(db: AsyncSession, request_id: uuid.UUID) -> list[tuple[bytes, str]]:
    """Compatibility helper; workers use separate metadata and download phases."""
    return await _download_evidence_files(await _load_evidence_sources(db, request_id))


async def _process_job(job: ExtractionJob) -> None:
    """Process a single IN_PROGRESS extraction job end-to-end."""
    start_time = datetime.now(UTC)

    try:
        async with AsyncSessionLocal() as db:
            req = await request_repository.get_by_id(db, job.request_id)
            if req is None:
                logger.error("ExtractionJob %s: request %s not found", job.job_id, job.request_id)
                return
            sources = await _load_evidence_sources(db, job.request_id)
            await db.commit()  # End the read transaction before any remote I/O.

        evidence_files = await _download_evidence_files(sources)
        provider = get_extraction_provider()
        result = await provider.extract(evidence_files=evidence_files, request_id=job.request_id)
        if not result.header or not any(item.line_total is not None for item in result.line_items):
            raise ValueError("Extraction returned no usable invoice values. Please retry extraction.")

        async with AsyncSessionLocal() as db:
            try:
                # Reload live state on a fresh checkout after the potentially long model call.
                req = await db.scalar(
                    select(InvoiceRequest)
                    .where(InvoiceRequest.request_id == job.request_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if req is None:
                    raise ValueError("Request was removed during extraction.")
                # Idempotency check: if Draft already exists, skip creation
                existing_draft = await db.scalar(select(Draft).where(Draft.request_id == job.request_id))

                if not existing_draft:
                    await _persist_extraction(db, job.request_id, result)

                await TelemetryService.record_document_timings(db, job.request_id, result.document_timings)
                from app.services.workflow import finish_extraction

                await finish_extraction(db, req)
                await log_event(
                    db,
                    AuditEventType.STATUS_CHANGED,
                    payload={"from": "PROCESSING", "to": req.status.value, "job_id": str(job.job_id)},
                    request_id=job.request_id,
                    actor_id=None,
                )

                # Telemetry - record extraction duration and check for SLA breach
                end_time = datetime.now(UTC)
                record = await TelemetryService.record_extraction_duration(
                    db=db,
                    job_id=job.job_id,
                    start_time=start_time,
                    end_time=end_time,
                    page_count=req.invoice_page_count,
                )

                # Log breach event if SLA was exceeded
                if record.is_breach:
                    logger.warning(
                        "ExtractionJob %s exceeded SLA threshold: duration=%dms > threshold=%dms (page_count=%s)",
                        job.job_id,
                        record.duration_ms,
                        record.breach_threshold_ms,
                        req.invoice_page_count,
                    )

                # Mark job as DONE
                await db.execute(
                    update(ExtractionJob)
                    .where(ExtractionJob.job_id == job.job_id)
                    .values(status=ExtractionJobStatus.DONE, completed_at=end_time, error_detail=None)
                )
                await db.commit()
                logger.info(
                    "ExtractionJob %s completed → request %s status=%s, duration=%dms",
                    job.job_id,
                    job.request_id,
                    req.status.value,
                    record.duration_ms,
                )

            except Exception:
                await rollback_safely(db)
                raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("ExtractionJob %s failed (%s)", job.job_id, type(exc).__name__)
        await _handle_failure(job, str(exc))


async def _handle_failure(job: ExtractionJob, error_detail: str) -> None:
    """Increment retry_count; reset to PENDING or mark FAILED after max retries."""
    async with AsyncSessionLocal() as db:
        # Re-fetch the latest retry_count from DB
        result = await db.execute(select(ExtractionJob).where(ExtractionJob.job_id == job.job_id))
        fresh_job = result.scalar_one_or_none()
        if fresh_job is None:
            return

        new_retry_count = fresh_job.retry_count + 1
        now = datetime.now(UTC)

        if new_retry_count >= MAX_RETRIES:
            # Give up — mark job FAILED and request EXCEPTION
            await db.execute(
                update(ExtractionJob)
                .where(ExtractionJob.job_id == job.job_id)
                .values(
                    status=ExtractionJobStatus.FAILED,
                    retry_count=new_retry_count,
                    completed_at=now,
                    error_detail=error_detail,
                )
            )
            await request_repository.update_status(db, job.request_id, RequestStatus.EXCEPTION)
            await log_event(
                db,
                AuditEventType.STATUS_CHANGED,
                payload={"from": "PROCESSING", "to": "EXCEPTION"},
                request_id=job.request_id,
                actor_id=None,
            )
            await log_event(
                db,
                AuditEventType.PROCESSING_FAILED,
                payload={
                    "job_id": str(job.job_id),
                    "error": error_detail,
                    "retry_count": new_retry_count,
                },
                request_id=job.request_id,
                actor_id=None,
            )
            logger.error(
                "ExtractionJob %s permanently failed after %d retries → request EXCEPTION",
                job.job_id,
                new_retry_count,
            )
        else:
            # Reset to PENDING for retry
            await db.execute(
                update(ExtractionJob)
                .where(ExtractionJob.job_id == job.job_id)
                .values(
                    status=ExtractionJobStatus.PENDING,
                    retry_count=new_retry_count,
                    started_at=None,
                    error_detail=error_detail,
                )
            )
            logger.warning(
                "ExtractionJob %s retry %d/%d scheduled",
                job.job_id,
                new_retry_count,
                MAX_RETRIES,
            )

        await db.commit()


async def extraction_worker_loop() -> None:
    """
    Polls the extraction_jobs table every POLL_INTERVAL_SECONDS seconds.
    Intended to run as an asyncio background task via FastAPI lifespan.
    """
    logger.info("Extraction worker started (poll interval=%ds)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                job = await _claim_pending_job(db)

            if job is not None:
                # Process the job outside the claim transaction
                await _process_job(job)
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Extraction worker shutting down.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.exception("Extraction worker loop error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
