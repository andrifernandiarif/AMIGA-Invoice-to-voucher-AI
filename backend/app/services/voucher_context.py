"""Map reviewed invoice data into editable Indonesian voucher templates."""

from datetime import date
from decimal import Decimal

from app.services.tax_service import compute_tax_summary


def number(value) -> str:
    if value is None:
        return ""
    value = Decimal(str(value))
    digits = f"{value:,.2f}" if value != value.to_integral_value() else f"{value:,.0f}"
    return digits.replace(",", "_").replace(".", ",").replace("_", ".")


def display_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime("%d/%m/%Y")


def build_voucher_context(req, header, line_items, payment, tax_map, disclaimer):
    h = header
    subtotal = Decimal(str(h.subtotal)) if h and h.subtotal is not None else Decimal("0")
    tax = compute_tax_summary(line_items, tax_map, subtotal)
    total_amount = h.total_amount if h and h.total_amount is not None else tax["grand_total"]
    currency = h.currency or "" if h else ""
    currency_label = "Rp" if currency == "IDR" else currency
    items = []
    for item in line_items:
        assignment = tax_map.get(str(item.line_item_id))
        items.append(
            {
                "description": item.description or "",
                "quantity": str(item.quantity) if item.quantity is not None else "",
                "unit": item.unit or "",
                "unit_price": str(item.unit_price) if item.unit_price is not None else "",
                "line_total": str(item.line_total) if item.line_total is not None else "",
                "tax_type": (
                    assignment.tax_type.value if hasattr(assignment.tax_type, "value") else str(assignment.tax_type)
                )
                if assignment
                else "NO_TAX",
                "tax_amount": str(assignment.tax_amount) if assignment else "0",
                "date": display_date(item.item_date),
                "quantity_display": number(item.quantity),
                "unit_price_display": number(item.unit_price),
                "line_total_display": number(item.line_total),
            }
        )
    items_total = (
        sum((Decimal(str(item.line_total)) for item in line_items), Decimal("0"))
        if line_items and all(item.line_total is not None for item in line_items)
        else None
    )
    return {
        # Indonesian voucher header. Purpose and item dates stay blank until entered.
        "voucher_number": (h.voucher_number if h else None)
        or f"VCR-{req.request_date:%Y%m%d}-{req.request_id.hex[:12].upper()}",
        "voucher_name": (h.voucher_name if h else None) or (h.vendor_name or "" if h else ""),
        "voucher_purpose": h.voucher_purpose or "" if h else "",
        "voucher_period": (h.voucher_period if h else None)
        or display_date((h.invoice_date if h else None) or req.request_date),
        "voucher_nominal": f"{currency_label} {number(total_amount)}".strip(),
        "voucher_line_total": number(items_total),
        # Keep legacy variables available to previously saved custom templates.
        "vendor_name": h.vendor_name or "" if h else "",
        "invoice_number": h.invoice_number or "" if h else "",
        "invoice_date": str(h.invoice_date) if h and h.invoice_date else "",
        "currency": currency,
        "subtotal": str(subtotal),
        "total_amount": str(h.total_amount) if h and h.total_amount is not None else "",
        "tax_amount": str(h.tax_amount) if h and h.tax_amount is not None else "",
        "fee_amount": str(h.fee_amount) if h and h.fee_amount is not None else "",
        "rounding_adj": str(h.rounding_adj) if h and h.rounding_adj is not None else "",
        "line_items_table": items,
        "tax_summary_table": [
            {"tax_type": row["tax_type"], "subtotal": str(row["subtotal"])} for row in tax["per_type_subtotals"]
        ],
        "total_tax": str(h.tax_amount if h and h.tax_amount is not None else tax["total_tax"]),
        "grand_total": str(total_amount),
        "bank_account_number": payment.bank_account_number or "" if payment else "",
        "bank_name": payment.bank_name or "" if payment else "",
        "payment_reference": payment.payment_reference or "" if payment else "",
        "authorization_disclaimer": disclaimer,
    }
