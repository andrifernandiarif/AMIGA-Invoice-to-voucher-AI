# backend/app/services/tax_service.py
"""
Tax computation service.

compute_tax_summary() takes a list of (line_item, tax_assignment | None) pairs
and returns per-type subtotals, total tax, and grand total derived from
header subtotal + total tax.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TypedDict

from app.models.invoice_fields import LineItem
from app.models.tax_assignment import TaxAssignment


class TaxTypeSummary(TypedDict):
    tax_type: str
    subtotal: Decimal


class TaxSummaryResult(TypedDict):
    per_type_subtotals: list[TaxTypeSummary]
    total_tax: Decimal
    grand_total: Decimal  # header.subtotal + total_tax


def compute_tax_summary(
    line_items: list[LineItem],
    tax_assignments: dict[str, TaxAssignment],  # keyed by str(line_item_id)
    header_subtotal: Decimal,
) -> TaxSummaryResult:
    """
    Compute tax totals from line items and their optional tax assignments.

    Args:
        line_items: All line items from the draft.
        tax_assignments: Dict mapping str(line_item_id) → TaxAssignment.
            Line items without a TaxAssignment are treated as NO_TAX (zero).
        header_subtotal: The approved header subtotal (NUMERIC → Decimal).

    Returns:
        TaxSummaryResult with:
        - per_type_subtotals: list of {tax_type, subtotal} sorted by tax_type
        - total_tax: sum of all per-type subtotals
        - grand_total: header_subtotal + total_tax
    """
    per_type: dict[str, Decimal] = {}

    for item in line_items:
        key = str(item.line_item_id)
        ta = tax_assignments.get(key)
        if ta is None:
            continue  # no tax assigned — contributes nothing to tax totals
        tax_type = ta.tax_type.value if hasattr(ta.tax_type, "value") else str(ta.tax_type)
        amount = Decimal(str(ta.tax_amount)) if ta.tax_amount is not None else Decimal("0")
        per_type[tax_type] = per_type.get(tax_type, Decimal("0")) + amount

    per_type_subtotals: list[TaxTypeSummary] = [
        {"tax_type": k, "subtotal": v}
        for k, v in sorted(per_type.items())
    ]
    total_tax = sum(per_type.values(), Decimal("0"))
    grand_total = Decimal(str(header_subtotal)) + total_tax

    return TaxSummaryResult(
        per_type_subtotals=per_type_subtotals,
        total_tax=total_tax,
        grand_total=grand_total,
    )
