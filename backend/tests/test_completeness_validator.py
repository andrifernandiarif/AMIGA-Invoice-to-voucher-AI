"""
Unit and property-based tests for CompletenessValidator.

Requirement 6: Completeness Validation — Missing Evidence
Requirement 5.3: Line items with missing fields trigger MISSING_EVIDENCE
Requirement 6.1: Payment details must include non-None, non-empty required fields
Requirement 6.2: MISSING_EVIDENCE flag sets Request status to EXCEPTION
Requirement 6.3: Exception UX lists each missing Payment_Details field by name
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails, LineExtractionStatus
from app.models import Base
from app.validation.completeness import CompletenessValidator, ValidationResult


@pytest.fixture
async def test_db():
    """Create an in-memory SQLite database for testing."""
    # Use in-memory SQLite with proper async support
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
async def test_completeness_all_fields_present(test_db: AsyncSession):
    """
    Test that validation passes when all required fields are present.
    Validates Requirement 6.1.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    # Create payment details with all required fields
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="1234567890",
        bank_name="Bank of Example",
        payment_reference="REF-12345",
    )
    test_db.add(payment)

    # Create line items with all required fields
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Widget",
        quantity=10.0,
        unit="each",
        unit_price=5.0,
        line_total=50.0,
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is True
    assert result.missing_fields == []


@pytest.mark.asyncio
async def test_completeness_missing_payment_field(test_db: AsyncSession):
    """
    Test that validation fails when a payment field is missing.
    Validates Requirement 6.1, 6.3.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    # Create payment details with missing bank_account_number
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number=None,  # Missing!
        bank_name="Bank of Example",
        payment_reference="REF-12345",
    )
    test_db.add(payment)
    await test_db.commit()

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is False
    assert "bank_account_number" in result.missing_fields


@pytest.mark.asyncio
async def test_completeness_empty_payment_field(test_db: AsyncSession):
    """
    Test that validation fails when a payment field is empty string.
    Validates Requirement 6.1, 6.3.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    # Create payment details with empty bank_name
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="1234567890",
        bank_name="",  # Empty!
        payment_reference="REF-12345",
    )
    test_db.add(payment)
    await test_db.commit()

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is False
    assert "bank_name" in result.missing_fields


@pytest.mark.asyncio
async def test_completeness_missing_line_item_field(test_db: AsyncSession):
    """
    Test that validation fails when a line item field is missing.
    Validates Requirement 5.3.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    # Create payment details (valid)
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="1234567890",
        bank_name="Bank of Example",
        payment_reference="REF-12345",
    )
    test_db.add(payment)

    # Create line item with missing unit_price
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Widget",
        quantity=10.0,
        unit="each",
        unit_price=None,  # Missing!
        line_total=50.0,
        extraction_status=LineExtractionStatus.PARTIAL,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is False
    assert "line_items[1].unit_price" in result.missing_fields


@pytest.mark.asyncio
async def test_completeness_multiple_line_items(test_db: AsyncSession):
    """
    Test validation with multiple line items, each with different missing fields.
    Validates Requirement 5.3, 6.3.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)
    await test_db.flush()

    # Create payment details (valid)
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="1234567890",
        bank_name="Bank of Example",
        payment_reference="REF-12345",
    )
    test_db.add(payment)

    # Create line item 1 (valid)
    line_item_1 = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Widget A",
        quantity=10.0,
        unit="each",
        unit_price=5.0,
        line_total=50.0,
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item_1)

    # Create line item 2 (missing description)
    line_item_2 = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=2,
        description=None,  # Missing!
        quantity=5.0,
        unit="boxes",
        unit_price=10.0,
        line_total=50.0,
        extraction_status=LineExtractionStatus.PARTIAL,
    )
    test_db.add(line_item_2)

    # Create line item 3 (missing line_total)
    line_item_3 = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=3,
        description="Service",
        quantity=1.0,
        unit="service",
        unit_price=100.0,
        line_total=None,  # Missing!
        extraction_status=LineExtractionStatus.PARTIAL,
    )
    test_db.add(line_item_3)
    await test_db.commit()

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is False
    assert "line_items[2].description" in result.missing_fields
    assert "line_items[3].line_total" in result.missing_fields
    assert len(result.missing_fields) == 2


@pytest.mark.asyncio
async def test_completeness_no_payment_details(test_db: AsyncSession):
    """
    Test that validation fails when payment details are entirely absent.
    Validates Requirement 6.1.
    """
    # Create draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=datetime.now(timezone.utc),
    )
    test_db.add(draft)

    # Create line item (valid)
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Widget",
        quantity=10.0,
        unit="each",
        unit_price=5.0,
        line_total=50.0,
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    test_db.add(line_item)
    await test_db.commit()

    # Do not add PaymentDetails

    # Validate
    result = await CompletenessValidator.validate(draft, test_db)
    
    assert result.is_valid is False
    # All three payment fields should be missing
    assert "bank_account_number" in result.missing_fields
    assert "bank_name" in result.missing_fields
    assert "payment_reference" in result.missing_fields


@pytest.mark.asyncio
async def test_completeness_validation_result_dataclass(test_db: AsyncSession):
    """
    Test that ValidationResult is a proper dataclass with expected fields.
    """
    result = ValidationResult(is_valid=True, missing_fields=[])
    
    assert result.is_valid is True
    assert result.missing_fields == []
    
    result2 = ValidationResult(is_valid=False, missing_fields=["field1", "field2"])
    assert result2.is_valid is False
    assert len(result2.missing_fields) == 2
