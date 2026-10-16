"""Scoped audit timelines, workload allocation and operational measurements."""

import math
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.audit import AuditEvent, AuditEventType
from app.models.client_assignment import ClientAssignment
from app.models.request import InvoiceRequest
from app.models.telemetry import TelemetryRecord
from app.models.user import User, UserRole
from app.permissions import require_permission
from app.repositories.user_repository import user_repository
from app.routes.workspace import accessible_request
from app.services.audit_service import log_event

router = APIRouter(tags=["operations"])
from app.services.workload import overdue


@router.get("/requests/{request_id}/history")
async def history(
    request_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("history")),
):
    await accessible_request(request_id, db, user)
    rows = (
        await db.execute(
            select(AuditEvent, User.full_name, User.username)
            .outerjoin(User, User.user_id == AuditEvent.actor_id)
            .where(AuditEvent.request_id == request_id)
            .order_by(AuditEvent.occurred_at, AuditEvent.event_id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()

    def safe(value):
        if isinstance(value, dict):
            return {
                k: safe(v)
                for k, v in value.items()
                if not any(x in k.lower() for x in ("password", "signature", "token"))
            }
        if isinstance(value, list):
            return [safe(v) for v in value]
        return value

    return {
        "items": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "actor_name": name or username or "System",
                "occurred_at": e.occurred_at,
                "payload": safe(e.payload),
            }
            for e, name, username in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/requests/{request_id}/reviewers")
async def reviewers(
    request_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("allocate"))
):
    req = await accessible_request(request_id, db, user)
    rows = (
        await db.scalars(
            select(User)
            .join(ClientAssignment)
            .where(
                User.role == UserRole.FINANCE_REVIEWER,
                User.is_active.is_(True),
                ClientAssignment.client_id == req.client_id,
                ClientAssignment.is_active.is_(True),
            )
            .order_by(User.username)
        )
    ).all()
    return [{"user_id": u.user_id, "name": u.full_name or u.username} for u in rows]


class Allocation(BaseModel):
    assigned_reviewer: uuid.UUID | None = None
    due_date: date | None = None


@router.patch("/requests/{request_id}/allocation")
async def allocate(
    request_id: uuid.UUID,
    body: Allocation,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("allocate")),
):
    req = await accessible_request(request_id, db, user, lock=True)
    req.updated_at = datetime.now(ZoneInfo("UTC"))
    if body.assigned_reviewer:
        reviewer = await db.get(User, body.assigned_reviewer)
        if (
            not reviewer
            or not reviewer.is_active
            or reviewer.role != UserRole.FINANCE_REVIEWER
            or not await user_repository.is_assignment_active(db, reviewer.user_id, req.client_id)
        ):
            raise HTTPException(422, "Choose an active Finance Reviewer assigned to this client.")
    old = {
        "assigned_reviewer": str(req.assigned_reviewer) if req.assigned_reviewer else None,
        "due_date": req.due_date.isoformat() if req.due_date else None,
    }
    for key in body.model_fields_set:
        setattr(req, key, getattr(body, key))
    new = {
        "assigned_reviewer": str(req.assigned_reviewer) if req.assigned_reviewer else None,
        "due_date": req.due_date.isoformat() if req.due_date else None,
    }
    await log_event(db, AuditEventType.REALLOCATED, {"old_value": old, "new_value": new}, request_id, user.user_id)
    return {**new, "overdue": overdue(req)}


async def metric_records(db, user):
    stmt = select(TelemetryRecord).order_by(TelemetryRecord.measured_at.desc())
    if user.role.value != "SUPER_ADMIN":
        # Extraction telemetry references jobs; PDFs reference request IDs. Page timings contain no client data.
        from app.models.extraction_job import ExtractionJob

        ids = (
            await db.scalars(
                select(InvoiceRequest.request_id)
                .join(ClientAssignment, ClientAssignment.client_id == InvoiceRequest.client_id)
                .where(ClientAssignment.user_id == user.user_id, ClientAssignment.is_active.is_(True))
            )
        ).all()
        jobs = (await db.scalars(select(ExtractionJob.job_id).where(ExtractionJob.request_id.in_(ids)))).all()
        from app.models.voucher import VoucherPDF

        pdfs = (await db.scalars(select(VoucherPDF.voucher_pdf_id).where(VoucherPDF.request_id.in_(ids)))).all()
        allowed = {str(i) for i in [*ids, *jobs, *pdfs]}
        rows = (await db.scalars(stmt)).all()
        return [
            r
            for r in rows
            if r.subject_id in allowed
            or (
                r.metric_type.value == "EXTRACTION_DOCUMENT_DURATION"
                and r.subject_id.split("/document/", 1)[0] in allowed
            )
            or r.metric_type.value == "PAGE_LOAD_DURATION"
        ]
    return (await db.scalars(stmt)).all()


@router.get("/telemetry/metrics")
async def metrics(db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("metrics"))):
    rows = await metric_records(db, user)
    result = []
    for kind in sorted({r.metric_type.value for r in rows}):
        group = [r for r in rows if r.metric_type.value == kind]
        values = sorted(r.duration_ms for r in group)
        count, breaches = len(values), sum(r.is_breach for r in group)
        result.append(
            {
                "metric_type": kind,
                "count": count,
                "breaches": breaches,
                "breach_rate": breaches / count * 100,
                **{f"p{p}": values[max(0, math.ceil(p / 100 * count) - 1)] for p in (50, 90, 99)},
            }
        )
    return result


@router.get("/telemetry/breaches")
async def breaches(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("metrics")),
):
    rows = [r for r in await metric_records(db, user) if r.is_breach]
    return [
        {
            "metric_type": r.metric_type,
            "duration_ms": r.duration_ms,
            "threshold_ms": r.breach_threshold_ms,
            "measured_at": r.measured_at,
        }
        for r in rows[offset : offset + limit]
    ]


@router.post("/requests/{request_id}/retry-extraction")
async def retry_extraction(
    request_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("upload"))
):
    from sqlalchemy import func

    from app.models.audit import FieldRevision
    from app.models.draft import Draft
    from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
    from app.models.request import RequestStatus

    req = await accessible_request(request_id, db, user, lock=True)
    if req.status not in (RequestStatus.EXCEPTION, RequestStatus.READY_FOR_REFINEMENT) or req.refinement_submitted_at:
        raise HTTPException(409, "Only failed or empty, unsubmitted extractions can be retried.")
    draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
    if draft:
        for model in (HeaderFields, LineItem, PaymentDetails):
            if await db.scalar(select(func.count()).select_from(model).where(model.draft_id == draft.draft_id)):
                raise HTTPException(
                    409, "This draft contains extracted or edited values. Upload a new request to preserve them."
                )
        if await db.scalar(
            select(func.count()).select_from(FieldRevision).where(FieldRevision.request_id == request_id)
        ):
            raise HTTPException(409, "This draft has edits and cannot be replaced.")
    jobs = (
        await db.scalars(select(ExtractionJob).where(ExtractionJob.request_id == request_id).with_for_update())
    ).all()
    if any(job.status == ExtractionJobStatus.IN_PROGRESS for job in jobs):
        raise HTTPException(409, "Extraction is already running.")
    if not jobs:
        raise HTTPException(409, "No extraction job was found. Upload a new request.")
    if draft:
        await db.delete(draft)
    job = max(jobs, key=lambda j: j.created_at)
    job.status, job.retry_count, job.error_detail = ExtractionJobStatus.PENDING, 0, None
    job.started_at, job.completed_at = None, None
    previous = req.status.value
    req.status = RequestStatus.QUEUED
    req.workflow_stage = None
    req.flags, req.validation_issues = [], []
    await log_event(
        db,
        AuditEventType.STATUS_CHANGED,
        {"from": previous, "to": "QUEUED", "action": "RETRY_EXTRACTION"},
        request_id,
        user.user_id,
    )
    return {"status": req.status}
