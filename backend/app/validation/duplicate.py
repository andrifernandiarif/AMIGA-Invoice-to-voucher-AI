# backend/app/validation/duplicate.py
from __future__ import annotations

import uuid
from datetime import date
from typing import NamedTuple

from sqlalchemy import Numeric, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields
from app.models.request import InvoiceRequest, RequestStatus


class MatchResult(NamedTuple):
    """Result of a duplicate match: request details for display to Finance Reviewer."""
    request_id: uuid.UUID
    status: RequestStatus
    invoice_date: date | None
    total_amount: Numeric | None


class DuplicateDetector:
    """
    Checks for existing requests with the same vendor + invoice number.
    Excludes REJECTED and VOIDED requests.
    Returns a list of MatchResult objects containing request details.
    """

    EXCLUDED_STATUSES = [RequestStatus.REJECTED]

    @staticmethod
    async def detect(
        request_id: uuid.UUID,
        vendor_name: str,
        invoice_number: str,
        db: AsyncSession,
    ) -> list[MatchResult]:
        """
        Detect duplicate invoices by case-insensitive vendor name and invoice number match.
        
        Args:
            request_id: The current request being checked (excluded from results)
            vendor_name: Vendor name to match
            invoice_number: Invoice number to match
            db: AsyncSession for database access
            
        Returns:
            List of MatchResult objects with matching request details
        """
        result = await db.execute(
            select(
                InvoiceRequest.request_id,
                InvoiceRequest.status,
                HeaderFields.invoice_date,
                HeaderFields.total_amount,
            )
            .join(Draft, Draft.request_id == InvoiceRequest.request_id)
            .join(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
            .where(
                func.lower(HeaderFields.vendor_name) == vendor_name.lower(),
                func.lower(HeaderFields.invoice_number) == invoice_number.lower(),
                InvoiceRequest.status.notin_(DuplicateDetector.EXCLUDED_STATUSES),
                InvoiceRequest.request_id != request_id,
                InvoiceRequest.client_id == select(InvoiceRequest.client_id).where(
                    InvoiceRequest.request_id == request_id).scalar_subquery(),
            )
        )
        matches = result.fetchall()
        return [
            MatchResult(
                request_id=match[0],
                status=match[1],
                invoice_date=match[2],
                total_amount=match[3],
            )
            for match in matches
        ]
