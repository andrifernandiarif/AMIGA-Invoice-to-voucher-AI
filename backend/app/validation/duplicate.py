"""Client-scoped invoice identity matching across single and combined requests."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields
from app.models.request import InvoiceRequest, RequestStatus


def normalize(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()


def invoice_identities(header, sources):
    """Single invoices use live edited fields; combined invoices retain each source identity."""
    if sources and len(sources) > 1:
        return [
            dict(source.get("header") or {}, source_invoice_index=index + 1) for index, source in enumerate(sources)
        ]
    if header is None:
        return []
    return [
        {
            key: getattr(header, key, None)
            for key in ("vendor_name", "invoice_number", "invoice_date", "currency", "subtotal", "total_amount")
        }
    ]


class MatchResult(NamedTuple):
    request_id: uuid.UUID
    status: RequestStatus
    invoice_date: date | str | None
    total_amount: object
    matched_sources: tuple = ()


class DuplicateDetector:
    EXCLUDED_STATUSES = [RequestStatus.REJECTED]

    @staticmethod
    async def detect(request_id: uuid.UUID, vendor_name, invoice_number, db: AsyncSession, *, sources=None):
        if sources is None:
            sources = await db.scalar(select(Draft.source_invoices).where(Draft.request_id == request_id))
        # Arguments remain compatible with callers using a newly edited header.
        from types import SimpleNamespace

        target = invoice_identities(SimpleNamespace(vendor_name=vendor_name, invoice_number=invoice_number), sources)
        keys = {
            (normalize(i.get("vendor_name")), normalize(i.get("invoice_number")))
            for i in target
            if normalize(i.get("vendor_name")) and normalize(i.get("invoice_number"))
        }
        if not keys:
            return []
        rows = (
            await db.execute(
                select(InvoiceRequest, HeaderFields, Draft.source_invoices)
                .join(Draft, Draft.request_id == InvoiceRequest.request_id)
                .outerjoin(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
                .where(
                    InvoiceRequest.request_id != request_id,
                    InvoiceRequest.status.notin_(DuplicateDetector.EXCLUDED_STATUSES),
                    InvoiceRequest.client_id
                    == select(InvoiceRequest.client_id)
                    .where(InvoiceRequest.request_id == request_id)
                    .scalar_subquery(),
                )
            )
        ).all()
        matches = []
        for req, header, source_invoices in rows:
            overlap = [
                i
                for i in invoice_identities(header, source_invoices)
                if (normalize(i.get("vendor_name")), normalize(i.get("invoice_number"))) in keys
            ]
            if overlap:
                matches.append(
                    MatchResult(
                        req.request_id,
                        req.status,
                        overlap[0].get("invoice_date"),
                        overlap[0].get("total_amount"),
                        tuple(overlap),
                    )
                )
        return matches
