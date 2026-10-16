# backend/app/schemas/extraction.py
"""
Pydantic v2 schemas for the Bedrock extraction result.
All fields are Optional — the model must NOT fabricate values for fields
it cannot extract. Missing fields are represented as None.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class HeaderFieldsSchema(BaseModel):
    vendor_name: str | None = None
    voucher_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None  # ISO 4217, e.g. "IDR"
    subtotal: Decimal | None = None
    total_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    fee_amount: Decimal | None = None
    rounding_adj: Decimal | None = None


class LineItemSchema(BaseModel):
    sequence_number: int = Field(..., ge=1)
    item_date: date | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class PaymentDetailsSchema(BaseModel):
    bank_account_number: str | None = None
    bank_name: str | None = None
    payment_reference: str | None = None


class ChargeSchema(BaseModel):
    description: str = Field(min_length=1)
    amount: Decimal
    kind: Literal["fee", "tax", "rounding"]


class InvoiceExtraction(BaseModel):
    header: HeaderFieldsSchema | None = None
    line_items: list[LineItemSchema] = Field(default_factory=list)
    payment_details: PaymentDetailsSchema | None = None
    charges: list[ChargeSchema] = Field(default_factory=list)


class ExtractionResult(InvoiceExtraction):
    document_timings: list[dict] = Field(default_factory=list)
    source_invoices: list[InvoiceExtraction] = Field(default_factory=list)
    extraction_prompt_version: str = "unknown"
    raw_response: str | None = None  # model's raw JSON output for debugging
