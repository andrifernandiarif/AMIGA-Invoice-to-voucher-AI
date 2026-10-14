# backend/app/routes/requests.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.audit import AuditEventType
from app.models.draft import Draft
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
from app.services.audit_service import log_event
from app.services.request_service import RequestIdGenerationError, generate_request_id
from app.storage import StorageError, file_store

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB per file
MAX_FILES_PER_SESSION = 20
ACCEPTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}


# ─────────────────────────────────────────────────────────────────────────────
# POST /requests  — upload invoice
# ─────────────────────────────────────────────────────────────────────────────


async def generate_approved_voucher(request_id, db, user):
    from app.routes.voucher import generate_voucher_pdf

    return await generate_voucher_pdf(request_id=request_id, db=db, current_user=user)


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

    if current_user.role in (UserRole.INTAKE_SUPPORT, UserRole.CLIENT_PIC):
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
        invoice_page_count=None,
    )

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
                uploaded_at=datetime.now(timezone.utc),
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
    if current_user.role != UserRole.FINANCE_REVIEWER:
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
    if req.uploader_id == current_user.user_id:
        raise HTTPException(403, "Self-approval is not permitted.")

    if req.status != RequestStatus.READY_FOR_REVIEW:
        raise HTTPException(409, "The draft must be submitted by Client PIC before finance approval.")

    if req.refinement_submitted_at is not None:
        from app.models.invoice_fields import HeaderFields
        from app.services.manual_tax import refresh_assignments, totals

        draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
        if not draft:
            raise HTTPException(409, "No submitted draft is available.")
        try:
            items, assignments = await refresh_assignments(db, draft.draft_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        header = await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
        summary = totals(req, header, items, assignments)
        if summary["voucher_total"] is None or Decimal(summary["voucher_total"]) < 0:
            raise HTTPException(422, "Complete item totals and tax amounts before accepting the voucher.")

    req.flags = []
    # ── Approve: transition status and write audit event ──────────────────────
    await request_repository.update_status(db, request_id, RequestStatus.AMIGA_REVIEWED)

    await log_event(
        db,
        AuditEventType.APPROVED,
        payload={
            "approver_id": str(current_user.user_id),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    # Generate with the active template in the same transaction as approval.
    # A rendering/storage failure rolls back approval so the reviewer can retry.
    try:
        voucher = await generate_approved_voucher(request_id, db, current_user)
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
    if current_user.role != UserRole.FINANCE_REVIEWER:
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
    if req.uploader_id == current_user.user_id:
        raise HTTPException(403, "Self-rejection is not permitted.")

    # ── Step 5: Status check — must be READY_FOR_REVIEW or EXCEPTION ──────────
    if req.status != RequestStatus.READY_FOR_REVIEW:
        raise HTTPException(
            409,
            "The draft must be submitted by Client PIC before finance rejection.",
        )

    # ── Reject: persist reason, transition status, write audit event ──────────
    await request_repository.update_status_with_reason(db, request_id, RequestStatus.REJECTED, body.rejection_reason)

    await log_event(
        db,
        AuditEventType.REJECTED,
        payload={
            "reason": body.rejection_reason,
            "reviewer_id": str(current_user.user_id),
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=request_id,
        actor_id=current_user.user_id,
    )

    await db.commit()
    return {"status": "rejected", "request_id": str(request_id)}
