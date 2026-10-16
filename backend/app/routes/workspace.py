"""Authenticated APIs supporting the invoice workspace."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, load_only

from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft
from app.models.evidence_file import EvidenceFile
from app.models.extraction_job import ExtractionJob
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.tax_assignment import TaxAssignment
from app.models.telemetry import MetricType, TelemetryRecord
from app.models.user import User
from app.permissions import require_permission
from app.repositories.request_repository import request_repository
from app.repositories.user_repository import user_repository
from app.schemas.request_schemas import FieldRevisionResponse
from app.schemas.user_schemas import UserResponse
from app.services.audit_service import log_event
from app.services.extraction_status import describe_extraction
from app.services.manual_tax import assignment_dict, refresh_assignments, totals
from app.services.workflow import FINANCE_REVIEW, REFINEMENT, stage_of
from app.services.workload import CLOSED, overdue, today_wib
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
            or_(
                and_(InvoiceRequest.status == RequestStatus.EXCEPTION, InvoiceRequest.workflow_stage == FINANCE_REVIEW),
                InvoiceRequest.status.in_(
                    [
                        RequestStatus.READY_FOR_REVIEW,
                        RequestStatus.AMIGA_REVIEWED,
                        RequestStatus.SENT_TO_CLIENT,
                        RequestStatus.REJECTED,
                    ]
                ),
            )
        )
    if user.role.value == "SUPER_ADMIN":
        stmt = select(InvoiceRequest, Client.client_name).join(Client).order_by(InvoiceRequest.created_at.desc())
    stmt = stmt.options(
        load_only(
            InvoiceRequest.request_id,
            InvoiceRequest.client_id,
            InvoiceRequest.request_type,
            InvoiceRequest.channel,
            InvoiceRequest.request_date,
            InvoiceRequest.status,
            InvoiceRequest.workflow_stage,
            InvoiceRequest.invoice_page_count,
            InvoiceRequest.flags,
            InvoiceRequest.validation_issues,
            InvoiceRequest.due_date,
            InvoiceRequest.assigned_reviewer,
            InvoiceRequest.created_at,
            InvoiceRequest.updated_at,
            InvoiceRequest.refinement_submitted_at,
        )
    )
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
    reviewers = (
        (
            await db.execute(select(User.user_id, User.full_name, User.username).where(User.user_id.in_(reviewer_ids)))
        ).all()
        if reviewer_ids
        else []
    )
    reviewer_names = {r.user_id: r.full_name or r.username for r in reviewers}
    return [
        {
            **fields(req, "request_id client_id request_type channel request_date status created_at updated_at"),
            "status": req.status.value,
            "workflow_stage": stage_of(req),
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
    granted = or_(
        literal(user.role.value == "SUPER_ADMIN"),
        exists().where(
            ClientAssignment.client_id == InvoiceRequest.client_id,
            ClientAssignment.user_id == user.user_id,
            ClientAssignment.is_active.is_(True),
        ),
    )
    row = (
        await db.execute(
            select(InvoiceRequest, User.full_name, User.job_title, granted)
            .join(User, User.user_id == InvoiceRequest.uploader_id)
            .where(InvoiceRequest.request_id == request_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Request not found.")
    req, uploader_name, uploader_title, allowed = row
    if not allowed:
        raise HTTPException(403, "You are not authorized to access Requests for this client.")
    submitter = req.submitter_snapshot or {"full_name": uploader_name or "", "job_title": uploader_title or ""}
    earlier_request, earlier_draft = aliased(InvoiceRequest), aliased(Draft)
    legacy_sources = exists(
        select(literal(1))
        .select_from(earlier_draft)
        .join(earlier_request, earlier_request.request_id == earlier_draft.request_id)
        .where(
            earlier_request.client_id == req.client_id,
            earlier_request.request_id != req.request_id,
            earlier_request.status != RequestStatus.REJECTED,
            func.json_array_length(earlier_draft.source_invoices) == 0,
        )
    )
    # Related metadata is bounded by the upload limit and fetched in one round trip.
    related = (
        await db.execute(
            select(Draft, HeaderFields, PaymentDetails, EvidenceFile, ExtractionJob, legacy_sources)
            .select_from(InvoiceRequest)
            .outerjoin(Draft, Draft.request_id == InvoiceRequest.request_id)
            .outerjoin(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
            .outerjoin(PaymentDetails, PaymentDetails.draft_id == Draft.draft_id)
            .outerjoin(EvidenceFile, EvidenceFile.request_id == InvoiceRequest.request_id)
            .outerjoin(ExtractionJob, ExtractionJob.request_id == InvoiceRequest.request_id)
            .where(InvoiceRequest.request_id == request_id)
            .order_by(EvidenceFile.upload_order)
        )
    ).all()
    draft, header, payment = related[0][:3] if related else (None, None, None)
    evidence = list({r[3].evidence_id: r[3] for r in related if r[3] is not None}.values())
    jobs = {r[4].job_id: r[4] for r in related if r[4] is not None}.values()
    job = max(jobs, key=lambda j: (j.created_at, str(j.job_id)), default=None)
    data = None
    tax_data = {}
    total_data = None
    if draft:
        item_rows = (
            await db.execute(
                select(LineItem, TaxAssignment)
                .outerjoin(TaxAssignment, TaxAssignment.line_item_id == LineItem.line_item_id)
                .where(LineItem.draft_id == draft.draft_id)
                .order_by(LineItem.sequence_number)
            )
        ).all()
        items = [row[0] for row in item_rows]
        tax_map = {str(row[0].line_item_id): row[1] for row in item_rows if row[1] is not None}
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
    from app.services.manual_tax import SOURCES, TAX_OPTIONS

    available_reviewers = None
    if user.role.value in ("SUPER_ADMIN", "SERVICE_LEAD"):
        from app.models.user import UserRole

        available_reviewers = [
            {"user_id": str(person.user_id), "name": person.full_name or person.username}
            for person in (
                await db.execute(
                    select(User.user_id, User.full_name, User.username)
                    .join(ClientAssignment, ClientAssignment.user_id == User.user_id)
                    .where(
                        ClientAssignment.client_id == req.client_id,
                        ClientAssignment.is_active.is_(True),
                        User.is_active.is_(True),
                        User.role == UserRole.FINANCE_REVIEWER,
                    )
                    .order_by(User.username)
                )
            ).all()
        ]
    from app.validation.duplicate import invoice_identities, normalize

    complete_identities = bool(draft and draft.source_invoices) and all(
        normalize(identity.get("vendor_name")) and normalize(identity.get("invoice_number"))
        for identity in invoice_identities(header, draft.source_invoices)
    )
    coverage_note = (
        None
        if complete_identities or draft is None
        else (
            "Legacy source identities are unavailable; duplicate matching uses the current header and may be incomplete."
            if draft and not draft.source_invoices
            else "Some source invoice identities are missing; duplicate detection is incomplete."
        )
    )
    if related and related[0][5]:
        complete_identities = False
        coverage_note = (
            "Some earlier client requests lack source invoice identities; duplicate detection may be incomplete."
        )
    return {
        "request_id": req.request_id,
        "tax_options": [{**option, "source_url": SOURCES.get(option["tax_type"])} for option in TAX_OPTIONS],
        "available_reviewers": available_reviewers,
        "extraction": describe_extraction(job),
        "client_id": req.client_id,
        "status": req.status,
        "workflow_stage": stage_of(req),
        "flags": req.flags or [],
        "validation_issues": req.validation_issues or [],
        "due_date": req.due_date,
        "assigned_reviewer": req.assigned_reviewer,
        "overdue": overdue(req),
        "rejection_reason": req.rejection_reason,
        "duplicate_detection_coverage": {"complete": complete_identities, "note": coverage_note},
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
    row = (
        await db.execute(
            select(EvidenceFile, InvoiceRequest)
            .join(InvoiceRequest, InvoiceRequest.request_id == EvidenceFile.request_id)
            .where(EvidenceFile.file_key == file_key)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Evidence not found.")
    record, req = row
    if not await user_repository.is_assignment_active(db, user.user_id, req.client_id):
        raise HTTPException(403, "You are not authorized to access Requests for this client.")
    await db.commit()  # Release the read transaction during S3 streaming.
    try:
        data = await file_store.download(record.file_key)
    except StorageError:
        raise HTTPException(502, "Unable to retrieve evidence.")
    headers = {}
    if download:
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(record.original_filename, safe="")
    return Response(data, media_type=record.content_type, headers=headers)


def ensure_editable(req, user):
    stage = stage_of(req)
    role = user.role.value
    if role == "SUPER_ADMIN" and stage:
        return
    if role == "CLIENT_PIC" and stage == REFINEMENT:
        return
    if role == "FINANCE_REVIEWER" and stage == FINANCE_REVIEW:
        return
    raise HTTPException(
        409 if role in ("CLIENT_PIC", "FINANCE_REVIEWER", "SUPER_ADMIN") else 403,
        "You cannot edit this request at its current workflow stage.",
    )


class FieldEdit(BaseModel):
    field_path: str = Field(max_length=200)
    new_value: str = Field(max_length=2000)


@router.put("/requests/{request_id}/fields")
async def edit(
    request_id: uuid.UUID,
    body: FieldEdit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("CLIENT_PIC", "FINANCE_REVIEWER")),
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


@router.post("/telemetry/voucher-availability", status_code=201)
async def voucher_availability(
    body: PageLoad, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    db.add(
        TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.VOUCHER_AVAILABILITY_DURATION,
            subject_id=body.page,
            duration_ms=body.duration_ms,
            is_breach=body.duration_ms > 3000,
            breach_threshold_ms=3000,
            measured_at=datetime.now(UTC),
        )
    )
    return {"recorded": True}
