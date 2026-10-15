"""Combine independently extracted invoices using Decimal, never model-generated sums."""

from collections import Counter
from decimal import Decimal
from app.schemas.extraction import (
    ExtractionResult,
    InvoiceExtraction,
    HeaderFieldsSchema,
    LineItemSchema,
    PaymentDetailsSchema,
)


def common(values):
    present = [value for value in values if value is not None and value != ""]
    return present[0] if present and len(present) == len(values) and all(v == present[0] for v in present) else None


def combine_invoices(invoices: list[InvoiceExtraction]) -> ExtractionResult:
    if not invoices:
        raise ValueError("No invoice data was extracted. Please retry extraction.")
    # Copies or overlapping pages of the same numbered invoice are one invoice, not extra payments.
    grouped = []
    for invoice in invoices:
        h = invoice.header
        if not h or not invoice.line_items or not any(item.line_total is not None for item in invoice.line_items):
            raise ValueError("A document did not produce usable invoice values. Please retry extraction.")
        key = ((h.vendor_name or "").strip().casefold(), (h.invoice_number or "").strip().casefold())
        previous = next(
            (
                p
                for p in grouped
                if key[1]
                and (
                    (p.header.vendor_name or "").strip().casefold(),
                    (p.header.invoice_number or "").strip().casefold(),
                )
                == key
            ),
            None,
        )
        if previous is None:
            grouped.append(invoice.model_copy(deep=True))
            continue
        for field in ("currency", "total_amount", "subtotal", "tax_amount", "fee_amount", "rounding_adj"):
            old, new = getattr(previous.header, field), getattr(h, field)
            if old is not None and new is not None and old != new:
                raise ValueError(
                    "Copies of the same invoice contain conflicting amounts or currencies; review the source files."
                )
            if old is None:
                setattr(previous.header, field, new)
        # Keep repeated rows within a document; deduplicate overlaps between copies/pages.
        for field in ("line_items", "charges"):
            existing = getattr(previous, field)
            signature = lambda row: row.model_dump_json(exclude={"sequence_number"})
            counts = Counter(signature(row) for row in existing)
            seen = Counter()
            for row in getattr(invoice, field):
                identity = signature(row)
                seen[identity] += 1
                if seen[identity] > counts[identity]:
                    existing.append(row.model_copy(deep=True))
    currencies = {i.header.currency for i in grouped if i.header.currency}
    if len(currencies) > 1:
        raise ValueError(
            "Invoices use different currencies. Upload them in separate requests; no exchange rate is assumed."
        )
    if len(grouped) == 1:
        invoice = grouped[0]
        return ExtractionResult(
            **invoice.model_dump(), source_invoices=grouped, extraction_prompt_version="bedrock-v2.0"
        )
    if any(i.header.currency is None for i in grouped):
        raise ValueError(
            "A document's currency could not be identified; combined invoices must have a known shared currency."
        )
    rows = []
    for invoice in grouped:
        h = invoice.header
        label = h.invoice_number or f"Invoice {len(rows) + 1}"
        for item in invoice.line_items:
            rows.append(
                item.model_copy(
                    update={
                        "description": f"[{label}] {item.description or 'Item'}",
                        "item_date": item.item_date or h.invoice_date,
                    }
                )
            )
        # Charges stay visible in the voucher rather than being dropped or assigned an Indonesian tax preset.
        for charge in invoice.charges:
            rows.append(
                LineItemSchema(
                    sequence_number=1,
                    description=f"[{label}] {charge.description}",
                    item_date=h.invoice_date,
                    quantity=Decimal(1),
                    unit=charge.kind,
                    unit_price=charge.amount,
                    line_total=charge.amount,
                )
            )
        for field, kind, title in (
            ("fee_amount", "fee", "Additional charges"),
            ("tax_amount", "tax", "Invoice tax"),
            ("rounding_adj", "rounding", "Rounding adjustment"),
        ):
            expected = getattr(h, field)
            supplied = sum((c.amount for c in invoice.charges if c.kind == kind), Decimal(0))
            if expected is not None and supplied != expected:
                if supplied:
                    raise ValueError(
                        f"{label}: extracted {title.lower()} do not match the header amount. Retry extraction."
                    )
                if expected:
                    rows.append(
                        LineItemSchema(
                            sequence_number=1,
                            description=f"[{label}] {title}",
                            item_date=h.invoice_date,
                            quantity=Decimal(1),
                            unit=kind,
                            unit_price=expected,
                            line_total=expected,
                        )
                    )
    for index, row in enumerate(rows, start=1):
        row.sequence_number = index
    total = (
        sum((i.header.total_amount for i in grouped), Decimal(0))
        if all(i.header.total_amount is not None for i in grouped)
        else None
    )
    subtotal = sum((r.line_total for r in rows), Decimal(0)) if all(r.line_total is not None for r in rows) else None
    numbers = ", ".join(dict.fromkeys(i.header.invoice_number for i in grouped if i.header.invoice_number))
    if len(numbers) > 200:
        numbers = None  # Full references remain on source invoices and each item.
    return ExtractionResult(
        header=HeaderFieldsSchema(
            vendor_name=common([i.header.vendor_name for i in grouped]),
            voucher_name=common([i.header.voucher_name for i in grouped]),
            invoice_number=numbers,
            invoice_date=common([i.header.invoice_date for i in grouped]),
            currency=next(iter(currencies), None),
            subtotal=subtotal,
            total_amount=total,
            tax_amount=Decimal(0),
            fee_amount=Decimal(0),
            rounding_adj=Decimal(0),
        ),
        line_items=rows,
        payment_details=PaymentDetailsSchema(
            **{
                field: common([getattr(i.payment_details, field, None) for i in grouped])
                for field in ("bank_account_number", "bank_name", "payment_reference")
            }
        ),
        source_invoices=grouped,
        extraction_prompt_version="bedrock-v2.0",
    )
