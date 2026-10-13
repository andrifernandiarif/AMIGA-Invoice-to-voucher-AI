# backend/app/routes/requests.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.audit import AuditEventType
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineItem, LineExtractionStatus, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.telemetry import MetricType, TelemetryRecord
from app.models.user import User, UserRole
from app.providers import get_extraction_provider
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import CreateRequestResponse
from app.services.audit_service import log_event
from app.services.request_service import RequestIdGenerationError, generate_request_id
from app.storage import StorageError, file_store
from app.validation.completeness import CompletenessValidator
from app.validation.calculation import CalculationValidator
from app.validation.duplicate import DuplicateDetector

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB per file
MAX_FILES_PER_SESSION = 20
ACCEPTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateRequestResponse)
async def upload_invoice(
    files: List[UploadFile],
    client_id: uuid.UUID = Form(...),
    request_type: str = Form(..., min_length=1, max_length=100),
    channel: str = Form(..., min_length=1, max_length=100),
    request_date: date = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("CLIENT_PIC", "INTAKE_SUPPORT")),
):
    """
    Upload one or more evidence files (PDF/PNG/JPG/JPEG).
    Extraction runs inline via Bedrock (no Redis/RQ required).
    """
    # ── Validate file count ────────────────────────────────────────────────────
    if not files:
        raise HTTPException(422, "At least one file is required.")
    if len(files) > MAX_FILES_PER_SESSION:
        raise HTTPException(422, f"Maximum {MAX_FILES_PER_SESSION} files per session.")

    # ── INTAKE_SUPPORT assignment check ───────────────────────────────────────
    if current_user.role == UserRole.INTAKE_SUPPORT:
        active = await user_repository.is_assignment_active(db, current_user.user_id, client_id)
        if not active:
            raise HTTPException(403, "You are not authorized to submit for the selected client.")

    # ── Read and validate all files ───────────────────────────────────────────
    validated: list[tuple[str, bytes, str]] = []   # (filename, bytes, content_type)
    for f in files:
        ct = (f.content_type or "").lower()
        name = f.filename or "unknown"
        if ct not in ACCEPTED_TYPES:
            raise HTTPException(415, f"'{name}': unsupported format '{ct}'. Accepted: PDF, PNG, JPG, JPEG.")
        data = await f.read()
        if len(data) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(413, f"'{name}' exceeds the 20 MB limit.")
        validated.append((name, data, ct))

    # ── Generate request ID ────────────────────────────────────────────────────
    try:
        request_id = await generate_request_id(db)
    except RequestIdGenerationError as exc:
        raise HTTPException(500, str(exc))

    # ── Upload all files to S3 ─────────────────────────────────────────────────
    evidence_keys: list[str] = []
    evidence_content_types: list[str] = []
    evidence_meta: list[dict] = []
    for order, (name, data, ct) in enumerate(validated, start=1):
        key = f"evidence/{request_id}/{order}_{name}"
        try:
            await file_store.upload(key, data, ct)
        except StorageError as exc:
            raise HTTPException(502, f"S3 upload failed for '{name}': {exc}")
        evidence_keys.append(key)
        evidence_content_types.append(ct)
        evidence_meta.append({"filename": name, "content_type": ct, "file_key": key, "upload_order": order})

    # ── Create Request in QUEUED then immediately PROCESSING ──────────────────
    req = await request_repository.create_request(
        db,
        request_id=request_id,
        client_id=client_id,
        uploader_id=current_user.user_id,
        request_type=request_type,
        channel=channel,
        request_date=request_date,
        invoice_file_key=evidence_keys[0],
        invoice_page_count=None,
    )

    await log_event(db, AuditEventType.REQUEST_CREATED, payload={
        "request_id": str(request_id),
        "client_id": str(client_id),
        "uploader_id": str(current_user.user_id),
        "upload_timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence_files": evidence_meta,
    }, request_id=request_id, actor_id=current_user.user_id)

    await db.execute(
        sa_update(InvoiceRequest)
        .where(InvoiceRequest.request_id == request_id)
        .values(status=RequestStatus.PROCESSING, updated_at=datetime.now(timezone.utc))
    )
    await log_event(db, AuditEventType.STATUS_CHANGED,
        payload={"from_status": "QUEUED", "to_status": "PROCESSING"},
        request_id=request_id)
    await db.flush()

    # ── Run Bedrock extraction inline ──────────────────────────────────────────
    start_time = datetime.now(timezone.utc)
    provider = get_extraction_provider()
    evidence_files = [(data, ct) for (_, data, ct) in validated]

    try:
        result = await provider.extract(evidence_files, request_id)
    except Exception as exc:
        # Mark EXCEPTION + PROCESSING_FAILED and return the request anyway
        await db.execute(sa_update(InvoiceRequest).where(InvoiceRequest.request_id == request_id)
            .values(status=RequestStatus.EXCEPTION, updated_at=datetime.now(timezone.utc)))
        await log_event(db, AuditEventType.PROCESSING_FAILED,
            payload={"error": str(exc)}, request_id=request_id)
        await db.flush()
        raise HTTPException(502, f"Bedrock extraction failed: {exc}")

    duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    # ── Persist Draft ──────────────────────────────────────────────────────────
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    await db.flush()

    # ── Persist HeaderFields ───────────────────────────────────────────────────
    h = result.header
    db.add(HeaderFields(
        header_id=uuid.uuid4(), draft_id=draft.draft_id,
        vendor_name=h.vendor_name if h else None,
        invoice_number=h.invoice_number if h else None,
        invoice_date=h.invoice_date if h else None,
        currency=h.currency if h else None,
        subtotal=h.subtotal if h else None,
        total_amount=h.total_amount if h else None,
        tax_amount=h.tax_amount if h else None,
        fee_amount=h.fee_amount if h else None,
        rounding_adj=h.rounding_adj if h else None,
    ))

    # ── Persist LineItems ──────────────────────────────────────────────────────
    for li in result.line_items:
        ext_st = (LineExtractionStatus.PARTIAL
                  if any(v is None for v in [li.description, li.quantity, li.unit, li.unit_price, li.line_total])
                  else LineExtractionStatus.COMPLETE)
        db.add(LineItem(
            line_item_id=uuid.uuid4(), draft_id=draft.draft_id,
            sequence_number=li.sequence_number,
            description=li.description, quantity=li.quantity,
            unit=li.unit, unit_price=li.unit_price,
            line_total=li.line_total, extraction_status=ext_st,
        ))

    # ── Persist PaymentDetails ─────────────────────────────────────────────────
    pd = result.payment_details
    db.add(PaymentDetails(
        payment_id=uuid.uuid4(), draft_id=draft.draft_id,
        bank_account_number=pd.bank_account_number if pd else None,
        bank_name=pd.bank_name if pd else None,
        payment_reference=pd.payment_reference if pd else None,
    ))
    await db.flush()

    # ── Telemetry ──────────────────────────────────────────────────────────────
    db.add(TelemetryRecord(
        record_id=uuid.uuid4(), metric_type=MetricType.EXTRACTION_DURATION,
        subject_id=str(request_id), duration_ms=duration_ms,
        is_breach=duration_ms > 30_000, breach_threshold_ms=30_000,
        measured_at=datetime.now(timezone.utc),
    ))

    # ── Validation + status transition ─────────────────────────────────────────
    from decimal import Decimal
    header_row = await db.get(HeaderFields, draft.draft_id)  # type: ignore
    from sqlalchemy import select
    li_rows_r = await db.execute(select(LineItem).where(LineItem.draft_id == draft.draft_id))
    li_rows = list(li_rows_r.scalars().all())
    pd_row = await db.get(PaymentDetails, draft.draft_id)  # type: ignore

    flags: list[str] = []
    tolerance = Decimal(settings.CALCULATION_TOLERANCE)

    missing = CompletenessValidator.validate(pd_row, li_rows)
    if missing:
        flags.append("MISSING_EVIDENCE")
        await log_event(db, AuditEventType.FLAG_APPLIED,
            payload={"flag": "MISSING_EVIDENCE", "missing_fields": missing},
            request_id=request_id)

    calc_issues = CalculationValidator.validate(header_row, li_rows, tolerance)
    if calc_issues:
        flags.append("CALC_DISCREPANCY")
        await log_event(db, AuditEventType.FLAG_APPLIED,
            payload={"flag": "CALC_DISCREPANCY", "discrepancies": calc_issues},
            request_id=request_id)

    if h and h.vendor_name and h.invoice_number:
        matches = await DuplicateDetector.detect(request_id, h.vendor_name, h.invoice_number, db)
        if matches:
            flags.append("POSSIBLE_DUPLICATE")
            await log_event(db, AuditEventType.FLAG_APPLIED,
                payload={"flag": "POSSIBLE_DUPLICATE", "matches": [str(m) for m in matches]},
                request_id=request_id)

    new_status = RequestStatus.EXCEPTION if flags else RequestStatus.READY_FOR_REVIEW
    await db.execute(sa_update(InvoiceRequest).where(InvoiceRequest.request_id == request_id)
        .values(status=new_status, flags=flags, updated_at=datetime.now(timezone.utc)))
    await log_event(db, AuditEventType.STATUS_CHANGED,
        payload={"from_status": "PROCESSING", "to_status": new_status.value,
                 "flags": flags, "duration_ms": duration_ms,
                 "extraction_prompt_version": result.extraction_prompt_version},
        request_id=request_id)

    await db.flush()

    # Reload request to return updated status
    req = await request_repository.get_by_id(db, request_id)
    return req
