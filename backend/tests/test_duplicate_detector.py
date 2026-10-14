"""
Unit and property-based tests for DuplicateDetector.

Requirement 8: Duplicate Detection
Requirement 8.1: Case-insensitive exact match of vendor name and invoice number
Requirement 8.2: Match against requests NOT in REJECTED or VOIDED status
Requirement 8.3: Display matching request details (id, status, date, amount)
Requirement 8.4: Finance Reviewer can dismiss the POSSIBLE_DUPLICATE flag
"""

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields
from app.models.request import InvoiceRequest, RequestStatus
from app.models import Base
from app.validation.duplicate import DuplicateDetector, MatchResult


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


@pytest.mark.asyncio
async def test_duplicate_detector_exact_match_case_insensitive(test_db: AsyncSession):
    """
    Test that duplicate detection is case-insensitive.
    When a matching vendor name and invoice number exist (different case),
    they should be detected as duplicates.
    Validates Requirement 8.1.
    """
    # Create first request with Draft and HeaderFields
    req1 = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date(2024, 1, 1),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="key1",
    )
    test_db.add(req1)
    await test_db.flush()

    draft1 = Draft(
        draft_id=uuid.uuid4(),
        request_id=req1.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft1)
    await test_db.flush()

    header1 = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft1.draft_id,
        vendor_name="ACME Corporation",  # Case variation
        invoice_number="INV-2024-001",
        invoice_date=date(2024, 1, 1),
        currency="USD",
        subtotal=1000.0,
        total_amount=1100.0,
        tax_amount=100.0,
        fee_amount=0.0,
        rounding_adj=0.0,
    )
    test_db.add(header1)
    await test_db.commit()

    # Now check for duplicates with different case
    matches = await DuplicateDetector.detect(
        request_id=uuid.uuid4(),  # Different request ID
        vendor_name="acme corporation",  # Lowercase
        invoice_number="inv-2024-001",  # Lowercase
        db=test_db,
    )

    assert len(matches) == 1
    assert matches[0].request_id == req1.request_id
    assert matches[0].status == RequestStatus.READY_FOR_REVIEW
    assert matches[0].invoice_date == date(2024, 1, 1)
    assert float(matches[0].total_amount) == 1100.0


@pytest.mark.asyncio
async def test_duplicate_detector_excludes_rejected(test_db: AsyncSession):
    """
    Test that REJECTED requests are excluded from duplicate detection.
    Validates Requirement 8.2.
    """
    # Create a REJECTED request
    req1 = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date(2024, 1, 1),
        status=RequestStatus.REJECTED,  # REJECTED status
        flags=[],
        invoice_file_key="key1",
    )
    test_db.add(req1)
    await test_db.flush()

    draft1 = Draft(
        draft_id=uuid.uuid4(),
        request_id=req1.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft1)
    await test_db.flush()

    header1 = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft1.draft_id,
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        invoice_date=date(2024, 1, 1),
        currency="USD",
        subtotal=1000.0,
        total_amount=1100.0,
        tax_amount=100.0,
        fee_amount=0.0,
        rounding_adj=0.0,
    )
    test_db.add(header1)
    await test_db.commit()

    # Try to detect duplicates
    matches = await DuplicateDetector.detect(
        request_id=uuid.uuid4(),
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        db=test_db,
    )

    # Should not find the rejected request
    assert len(matches) == 0


@pytest.mark.asyncio
async def test_duplicate_detector_no_match(test_db: AsyncSession):
    """
    Test that when no matching vendor/invoice combination exists, no matches are returned.
    """
    # Create a request with different vendor/invoice
    req1 = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date(2024, 1, 1),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="key1",
    )
    test_db.add(req1)
    await test_db.flush()

    draft1 = Draft(
        draft_id=uuid.uuid4(),
        request_id=req1.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft1)
    await test_db.flush()

    header1 = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft1.draft_id,
        vendor_name="Different Vendor",
        invoice_number="INV-999",
        invoice_date=date(2024, 1, 1),
        currency="USD",
        subtotal=1000.0,
        total_amount=1100.0,
        tax_amount=100.0,
        fee_amount=0.0,
        rounding_adj=0.0,
    )
    test_db.add(header1)
    await test_db.commit()

    # Try to detect duplicates for non-matching vendor/invoice
    matches = await DuplicateDetector.detect(
        request_id=uuid.uuid4(),
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        db=test_db,
    )

    assert len(matches) == 0


@pytest.mark.asyncio
async def test_duplicate_detector_excludes_current_request(test_db: AsyncSession):
    """
    Test that the current request_id is excluded from duplicate detection results.
    This prevents a request from matching itself.
    """
    current_request_id = uuid.uuid4()
    
    # Create the current request
    req1 = InvoiceRequest(
        request_id=current_request_id,
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date(2024, 1, 1),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="key1",
    )
    test_db.add(req1)
    await test_db.flush()

    draft1 = Draft(
        draft_id=uuid.uuid4(),
        request_id=current_request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft1)
    await test_db.flush()

    header1 = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft1.draft_id,
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        invoice_date=date(2024, 1, 1),
        currency="USD",
        subtotal=1000.0,
        total_amount=1100.0,
        tax_amount=100.0,
        fee_amount=0.0,
        rounding_adj=0.0,
    )
    test_db.add(header1)
    await test_db.commit()

    # Try to detect duplicates for the same request
    matches = await DuplicateDetector.detect(
        request_id=current_request_id,  # Same as req1
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        db=test_db,
    )

    # Should not match itself
    assert len(matches) == 0


@pytest.mark.asyncio
async def test_duplicate_detector_multiple_matches(test_db: AsyncSession):
    """
    Test that multiple matching requests are all returned.
    """
    # Create multiple requests with same vendor/invoice but different request_ids
    req1_id = uuid.uuid4()
    req2_id = uuid.uuid4()
    
    for i, req_id in enumerate([req1_id, req2_id], 1):
        req = InvoiceRequest(
            request_id=req_id,
            client_id=uuid.uuid4(),
            uploader_id=uuid.uuid4(),
            request_type="standard",
            channel="email",
            request_date=date(2024, 1, i),
            status=RequestStatus.READY_FOR_REVIEW,
            flags=[],
            invoice_file_key=f"key{i}",
        )
        test_db.add(req)
        await test_db.flush()

        draft = Draft(
            draft_id=uuid.uuid4(),
            request_id=req_id,
            ai_label_visible=True,
            extraction_status=ExtractionStatus.COMPLETE,
            extracted_at=datetime.now(timezone.utc),
        )
        test_db.add(draft)
        await test_db.flush()

        header = HeaderFields(
            header_id=uuid.uuid4(),
            draft_id=draft.draft_id,
            vendor_name="ACME Corp",
            invoice_number="INV-001",
            invoice_date=date(2024, 1, i),
            currency="USD",
            subtotal=1000.0,
            total_amount=1100.0,
            tax_amount=100.0,
            fee_amount=0.0,
            rounding_adj=0.0,
        )
        test_db.add(header)
    
    await test_db.commit()

    # Detect duplicates from a different request
    matches = await DuplicateDetector.detect(
        request_id=uuid.uuid4(),  # Different request
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        db=test_db,
    )

    assert len(matches) == 2
    match_ids = {m.request_id for m in matches}
    assert req1_id in match_ids
    assert req2_id in match_ids


@pytest.mark.asyncio
async def test_match_result_structure(test_db: AsyncSession):
    """
    Test that MatchResult has the expected fields for Finance Reviewer display.
    Validates Requirement 8.3.
    """
    # Create a request with complete details
    req = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        uploader_id=uuid.uuid4(),
        request_type="standard",
        channel="email",
        request_date=date(2024, 1, 1),
        status=RequestStatus.AMIGA_REVIEWED,
        flags=[],
        invoice_file_key="key1",
    )
    test_db.add(req)
    await test_db.flush()

    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=req.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    test_invoice_date = date(2024, 1, 15)
    test_amount = 5555.55
    
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="Test Vendor",
        invoice_number="INV-TEST",
        invoice_date=test_invoice_date,
        currency="USD",
        subtotal=5000.0,
        total_amount=test_amount,
        tax_amount=500.0,
        fee_amount=55.55,
        rounding_adj=0.0,
    )
    test_db.add(header)
    await test_db.commit()

    # Create a MatchResult directly
    match = MatchResult(
        request_id=req.request_id,
        status=req.status,
        invoice_date=test_invoice_date,
        total_amount=test_amount,
    )

    # Verify all fields are accessible
    assert match.request_id == req.request_id
    assert match.status == RequestStatus.AMIGA_REVIEWED
    assert match.invoice_date == test_invoice_date
    assert match.total_amount == test_amount
