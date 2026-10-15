"""Four stable, bounded chunks per request; no signature images are indexed."""

from dataclasses import dataclass
import json
import uuid

from sqlalchemy import select
from app.models.request import InvoiceRequest
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.tax_assignment import TaxAssignment
from app.services.manual_tax import assignment_dict, totals


@dataclass(frozen=True)
class TextChunk:
    chunk_type: str
    chunk_text: str
    metadata: dict


def record(obj, names):
    return (
        {name: str(getattr(obj, name)) if getattr(obj, name) is not None else None for name in names.split()}
        if obj
        else {}
    )


async def chunk_request(request_id: uuid.UUID, db) -> list[TextChunk]:
    req = await db.scalar(select(InvoiceRequest).where(InvoiceRequest.request_id == request_id))
    if req is None:
        raise ValueError("Request not found.")
    draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
    header = None
    payment = None
    items = []
    taxes = []
    if draft:
        header = await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
        items = list(
            (
                await db.scalars(
                    select(LineItem).where(LineItem.draft_id == draft.draft_id).order_by(LineItem.sequence_number)
                )
            ).all()
        )
        payment = await db.scalar(select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id))
        if items:
            taxes = list(
                (
                    await db.scalars(
                        select(TaxAssignment).where(TaxAssignment.line_item_id.in_([i.line_item_id for i in items]))
                    )
                ).all()
            )
    tax_map = {str(t.line_item_id): t for t in taxes}
    metadata = {
        "request_id": str(req.request_id),
        "client_id": str(req.client_id),
        "vendor_name": header.vendor_name if header else None,
        "invoice_number": header.invoice_number if header else None,
        "status": req.status.value,
        "request_created_at": req.created_at.isoformat(),
    }
    common = json.dumps(metadata, ensure_ascii=False)
    sections = {
        "HEADER": {
            **record(
                header,
                "vendor_name voucher_name invoice_number invoice_date currency subtotal total_amount tax_amount fee_amount rounding_adj",
            ),
            **record(req, "voucher_number voucher_purpose voucher_created_at request_date"),
            "status": req.status.value,
        },
        "LINE_ITEMS": [
            record(item, "sequence_number item_date description quantity unit unit_price line_total") for item in items
        ],
        "PAYMENT": record(payment, "bank_account_number bank_name payment_reference")
        or {"note": "No payment details recorded."},
        "TAX_SUMMARY": {
            "assignments": [{"line_item_id": str(t.line_item_id), **assignment_dict(t)} for t in taxes],
            "totals": totals(req, header, items, tax_map),
        },
    }
    chunks = []
    for kind, data in sections.items():
        text = f"Request context: {common}\n{kind}: {json.dumps(data, ensure_ascii=False, default=str)}"
        # Bound even very large invoices conservatively by UTF-8 bytes, below
        # Titan's token limit; make omissions explicit rather than silently lying.
        truncated = len(text.encode("utf-8")) > 7800
        if truncated:
            text = (
                text.encode("utf-8")[:7600].decode("utf-8", errors="ignore")
                + "\n[Truncated: remaining records are not included.]"
            )
        chunks.append(TextChunk(kind, text, {**metadata, "truncated": truncated}))
    return chunks
