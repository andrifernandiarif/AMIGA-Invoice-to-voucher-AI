# backend/app/validation/engine.py
"""
Validation engine that coordinates all validators.

Runs completeness, calculation, and duplicate detection validators
and aggregates their results into a flag list for the Request.

Requirement 4.2: Wire validators into extraction pipeline
Requirement 4.5: Aggregate validation results
Requirement 6.2: Apply MISSING_EVIDENCE flag
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.validation.calculation import CalculationValidator
from app.validation.completeness import CompletenessValidator
from app.validation.duplicate import DuplicateDetector


@dataclass
class Flag:
    """
    Represents a validation flag raised during processing.

    Attributes:
        flag_type: The type of flag (e.g., MISSING_EVIDENCE, CALC_DISCREPANCY, POSSIBLE_DUPLICATE).
        details: Optional metadata about the flag (e.g., missing field names, discrepancy info).
    """

    flag_type: str
    """Type of flag: MISSING_EVIDENCE, CALC_DISCREPANCY, or POSSIBLE_DUPLICATE."""

    details: dict = field(default_factory=dict)
    """Optional metadata associated with the flag."""


class ValidationEngine:
    """
    Orchestrates all validation checks and aggregates results.

    Coordinates:
    1. CompletenessValidator — checks for required payment details and line item fields
    2. CalculationValidator — verifies arithmetic (subtotal and total calculations)
    3. DuplicateDetector — checks for potential duplicate invoices

    The engine loads extracted data from the database, runs validators sequentially,
    and returns a flat list of Flag objects.
    """

    @staticmethod
    async def run(
        request_id: uuid.UUID,
        draft: Draft,
        db: AsyncSession,
    ) -> list[Flag]:
        """
        Run all validators and return a list of Flag objects.

        Loads header, line items, and payment details from the database for the given draft,
        then runs completeness, calculation, and duplicate detection validators sequentially.

        Args:
            request_id: The Request ID being validated.
            draft: The Draft object containing the extraction results.
            db: AsyncSession for database queries.

        Returns:
            List of Flag objects. Empty list → Request can proceed to READY_FOR_REVIEW.
            Non-empty list → Request should be transitioned to EXCEPTION status.

        Validates Requirements 4.2, 4.5, 4.6, 5.3, 6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 8.3.
        """
        flags: list[Flag] = []
        tolerance = Decimal(settings.CALCULATION_TOLERANCE)

        # Load header fields
        header_result = await db.execute(
            select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
        )
        header = header_result.scalar_one_or_none()

        # Load line items
        line_items_result = await db.execute(
            select(LineItem)
            .where(LineItem.draft_id == draft.draft_id)
            .order_by(LineItem.sequence_number)
        )
        line_items = line_items_result.scalars().all()

        # Run completeness validation (Requirement 5.3, 6.1, 6.2, 6.3)
        completeness_result = await CompletenessValidator.validate(draft, db)
        if not completeness_result.is_valid:
            flags.append(
                Flag(
                    flag_type="MISSING_EVIDENCE",
                    details={"missing_fields": completeness_result.missing_fields},
                )
            )

        # Run calculation validation (Requirement 7.1, 7.2, 7.3)
        calc_issues = CalculationValidator.validate(header, line_items, tolerance)
        if calc_issues:
            flags.append(
                Flag(
                    flag_type="CALC_DISCREPANCY",
                    details={"discrepancies": calc_issues},
                )
            )

        # Run duplicate detection (Requirement 8.1, 8.2, 8.3)
        if header and header.vendor_name and header.invoice_number:
            matches = await DuplicateDetector.detect(
                request_id, header.vendor_name, header.invoice_number, db
            )
            if matches:
                flags.append(
                    Flag(
                        flag_type="POSSIBLE_DUPLICATE",
                        details={
                            "matches": [
                                {
                                    "request_id": str(m.request_id),
                                    "status": m.status.value,
                                    "invoice_date": m.invoice_date.isoformat()
                                    if m.invoice_date
                                    else None,
                                    "total_amount": str(m.total_amount) if m.total_amount else None,
                                }
                                for m in matches
                            ]
                        },
                    )
                )

        return flags


# Backwards compatibility function for existing extraction_worker code
async def run_validation(
    request_id: uuid.UUID,
    draft: Draft,
    db: AsyncSession,
) -> list[str]:
    """
    Legacy function that runs validation and returns flag type strings.

    This function maintains backwards compatibility with extraction_worker.py.
    New code should use ValidationEngine.run() directly.

    Args:
        request_id: The Request ID being validated.
        draft: The Draft object containing the extraction results.
        db: AsyncSession for database queries.

    Returns:
        List of flag type strings: MISSING_EVIDENCE, CALC_DISCREPANCY, POSSIBLE_DUPLICATE.
        Empty list → READY_FOR_REVIEW. Non-empty → EXCEPTION.
    """
    flag_objects = await ValidationEngine.run(request_id, draft, db)
    return [flag.flag_type for flag in flag_objects]
