"""Authenticated APIs supporting the invoice workspace."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.permissions import require_permission
from app.services.workload import overdue, today_wib, CLOSED
from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft
from app.models.evidence_file import EvidenceFile
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.telemetry import MetricType, TelemetryRecord
from app.models.user import User
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import FieldRevisionResponse
from app.schemas.user_schemas import UserResponse
from app.services.audit_service import log_event
from app.services.manual_tax import load_assignments, assignment_dict, totals, refresh_assignments
from app.storage import StorageError, file_store

router = APIRouter(tags=["workspace"])


async def accessible_request(request_id, db, user, lock=False):
    stmt = select(InvoiceRequest).where(InvoiceRequest.request_id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    req = (await db.execute(stmt)).scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "Request not found.")
    if not await user_repository.is_assignment_active(db, user.user_id, req.client_id):
        raise HTTPException(403, "You are not authorized to access Requests for this client.")
    return req


def fields(obj, names):
    return {name: str(getattr(obj, name)) if getattr(obj, name) is not None else None for name in names.split()}


@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    from app.permissions import permissions

    return {**UserResponse.model_validate(user).model_dump(), "permissions": permissions(user)}


@router.get("/requests")
async def requests(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("track")),
    client_id: uuid.UUID | None = None,
    status: RequestStatus | None = None,
    reviewer_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    overdue_only: bool = False,
):
    stmt = (
        select(InvoiceRequest, Client.client_name)
        .join(Client, Client.client_id == InvoiceRequest.client_id)
        .join(ClientAssignment, ClientAssignment.client_id == InvoiceRequest.client_id)
        .where(ClientAssignment.user_id == user.user_id, ClientAssignment.is_active.is_(True))
        .order_by(InvoiceRequest.created_at.desc())
    )
    if user.role.value == "FINANCE_REVIEWER":
        stmt = stmt.where(
            InvoiceRequest.status.in_(
                [
                    RequestStatus.READY_FOR_REVIEW,
                    RequestStatus.AMIGA_REVIEWED,
                    RequestStatus.SENT_TO_CLIENT,
                    RequestStatus.REJECTED,
                ]
            )
        )
    if user.role.value == "SUPER_ADMIN":
        stmt = select(InvoiceRequest, Client.client_name).join(Client).order_by(InvoiceRequest.created_at.desc())
    if client_id:
        stmt = stmt.where(InvoiceRequest.client_id == client_id)
    if status:
        stmt = stmt.where(InvoiceRequest.status == status)
    if reviewer_id:
        stmt = stmt.where(InvoiceRequest.assigned_reviewer == reviewer_id)
    if due_from:
        stmt = stmt.where(InvoiceRequest.due_date >= due_from)
    if due_to:
        stmt = stmt.where(InvoiceRequest.due_date <= due_to)
    if overdue_only:
        stmt = stmt.where(InvoiceRequest.due_date < today_wib(), InvoiceRequest.status.notin_(CLOSED))
    rows = (await db.execute(stmt)).all()
    reviewer_ids = {req.assigned_reviewer for req, _ in rows if req.assigned_reviewer}
    reviewers = (await db.scalars(select(User).where(User.user_id.in_(reviewer_ids)))).all() if reviewer_ids else []
    reviewer_names = {r.user_id: r.full_name or r.username for r in reviewers}
    return [
        {
            **fields(req, "request_id client_id request_type channel request_date status created_at updated_at"),
            "status": req.status.value,
            "flags": req.flags or [],
            "validation_issues": req.validation_issues or [],
            "due_date": req.due_date,
            "assigned_reviewer": req.assigned_reviewer,
            "assigned_reviewer_name": reviewer_names.get(req.assigned_reviewer),
            "overdue": overdue(req),
            "client_name": name,
            "invoice_page_count": req.invoice_page_count,
        }
        for req, name in rows
    ]


@router.get("/requests/{request_id}")
async def detail(
    request_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("track"))
):
    req = await accessible_request(request_id, db, user)
    uploader = await db.get(User, req.uploader_id)
    submitter = req.submitter_snapshot or {"full_name": uploader.full_name or "", "job_title": uploader.job_title or ""}
    evidence = (
        (
            await db.execute(
                select(EvidenceFile).where(EvidenceFile.request_id == request_id).order_by(EvidenceFile.upload_order)
            )
        )
        .scalars()
        .all()
    )
    draft = (await db.execute(select(Draft).where(Draft.request_id == request_id))).scalar_one_or_none()
    data = None
    tax_data = {}
    total_data = None
    if draft:
        header = (
            await db.execute(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
        ).scalar_one_or_none()
        items = (
            (
                await db.execute(
                    select(LineItem).where(LineItem.draft_id == draft.draft_id).order_by(LineItem.sequence_number)
                )
            )
            .scalars()
            .all()
        )
        payment = (
            await db.execute(select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id))
        ).scalar_one_or_none()
        tax_map = await load_assignments(db, items)
        tax_data = {key: assignment_dict(value) for key, value in tax_map.items()}
        total_data = totals(req, header, items, tax_map)
        data = {
            "draft_id": str(draft.draft_id),
            "ai_label_visible": draft.ai_label_visible,
            "extraction_status": draft.extraction_status,
            "source_invoices": draft.source_invoices or [],
            "header_fields": fields(
                header,
                "vendor_name invoice_number invoice_date currency subtotal total_amount "
                "tax_amount fee_amount rounding_adj voucher_number voucher_name voucher_purpose voucher_period",
            )
            if header
            else None,
            "line_items": [
                {
                    **fields(i, "line_item_id draft_id item_date description quantity unit unit_price line_total"),
                    "sequence_number": i.sequence_number,
                }
                for i in items
            ],
            "payment_details": fields(payment, "bank_account_number bank_name payment_reference") if payment else None,
        }
    return {
        "request_id": req.request_id,
        "client_id": req.client_id,
        "status": req.status,
        "flags": req.flags or [],
        "validation_issues": req.validation_issues or [],
        "due_date": req.due_date,
        "assigned_reviewer": req.assigned_reviewer,
        "overdue": overdue(req),
        "rejection_reason": req.rejection_reason,
        "draft": data,
        "tax_assignments": tax_data,
        "totals": total_data,
        "refinement_submitted_at": req.refinement_submitted_at,
        "voucher_purpose": req.voucher_purpose,
        "approvers": req.approvers or [],
        "voucher_number": req.voucher_number,
        "voucher_created_at": req.voucher_created_at,
        "submitter": {k: v for k, v in submitter.items() if k != "signature_data"},
        "evidence_files": [fields(e, "evidence_id file_key original_filename content_type") for e in evidence],
    }


@router.get("/evidence/{file_key:path}")
async def evidence(
    file_key: str,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("track")),
):
    record = (await db.execute(select(EvidenceFile).where(EvidenceFile.file_key == file_key))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Evidence not found.")
    await accessible_request(record.request_id, db, user)
    try:
        data = await file_store.download(record.file_key)
    except StorageError:
        raise HTTPException(502, "Unable to retrieve evidence.")
    headers = {}
    if download:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(record.original_filename, safe="")
    return Response(data, media_type=record.content_type, headers=headers)


def ensure_editable(req, user):
    if user.role.value == "SUPER_ADMIN":
        if req.status not in (RequestStatus.READY_FOR_REFINEMENT, RequestStatus.READY_FOR_REVIEW):
            raise HTTPException(409, "This request is not editable.")
    elif user.role.value == "CLIENT_PIC":
        if req.status != RequestStatus.READY_FOR_REFINEMENT:
            raise HTTPException(409, "Client edits are available only before submission to Finance Review.")
    elif user.role.value in ("FINANCE_REVIEWER", "SYSTEM_ADMIN"):
        if req.status != RequestStatus.READY_FOR_REVIEW:
            raise HTTPException(409, "This request is not awaiting finance review.")
    else:
        raise HTTPException(403, "You cannot edit this request.")


class FieldEdit(BaseModel):
    field_path: str = Field(max_length=200)
    new_value: str = Field(max_length=2000)


@router.put("/requests/{request_id}/fields")
async def edit(
    request_id: uuid.UUID,
    body: FieldEdit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("CLIENT_PIC", "FINANCE_REVIEWER", "SYSTEM_ADMIN")),
):
    req = await accessible_request(request_id, db, user, lock=True)
    ensure_editable(req, user)
    draft = (await db.execute(select(Draft).where(Draft.request_id == request_id))).scalar_one_or_none()
    if not draft:
        raise HTTPException(409, "No extracted draft is available.")
    parts = body.field_path.split(".")
    allowed = {
        "header": (
            HeaderFields,
            "vendor_name invoice_number invoice_date currency subtotal total_amount "
            "tax_amount fee_amount rounding_adj voucher_name",
        ),
        "payment": (PaymentDetails, "bank_account_number bank_name payment_reference"),
        "line_items": (LineItem, "item_date description quantity unit unit_price line_total"),
    }
    if parts[0] not in allowed or len(parts) != (3 if parts[0] == "line_items" else 2):
        raise HTTPException(422, "Invalid field path.")
    model, names = allowed[parts[0]]
    name = parts[-1]
    if name not in names.split():
        raise HTTPException(422, "Field is not editable.")
    objects = (await db.execute(select(model).where(model.draft_id == draft.draft_id))).scalars().all()
    if model is LineItem:
        objects = sorted(objects, key=lambda obj: obj.sequence_number)
        if not parts[1].isdigit() or int(parts[1]) >= len(objects):
            raise HTTPException(422, "Invalid line item index.")
        obj = objects[int(parts[1])]
    else:
        obj = objects[0] if objects else None
    if obj is None:
        obj = model(draft_id=draft.draft_id)
        db.add(obj)
    value = body.new_value or None
    try:
        if value is not None and name in ("invoice_date", "item_date"):
            value = date.fromisoformat(value)
        elif (
            value is not None
            and name
            in "subtotal total_amount tax_amount fee_amount rounding_adj quantity unit_price line_total".split()
        ):
            value = Decimal(value)
            if not value.is_finite() or abs(value) >= Decimal("1000000000000"):
                raise ValueError()
    except (ValueError, InvalidOperation):
        raise HTTPException(422, "Enter a valid date or finite numeric amount.")
    column = model.__table__.c[name]
    if isinstance(value, str) and getattr(column.type, "length", None) and len(value) > column.type.length:
        raise HTTPException(422, "Value exceeds the maximum field length.")
    if isinstance(value, Decimal) and value != value.quantize(Decimal("0.0001")):
        raise HTTPException(422, "Use at most four decimal places.")
    old = getattr(obj, name)
    if old == value:
        return {"changed": False}
    setattr(obj, name, value)
    await request_repository.create_field_revision(
        db,
        request_id,
        body.field_path,
        str(old) if old is not None else None,
        str(value) if value is not None else None,
        user.user_id,
    )
    await log_event(
        db,
        AuditEventType.FIELD_REVISED,
        payload={**body.model_dump(), "old_value": str(old) if old is not None else None},
        request_id=request_id,
        actor_id=user.user_id,
    )
    if (
        model is LineItem
        and name in ("quantity", "unit_price")
        and obj.quantity is not None
        and obj.unit_price is not None
    ):
        previous_total = obj.line_total
        obj.line_total = (Decimal(str(obj.quantity)) * Decimal(str(obj.unit_price))).quantize(Decimal("0.0001"))
        if abs(obj.line_total) >= Decimal("1000000000000"):
            raise HTTPException(422, "Calculated item total exceeds the supported amount.")
        if previous_total != obj.line_total:
            await request_repository.create_field_revision(
                db,
                request_id,
                f"line_items.{parts[1]}.line_total",
                str(previous_total) if previous_total is not None else None,
                str(obj.line_total),
                user.user_id,
            )
            await log_event(
                db,
                AuditEventType.FIELD_REVISED,
                {
                    "field_path": f"line_items.{parts[1]}.line_total",
                    "old_value": str(previous_total) if previous_total is not None else None,
                    "new_value": str(obj.line_total),
                    "automatic": True,
                },
                request_id=request_id,
                actor_id=user.user_id,
            )
    await db.flush()
    try:
        await refresh_assignments(db, draft.draft_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    from app.services.advisory_validation import validate_request

    await validate_request(db, req, user.user_id)
    req.updated_at = datetime.now(UTC)
    return {"changed": True, "status": req.status, "flags": req.flags, "validation_issues": req.validation_issues}


@router.get("/requests/{request_id}/revisions", response_model=list[FieldRevisionResponse])
async def revisions(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("CLIENT_PIC", "FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN")),
):
    await accessible_request(request_id, db, user)
    return await request_repository.get_field_revisions(db, request_id)


class PageLoad(BaseModel):
    page: str = Field(min_length=1, max_length=500)
    duration_ms: int = Field(ge=0, le=86400000)


@router.post("/telemetry/page-load", status_code=201)
async def telemetry(body: PageLoad, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    db.add(
        TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.PAGE_LOAD_DURATION,
            subject_id=body.page,
            duration_ms=body.duration_ms,
            is_breach=body.duration_ms > 2000,
            breach_threshold_ms=2000,
            measured_at=datetime.now(UTC),
        )
    )
    return {"recorded": True}
