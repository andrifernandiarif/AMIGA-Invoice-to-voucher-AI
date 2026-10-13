# backend/app/validation/calculation.py
from __future__ import annotations

from decimal import Decimal

from app.models.invoice_fields import HeaderFields, LineItem


class CalculationValidator:
    """
    Validates invoice arithmetic using Decimal (never float).
    Returns a list of discrepancy dicts — empty list = all checks passed.
    """

    @staticmethod
    def validate(
        header: HeaderFields | None,
        line_items: list[LineItem],
        tolerance: Decimal,
    ) -> list[dict]:
        if not header:
            return []

        discrepancies: list[dict] = []

        # Check 1: sum of line totals vs subtotal
        if header.subtotal is not None and line_items:
            computed_subtotal = sum(
                Decimal(str(li.line_total)) for li in line_items if li.line_total is not None
            )
            extracted_subtotal = Decimal(str(header.subtotal))
            if abs(computed_subtotal - extracted_subtotal) > tolerance:
                discrepancies.append({
                    "field": "subtotal",
                    "computed": str(computed_subtotal),
                    "extracted": str(extracted_subtotal),
                    "difference": str(abs(computed_subtotal - extracted_subtotal)),
                })

        # Check 2: subtotal + tax + fees + rounding vs total_amount
        if all(v is not None for v in [header.subtotal, header.total_amount]):
            computed_total = (
                Decimal(str(header.subtotal or 0))
                + Decimal(str(header.tax_amount or 0))
                + Decimal(str(header.fee_amount or 0))
                + Decimal(str(header.rounding_adj or 0))
            )
            extracted_total = Decimal(str(header.total_amount))
            if abs(computed_total - extracted_total) > tolerance:
                discrepancies.append({
                    "field": "total_amount",
                    "computed": str(computed_total),
                    "extracted": str(extracted_total),
                    "difference": str(abs(computed_total - extracted_total)),
                })

        return discrepancies
