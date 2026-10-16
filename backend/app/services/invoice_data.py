"""Request-local invoice snapshot shared by validation and voucher rendering."""

from dataclasses import dataclass

from sqlalchemy import select

from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.tax_assignment import TaxAssignment


@dataclass
class InvoiceData:
    draft: Draft | None
    header: HeaderFields | None
    items: list[LineItem]
    payment: PaymentDetails | None
    taxes: dict


async def load_invoice_data(request_id, db):
    row = (
        await db.execute(
            select(Draft, HeaderFields, PaymentDetails)
            .outerjoin(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
            .outerjoin(PaymentDetails, PaymentDetails.draft_id == Draft.draft_id)
            .where(Draft.request_id == request_id)
        )
    ).first()
    if row is None:
        return InvoiceData(None, None, [], None, {})
    draft, header, payment = row
    rows = (
        await db.execute(
            select(LineItem, TaxAssignment)
            .outerjoin(TaxAssignment, TaxAssignment.line_item_id == LineItem.line_item_id)
            .where(LineItem.draft_id == draft.draft_id)
            .order_by(LineItem.sequence_number)
        )
    ).all()
    return InvoiceData(
        draft,
        header,
        [row[0] for row in rows],
        payment,
        {str(row[0].line_item_id): row[1] for row in rows if row[1] is not None},
    )
