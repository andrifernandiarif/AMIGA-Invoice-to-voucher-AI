"""Client refinement, manual per-item taxes, and explicit submission for review."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem
from app.models.request import RequestStatus
from app.models.tax_assignment import IndonesianTaxType, TaxAssignment
from app.models.user import User
from app.repositories.request_repository import request_repository
from app.routes.workspace import accessible_request, ensure_editable
from app.schemas.voucher_metadata import VoucherMetadata
from app.services.audit_service import log_event
from app.services.manual_tax import (
    OPTIONS_BY_ID,
    SOURCES,
    TAX_OPTIONS,
    amount,
    assignment_dict,
    refresh_assignments,
    totals,
)
from app.services.workflow import FINANCE_REVIEW, REFINEMENT, stage_of

router = APIRouter(tags=["refinement"])


@router.get("/tax-options")
async def options(user: User = Depends(get_current_user)):
    return [{**option, "source_url": SOURCES.get(option["tax_type"])} for option in TAX_OPTIONS]


class TaxEdit(BaseModel):
    profile: str = Field(max_length=80)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=100, max_digits=7, decimal_places=4)
    base_amount: Decimal | None = Field(default=None, ge=0, lt=1000000000000, max_digits=16, decimal_places=4)
    effect: Literal["ADD", "DEDUCT"] = "ADD"


@router.put("/requests/{request_id}/items/{item_id}/tax")
async def assign_tax(
    request_id: uuid.UUID,
    item_id: uuid.UUID,
    body: TaxEdit,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("CLIENT_PIC", "FINANCE_REVIEWER")),
):
    req = await accessible_request(request_id, db, user, lock=True)
    ensure_editable(req, user)
    item = await db.scalar(
        select(LineItem)
        .join(Draft, LineItem.draft_id == Draft.draft_id)
        .where(Draft.request_id == request_id, LineItem.line_item_id == item_id)
    )
    if not item:
        raise HTTPException(404, "Line item not found in this request.")
    option = OPTIONS_BY_ID.get(body.profile)
    if not option:
        raise HTTPException(422, "Choose a supported tax option.")
    tax = await db.scalar(select(TaxAssignment).where(TaxAssignment.line_item_id == item_id))
    old = assignment_dict(tax) if tax else None
    if option["tax_type"] == "NO_TAX":
        if tax:
            await db.delete(tax)
        new = None
    else:
        preset = option["rate_percent"]
        rate_percent = Decimal(preset) if preset is not None else body.rate_percent
        if rate_percent is None:
            raise HTTPException(422, "Enter the applicable manual rate.")
        if preset is not None and body.rate_percent is not None and body.rate_percent != rate_percent:
            raise HTTPException(422, "Choose the manual-rate option to override a preset.")
        base = body.base_amount if body.base_amount is not None else item.line_total
        if base is None or Decimal(str(base)) < 0:
            raise HTTPException(422, "Enter a nonnegative item total or a manual tax base.")
        base = Decimal(str(base))
        if not tax:
            tax = TaxAssignment(tax_assignment_id=uuid.uuid4(), line_item_id=item_id)
            db.add(tax)
        tax.tax_type = IndonesianTaxType(option["tax_type"])
        tax.rate_profile = body.profile
        tax.tax_rate = rate_percent / 100
        tax.tax_base = base
        tax.base_is_manual = body.base_amount is not None
        tax.tax_effect = body.effect
        tax.tax_amount = amount(base, tax.tax_rate)
        tax.assigned_at = datetime.now(UTC)
        tax.assigned_by = user.user_id
        new = assignment_dict(tax)
    await request_repository.create_field_revision(
        db,
        request_id,
        f"items.{item_id}.tax",
        json.dumps(old) if old else None,
        json.dumps(new) if new else None,
        user.user_id,
    )
    await log_event(
        db,
        AuditEventType.FIELD_REVISED,
        payload={"field_path": f"items.{item_id}.tax", "old_value": old, "new_value": new},
        request_id=request_id,
        actor_id=user.user_id,
    )
    req.updated_at = datetime.now(UTC)
    await db.flush()
    from app.services.advisory_validation import validate_request

    await validate_request(db, req, user.user_id)
    return {"tax_assignment": new}


@router.put("/requests/{request_id}/voucher-metadata")
async def update_metadata(
    request_id: uuid.UUID,
    body: VoucherMetadata,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("CLIENT_PIC")),
):
    req = await accessible_request(request_id, db, user, lock=True)
    if stage_of(req) != REFINEMENT:
        raise HTTPException(409, "Upload details can only be changed before submitting for review.")
    old = {"voucher_purpose": req.voucher_purpose, "approvers": req.approvers}
    req.voucher_purpose = body.voucher_purpose
    req.approvers = [person.model_dump() for person in body.approvers]
    req.updated_at = datetime.now(UTC)
    await log_event(
        db,
        AuditEventType.FIELD_REVISED,
        payload={"field_path": "voucher_metadata", "old_value": old, "new_value": body.model_dump()},
        request_id=request_id,
        actor_id=user.user_id,
    )
    await db.flush()
    return body


@router.post("/requests/{request_id}/submit-review")
async def submit_review(
    request_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_role("CLIENT_PIC"))
):
    req = await accessible_request(request_id, db, user, lock=True)
    if stage_of(req) != REFINEMENT:
        raise HTTPException(409, "This request is not awaiting client refinement.")
    if not req.voucher_purpose or not req.approvers:
        raise HTTPException(422, "Complete the expense purpose and external approvers before submitting.")
    VoucherMetadata(voucher_purpose=req.voucher_purpose, approvers=req.approvers)
    uploader = await db.get(User, req.uploader_id)
    if not uploader.full_name or not uploader.job_title:
        raise HTTPException(422, "The submitter must complete their full name and professional role in Account.")
    draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
    if not draft:
        raise HTTPException(409, "Extraction has not produced a draft.")
    try:
        items, tax_map = await refresh_assignments(db, draft.draft_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    header = await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
    summary = totals(req, header, items, tax_map)
    if header is None or not any(item.line_total is not None for item in items):
        raise HTTPException(409, "Extraction has not produced a usable draft. Retry extraction first.")
    previous_status = req.status.value
    req.submitter_snapshot = {
        "full_name": uploader.full_name,
        "job_title": uploader.job_title,
        "signature_data": uploader.signature_data or "",
    }
    from app.services.advisory_validation import validate_request

    await validate_request(db, req, user.user_id)
    req.refinement_submitted_at = datetime.now(UTC)
    req.workflow_stage = FINANCE_REVIEW
    req.status = RequestStatus.EXCEPTION if req.flags else RequestStatus.READY_FOR_REVIEW
    req.updated_at = req.refinement_submitted_at
    await log_event(
        db,
        AuditEventType.STATUS_CHANGED,
        payload={
            "from_status": previous_status,
            "to_status": req.status.value,
            "workflow_stage": FINANCE_REVIEW,
            "action": "CLIENT_SUBMITTED_REFINEMENT",
            "totals": summary,
        },
        request_id=request_id,
        actor_id=user.user_id,
    )
    await db.flush()
    return {"status": req.status, "workflow_stage": req.workflow_stage, "totals": summary}
