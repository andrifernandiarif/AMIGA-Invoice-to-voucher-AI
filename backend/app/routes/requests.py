# backend/app/routes/requests.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.audit import AuditEventType
from app.models.draft import Draft
from app.models.evidence_file import EvidenceFile
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import (
    CreateRequestResponse,
    DraftResponse,
    EvidenceFileResponse,
    FieldRevisionResponse,
    HeaderFieldsResponse,
    LineItemResponse,
    PaymentDetailsResponse,
    RequestDetailResponse,
    RequestSummary,
)
from app.services.audit_service import log_event
from app.services.request_service import RequestIdGenerationError, generate_request_id
from app.storage import StorageError, file_store

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB per file
MAX_FILES_PER_SESSION = 20
ACCEPTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}


# ─────────────────────────────────────────────────────────────────────────────
# POST /requests  — upload invoice
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateRequestResponse)
async def upload_invoice(
    files: List[UploadFile],
    client_id: uuid.UUID = Form(..., description="Use GET /clients to find your client_id"),
    request_date: date = Form(..., description="Invoice date, e.g. 2026-10-14"),
    request_type: str = Form(default="standard", description="Type of request (default: standard)"),
    channel: str = Form(default="upload", description="Submission channel (default: upload)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("CLIENT_PIC", "INTAKE_SUPPORT")),
):
    """
    Upload one or more evidence files (PDF/PNG/JPG/JPEG).
    Creates the Request in QUEUED status and inserts an ExtractionJob row
    with status=PENDING. The background worker picks up the job and
    transitions the Request to PROCESSING.
    """
    if not files:
        raise HTTPException(422, "At least one file is required.")
    if len(files) > MAX_FILES_PER_SESSION:
        raise HTTPException(422, f"Maximum {MAX_FILES_PER_SESSION} files per session.")

    if current_user.role == UserRole.INTAKE_SUPPORT:
        active = await user_repository.is_assignment_active(db, current_user.user_id, client_id)
        if not active:
            raise HTTPException(403, "You are not authorized to submit for the selected client.")

    validated: list[tuple[str, bytes, str]] = []
    for f in files:
        ct = (f.content_type or "").lower()
        name = f.filename or "unknown"
        if ct not in ACCEPTED_TYPES:
            raise HTTPException(
                415,
                f"'{name}': unsupported format '{ct}'. Accepted: PDF, PNG, JPG, JPEG.",
            )
        data = await f.read()
        if len(data) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(413, f"'{name}' exceeds the 20 MB limit.")
        validated.append((name, data, ct))

    try:
        request_id = await generate_request_id(db)
    except RequestIdGenerationError as exc:
        raise HTTPException(500, str(exc))

    evidence_keys: list[str] = []
    evidence_meta: list[dict] = []
    for order, (name, data, ct) in enumerate(validated, start=1):
        key = f"evidence/{request_id}/{order}_{name}"
        try:
            await file_store.upload(key, data, ct)
        except StorageError as exc:
            raise HTTPException(502, detail=f"S3 upload failed for '{name}': {exc}")
        except Exception as exc:
            raise HTTPException(502, detail=f"File storage error for '{name}': {exc}")
        evidence_keys.append(key)
        evidence_meta.append(
            {"filename": name, "content_type": ct, "file_key": key, "upload_order": order}
        )

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

    await log_event(
        db,
        AuditEventType.REQUEST_CREATED,
        payload={
            "request_id": str(request_id),
            "client_id": str(client_id),
            "uploader_id": str(current_user.user_id),
            "upload_timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_files": evidence_meta,
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    now = datetime.now(timezone.utc)
    extraction_job = ExtractionJob(
        job_id=uuid.uuid4(),
        request_id=request_id,
        status=ExtractionJobStatus.PENDING,
        retry_count=0,
        created_at=now,
    )
    db.add(extraction_job)
    await db.flush()
    await db.commit()

    req = await request_repository.get_by_id(db, request_id)
    return req


# ─────────────────────────────────────────────────────────────────────────────
# GET /requests/{request_id}  — request detail with draft
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{request_id}", response_model=RequestDetailResponse)
async def get_request_detail(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "CLIENT_PIC", "INTAKE_SUPPORT")
    ),
):
    """
    Return the full request detail including the AI-extracted draft (HeaderFields,
    LineItems, PaymentDetails) and all associated evidence file metadata.
    Requires an active ClientAssignment for non-admin roles.
    """
    req = await request_repository.get_by_id(db, request_id)
    if req is None:
        raise HTTPException(404, "Request not found.")

    # Enforce client assignment for non-admin roles
    if current_user.role not in (UserRole.SYSTEM_ADMIN, UserRole.SERVICE_LEAD):
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(403, "You are not authorized to access Requests for this client.")

    # Load evidence files
    ef_result = await db.execute(
        select(EvidenceFile)
        .where(EvidenceFile.request_id == request_id)
        .order_by(EvidenceFile.upload_order)
    )
    evidence_files = ef_result.scalars().all()

    # Load draft + related records
    draft_result = await db.execute(
        select(Draft).where(Draft.request_id == request_id)
    )
    draft = draft_result.scalar_one_or_none()

    draft_response: DraftResponse | None = None
    if draft is not None:
        # Header fields
        hf_result = await db.execute(
            select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
        )
        header = hf_result.scalar_one_or_none()

        # Line items
        li_result = await db.execute(
            select(LineItem)
            .where(LineItem.draft_id == draft.draft_id)
            .order_by(LineItem.sequence_number)
        )
        line_items = li_result.scalars().all()

        # Payment details
        pd_result = await db.execute(
            select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
        )
        payment = pd_result.scalar_one_or_none()

        # Coerce numeric fields to str for the response schema
        def _str_or_none(v) -> str | None:
            return str(v) if v is not None else None

        header_response = None
        if header is not None:
            header_response = HeaderFieldsResponse(
                header_id=header.header_id,
                draft_id=header.draft_id,
                vendor_name=header.vendor_name,
                invoice_number=header.invoice_number,
                invoice_date=str(header.invoice_date) if header.invoice_date else None,
                currency=header.currency,
                subtotal=_str_or_none(header.subtotal),
                total_amount=_str_or_none(header.total_amount),
                tax_amount=_str_or_none(header.tax_amount),
                fee_amount=_str_or_none(header.fee_amount),
                rounding_adj=_str_or_none(header.rounding_adj),
            )

        line_item_responses = [
            LineItemResponse(
                line_item_id=li.line_item_id,
                draft_id=li.draft_id,
                sequence_number=li.sequence_number,
                description=li.description,
                quantity=_str_or_none(li.quantity),
                unit=li.unit,
                unit_price=_str_or_none(li.unit_price),
                line_total=_str_or_none(li.line_total),
                extraction_status=li.extraction_status,
            )
            for li in line_items
        ]

        payment_response = None
        if payment is not None:
            payment_response = PaymentDetailsResponse(
                payment_id=payment.payment_id,
                draft_id=payment.draft_id,
                bank_account_number=payment.bank_account_number,
                bank_name=payment.bank_name,
                payment_reference=payment.payment_reference,
            )

        draft_response = DraftResponse(
            draft_id=draft.draft_id,
            ai_label_visible=draft.ai_label_visible,
            extraction_status=draft.extraction_status.value,
            header_fields=header_response,
            line_items=line_item_responses,
            payment_details=payment_response,
        )

    return RequestDetailResponse(
        request_id=req.request_id,
        client_id=req.client_id,
        status=req.status,
        flags=req.flags or [],
        evidence_files=[
            {
                "evidence_id": str(ef.evidence_id),
                "file_key": ef.file_key,
                "original_filename": ef.original_filename or "",
                "content_type": ef.content_type,
                "upload_order": ef.upload_order,
            }
            for ef in evidence_files
        ],
        draft=draft_response,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUT /requests/{request_id}/fields  — update a draft field value
# ─────────────────────────────────────────────────────────────────────────────

class FieldUpdateBody(BaseModel):
    field_path: str
    new_value: str


@router.put("/{request_id}/fields", status_code=status.HTTP_200_OK)
async def update_field(
    request_id: uuid.UUID,
    body: FieldUpdateBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER")),
):
    """
    Update a single draft field value (header, line item, or payment detail).
    Atomically writes the new value AND inserts a FieldRevision record.
    No-op if new_value equals old_value. Appends a FIELD_REVISED AuditEvent.
    Requires Finance Reviewer role and active ClientAssignment.
    """
    req = await request_repository.get_by_id(db, request_id)
    if req is None:
        raise HTTPException(404, "Request not found.")

    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(403, "You are not authorized to access Requests for this client.")

    # Fetch the current value before updating
    draft_result = await db.execute(select(Draft).where(Draft.request_id == request_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(404, "No draft found for this request.")

    # Determine old value from the appropriate table
    old_value: str | None = None
    parts = body.field_path.split(".", 1)
    if len(parts) != 2:
        raise HTTPException(422, f"Invalid field_path: {body.field_path!r}")

    table_name, field_name = parts
    if table_name == "header":
        hf = (await db.execute(
            select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
        )).scalar_one_or_none()
        if hf is not None:
            val = getattr(hf, field_name, None)
            old_value = str(val) if val is not None else None
    elif table_name == "line_items":
        import re as _re
        m = _re.match(r"(\d+)\.(.+)", field_name)
        if not m:
            raise HTTPException(422, f"Invalid line_items path: {field_name!r}")
        idx = int(m.group(1))
        attr = m.group(2)
        li = (await db.execute(
            select(LineItem).where(
                LineItem.draft_id == draft.draft_id,
                LineItem.sequence_number == idx,
            )
        )).scalar_one_or_none()
        if li is not None:
            val = getattr(li, attr, None)
            old_value = str(val) if val is not None else None
    elif table_name == "payment":
        pd = (await db.execute(
            select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
        )).scalar_one_or_none()
        if pd is not None:
            val = getattr(pd, field_name, None)
            old_value = str(val) if val is not None else None

    # No-op if unchanged
    if old_value == body.new_value:
        return {"changed": False}

    # Perform the atomic update + revision insert
    try:
        changed = await request_repository.update_field_value(
            db, request_id, body.field_path, body.new_value
        )
        if changed:
            await request_repository.create_field_revision(
                db,
                request_id=request_id,
                field_path=body.field_path,
                old_value=old_value,
                new_value=body.new_value,
                actor_id=current_user.user_id,
            )
            await log_event(
                db,
                AuditEventType.FIELD_REVISED,
                payload={
                    "request_id": str(request_id),
                    "field_path": body.field_path,
                    "old_value": old_value,
                    "new_value": body.new_value,
                },
                request_id=request_id,
                actor_id=current_user.user_id,
            )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc))
    except Exception:
        await db.rollback()
        raise HTTPException(500, "The change could not be saved. Please try again.")

    return {"changed": changed}


# ─────────────────────────────────────────────────────────────────────────────
# GET /requests/{request_id}/revisions  — field revision history
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{request_id}/revisions", response_model=list[FieldRevisionResponse])
async def get_revisions(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN")
    ),
):
    """
    Return the complete FieldRevision list for a Request ordered by revised_at ascending.
    Accessible to Finance Reviewer, Service Lead, and System Admin.
    """
    req = await request_repository.get_by_id(db, request_id)
    if req is None:
        raise HTTPException(404, "Request not found.")

    revisions = await request_repository.get_field_revisions(db, request_id)
    return revisions


# ─────────────────────────────────────────────────────────────────────────────
# POST /requests/{request_id}/dismiss-duplicate
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{request_id}/dismiss-duplicate", status_code=status.HTTP_200_OK)
async def dismiss_duplicate(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER")),
):
    """
    Remove the POSSIBLE_DUPLICATE flag from a request.
    Only Finance Reviewer role can dismiss duplicates.
    Requires active client assignment for the request's client.
    """
    req = await request_repository.get_by_id(db, request_id)
    if req is None:
        raise HTTPException(404, "Request not found.")

    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(403, "You are not authorized to access this request.")

    await request_repository.remove_flag(db, request_id, "POSSIBLE_DUPLICATE")

    await log_event(
        db,
        AuditEventType.FLAG_DISMISSED,
        payload={
            "request_id": str(request_id),
            "flag": "POSSIBLE_DUPLICATE",
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    await db.commit()
    return {"status": "success"}
