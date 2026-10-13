# backend/app/validation/engine.py
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.validation.calculation import CalculationValidator
from app.validation.completeness import CompletenessValidator
from app.validation.duplicate import DuplicateDetector


async def run_validation(
    request_id: uuid.UUID,
    header: HeaderFields | None,
    line_items: list[LineItem],
    payment: PaymentDetails | None,
    db: AsyncSession,
) -> list[str]:
    """
    Run all validators and return a list of flag strings.
    Empty list → READY_FOR_REVIEW. Non-empty → EXCEPTION.
    """
    flags: list[str] = []
    tolerance = Decimal(settings.CALCULATION_TOLERANCE)

    missing = CompletenessValidator.validate(payment, line_items)
    if missing:
        flags.append("MISSING_EVIDENCE")

    calc_issues = CalculationValidator.validate(header, line_items, tolerance)
    if calc_issues:
        flags.append("CALC_DISCREPANCY")

    if header and header.vendor_name and header.invoice_number:
        matches = await DuplicateDetector.detect(
            request_id, header.vendor_name, header.invoice_number, db
        )
        if matches:
            flags.append("POSSIBLE_DUPLICATE")

    return flags
