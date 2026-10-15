"""Map reviewed invoice data into editable Indonesian voucher templates."""

from datetime import date, datetime, UTC
from zoneinfo import ZoneInfo
from decimal import Decimal

from app.services.manual_tax import TAX_LABELS, effect_of, totals
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


def display_created_at(value):
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo("Asia/Jakarta")).strftime("%d/%m/%Y %H:%M WIB")


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
    summary = totals(req, h, line_items, tax_map)
    if summary["calculated_from_items"]:
        expanded = []
        for index, (item, row) in enumerate(zip(line_items, items), start=1):
            expanded.append(row)
            assignment = tax_map.get(str(item.line_item_id))
            if assignment and assignment.tax_type.value != "NO_TAX":
                value = Decimal(str(assignment.tax_amount)) * (-1 if effect_of(assignment) == "DEDUCT" else 1)
                percentage = number(Decimal(str(assignment.tax_rate)) * 100)
                rate_label = (
                    "12% x 11/12 DPP (efektif 11%)" if assignment.rate_profile == "PPN_NON_LUXURY" else f"{percentage}%"
                )
                kind = TAX_LABELS.get(assignment.tax_type.value, assignment.tax_type.value)
                treatment = "potongan" if effect_of(assignment) == "DEDUCT" else "biaya pajak"
                expanded.append(
                    dict(
                        description=f'{kind} {rate_label} ({treatment}) - item {index}: {item.description or ""}',
                        quantity="1",
                        unit="pajak",
                        unit_price=str(value),
                        line_total=str(value),
                        tax_type=assignment.tax_type.value,
                        tax_amount=str(assignment.tax_amount),
                        date=row["date"],
                        quantity_display="1",
                        unit_price_display=number(value),
                        line_total_display=number(value),
                    )
                )
        for key, description in [("fees", "Biaya tambahan"), ("rounding", "Penyesuaian pembulatan")]:
            value = Decimal(summary[key])
            if value:
                expanded.append(
                    dict(
                        description=description,
                        quantity="1",
                        unit="",
                        unit_price=str(value),
                        line_total=str(value),
                        date="",
                        quantity_display="1",
                        unit_price_display=number(value),
                        line_total_display=number(value),
                        tax_type="NO_TAX",
                        tax_amount="0",
                    )
                )
        items = expanded
        total_amount = Decimal(summary["voucher_total"]) if summary["voucher_total"] is not None else None
        items_total = total_amount
    return {
        # Indonesian voucher header. Purpose and item dates stay blank until entered.
        "voucher_number": getattr(req, "voucher_number", None) or "",
        "voucher_name": (h.voucher_name if h else None) or (h.vendor_name or "" if h else ""),
        "voucher_purpose": getattr(req, "voucher_purpose", None) or "",
        "voucher_period": display_created_at(getattr(req, "voucher_created_at", None)),
        "voucher_nominal": f"{currency_label} {number(h.total_amount)}".strip()
        if h and h.total_amount is not None
        else "",
        "submitter": getattr(req, "submitter_snapshot", None) or {},
        "approvers": getattr(req, "approvers", None) or [],
        "voucher_line_total": number(items_total),
        # Keep legacy variables available to previously saved custom templates.
        "vendor_name": h.vendor_name or "" if h else "",
        "invoice_number": h.invoice_number or "" if h else "",
        "invoice_date": str(h.invoice_date) if h and h.invoice_date else "",
        "currency": currency,
        "subtotal": str(subtotal),
        "total_amount": str(total_amount) if total_amount is not None else "",
        "tax_amount": str(h.tax_amount) if h and h.tax_amount is not None else "",
        "fee_amount": str(h.fee_amount) if h and h.fee_amount is not None else "",
        "rounding_adj": str(h.rounding_adj) if h and h.rounding_adj is not None else "",
        "line_items_table": items,
        "tax_summary_table": [
            {"tax_type": row["tax_type"], "subtotal": str(row["subtotal"])} for row in tax["per_type_subtotals"]
        ],
        "total_tax": str(Decimal(summary["added_tax"]) - Decimal(summary["withheld_tax"]))
        if summary["calculated_from_items"]
        else str(h.tax_amount if h and h.tax_amount is not None else tax["total_tax"]),
        "grand_total": str(total_amount),
        "bank_account_number": payment.bank_account_number or "" if payment else "",
        "bank_name": payment.bank_name or "" if payment else "",
        "payment_reference": payment.payment_reference or "" if payment else "",
        "authorization_disclaimer": disclaimer,
    }
