# backend/app/routes/requests.py
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List
from fastapi import Body
from fastapi.responses import StreamingResponse

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.dependencies import get_db, require_role
from app.models.audit import AuditEventType, FieldRevision
from app.models.client import Client
from app.models.draft import Draft, ExtractionStatus
from app.models.evidence_file import EvidenceFile
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.models.header_fields import HeaderFields
from app.models.invoice_fields import LineItem
from app.models.payment_details import PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import (
    CreateRequestResponse,
    FieldRevisionResponse,
    LineItemResponse,
    PaymentDetailsResponse,
    RequestDetailResponse,
    RequestSummary,
    EvidenceFileResponse,
)
from app.services.audit_service import log_event
from app.services.request_service import RequestIdGenerationError, generate_request_id
from app.storage import StorageError, file_store

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB per file
MAX_FILES_PER_SESSION = 20
ACCEPTED_TYPES = {"application/pdf", "image/png", "image.jpeg", "image/jpg"}


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
    Creates the Request in QUEUED status and inserts an ExtractionJob row
    with status=PENDING. The background worker picks up the job and
    transitions the Request to PROCESSING.
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
            raise HTTPException(
                415,
                f"'{name}': unsupported format '{ct}'. Accepted: PDF, PNG, JPG, JPEG.",
            )
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
    evidence_meta: list[dict] = []
    for order, (name, data, ct) in enumerate(validated, start=1):
        key = f"evidence/{request_id}/{order}_{name}"
        try:
            await file_store.upload(key, data, ct)
        except StorageError as exc:
            raise HTTPException(502, f"S3 upload failed for '{name}': {exc}")
        evidence_keys.append(key)
        evidence_meta.append(
            {"filename": name, "content_type": ct, "file_key": key, "upload_order": order}
        )

    # ── Create Request in QUEUED status ───────────────────────────────────────
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

    # ── Insert ExtractionJob row with status=PENDING ──────────────────────────
    # The background worker picks this up and transitions the Request to PROCESSING.
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

    # ── Commit everything: Request + AuditEvent + ExtractionJob ──────────────
    await db.commit()

    # Reload request to return (still in QUEUED status)
    req = await request_repository.get_by_id(db, request_id)
    return req


@router.get("/{id}", response_model=RequestDetailResponse)
async def get_request_detail(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "CLIENT_PIC")),
):
    """
    Return detailed information about a request including draft, header fields,
    line items, payment details, and flags.
    
    Authorization: Finance Reviewer, Service Lead, System Admin, or Client PIC.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    if current_user.role not in ["SYSTEM_ADMIN", "SERVICE_LEAD"]:
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access this request.",
            )

    # Load draft
    stmt = select(Draft).where(Draft.request_id == id)
    result = await db.execute(stmt)
    draft = result.scalar_one_or_none()

    draft_data = None
    if draft:
        # Load header fields
        header_stmt = select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
        header_result = await db.execute(header_stmt)
        header = header_result.scalar_one_or_none()

        # Load line items
        line_items_stmt = (
            select(LineItem)
            .where(LineItem.draft_id == draft.draft_id)
            .order_by(LineItem.sequence_number)
        )
        line_items_result = await db.execute(line_items_stmt)
        line_items = line_items_result.scalars().all()

        # Load payment details
        payment_stmt = select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
        payment_result = await db.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        draft_data = {
            "draft_id": str(draft.draft_id),
            "ai_label_visible": draft.ai_label_visible,
            "extraction_status": draft.extraction_status.value,
            "header_fields": header.__dict__ if header else None,
            "line_items": [
                {
                    "line_item_id": str(li.line_item_id),
                    "sequence_number": li.sequence_number,
                    "description": li.description,
                    "quantity": str(li.quantity) if li.quantity else None,
                    "unit": li.unit,
                    "unit_price": str(li.unit_price) if li.unit_price else None,
                    "line_total": str(li.line_total) if li.line_total else None,
                }
                for li in line_items
            ],
            "payment_details": payment.__dict__ if payment else None,
        }

    # Load evidence files
    evidence_stmt = (
        select(EvidenceFile)
        .where(EvidenceFile.request_id == id)
        .order_by(EvidenceFile.upload_order)
    )
    evidence_result = await db.execute(evidence_stmt)
    evidence_files = evidence_result.scalars().all()

    return {
        "request_id": str(req.request_id),
        "client_id": str(req.client_id),
        "status": req.status.value,
        "flags": req.flags or [],
        "evidence_files": [
            {
                "file_key": ef.file_key,
                "original_filename": ef.original_filename,
            }
            for ef in evidence_files
        ],
        "draft": draft_data,
    }


@router.get("/{id}/revisions", response_model=list[FieldRevisionResponse])
async def get_request_revisions(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN")
    ),
):
    """
    Return the complete FieldRevision list for the Request ordered by revised_at ascending.
    
    Authorization: Finance Reviewer, Service Lead, or System Admin only.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Get all field revisions for this request
    revisions = await request_repository.get_field_revisions(db, id)
    
    return revisions


@router.put("/{id}/fields")
async def update_request_field(
    id: uuid.UUID,
    field_path: str,
    new_value: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER", "SYSTEM_ADMIN")),
):
    """
    Update a field value in the draft.
    
    Authorization: Finance Reviewer or System Admin.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    if current_user.role != "SYSTEM_ADMIN":
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access this request.",
            )

    # Parse field_path to determine which table and field to update
    # Format: "header.subtotal", "line_items[0].description", "payment.bank_account_number"
    parts = field_path.split(".", 1)
    if len(parts) != 2:
        raise HTTPException(422, f"Invalid field_path: {field_path}")

    table_name, field_name = parts

    # Get draft_id for this request
    stmt = select(Draft).where(Draft.request_id == id)
    result = await db.execute(stmt)
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(404, f"No draft found for request {id}")

    # Determine the current value before updating
    old_value = None
    if table_name == "header":
        table = HeaderFields
        filter_col = table.header_id
        stmt = select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            old_value = str(getattr(record, field_name, None))
    elif table_name == "line_items":
        import re
        match = re.match(r"(\d+)\.(.+)", field_name)
        if not match:
            raise HTTPException(422, f"Invalid line_items field_path: {field_name}")
        index = int(match.group(1))
        field_name = match.group(2)

        stmt = select(LineItem).where(
            LineItem.draft_id == draft.draft_id,
            LineItem.sequence_number == index
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            old_value = str(getattr(record, field_name, None))
    elif table_name == "payment":
        stmt = select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            old_value = str(getattr(record, field_name, None))
    else:
        raise HTTPException(422, f"Unknown table name: {table_name}")

    # Check if value actually changed
    if old_value == new_value:
        return {"message": "No change", "old_value": old_value, "new_value": new_value}

    # Perform the update
    try:
        if table_name == "header":
            await db.execute(
                update(HeaderFields)
                .where(HeaderFields.draft_id == draft.draft_id)
                .values(**{field_name: new_value})
            )
        elif table_name == "line_items":
            match = re.match(r"(\d+)\.(.+)", field_name)
            index = int(match.group(1))
            field_name = match.group(2)

            stmt = select(LineItem).where(
                LineItem.draft_id == draft.draft_id,
                LineItem.sequence_number == index
            )
            result = await db.execute(stmt)
            line_item = result.scalar_one_or_none()
            if line_item is None:
                raise HTTPException(404, f"LineItem at index {index} not found")

            await db.execute(
                update(LineItem)
                .where(LineItem.line_item_id == line_item.line_item_id)
                .values(**{field_name: new_value})
            )
        elif table_name == "payment":
            await db.execute(
                update(PaymentDetails)
                .where(PaymentDetails.draft_id == draft.draft_id)
                .values(**{field_name: new_value})
            )

        await db.flush()

        # Create FieldRevision record
        from app.models.audit import FieldRevision
        revision = FieldRevision(
            revision_id=uuid.uuid4(),
            request_id=id,
            field_path=field_path,
            old_value=old_value,
            new_value=new_value,
            actor_id=current_user.user_id,
            revised_at=datetime.now(timezone.utc),
        )
        db.add(revision)
        await db.flush()

        # Write FIELD_REVISED audit event
        await log_event(
            db,
            AuditEventType.FIELD_REVISED,
            payload={
                "field_path": field_path,
                "old_value": old_value,
                "new_value": new_value,
            },
            request_id=id,
            actor_id=current_user.user_id,
        )

        await db.commit()
        
        return {
            "message": "Field updated successfully",
            "field_path": field_path,
            "old_value": old_value,
            "new_value": new_value,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(500, f"Failed to update field: {str(e)}")


@router.post("/{id}/approve")
async def approve_request(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER")),
):
    """
    Approve a request in READY_FOR_REVIEW status.
    
    Authorization: Finance Reviewer only.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this request.",
        )

    # Enforce SOD: uploader != approver
    if req.uploader_id == current_user.user_id:
        raise HTTPException(403, "Self-approval is not permitted.")

    # Check for EXCEPTION flags
    if req.status == RequestStatus.EXCEPTION and req.flags:
        raise HTTPException(409, "Unresolved exceptions must be addressed before this Request can be approved.")

    # Transition status
    if req.status != RequestStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"Request must be in READY_FOR_REVIEW status, currently {req.status.value}")

    await request_repository.update_status(db, id, RequestStatus.AMIGA_REVIEWED)

    # Log APPROVED audit event
    await log_event(
        db,
        AuditEventType.APPROVED,
        payload={
            "approver_id": str(current_user.user_id),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=id,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return {"message": "Request approved successfully", "status": RequestStatus.AMIGA_REVIEWED.value}


@router.post("/{id}/reject")
async def reject_request(
    id: uuid.UUID,
    rejection_reason: str = Form(..., min_length=1, max_length=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER")),
):
    """
    Reject a request with a reason.
    
    Authorization: Finance Reviewer only.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this request.",
        )

    # Enforce SOD: uploader != rejector
    if req.uploader_id == current_user.user_id:
        raise HTTPException(403, "Self-rejection is not permitted.")

    # Transition status
    await request_repository.update_status(db, id, RequestStatus.REJECTED)

    # Log REJECTED audit event
    await log_event(
        db,
        AuditEventType.REJECTED,
        payload={
            "rejection_reason": rejection_reason,
            "rejector_id": str(current_user.user_id),
            "rejected_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=id,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return {"message": "Request rejected successfully", "status": RequestStatus.REJECTED.value}


@router.post("/{id}/dismiss-duplicate")
async def dismiss_duplicate(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER")),
):
    """
    Dismiss the POSSIBLE_DUPLICATE flag.
    
    Authorization: Finance Reviewer only.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this request.",
        )

    # Remove POSSIBLE_DUPLICATE flag
    if "POSSIBLE_DUPLICATE" in (req.flags or []):
        await request_repository.remove_flag(db, id, "POSSIBLE_DUPLICATE")
    else:
        raise HTTPException(409, "No POSSIBLE_DUPLICATE flag to dismiss")

    # Log FLAG_DISMISSED audit event
    await log_event(
        db,
        AuditEventType.FLAG_DISMISSED,
        payload={
            "flag": "POSSIBLE_DUPLICATE",
            "dismissed_by": str(current_user.user_id),
            "dismissed_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=id,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return {"message": "Duplicate flag dismissed successfully"}


@router.get("/{id}/evidence", response_model=List[EvidenceFile])
async def get_evidence_files(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "CLIENT_PIC")),
):
    """
    Return the list of evidence files for a request.
    
    Authorization: Finance Reviewer, Service Lead, System Admin, or Client PIC.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    if current_user.role not in ["SYSTEM_ADMIN", "SERVICE_LEAD"]:
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access this request.",
            )

    # Load evidence files
    stmt = (
        select(EvidenceFile)
        .where(EvidenceFile.request_id == id)
        .order_by(EvidenceFile.upload_order)
    )
    result = await db.execute(stmt)
    evidence_files = result.scalars().all()

    return evidence_files


@router.get("/{id}/evidence/{file_key}")
async def download_evidence_file(
    id: uuid.UUID,
    file_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "CLIENT_PIC")),
):
    """
    Download an evidence file.
    
    Authorization: Finance Reviewer, Service Lead, System Admin, or Client PIC.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    if current_user.role not in ["SYSTEM_ADMIN", "SERVICE_LEAD"]:
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access this request.",
            )

    try:
        file_bytes = await file_store.download(file_key)
        return StreamingResponse(
            iter([file_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={file_key.split('/')[-1]}",
            },
        )
    except StorageError as exc:
        raise HTTPException(502, f"Failed to download file: {exc}")


@router.get("/mine", response_model=List[RequestSummary])
async def get_my_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "CLIENT_PIC")),
):
    """
    Return requests for which the user has assignments.
    
    Authorization: Finance Reviewer, Service Lead, System Admin, or Client PIC.
    """
    # Get client assignments for the user
    assignments_stmt = select(ClientAssignment).where(
        ClientAssignment.user_id == current_user.user_id,
        ClientAssignment.is_active == True
    )
    assignments_result = await db.execute(assignments_stmt)
    assignments = assignments_result.scalars().all()
    client_ids = [a.client_id for a in assignments]

    if not client_ids:
        return []

    # Filter by status based on role
    statuses: List[RequestStatus] = []
    if current_user.role == "FINANCE_REVIEWER":
        statuses = [
            RequestStatus.READY_FOR_REVIEW,
            RequestStatus.EXCEPTION,
            RequestStatus.AMIGA_REVIEWED,
        ]
    elif current_user.role == "CLIENT_PIC":
        statuses = [
            RequestStatus.AMIGA_REVIEWED,
            RequestStatus.SENT_TO_CLIENT,
        ]
    elif current_user.role in ["SYSTEM_ADMIN", "SERVICE_LEAD"]:
        # No status filter for admins
        pass

    # Load requests
    stmt = select(InvoiceRequest).where(InvoiceRequest.client_id.in_(client_ids))
    if statuses:
        stmt = stmt.where(InvoiceRequest.status.in_(statuses))
    stmt = stmt.order_by(InvoiceRequest.created_at.desc())
    result = await db.execute(stmt)
    requests = result.scalars().all()

    # Load client names
    client_stmt = select(Client).where(Client.client_id.in_(client_ids))
    client_result = await db.execute(client_stmt)
    clients = {c.client_id: c.client_name for c in client_result.scalars().all()}

    return [
        {
            "request_id": str(r.request_id),
            "client_id": str(r.client_id),
            "client_name": clients.get(r.client_id, "Unknown"),
            "request_type": r.request_type,
            "channel": r.channel,
            "request_date": r.request_date,
            "status": r.status.value,
            "flags": r.flags or [],
            "invoice_page_count": r.invoice_page_count,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in requests
    ]


@router.post("/{id}/reallocate")
async def reallocate_request(
    id: uuid.UUID,
    reviewer_id: uuid.UUID = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("SERVICE_LEAD")),
):
    """
    Reallocate a request to a different Finance Reviewer.
    
    Authorization: Service Lead only.
    """
    # Verify request exists
    req = await request_repository.get_by_id(db, id)
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request {id} not found.",
        )

    # Check authorization (client assignment)
    active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this request.",
        )

    # Verify target user is a Finance Reviewer
    reviewer = await db.get(User, reviewer_id)
    if not reviewer or reviewer.role != UserRole.FINANCE_REVIEWER:
        raise HTTPException(400, "Target user must be a Finance Reviewer")

    # Set assigned reviewer
    await request_repository.set_assigned_reviewer(db, id, reviewer_id)

    # Log REALLOCATED audit event
    await log_event(
        db,
        AuditEventType.REALLOCATED,
        payload={
            "previous_reviewer_id": str(req.assigned_reviewer) if req.assigned_reviewer else None,
            "new_reviewer_id": str(reviewer_id),
            "reallocator_id": str(current_user.user_id),
            "reallocator_at": datetime.now(timezone.utc).isoformat(),
        },
        request_id=id,
        actor_id=current_user.user_id,
    )

    await db.commit()

    return {"message": "Request reallocated successfully", "reviewer_id": str(reviewer_id)}
