# backend/app/routes/voucher.py
"""
Voucher PDF generation endpoint.

POST /requests/{request_id}/voucher-pdf
  - Authorized: CLIENT_PIC with active ClientAssignment
  - Guard: request.status must be AMIGA_REVIEWED
  - Pipeline: load data → render HTML → generate PDF → upload S3 → persist → audit → telemetry
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import jinja2
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.tax_assignment import TaxAssignment
from app.models.telemetry import MetricType, TelemetryRecord
from app.models.user import User
from app.models.voucher import VoucherPDF
from app.repositories.user_repository import user_repository
from app.services.audit_service import log_event
from app.services.tax_service import compute_tax_summary
from app.services.template_service import load_active_template
from app.storage import StorageError, file_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["voucher"])

# SLA threshold for PDF generation (ms)
_PDF_SLA_MS = 5_000


@router.post(
    "/{request_id}/voucher-pdf",
    status_code=status.HTTP_201_CREATED,
    summary="Generate a Voucher PDF for an approved request (Client PIC only)",
)
async def generate_voucher_pdf(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("CLIENT_PIC")),
) -> dict:
    """
    Full pipeline:
    1.  Status guard: request must be AMIGA_REVIEWED (HTTP 422 otherwise)
    2.  Client assignment check
    3.  Load active VoucherTemplate
    4.  Build Jinja2 context from HeaderFields, LineItems, TaxAssignments, PaymentDetails
    5.  Render HTML via Jinja2
    6.  Convert to PDF via WeasyPrint
    7.  Compute SHA-256 hashes (disclaimer + rendered HTML)
    8.  Upload PDF to S3
    9.  Persist VoucherPDF record
    10. Write VOUCHER_GENERATED audit event
    11. Record PDF_GENERATION_DURATION telemetry (best-effort)

    Returns 201 with voucher_pdf_id, file_key, generated_at.
    """
    # ── 0. Load request ───────────────────────────────────────────────────────
    result = await db.execute(
        select(InvoiceRequest).where(InvoiceRequest.request_id == request_id)
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

    # ── 1. Status guard (Req 15.3) ────────────────────────────────────────────
    if req.status != RequestStatus.AMIGA_REVIEWED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This Request has not completed the required independent review.",
        )

    # ── 2. Client assignment check ────────────────────────────────────────────
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access Requests for this client.",
        )

    # ── 3. Load active template (Req 22.3) ────────────────────────────────────
    template = await load_active_template(db)

    # ── 4. Gather draft data ──────────────────────────────────────────────────
    draft_result = await db.execute(
        select(Draft).where(Draft.request_id == request_id)
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voucher PDF generation failed. Please try again.",
        )

    header_result = await db.execute(
        select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
    )
    header = header_result.scalar_one_or_none()

    line_items_result = await db.execute(
        select(LineItem).where(LineItem.draft_id == draft.draft_id).order_by(LineItem.sequence_number)
    )
    line_items = list(line_items_result.scalars().all())

    payment_result = await db.execute(
        select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
    )
    payment = payment_result.scalar_one_or_none()

    # Load tax assignments for all line items
    line_item_ids = [item.line_item_id for item in line_items]
    tax_map: dict[str, TaxAssignment] = {}
    if line_item_ids:
        ta_result = await db.execute(
            select(TaxAssignment).where(TaxAssignment.line_item_id.in_(line_item_ids))
        )
        for ta in ta_result.scalars().all():
            tax_map[str(ta.line_item_id)] = ta

    # ── 5. Build Jinja2 context ───────────────────────────────────────────────
    disclaimer_text = settings.AUTHORIZATION_DISCLAIMER

    # Header fields (safe defaults if None)
    h = header
    header_subtotal = Decimal(str(h.subtotal)) if (h and h.subtotal is not None) else Decimal("0")

    # Line items table: list of dicts
    line_items_table = []
    for item in line_items:
        ta = tax_map.get(str(item.line_item_id))
        line_items_table.append({
            "description": item.description or "",
            "quantity": str(item.quantity) if item.quantity is not None else "",
            "unit": item.unit or "",
            "unit_price": str(item.unit_price) if item.unit_price is not None else "",
            "line_total": str(item.line_total) if item.line_total is not None else "",
            "tax_type": (ta.tax_type.value if hasattr(ta.tax_type, "value") else str(ta.tax_type)) if ta else "NO_TAX",
            "tax_amount": str(ta.tax_amount) if (ta and ta.tax_amount is not None) else "0",
        })

    # Tax summary
    tax_result = compute_tax_summary(line_items, tax_map, header_subtotal)
    tax_summary_table = [
        {"tax_type": row["tax_type"], "subtotal": str(row["subtotal"])}
        for row in tax_result["per_type_subtotals"]
    ]

    context = {
        # Header fields
        "vendor_name": h.vendor_name or "" if h else "",
        "invoice_number": h.invoice_number or "" if h else "",
        "invoice_date": str(h.invoice_date) if (h and h.invoice_date) else "",
        "currency": h.currency or "" if h else "",
        "subtotal": str(header_subtotal),
        "total_amount": str(h.total_amount) if (h and h.total_amount is not None) else "",
        "tax_amount": str(h.tax_amount) if (h and h.tax_amount is not None) else "",
        "fee_amount": str(h.fee_amount) if (h and h.fee_amount is not None) else "",
        "rounding_adj": str(h.rounding_adj) if (h and h.rounding_adj is not None) else "",
        # Line items
        "line_items_table": line_items_table,
        # Tax summary
        "tax_summary_table": tax_summary_table,
        "total_tax": str(tax_result["total_tax"]),
        "grand_total": str(tax_result["grand_total"]),
        # Payment details
        "bank_account_number": (payment.bank_account_number or "") if payment else "",
        "bank_name": (payment.bank_name or "") if payment else "",
        "payment_reference": (payment.payment_reference or "") if payment else "",
        # Disclaimer
        "authorization_disclaimer": disclaimer_text,
    }

    # ── Start timer (after data load, before render + PDF) ───────────────────
    start_ms = int(time.monotonic() * 1000)

    try:
        # ── 6. Render HTML ────────────────────────────────────────────────────
        rendered_html = (
            jinja2.Environment(loader=jinja2.BaseLoader())
            .from_string(template.template_content)
            .render(context)
        )

        # ── 7. Convert to PDF ─────────────────────────────────────────────────
        import weasyprint  # lazy import — requires native Pango/GLib at runtime
        pdf_bytes: bytes = weasyprint.HTML(string=rendered_html).write_pdf()

        # ── 8. Compute hashes ─────────────────────────────────────────────────
        disclaimer_hash = hashlib.sha256(disclaimer_text.encode()).hexdigest()
        template_hash = hashlib.sha256(rendered_html.encode()).hexdigest()

        # ── 9. Generate IDs and upload to S3 ─────────────────────────────────
        voucher_pdf_id = uuid.uuid4()
        file_key = f"vouchers/{request_id}/{voucher_pdf_id}.pdf"
        await file_store.upload(file_key, pdf_bytes, "application/pdf")

    except (StorageError, Exception) as exc:
        logger.error("Voucher PDF generation failed for request %s: %s", request_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voucher PDF generation failed. Please try again.",
        )

    # ── Stop timer ────────────────────────────────────────────────────────────
    end_ms = int(time.monotonic() * 1000)
    duration_ms = end_ms - start_ms

    # ── 10. Persist VoucherPDF + AuditEvent (Req 15.6, 22.4) ─────────────────
    generated_at = datetime.now(timezone.utc)
    try:
        voucher_record = VoucherPDF(
            voucher_pdf_id=voucher_pdf_id,
            request_id=request_id,
            file_key=file_key,
            generated_at=generated_at,
            generated_by=current_user.user_id,
            template_version=template.version,
            disclaimer_hash=disclaimer_hash,
            template_hash=template_hash,
        )
        db.add(voucher_record)
        await db.flush()

        await log_event(
            db,
            AuditEventType.VOUCHER_GENERATED,
            payload={
                "voucher_pdf_id": str(voucher_pdf_id),
                "template_version": template.version,
                "generated_by": str(current_user.user_id),
                "file_key": file_key,
            },
            request_id=request_id,
            actor_id=current_user.user_id,
        )

        await db.commit()

    except Exception as exc:
        logger.error("Failed to persist VoucherPDF record for request %s: %s", request_id, exc)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voucher PDF generation failed. Please try again.",
        )

    # ── 11. Telemetry (best-effort — Req 20.3, 20.6) ─────────────────────────
    is_breach = duration_ms > _PDF_SLA_MS
    try:
        telemetry_record = TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.PDF_GENERATION_DURATION,
            subject_id=str(voucher_pdf_id),
            duration_ms=duration_ms,
            page_count=None,
            is_breach=is_breach,
            breach_threshold_ms=_PDF_SLA_MS,
            measured_at=generated_at,
        )
        db.add(telemetry_record)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telemetry write failed for voucher %s (best-effort): %s", voucher_pdf_id, exc)

    return {
        "voucher_pdf_id": str(voucher_pdf_id),
        "file_key": file_key,
        "generated_at": generated_at.isoformat(),
    }
