# backend/app/providers/mock.py
"""
Mock extraction provider for local development and automated tests.
Returns configurable fixture data — no AWS credentials required.
Configure via MOCK_EXTRACTION_RESULT env var (JSON) or use the built-in fixture.
"""
import json
import os
from decimal import Decimal
from uuid import UUID

from app.providers.base import ExtractionProvider
from app.schemas.extraction import (
    ExtractionResult,
    HeaderFieldsSchema,
    LineItemSchema,
    PaymentDetailsSchema,
)

_DEFAULT_FIXTURE = ExtractionResult(
    header=HeaderFieldsSchema(
        vendor_name="PT Contoh Vendor Indonesia",
        invoice_number="INV-2024-001",
        invoice_date=None,          # will be set from fixture or left None
        currency="IDR",
        subtotal=Decimal("1000000.0000"),
        total_amount=Decimal("1110000.0000"),
        tax_amount=Decimal("110000.0000"),
        fee_amount=Decimal("0.0000"),
        rounding_adj=Decimal("0.0000"),
    ),
    line_items=[
        LineItemSchema(
            sequence_number=1,
            description="Jasa Konsultasi IT",
            quantity=Decimal("1.0000"),
            unit="paket",
            unit_price=Decimal("600000.0000"),
            line_total=Decimal("600000.0000"),
        ),
        LineItemSchema(
            sequence_number=2,
            description="Lisensi Software Tahunan",
            quantity=Decimal("2.0000"),
            unit="lisensi",
            unit_price=Decimal("200000.0000"),
            line_total=Decimal("400000.0000"),
        ),
    ],
    payment_details=PaymentDetailsSchema(
        bank_account_number="1234567890",
        bank_name="Bank Central Asia",
        payment_reference="REF-2024-001",
    ),
    extraction_prompt_version="mock-v1.0",
    raw_response=None,
)


class MockExtractionProvider(ExtractionProvider):
    """
    Returns a static fixture. Override with MOCK_EXTRACTION_RESULT env var
    (JSON string matching ExtractionResult schema) to test different scenarios.
    """

    async def extract(
        self,
        evidence_files: list[tuple[bytes, str]],
        request_id: UUID,
    ) -> ExtractionResult:
        raw = os.getenv("MOCK_EXTRACTION_RESULT")
        if raw:
            return ExtractionResult.model_validate_json(raw)
        return _DEFAULT_FIXTURE
