# backend/app/validation/duplicate.py
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields
from app.models.request import InvoiceRequest, RequestStatus


class DuplicateDetector:
    """
    Checks for existing requests with the same vendor + invoice number.
    Excludes REJECTED and VOIDED requests.
    Returns a list of matching request_ids.
    """

    EXCLUDED_STATUSES = [RequestStatus.REJECTED]

    @staticmethod
    async def detect(
        request_id: uuid.UUID,
        vendor_name: str,
        invoice_number: str,
        db: AsyncSession,
    ) -> list[uuid.UUID]:
        result = await db.execute(
            select(InvoiceRequest.request_id)
            .join(Draft, Draft.request_id == InvoiceRequest.request_id)
            .join(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
            .where(
                func.lower(HeaderFields.vendor_name) == vendor_name.lower(),
                func.lower(HeaderFields.invoice_number) == invoice_number.lower(),
                InvoiceRequest.status.notin_(DuplicateDetector.EXCLUDED_STATUSES),
                InvoiceRequest.request_id != request_id,
            )
        )
        return list(result.scalars().all())
