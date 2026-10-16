# backend/app/routes/requests.py
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.audit import AuditEventType
from app.models.evidence_file import EvidenceFile
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import (
    CreateRequestResponse,
    RejectRequestBody,
)
from app.schemas.voucher_metadata import VoucherMetadata
from app.services.audit_service import log_event
from app.services.indexing_worker import enqueue_indexing
from app.services.request_service import RequestIdGenerationError, generate_request_id
from app.services.workflow import FINANCE_REVIEW, stage_of
from app.storage import StorageError, file_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/requests", tags=["requests"])

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB per file
MAX_FILES_PER_SESSION = 20
ACCEPTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}


# ─────────────────────────────────────────────────────────────────────────────
# POST /requests  — upload invoice
# ─────────────────────────────────────────────────────────────────────────────


async def generate_approved_voucher(request_id, db, user, *, invoice_data=None):
    from app.routes.voucher import _generate_voucher_pdf

    return await _generate_voucher_pdf(
        request_id=request_id, db=db, current_user=user, commit=False, invoice_data=invoice_data
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateRequestResponse)
async def upload_invoice(
    files: list[UploadFile],
    client_id: uuid.UUID = Form(..., description="Use GET /clients to find your client_id"),
    request_date: date = Form(..., description="Invoice date, e.g. 2026-10-14"),
    voucher_purpose: str = Form(..., max_length=1000),
    approvers: str = Form(..., max_length=12000, description="JSON array of full_name and job_title"),
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

    if current_user.role in (UserRole.INTAKE_SUPPORT, UserRole.CLIENT_PIC):
        active = await user_repository.is_assignment_active(db, current_user.user_id, client_id)
        if not active:
            raise HTTPException(403, "You are not authorized to submit for the selected client.")

    try:
        metadata = VoucherMetadata(voucher_purpose=voucher_purpose, approvers=json.loads(approvers))
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            422, "Enter the expense purpose and at least one approver's full name and professional role."
        )
    from app.models.client import Client

    target_client = await db.get(Client, client_id)
    if not target_client or not target_client.is_active:
        raise HTTPException(422, "Choose an active client.")
    if not current_user.full_name or not current_user.job_title:
        raise HTTPException(409, "Complete your full name and professional role in Account before uploading.")

    from io import BytesIO

    from pypdf import PdfReader

    page_count = 0
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
        if ct == "application/pdf":
            try:
                page_count += len(PdfReader(BytesIO(data)).pages)
            except Exception:
                raise HTTPException(422, f"{name}: unable to read PDF pages.")
        else:
            page_count += 1
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
            {"filename": name, "content_type": ct, "file_key": key, "upload_order": order, "file_size_bytes": len(data)}
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
        invoice_page_count=page_count,
    )

    req.voucher_purpose = metadata.voucher_purpose
    req.approvers = [person.model_dump() for person in metadata.approvers]

    for evidence in evidence_meta:
        db.add(
            EvidenceFile(
                evidence_id=uuid.uuid4(),
                request_id=request_id,
                file_key=evidence["file_key"],
                original_filename=evidence["filename"],
                content_type=evidence["content_type"],
                file_size_bytes=evidence["file_size_bytes"],
                upload_order=evidence["upload_order"],
                uploaded_at=datetime.now(UTC),
            )
        )
    await db.flush()

    await log_event(
        db,
        AuditEventType.REQUEST_CREATED,
        payload={
            "request_id": str(request_id),
            "client_id": str(client_id),
            "uploader_id": str(current_user.user_id),
            "upload_timestamp": datetime.now(UTC).isoformat(),
            "evidence_files": evidence_meta,
            "voucher_purpose": req.voucher_purpose,
            "approvers": req.approvers,
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    now = datetime.now(UTC)
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


@router.post("/{request_id}/approve", status_code=status.HTTP_200_OK)
async def approve_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("FINANCE_REVIEWER", "CLIENT_PIC", "INTAKE_SUPPORT", "SERVICE_LEAD", "SYSTEM_ADMIN")
    ),
):
    """
    Approve a Request for payment processing.
    Only Finance Reviewers with an active client assignment may approve.
    Enforces segregation of duties (uploader cannot approve their own submission).
    SYSTEM_ADMIN attempts are logged as UNAUTHORIZED_APPROVAL_ATTEMPT.
    """
    # ── Step 1: Role check ─────────────────────────────────────────────────────
    # SYSTEM_ADMIN: log an audit event, commit it, then raise 403
    if current_user.role == UserRole.SYSTEM_ADMIN:
        await log_event(
            db,
            AuditEventType.UNAUTHORIZED_APPROVAL_ATTEMPT,
            payload={
                "attempted_by": str(current_user.user_id),
                "role": current_user.role.value,
                "request_id": str(request_id),
            },
            request_id=request_id,
            actor_id=current_user.user_id,
        )
        await db.commit()
        raise HTTPException(403, "You do not have authority to approve Requests.")

    # Any other non-FINANCE_REVIEWER role
    if current_user.role not in (UserRole.FINANCE_REVIEWER, UserRole.SUPER_ADMIN):
        raise HTTPException(403, "You do not have authority to approve Requests.")

    # ── Step 2 & 3: Load request and verify client assignment ──────────────────
    req = (
        await db.execute(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id).with_for_update())
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "Request not found.")

    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(403, "You are not authorized to access Requests for this client.")

    # ── Step 4: Segregation of duties check ────────────────────────────────────
    if req.uploader_id == current_user.user_id and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Self-approval is not permitted.")

    if stage_of(req) != FINANCE_REVIEW:
        raise HTTPException(409, "The draft must be submitted by Client PIC before finance approval.")

    from app.services.invoice_data import load_invoice_data
    from app.services.manual_tax import refresh_assignments

    invoice_data = await load_invoice_data(request_id, db)
    if (
        invoice_data.draft is None
        or invoice_data.header is None
        or not any(item.line_total is not None for item in invoice_data.items)
    ):
        raise HTTPException(409, "Extraction has not produced a usable draft. Retry extraction first.")
    try:
        await refresh_assignments(db, invoice_data.draft.draft_id, data=invoice_data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    from app.services.advisory_validation import validate_request

    await validate_request(db, req, current_user.user_id, data=invoice_data)
    # ── Approve: transition status and write audit event ──────────────────────
    await request_repository.update_status(db, request_id, RequestStatus.AMIGA_REVIEWED)
    req.workflow_stage = None

    await log_event(
        db,
        AuditEventType.APPROVED,
        payload={
            "approver_id": str(current_user.user_id),
            "approved_at": datetime.now(UTC).isoformat(),
            "outstanding_issues": req.validation_issues or [],
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    # Generate with the active template in the same transaction as approval.
    # A rendering/storage failure rolls back approval so the reviewer can retry.
    try:
        voucher = await generate_approved_voucher(request_id, db, current_user, invoice_data=invoice_data)
        await enqueue_indexing(db, request_id)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"status": "approved", "request_id": str(request_id), "voucher": voucher}


@router.post("/{request_id}/reject", status_code=status.HTTP_200_OK)
async def reject_request(
    request_id: uuid.UUID,
    body: RejectRequestBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("FINANCE_REVIEWER", "CLIENT_PIC", "INTAKE_SUPPORT", "SERVICE_LEAD", "SYSTEM_ADMIN")
    ),
):
    """
    Reject a Request with a mandatory reason (1–500 characters).

    Only Finance Reviewers with an active client assignment may reject.
    Enforces segregation of duties (uploader cannot reject their own submission).
    Request must be in READY_FOR_REVIEW or EXCEPTION status.

    Requirements: 13.1, 13.2, 13.3, 13.4
    """
    # ── Step 1: Role check ─────────────────────────────────────────────────────
    if current_user.role not in (UserRole.FINANCE_REVIEWER, UserRole.SUPER_ADMIN):
        raise HTTPException(403, "You do not have authority to reject Requests.")

    # ── Step 2: Load request ───────────────────────────────────────────────────
    req = (
        await db.execute(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id).with_for_update())
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "Request not found.")

    # ── Step 3: Client assignment check ───────────────────────────────────────
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(403, "You are not authorized to access Requests for this client.")

    # ── Step 4: Segregation of duties check ────────────────────────────────────
    if req.uploader_id == current_user.user_id and current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(403, "Self-rejection is not permitted.")

    # ── Step 5: Status check — must be READY_FOR_REVIEW or EXCEPTION ──────────
    if stage_of(req) != FINANCE_REVIEW:
        raise HTTPException(
            409,
            "The draft must be submitted by Client PIC before finance rejection.",
        )

    # ── Reject: persist reason, transition status, write audit event ──────────
    await request_repository.update_status_with_reason(db, request_id, RequestStatus.REJECTED, body.rejection_reason)
    req.workflow_stage = None

    await log_event(
        db,
        AuditEventType.REJECTED,
        payload={
            "reason": body.rejection_reason,
            "reviewer_id": str(current_user.user_id),
            "rejected_at": datetime.now(UTC).isoformat(),
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    await db.commit()
    return {"status": "rejected", "request_id": str(request_id)}
