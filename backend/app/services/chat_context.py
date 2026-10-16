"""Bounded, live, request-scoped context. Call only after relational authorization."""

import json

from sqlalchemy import select

from app.models.audit import AuditEvent, AuditEventType
from app.models.client import Client
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.tax_assignment import TaxAssignment
from app.services.chunker import record
from app.services.manual_tax import assignment_dict, totals
from app.services.workflow import FINANCE_REVIEW, REFINEMENT, stage_of

PAGE_SIZE = 40
AUDIT_TYPES = {
    AuditEventType.REQUEST_CREATED,
    AuditEventType.STATUS_CHANGED,
    AuditEventType.FLAG_APPLIED,
    AuditEventType.FLAG_DISMISSED,
    AuditEventType.FIELD_REVISED,
    AuditEventType.APPROVED,
    AuditEventType.REJECTED,
    AuditEventType.DUPLICATE_DISMISSED,
    AuditEventType.REALLOCATED,
    AuditEventType.VOUCHER_GENERATED,
    AuditEventType.VOUCHER_DOWNLOADED,
    AuditEventType.PROCESSING_FAILED,
}
NEXT_ACTION = {
    "QUEUED": "Await extraction processing.",
    "PROCESSING": "Await extraction completion.",
    "READY_FOR_REFINEMENT": "Complete and submit invoice refinement.",
    "READY_FOR_REVIEW": "An authorized independent reviewer must review and approve or reject.",
    "EXCEPTION": "Resolve the processing or validation exception before continuing.",
    "AMIGA_REVIEWED": "The approved voucher is available for download.",
    "SENT_TO_CLIENT": "Voucher delivery has been recorded; no further workflow action is specified.",
    "REJECTED": "Review the rejection reason; no subsequent transition is recorded.",
}


def serialize(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def safe_event(event):
    # Payloads can contain arbitrary user data, errors, and account details.
    # Only status transitions are necessary to explain milestones.
    result = {"event": event.event_type.value, "at": event.occurred_at.isoformat()}
    if event.event_type == AuditEventType.STATUS_CHANGED:
        from app.models.request import RequestStatus

        statuses = {s.value for s in RequestStatus}
        for key in ("from", "to", "old_status", "new_status", "from_status", "to_status"):
            value = (event.payload or {}).get(key)
            if isinstance(value, str) and value in statuses:
                result[key] = value
        stage = (event.payload or {}).get("workflow_stage")
        if stage in (REFINEMENT, FINANCE_REVIEW):
            result["workflow_stage"] = stage
        if (event.payload or {}).get("action") == "CLIENT_SUBMITTED_REFINEMENT":
            result["milestone"] = "Client submitted refinement for Finance review"
    return result


async def request_context(req, db, *, include_history, item_page=0):
    company = await db.get(Client, req.client_id)
    draft = await db.scalar(select(Draft).where(Draft.request_id == req.request_id))
    header = payment = None
    items, taxes = [], []
    if draft:
        header = await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
        payment = await db.scalar(select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id))
        items = list(
            (
                await db.scalars(
                    select(LineItem)
                    .where(LineItem.draft_id == draft.draft_id)
                    .order_by(LineItem.sequence_number, LineItem.line_item_id)
                )
            ).all()
        )
        if items:
            taxes = list(
                (
                    await db.scalars(
                        select(TaxAssignment).where(TaxAssignment.line_item_id.in_([i.line_item_id for i in items]))
                    )
                ).all()
            )
    start = item_page * PAGE_SIZE
    page_items = items[start : start + PAGE_SIZE]
    tax_map = {str(t.line_item_id): t for t in taxes}
    context = {
        "source": f"request:{req.request_id}",
        "company": record(company, "client_id client_name client_code is_active"),
        "request": record(
            req,
            "request_id status request_type channel request_date created_at updated_at "
            "due_date refinement_submitted_at voucher_number voucher_purpose voucher_created_at "
            "rejection_reason",
        ),
        "flags": req.flags,
        "validation_issues": req.validation_issues,
        "workflow_stage": stage_of(req),
        "next_required_action": (
            "Client PIC must refine and submit; validation flags remain visible and may be submitted for demo review."
            if stage_of(req) == REFINEMENT
            else "An authorized independent reviewer in Finance must review and approve or reject; unresolved validation flags do not block demo approval."
            if stage_of(req) == FINANCE_REVIEW
            else NEXT_ACTION[req.status.value]
        ),
        "invoice_header": record(
            header,
            "vendor_name voucher_name invoice_number invoice_date currency subtotal "
            "total_amount tax_amount fee_amount rounding_adj",
        ),
        "payment": record(payment, "bank_name bank_account_number payment_reference"),
        "line_items": [
            record(i, "sequence_number item_date description quantity unit unit_price line_total") for i in page_items
        ],
        "line_item_coverage": {
            "page": item_page,
            "page_size": PAGE_SIZE,
            "total": len(items),
            "has_more": start + PAGE_SIZE < len(items),
        },
        "tax_assignments": [
            {"sequence_number": i.sequence_number, **assignment_dict(tax_map[str(i.line_item_id)])}
            for i in page_items
            if str(i.line_item_id) in tax_map
        ],
        "calculated_totals_all_items": totals(req, header, items, tax_map),
        "missing_data_policy": "Missing fields are unknown, not zero or evidence of completion.",
    }
    if include_history:
        events = list(
            (
                await db.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.request_id == req.request_id, AuditEvent.event_type.in_(AUDIT_TYPES))
                    .order_by(AuditEvent.occurred_at.desc(), AuditEvent.event_id.desc())
                    .limit(51)
                )
            ).all()
        )
        context["audit_timeline"] = [safe_event(e) for e in reversed(events[:50])]
        context["audit_coverage"] = (
            "Latest 50 recorded milestones; older events omitted."
            if len(events) > 50
            else "Available recorded milestones only; historical completeness is not guaranteed."
        )
    else:
        context["audit_coverage"] = "This role is not authorized to view audit history."
    invoice_label = header.invoice_number if header and header.invoice_number else str(req.request_id)[:8]
    source = {
        "request_id": str(req.request_id),
        "label": f"{company.client_name if company else 'Request'} · {invoice_label}",
    }
    coverage = (
        f"Line items {start + 1 if page_items else 0}–{start + len(page_items)} of {len(items)} for {req.request_id}."
    )
    return serialize(context), source, coverage, start + PAGE_SIZE < len(items)
