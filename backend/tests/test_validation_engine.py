"""
Unit tests for ValidationEngine (Task 20.1).

Tests the ValidationEngine class and its run method that orchestrates
completeness, calculation, and duplicate detection validators.

Requirement 4.2: Wire validators into extraction pipeline
Requirement 4.5: Aggregate validation results
Requirement 6.2: Apply MISSING_EVIDENCE flag
Requirement 7.1, 7.2: Calculation validation
Requirement 8.1, 8.2: Duplicate detection
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models import Base
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import (
    HeaderFields,
    LineItem,
    PaymentDetails,
    LineExtractionStatus,
)
from app.models.request import InvoiceRequest, RequestStatus
from app.validation.engine import Flag, ValidationEngine


@pytest.fixture
async def test_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        yield session

    await engine.dispose()


async def setup_draft(db: AsyncSession):
    """Create a request and draft for testing."""
    request_id = uuid.uuid4()
    draft_id = uuid.uuid4()

    # Create request
    req = InvoiceRequest(
        request_id=request_id,
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=RequestStatus.PROCESSING,
        flags=[],
        invoice_file_key="s3://bucket/invoice.pdf",
        invoice_page_count=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(req)

    # Create draft
    draft = Draft(
        draft_id=draft_id,
        request_id=request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    await db.flush()

    return request_id, draft


@pytest.mark.asyncio
async def test_validation_engine_no_flags(test_db: AsyncSession):
    """Test ValidationEngine returns empty list when all validations pass."""
    request_id, draft = await setup_draft(test_db)

    # Create valid header
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(header)

    # Create valid payment details
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="123456789",
        bank_name="Test Bank",
        payment_reference="REF001",
    )
    test_db.add(payment)

    # Create valid line item
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Test Item",
        quantity=Decimal("1"),
        unit="each",
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Run validation
    flags = await ValidationEngine.run(request_id, draft, test_db)

    # Should have no flags
    assert flags == []


@pytest.mark.asyncio
async def test_validation_engine_missing_evidence_payment_details(test_db: AsyncSession):
    """Test ValidationEngine detects missing payment details."""
    request_id, draft = await setup_draft(test_db)

    # Create header
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(header)

    # Create line item
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Test Item",
        quantity=Decimal("1"),
        unit="each",
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)

    # No payment details created
    await test_db.commit()

    # Run validation
    flags = await ValidationEngine.run(request_id, draft, test_db)

    # Should have MISSING_EVIDENCE flag
    assert len(flags) == 1
    assert flags[0].flag_type == "MISSING_EVIDENCE"
    assert "missing_fields" in flags[0].details


@pytest.mark.asyncio
async def test_validation_engine_calculation_discrepancy(test_db: AsyncSession):
    """Test ValidationEngine detects calculation discrepancies."""
    request_id, draft = await setup_draft(test_db)

    # Create header with invalid calculation
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("150.00"),  # Doesn't match subtotal + tax
        tax_amount=Decimal("40.00"),  # Subtotal + tax should be 140, not 150
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(header)

    # Create payment details
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="123456789",
        bank_name="Test Bank",
        payment_reference="REF001",
    )
    test_db.add(payment)

    # Create line item
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Test Item",
        quantity=Decimal("1"),
        unit="each",
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Run validation
    flags = await ValidationEngine.run(request_id, draft, test_db)

    # Should have CALC_DISCREPANCY flag
    calc_flags = [f for f in flags if f.flag_type == "CALC_DISCREPANCY"]
    assert len(calc_flags) == 1
    assert "discrepancies" in calc_flags[0].details


@pytest.mark.asyncio
async def test_validation_engine_possible_duplicate(test_db: AsyncSession):
    """Test ValidationEngine detects possible duplicates."""
    # Create existing request with same vendor/invoice
    existing_request_id = uuid.uuid4()
    existing_draft_id = uuid.uuid4()

    existing_req = InvoiceRequest(
        request_id=existing_request_id,
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="s3://bucket/invoice1.pdf",
        invoice_page_count=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    test_db.add(existing_req)

    existing_draft = Draft(
        draft_id=existing_draft_id,
        request_id=existing_request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(existing_draft)
    await test_db.flush()

    # Create header for existing request
    existing_header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=existing_draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(existing_header)

    # Now create new request being tested
    request_id, draft = await setup_draft(test_db)

    current = await test_db.get(InvoiceRequest, request_id)
    existing_req.client_id = current.client_id

    # Create header with SAME vendor and invoice number
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(header)

    # Create payment details
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="123456789",
        bank_name="Test Bank",
        payment_reference="REF001",
    )
    test_db.add(payment)

    # Create line item
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Test Item",
        quantity=Decimal("1"),
        unit="each",
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Run validation
    flags = await ValidationEngine.run(request_id, draft, test_db)

    # Should have POSSIBLE_DUPLICATE flag
    dup_flags = [f for f in flags if f.flag_type == "POSSIBLE_DUPLICATE"]
    assert len(dup_flags) == 1
    assert "matches" in dup_flags[0].details


@pytest.mark.asyncio
async def test_validation_engine_multiple_flags(test_db: AsyncSession):
    """Test ValidationEngine aggregates multiple flags."""
    request_id, draft = await setup_draft(test_db)

    # Create header with calculation discrepancy
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Vendor Inc",
        invoice_number="INV001",
        invoice_date=date.today(),
        currency="USD",
        subtotal=Decimal("100.00"),
        total_amount=Decimal("150.00"),  # Discrepancy here
        tax_amount=Decimal("40.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    test_db.add(header)

    # Create line item
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Test Item",
        quantity=Decimal("1"),
        unit="each",
        unit_price=Decimal("100.00"),
        line_total=Decimal("100.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)

    # No payment details (missing evidence)
    await test_db.commit()

    # Run validation
    flags = await ValidationEngine.run(request_id, draft, test_db)

    # Should have both MISSING_EVIDENCE and CALC_DISCREPANCY
    flag_types = {f.flag_type for f in flags}
    assert "MISSING_EVIDENCE" in flag_types
    assert "CALC_DISCREPANCY" in flag_types


@pytest.mark.asyncio
async def test_flag_dataclass():
    """Test Flag dataclass structure and properties."""
    # Create a flag with details
    flag = Flag(
        flag_type="MISSING_EVIDENCE",
        details={"missing_fields": ["bank_account_number"]},
    )

    assert flag.flag_type == "MISSING_EVIDENCE"
    assert flag.details == {"missing_fields": ["bank_account_number"]}

    # Create a flag without details
    flag2 = Flag(flag_type="CALC_DISCREPANCY")
    assert flag2.flag_type == "CALC_DISCREPANCY"
    assert flag2.details == {}
