# backend/app/services/extraction_worker.py
"""
Asyncio background worker that polls the extraction_jobs table and
processes pending jobs without any Redis/RQ dependency.

Lifecycle:
  PENDING  → claims job (IN_PROGRESS), sets Request to PROCESSING
  success  → job DONE, Request to READY_FOR_REVIEW or EXCEPTION (based on validation flags)
  failure  → retry_count + 1; if retry_count < 3 reset to PENDING, else FAILED + Request EXCEPTION
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import AsyncSessionLocal
from app.models.audit import AuditEventType
from app.models.draft import Draft, ExtractionStatus
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
from app.storage import file_store
from app.validation.engine import run_validation

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2
MAX_RETRIES = 3


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

    now = datetime.now(timezone.utc)
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
) -> tuple[HeaderFields | None, list[LineItem], PaymentDetails | None]:
    """
    Persist extracted fields into drafts / header_fields / line_items /
    payment_details. Returns the ORM objects needed for validation.
    """
    now = datetime.now(timezone.utc)

    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE if result.header else ExtractionStatus.PARTIAL,
        extracted_at=now,
    )
    db.add(draft)
    await db.flush()

    header_orm: HeaderFields | None = None
    if result.header:
        h = result.header
        header_orm = HeaderFields(
            header_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            vendor_name=h.vendor_name,
            invoice_number=h.invoice_number,
            invoice_date=h.invoice_date,
            currency=h.currency,
            subtotal=float(h.subtotal) if h.subtotal is not None else None,
            total_amount=float(h.total_amount) if h.total_amount is not None else None,
            tax_amount=float(h.tax_amount) if h.tax_amount is not None else None,
            fee_amount=float(h.fee_amount) if h.fee_amount is not None else None,
            rounding_adj=float(h.rounding_adj) if h.rounding_adj is not None else None,
        )
        db.add(header_orm)

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
            description=li.description,
            quantity=float(li.quantity) if li.quantity is not None else None,
            unit=li.unit,
            unit_price=float(li.unit_price) if li.unit_price is not None else None,
            line_total=float(li.line_total) if li.line_total is not None else None,
            extraction_status=status,
        )
        db.add(line_orm)
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
    return header_orm, line_item_orms, payment_orm


async def _process_job(job: ExtractionJob) -> None:
    """Process a single IN_PROGRESS extraction job end-to-end."""
    async with AsyncSessionLocal() as db:
        try:
            # Fetch the request to get file key
            req = await request_repository.get_by_id(db, job.request_id)
            if req is None:
                logger.error("ExtractionJob %s: request %s not found", job.job_id, job.request_id)
                return

            # Download the evidence file from S3
            file_bytes = await file_store.download(req.invoice_file_key)
            # Infer content type from the key extension
            key_lower = req.invoice_file_key.lower()
            if key_lower.endswith(".pdf"):
                ct = "application/pdf"
            elif key_lower.endswith(".png"):
                ct = "image/png"
            else:
                ct = "image/jpeg"

            # Run extraction
            provider = get_extraction_provider()
            result = await provider.extract(
                evidence_files=[(file_bytes, ct)],
                request_id=job.request_id,
            )

            # Persist extracted fields
            header_orm, line_item_orms, payment_orm = await _persist_extraction(
                db, job.request_id, result
            )

            # Run validation
            flags = await run_validation(
                request_id=job.request_id,
                header=header_orm,
                line_items=line_item_orms,
                payment=payment_orm,
                db=db,
            )

            # Apply flags to request
            for flag in flags:
                await request_repository.apply_flag(db, job.request_id, flag)

            # Determine final request status
            new_req_status = (
                RequestStatus.EXCEPTION if flags else RequestStatus.READY_FOR_REVIEW
            )
            await request_repository.update_status(db, job.request_id, new_req_status)
            await log_event(
                db,
                AuditEventType.STATUS_CHANGED,
                payload={
                    "from": "PROCESSING",
                    "to": new_req_status.value,
                    "flags": flags,
                    "job_id": str(job.job_id),
                },
                request_id=job.request_id,
                actor_id=None,
            )

            # Mark job as DONE
            now = datetime.now(timezone.utc)
            await db.execute(
                update(ExtractionJob)
                .where(ExtractionJob.job_id == job.job_id)
                .values(status=ExtractionJobStatus.DONE, completed_at=now)
            )
            await db.commit()
            logger.info(
                "ExtractionJob %s completed → request %s status=%s",
                job.job_id, job.request_id, new_req_status.value,
            )

        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("ExtractionJob %s failed: %s", job.job_id, exc)
            await _handle_failure(job, str(exc))


async def _handle_failure(job: ExtractionJob, error_detail: str) -> None:
    """Increment retry_count; reset to PENDING or mark FAILED after max retries."""
    async with AsyncSessionLocal() as db:
        # Re-fetch the latest retry_count from DB
        result = await db.execute(
            select(ExtractionJob).where(ExtractionJob.job_id == job.job_id)
        )
        fresh_job = result.scalar_one_or_none()
        if fresh_job is None:
            return

        new_retry_count = fresh_job.retry_count + 1
        now = datetime.now(timezone.utc)

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
                job.job_id, new_retry_count,
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
                job.job_id, new_retry_count, MAX_RETRIES,
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
