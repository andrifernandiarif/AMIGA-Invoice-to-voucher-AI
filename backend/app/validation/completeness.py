# backend/app/validation/completeness.py
"""
Completeness validation for extracted invoice data.

Validates that all required fields (payment details and line item fields) are present
after AI extraction. Missing or empty fields trigger a MISSING_EVIDENCE flag.

Requirement 6: Completeness Validation — Missing Evidence
- Requirement 5.3: Line items with missing fields trigger MISSING_EVIDENCE
- Requirement 6.1: Payment details must include non-None, non-empty bank_account_number, bank_name, payment_reference
- Requirement 6.2: MISSING_EVIDENCE flag sets Request status to EXCEPTION
- Requirement 6.3: Exception UX lists each missing Payment_Details field by name
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    """Whether validation passed (True = no issues, False = issues found)."""

    missing_fields: list[str]
    """List of missing or empty required field names. Empty when is_valid=True."""


class CompletenessValidator:
    """
    Validates completeness of extracted invoice data.

    Checks that:
    1. PaymentDetails contains non-None, non-empty bank_account_number, bank_name, payment_reference
    2. Each LineItem has all required fields (description, quantity, unit, unit_price, line_total)

    Missing or empty fields result in ValidationResult.is_valid=False with missing_fields populated.
    """

    REQUIRED_HEADER_FIELDS = ["vendor_name", "invoice_number", "invoice_date", "currency", "subtotal", "total_amount"]

    REQUIRED_PAYMENT_FIELDS = ["bank_account_number", "bank_name", "payment_reference"]
    """Required fields in PaymentDetails."""

    REQUIRED_LINE_FIELDS = ["description", "quantity", "unit", "unit_price", "line_total"]
    """Required fields in each LineItem."""

    @staticmethod
    async def validate(draft: Draft, db: AsyncSession, *, data=None) -> ValidationResult:
        """
        Validate completeness of a draft's extracted fields.

        Loads the draft's PaymentDetails and LineItems from the database and checks that all
        required fields are present and non-empty.

        Args:
            draft: The Draft object to validate (must have draft_id set).
            db: AsyncSession for database queries.

        Returns:
            ValidationResult with is_valid=True if all required fields present,
            is_valid=False with missing_fields list if any are missing or empty.

        Validates Requirements 5.3, 6.1, 6.2, 6.3.
        """
        missing_fields: list[str] = []

        if data is not None:
            payment = data.payment
        else:
            # Load PaymentDetails for this draft
            payment_result = await db.execute(select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id))
            payment = payment_result.scalar_one_or_none()
        # Check payment details
        if payment is None:
            missing_fields.extend(CompletenessValidator.REQUIRED_PAYMENT_FIELDS)
        else:
            for field_name in CompletenessValidator.REQUIRED_PAYMENT_FIELDS:
                val = getattr(payment, field_name, None)
                # Field is missing if it is None or (for strings) empty after stripping whitespace
                if val is None or (isinstance(val, str) and not val.strip()):
                    missing_fields.append(field_name)

        if data is not None:
            line_items = data.items
        else:
            # Load LineItems for this draft
            line_items_result = await db.execute(
                select(LineItem).where(LineItem.draft_id == draft.draft_id).order_by(LineItem.sequence_number)
            )
            line_items = line_items_result.scalars().all()
        # Check each line item
        for line_item in line_items:
            for field_name in CompletenessValidator.REQUIRED_LINE_FIELDS:
                val = getattr(line_item, field_name, None)
                if val is None or (isinstance(val, str) and not val.strip()):
                    # Use 1-based sequence number in error message for user-friendly reference
                    missing_fields.append(f"line_items[{line_item.sequence_number}].{field_name}")

        if not line_items:
            missing_fields.append("line_items")
        header = (
            data.header
            if data is not None
            else await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
        )
        from app.validation.duplicate import invoice_identities

        for index, identity in enumerate(invoice_identities(header, draft.source_invoices)):
            for field in CompletenessValidator.REQUIRED_HEADER_FIELDS:
                value = identity.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    prefix = f"source_invoices[{index + 1}]" if len(draft.source_invoices or []) > 1 else "header"
                    missing_fields.append(f"{prefix}.{field}")
        if header is None:
            missing_fields.append("invoice_header")
        # Return result: valid if no missing fields, invalid otherwise
        is_valid = len(missing_fields) == 0
        return ValidationResult(is_valid=is_valid, missing_fields=missing_fields)
