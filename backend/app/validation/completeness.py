# backend/app/validation/completeness.py
from __future__ import annotations

from app.models.invoice_fields import LineItem, PaymentDetails


class CompletenessValidator:
    """
    Checks that all required fields are present after extraction.
    Returns a list of missing field names. Empty list = all required fields found.
    """

    REQUIRED_PAYMENT_FIELDS = ["bank_account_number", "bank_name", "payment_reference"]
    REQUIRED_LINE_FIELDS = ["description", "quantity", "unit", "unit_price", "line_total"]

    @staticmethod
    def validate(
        payment: PaymentDetails | None,
        line_items: list[LineItem],
    ) -> list[str]:
        missing: list[str] = []

        # Payment details check
        if payment is None:
            missing.extend(CompletenessValidator.REQUIRED_PAYMENT_FIELDS)
        else:
            for field in CompletenessValidator.REQUIRED_PAYMENT_FIELDS:
                val = getattr(payment, field, None)
                if not val or (isinstance(val, str) and not val.strip()):
                    missing.append(field)

        # Line item fields check
        for i, li in enumerate(line_items):
            for field in CompletenessValidator.REQUIRED_LINE_FIELDS:
                val = getattr(li, field, None)
                if val is None:
                    missing.append(f"line_items[{i + 1}].{field}")

        return missing
