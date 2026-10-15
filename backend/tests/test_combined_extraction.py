"""Regression coverage for two US freight invoices becoming one payable voucher."""

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app.schemas.extraction import InvoiceExtraction, HeaderFieldsSchema, LineItemSchema, ChargeSchema
from app.services.combine_invoices import combine_invoices
from app.services.extraction_worker import _persist_extraction
from app.services.manual_tax import totals
from app.models.request import RequestStatus
from app.models.draft import Draft, ExtractionStatus
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.providers.bedrock import BedrockExtractionProvider
from tests.test_approval import db_session  # noqa: F401
from tests.test_manual_workflow import seed
from tests.test_organization_workflows import http, bearer

# ruff: noqa: F811


def invoices():
    first = InvoiceExtraction(
        header=HeaderFieldsSchema(
            vendor_name="Northwind Logistics LLC",
            voucher_name="Riverbend Construction",
            invoice_number="NWL-20240322-008",
            invoice_date=date(2024, 3, 22),
            currency="USD",
            subtotal=770,
            total_amount="945.05",
            tax_amount="50.05",
            fee_amount=125,
        ),
        line_items=[
            LineItemSchema(
                sequence_number=1,
                description="Reinforced shipping pallets, 48x40",
                quantity=20,
                unit_price="38.50",
                line_total=770,
            )
        ],
        charges=[
            ChargeSchema(description="Freight", amount=125, kind="fee"),
            ChargeSchema(description="Sales Tax (6.5%)", amount="50.05", kind="tax"),
        ],
    )
    second = InvoiceExtraction(
        header=HeaderFieldsSchema(
            vendor_name="Northwind Logistics LLC",
            voucher_name="Riverbend Construction",
            invoice_number="NWL-20240410-012",
            invoice_date=date(2024, 4, 10),
            currency="USD",
            subtotal=2850,
            total_amount="3552.25",
            tax_amount="185.25",
            fee_amount=517,
        ),
        line_items=[
            LineItemSchema(
                sequence_number=1,
                description="Freight transport, Seattle to Denver (48 ft dry van)",
                quantity=1,
                unit_price=2850,
                line_total=2850,
            )
        ],
        charges=[
            ChargeSchema(description="Accessorial Freight", amount=175, kind="fee"),
            ChargeSchema(description="Fuel Surcharge (12%)", amount=342, kind="fee"),
            ChargeSchema(description="Sales Tax (6.5%)", amount="185.25", kind="tax"),
        ],
    )
    return first, second


def test_two_invoices_preserve_every_charge_and_exact_total():
    result = combine_invoices(list(invoices()))
    assert result.header.total_amount == Decimal("4497.30")
    assert result.header.subtotal == Decimal("4497.30")
    assert result.header.invoice_date is None
    assert result.header.currency == "USD"
    assert len(result.source_invoices) == 2 and len(result.line_items) == 7
    assert [r.sequence_number for r in result.line_items] == list(range(1, 8))
    assert sum(r.line_total for r in result.line_items) == Decimal("4497.30")
    assert any("Fuel Surcharge" in r.description and r.line_total == 342 for r in result.line_items)
    assert {r.item_date for r in result.line_items} == {date(2024, 3, 22), date(2024, 4, 10)}
    req = SimpleNamespace(status=RequestStatus.READY_FOR_REFINEMENT, refinement_submitted_at=None)
    assert Decimal(totals(req, result.header, result.line_items, {})["voucher_total"]) == Decimal("4497.30")
    assert result.header.tax_amount == result.header.fee_amount == 0  # Already represented by visible rows.


def test_duplicate_copy_is_not_paid_twice_and_mixed_currencies_rejected():
    first, second = invoices()
    result = combine_invoices([first, first.model_copy(deep=True), second])
    assert len(result.source_invoices) == 2 and result.header.total_amount == Decimal("4497.30")
    second.header.currency = "IDR"
    with pytest.raises(ValueError, match="different currencies"):
        combine_invoices([first, second])


def test_multiple_invoice_pages_keep_repeated_rows_but_not_duplicate_pages():
    first, _ = invoices()
    first.line_items.append(first.line_items[0].model_copy(update={"sequence_number": 2}))
    result = combine_invoices([first, first.model_copy(deep=True)])
    assert len(result.line_items) == 2


def test_conflicting_copy_and_empty_extraction_fail_explicitly():
    first, _ = invoices()
    changed = first.model_copy(deep=True)
    changed.header.total_amount = Decimal(123)
    with pytest.raises(ValueError, match="conflicting"):
        combine_invoices([first, changed])
    with pytest.raises(ValueError, match="usable"):
        combine_invoices([InvoiceExtraction()])


async def test_provider_extracts_each_document_and_does_not_silently_drop_invalid_values():
    first, second = invoices()
    provider = object.__new__(BedrockExtractionProvider)

    def invoke(files):
        return (first if files[0][0] == b"first" else second).model_dump_json()

    with patch.object(provider, "_invoke_sync", side_effect=invoke) as call:
        result = await provider.extract([(b"first", "application/pdf"), (b"second", "application/pdf")], uuid.uuid4())
    assert call.call_count == 2 and result.header.total_amount == Decimal("4497.30")
    malformed = first.model_dump(mode="json")
    malformed["header"]["invoice_date"] = "2024-03-22, 2024-04-10"
    with patch.object(provider, "_invoke_sync", return_value=json.dumps(malformed)):
        with pytest.raises(ValueError, match="invalid invoice values"):
            await provider.extract([(b"first", "application/pdf")], uuid.uuid4())


async def test_provider_handles_multiple_invoices_inside_one_pdf():
    provider = object.__new__(BedrockExtractionProvider)
    raw = json.dumps({"invoices": [i.model_dump(mode="json") for i in invoices()]})
    with patch.object(provider, "_invoke_sync", return_value=raw):
        result = await provider.extract([(b"one-pdf", "application/pdf")], uuid.uuid4())
    assert result.header.total_amount == Decimal("4497.30")


async def test_persisted_combined_draft_and_voucher_totals(db_session):
    data = await seed(db_session)
    # Create another request using the same test company; preserve the existing reviewed draft.
    from app.models.request import InvoiceRequest
    from datetime import datetime, timezone

    req = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=data["client"].client_id,
        uploader_id=data["uploader"].user_id,
        request_type="standard",
        channel="upload",
        request_date=date.today(),
        status=RequestStatus.READY_FOR_REFINEMENT,
        flags=[],
        invoice_file_key="combined",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(req)
    await db_session.flush()
    result = combine_invoices(list(invoices()))
    draft, header, rows, payment = await _persist_extraction(db_session, req.request_id, result)
    await db_session.commit()
    await db_session.refresh(header)
    assert len(draft.source_invoices) == 2 and len(rows) == 7
    assert header.total_amount == Decimal("4497.3000")
    assert Decimal(totals(req, header, rows, {})["voucher_total"]) == Decimal("4497.30")


async def test_retry_only_replaces_an_empty_unsubmitted_draft(db_session):
    data = await seed(db_session)
    req = data["request"]
    async with await http(db_session) as client:
        protected = await client.post(f"/requests/{req.request_id}/retry-extraction", headers=bearer(data["uploader"]))
        assert protected.status_code == 409
    from datetime import datetime, timezone
    from app.models.request import InvoiceRequest

    empty_req = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=req.client_id,
        uploader_id=req.uploader_id,
        request_type="standard",
        channel="upload",
        request_date=date.today(),
        status=RequestStatus.READY_FOR_REFINEMENT,
        flags=[],
        invoice_file_key="empty",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(empty_req)
    await db_session.flush()
    db_session.add(
        Draft(
            draft_id=uuid.uuid4(),
            request_id=empty_req.request_id,
            ai_label_visible=True,
            extraction_status=ExtractionStatus.PARTIAL,
        )
    )
    job = ExtractionJob(
        job_id=uuid.uuid4(),
        request_id=empty_req.request_id,
        status=ExtractionJobStatus.DONE,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    async with await http(db_session) as client:
        response = await client.post(
            f"/requests/{empty_req.request_id}/retry-extraction", headers=bearer(data["uploader"])
        )
    assert response.status_code == 200
    assert empty_req.status == RequestStatus.QUEUED and job.status == ExtractionJobStatus.PENDING
    assert await db_session.scalar(select(Draft).where(Draft.request_id == empty_req.request_id)) is None


def test_currency_context_never_assumes_dollar_sign_alone():
    from app.providers.bedrock import _source_currency

    with patch("app.providers.bedrock._pdf_text", return_value=["TOTAL $945.05\nSeattle, WA 98101"]):
        assert _source_currency((b"pdf", "application/pdf")) == "USD"
    with patch("app.providers.bedrock._pdf_text", return_value=["TOTAL $945.05"]):
        assert _source_currency((b"pdf", "application/pdf")) is None
    with patch("app.providers.bedrock._pdf_text", return_value=["CAD $945.05\nSeattle, WA 98101"]):
        assert _source_currency((b"pdf", "application/pdf")) == "CAD"
