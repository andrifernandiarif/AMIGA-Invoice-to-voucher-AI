"""
Tests for Voucher PDF generation endpoint (Task 28.3).

POST /requests/{request_id}/voucher-pdf

Requirements tested:
  - Req 15.3: Reject generation if Request is not in AMIGA_REVIEWED status.
  - Req 15.5: PDF generation failure must NOT record a SENT_TO_CLIENT event or
              transition Request status; no audit event is persisted.
  - Req 15.6: On success, record generation event (VOUCHER_GENERATED), requesting
              user's identity, and UTC timestamp in AuditTrail.
  - Req 15.2: Authorization_Disclaimer text must appear in the rendered output.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.audit import AuditEvent, AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import (
    HeaderFields,
    LineItem,
    LineExtractionStatus,
    PaymentDetails,
)
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.models.voucher import VoucherPDF, VoucherTemplate

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types to SQLite-compatible equivalents.
# (mirrors the approach used in conftest.py for the existing test suite)
# ---------------------------------------------------------------------------
import app.models.audit as _audit_module
import app.models.request as _req_module

_req_module.InvoiceRequest.__table__.c["flags"].type = JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None

# ---------------------------------------------------------------------------
# Minimal Jinja2 voucher template used in all tests.
# It contains the authorization_disclaimer placeholder and basic fields.
# ---------------------------------------------------------------------------
_TEST_TEMPLATE_CONTENT = """\
<!DOCTYPE html>
<html>
<body>
<h1>VOUCHER</h1>
<p>Vendor: {{ vendor_name }}</p>
<p>Invoice: {{ invoice_number }}</p>
<p class="disclaimer">{{ authorization_disclaimer }}</p>
</body>
</html>
"""

# The disclaimer string configured in settings
_DISCLAIMER = (
    "This voucher is prepared for client review purposes only. "
    "Payment authorization remains with the client."
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
async def db_session():
    """
    Provide a fresh in-memory SQLite async session for each test.
    All tables are created before the test and the engine disposed after.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def seeded_db(db_session: AsyncSession):
    """
    Insert into the in-memory DB:
      - a Client
      - a CLIENT_PIC User
      - a ClientAssignment linking that user to the client
      - an InvoiceRequest in AMIGA_REVIEWED status
      - a Draft with HeaderFields, a LineItem, and PaymentDetails
      - an active VoucherTemplate

    Returns a dict with the key objects for use in tests.
    """
    # Client
    client = Client(client_id=uuid.uuid4(), client_name="ACME Corp", is_active=True)
    db_session.add(client)

    # User
    now = datetime.now(timezone.utc)
    user = User(
        user_id=uuid.uuid4(),
        username="pic_alice",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(user)

    # Uploader (different user — satisfies segregation-of-duties; not tested here)
    uploader = User(
        user_id=uuid.uuid4(),
        username="uploader_bob",
        role=UserRole.INTAKE_SUPPORT,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(uploader)

    await db_session.flush()

    # ClientAssignment
    assignment = ClientAssignment(
        assignment_id=uuid.uuid4(),
        user_id=user.user_id,
        client_id=client.client_id,
        is_active=True,
        assigned_at=now,
        revoked_at=None,
    )
    db_session.add(assignment)

    # Request in AMIGA_REVIEWED status
    request = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client.client_id,
        uploader_id=uploader.user_id,
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=RequestStatus.AMIGA_REVIEWED,
        flags=[],
        invoice_file_key="s3://bucket/invoice.pdf",
        invoice_page_count=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(request)
    await db_session.flush()

    # Draft
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=now,
    )
    db_session.add(draft)
    await db_session.flush()

    # HeaderFields
    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="PT Maju Bersama",
        invoice_number="INV-2024-001",
        invoice_date=date.today(),
        currency="IDR",
        subtotal=Decimal("1000000.00"),
        total_amount=Decimal("1110000.00"),
        tax_amount=Decimal("110000.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    db_session.add(header)

    # LineItem
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Consulting services",
        quantity=Decimal("1"),
        unit="day",
        unit_price=Decimal("1000000.00"),
        line_total=Decimal("1000000.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    db_session.add(line_item)

    # PaymentDetails
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="1234567890",
        bank_name="Bank Central Asia",
        payment_reference="PAY-REF-001",
    )
    db_session.add(payment)

    # Active VoucherTemplate
    template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version="v1.0",
        template_content=_TEST_TEMPLATE_CONTENT,
        is_active=True,
        created_by=None,
        created_at=now,
    )
    db_session.add(template)

    await db_session.commit()

    return {
        "client": client,
        "user": user,
        "uploader": uploader,
        "request": request,
        "draft": draft,
        "header": header,
        "line_item": line_item,
        "payment": payment,
        "template": template,
    }


def _build_app(db_session: AsyncSession, current_user: User) -> FastAPI:
    """
    Build a minimal FastAPI app including only the voucher router,
    with the DB session and current user both overridden for testing.
    """
    from app.dependencies import get_current_user, get_db
    from app.routes.voucher import router as voucher_router

    app = FastAPI()
    app.include_router(voucher_router)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


# ===========================================================================
# Req 15.3 — Status guard: reject non-AMIGA_REVIEWED requests
# ===========================================================================


class TestStatusGuard:
    """
    Req 15.3: When a user requests Voucher PDF generation for a Request that is
    not in AMIGA_REVIEWED status, the system SHALL reject with an error message
    stating the Request has not completed the required independent review.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_status",
        [
            RequestStatus.QUEUED,
            RequestStatus.PROCESSING,
            RequestStatus.READY_FOR_REVIEW,
            RequestStatus.EXCEPTION,
            RequestStatus.SENT_TO_CLIENT,
            RequestStatus.REJECTED,
        ],
    )
    async def test_non_amiga_reviewed_returns_422(
        self, db_session: AsyncSession, seeded_db: dict, bad_status: RequestStatus
    ):
        """All non-AMIGA_REVIEWED statuses must be rejected with HTTP 422."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        # Move the request to a non-approved status
        req.status = bad_status
        await db_session.commit()

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.upload = AsyncMock()
            with patch("app.routes.voucher.weasyprint") as mock_wp:
                mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-fake"
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 422, (
            f"Expected 422 for status {bad_status.value}, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_status",
        [
            RequestStatus.QUEUED,
            RequestStatus.PROCESSING,
            RequestStatus.READY_FOR_REVIEW,
            RequestStatus.EXCEPTION,
            RequestStatus.SENT_TO_CLIENT,
            RequestStatus.REJECTED,
        ],
    )
    async def test_rejection_message_mentions_independent_review(
        self, db_session: AsyncSession, seeded_db: dict, bad_status: RequestStatus
    ):
        """Error message must reference 'required independent review' (Req 15.3)."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        req.status = bad_status
        await db_session.commit()

        app = _build_app(db_session, user)
        with TestClient(app) as client:
            resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        detail = resp.json().get("detail", "")
        assert "independent review" in detail.lower(), (
            f"Expected 'independent review' in detail, got: {detail!r}"
        )

    @pytest.mark.asyncio
    async def test_amiga_reviewed_status_proceeds_past_guard(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """A Request in AMIGA_REVIEWED status should pass the status guard (not 422)."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]
        # Status is already AMIGA_REVIEWED in the fixture

        app = _build_app(db_session, user)

        # Mock both weasyprint and S3 so the rest of the pipeline succeeds
        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        # We should NOT get a 422 status-guard rejection
        assert resp.status_code != 422, (
            "AMIGA_REVIEWED request should pass the status guard"
        )

    @pytest.mark.asyncio
    async def test_unknown_request_id_returns_404(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """A non-existent request_id must return 404 (not 422 or 500)."""
        user: User = seeded_db["user"]
        fake_id = uuid.uuid4()

        app = _build_app(db_session, user)
        with TestClient(app) as client:
            resp = client.post(f"/requests/{fake_id}/voucher-pdf")

        assert resp.status_code == 404


# ===========================================================================
# Req 15.5 — PDF generation failure: no AuditEvent, no status transition
# ===========================================================================


class TestGenerationFailure:
    """
    Req 15.5: If Voucher PDF generation fails, the system SHALL display an error
    and SHALL NOT record a SENT_TO_CLIENT event or transition the Request status.
    """

    @pytest.mark.asyncio
    async def test_weasyprint_failure_returns_500(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """When WeasyPrint raises an exception, the endpoint returns HTTP 500."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.side_effect = RuntimeError("WeasyPrint crash")
            with TestClient(app) as client:
                resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_s3_failure_returns_500(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """When the S3 upload raises StorageError, the endpoint returns HTTP 500."""
        from app.storage import StorageError

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock(
                    side_effect=StorageError("S3 unavailable")
                )
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_weasyprint_failure_leaves_no_audit_event(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.5: When PDF generation fails (WeasyPrint crash), the system MUST NOT
        record any VOUCHER_GENERATED AuditEvent.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.side_effect = RuntimeError("crash")
            with TestClient(app) as client:
                client.post(f"/requests/{req.request_id}/voucher-pdf")

        # Verify no VOUCHER_GENERATED event was persisted
        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 0, (
            "A PDF generation failure must not produce a VOUCHER_GENERATED audit event"
        )

    @pytest.mark.asyncio
    async def test_s3_failure_leaves_no_audit_event(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.5: When PDF generation fails (S3 error), no VOUCHER_GENERATED
        AuditEvent must be persisted.
        """
        from sqlalchemy import select
        from app.storage import StorageError

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock(side_effect=StorageError("S3 down"))
                with TestClient(app) as client:
                    client.post(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 0, (
            "An S3 upload failure must not produce a VOUCHER_GENERATED audit event"
        )

    @pytest.mark.asyncio
    async def test_failure_does_not_create_voucher_pdf_record(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.5: A generation failure must not persist a VoucherPDF row.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.side_effect = Exception("PDF error")
            with TestClient(app) as client:
                client.post(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(VoucherPDF).where(VoucherPDF.request_id == req.request_id)
        )
        assert result.scalar_one_or_none() is None, (
            "VoucherPDF row must not be created when generation fails"
        )

    @pytest.mark.asyncio
    async def test_failure_does_not_change_request_status(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.5: Request status must remain AMIGA_REVIEWED after a generation failure
        (i.e., must not be transitioned to SENT_TO_CLIENT or any other status).
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.side_effect = Exception("PDF error")
            with TestClient(app) as client:
                client.post(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == req.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.AMIGA_REVIEWED, (
            f"Request status must remain AMIGA_REVIEWED after failure, "
            f"but got {refreshed.status}"
        )

    @pytest.mark.asyncio
    async def test_failure_error_message_in_response(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.5: The error response must include an error message identifying
        the failure (so the user has a context for retry).
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.side_effect = RuntimeError("crash")
            with TestClient(app) as client:
                resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        body = resp.json()
        assert "detail" in body, "Error response must include a 'detail' field"
        assert len(body["detail"]) > 0, "Error detail must not be empty"


# ===========================================================================
# Req 15.6 — Success: VoucherPDF record + AuditEvent created
# ===========================================================================


class TestGenerationSuccess:
    """
    Req 15.6: When a Voucher PDF is generated, the system SHALL record the
    generation event, the requesting user's identity, and the UTC timestamp
    in the Audit Trail.
    """

    @pytest.mark.asyncio
    async def test_success_returns_201(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Successful generation must return HTTP 201 with expected fields."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201
        body = resp.json()
        assert "voucher_pdf_id" in body
        assert "file_key" in body
        assert "generated_at" in body

    @pytest.mark.asyncio
    async def test_success_creates_voucher_pdf_record(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: A VoucherPDF row must be persisted after a successful generation.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(VoucherPDF).where(VoucherPDF.request_id == req.request_id)
        )
        voucher_pdf = result.scalar_one_or_none()
        assert voucher_pdf is not None, "VoucherPDF record must be created on success"

    @pytest.mark.asyncio
    async def test_success_voucher_pdf_links_to_correct_request(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """VoucherPDF.request_id must point to the correct request."""
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(VoucherPDF).where(VoucherPDF.request_id == req.request_id)
        )
        voucher_pdf = result.scalar_one()
        assert voucher_pdf.request_id == req.request_id

    @pytest.mark.asyncio
    async def test_success_voucher_pdf_records_generator_identity(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: VoucherPDF.generated_by must be the ID of the requesting user.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(VoucherPDF).where(VoucherPDF.request_id == req.request_id)
        )
        voucher_pdf = result.scalar_one()
        assert voucher_pdf.generated_by == user.user_id, (
            "VoucherPDF.generated_by must record the requesting user's identity"
        )

    @pytest.mark.asyncio
    async def test_success_voucher_pdf_has_generated_at_timestamp(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: VoucherPDF.generated_at must be a UTC timestamp.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(VoucherPDF).where(VoucherPDF.request_id == req.request_id)
        )
        voucher_pdf = result.scalar_one()
        assert voucher_pdf.generated_at is not None, (
            "VoucherPDF.generated_at must be set"
        )

    @pytest.mark.asyncio
    async def test_success_creates_voucher_generated_audit_event(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: A VOUCHER_GENERATED AuditEvent must be persisted after success.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            "Exactly one VOUCHER_GENERATED audit event must be recorded on success"
        )

    @pytest.mark.asyncio
    async def test_success_audit_event_records_actor_id(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: The VOUCHER_GENERATED AuditEvent must capture the requesting
        user's unique account identifier.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        event = result.scalars().first()
        assert event.actor_id == user.user_id, (
            "AuditEvent.actor_id must record the requesting user's identity"
        )

    @pytest.mark.asyncio
    async def test_success_audit_event_has_occurred_at_timestamp(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: The VOUCHER_GENERATED AuditEvent must include a UTC timestamp.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        event = result.scalars().first()
        assert event.occurred_at is not None, (
            "AuditEvent.occurred_at (UTC timestamp) must be set"
        )

    @pytest.mark.asyncio
    async def test_success_audit_event_payload_contains_generator_id(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        Req 15.6: The audit event payload must include 'generated_by' matching
        the requesting user's identity.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        event = result.scalars().first()
        payload = event.payload
        assert "generated_by" in payload, (
            "Audit event payload must contain 'generated_by'"
        )
        assert payload["generated_by"] == str(user.user_id), (
            "Audit event payload 'generated_by' must match the requesting user's ID"
        )

    @pytest.mark.asyncio
    async def test_success_audit_event_payload_contains_voucher_pdf_id(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        The audit event payload must reference the VoucherPDF that was created,
        allowing the event to be cross-referenced.
        """
        from sqlalchemy import select

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.return_value.write_pdf.return_value = b"%PDF-1.4 fake"
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201
        response_voucher_id = resp.json()["voucher_pdf_id"]

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        event = result.scalars().first()
        payload = event.payload
        assert "voucher_pdf_id" in payload, (
            "Audit event payload must contain 'voucher_pdf_id'"
        )
        assert payload["voucher_pdf_id"] == response_voucher_id, (
            "Audit event payload 'voucher_pdf_id' must match the generated VoucherPDF ID"
        )


# ===========================================================================
# Req 15.2 — Authorization Disclaimer present in generated output
# ===========================================================================


class TestDisclaimerPresence:
    """
    Req 15.2: Every generated Voucher PDF must include the Authorization_Disclaimer.

    Because WeasyPrint is mocked in most tests, we verify the disclaimer
    by inspecting the rendered HTML that is passed to WeasyPrint — the HTML
    is produced by Jinja2 from the voucher template which contains the
    {{ authorization_disclaimer }} placeholder.
    """

    @pytest.mark.asyncio
    async def test_disclaimer_injected_into_template_context(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        The Jinja2 rendering step must pass `authorization_disclaimer` in the
        context so it is present in the HTML handed to WeasyPrint.
        """
        from app.config import settings

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        captured_html: list[str] = []

        class CapturingHTML:
            def __init__(self, string: str):
                captured_html.append(string)
                self._html = string

            def write_pdf(self):
                return b"%PDF-1.4 disclaimer-test"

        import weasyprint as _real_weasyprint  # noqa: F401 (just for reference)

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.side_effect = CapturingHTML
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.json()}"
        assert captured_html, "WeasyPrint.HTML was never called — no HTML was rendered"

        rendered = captured_html[0]
        assert settings.AUTHORIZATION_DISCLAIMER in rendered, (
            f"The Authorization Disclaimer must appear in the rendered HTML.\n"
            f"Expected: {settings.AUTHORIZATION_DISCLAIMER!r}\n"
            f"Rendered HTML snippet: {rendered[:500]!r}"
        )

    @pytest.mark.asyncio
    async def test_disclaimer_text_matches_settings(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        The disclaimer injected into the template context must exactly match
        settings.AUTHORIZATION_DISCLAIMER (no truncation or alteration).
        """
        from app.config import settings

        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        captured_html: list[str] = []

        class CapturingHTML:
            def __init__(self, string: str):
                captured_html.append(string)

            def write_pdf(self):
                return b"%PDF-1.4 disclaimer-check"

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.side_effect = CapturingHTML
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201
        rendered = captured_html[0]

        # The full disclaimer text must appear verbatim in the rendered HTML
        assert settings.AUTHORIZATION_DISCLAIMER in rendered

    @pytest.mark.asyncio
    async def test_disclaimer_rendered_from_template_placeholder(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """
        The template fixture uses {{ authorization_disclaimer }} as a placeholder.
        Verify the rendered HTML replaces that placeholder with actual content.
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["user"]

        app = _build_app(db_session, user)

        captured_html: list[str] = []

        class CapturingHTML:
            def __init__(self, string: str):
                captured_html.append(string)

            def write_pdf(self):
                return b"%PDF-1.4 placeholder-check"

        with patch("app.routes.voucher.weasyprint") as mock_wp:
            mock_wp.HTML.side_effect = CapturingHTML
            with patch("app.routes.voucher.file_store") as mock_store:
                mock_store.upload = AsyncMock()
                with TestClient(app) as client:
                    resp = client.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201
        rendered = captured_html[0]

        # The raw Jinja2 placeholder must NOT appear in the rendered output
        assert "{{ authorization_disclaimer }}" not in rendered, (
            "Jinja2 placeholder was not rendered — disclaimer text was not substituted"
        )
        # The disclaimer content must be present
        assert "Payment authorization remains with the client" in rendered, (
            "Rendered HTML must contain the disclaimer text content"
        )
