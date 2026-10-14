# backend/app/schemas/voucher_schemas.py
"""
Pydantic v2 response schemas for voucher-related endpoints.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel

from app.models.request import RequestStatus


class LineItemPreview(BaseModel):
    """A single line item with its optional tax assignment."""

    line_item_id: uuid.UUID
    sequence_number: int
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[str] = None
    line_total: Optional[str] = None
    # Tax assignment fields — None when no tax is assigned to this line
    tax_type: Optional[str] = None
    tax_rate: Optional[str] = None
    tax_amount: Optional[str] = None

    model_config = {"from_attributes": True}


class HeaderFieldsPreview(BaseModel):
    """Approved header-level invoice fields."""

    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[str] = None
    total_amount: Optional[str] = None
    tax_amount: Optional[str] = None
    fee_amount: Optional[str] = None
    rounding_adj: Optional[str] = None

    model_config = {"from_attributes": True}


class PaymentDetailsPreview(BaseModel):
    """Approved payment / bank routing details."""

    bank_account_number: Optional[str] = None
    bank_name: Optional[str] = None
    payment_reference: Optional[str] = None

    model_config = {"from_attributes": True}


class VoucherPreviewResponse(BaseModel):
    """
    Read-only voucher preview payload.

    Returned by GET /requests/{id}/voucher-preview.
    Contains all approved fields plus the mandatory Authorization_Disclaimer.
    Satisfies Requirements 14.1, 14.2, 14.3.
    """

    request_id: uuid.UUID
    status: RequestStatus
    header_fields: Optional[HeaderFieldsPreview] = None
    line_items: list[LineItemPreview]
    payment_details: Optional[PaymentDetailsPreview] = None
    authorization_disclaimer: str

    model_config = {"from_attributes": True}
