"""
Tests for Voucher PDF download endpoint (Task 30.3).

GET /requests/{request_id}/voucher-pdf

Requirements tested:
  - Req 16.1: Client_PIC + active ClientAssignment → exactly one SENT_TO_CLIENT event
              (VOUCHER_DOWNLOADED AuditEvent written once even on repeated calls)
  - Req 16.2: On first download, transition Request status to SENT_TO_CLIENT
  - Req 16.3: No active ClientAssignment → 403, no client data in response
  - Req 16.4: Request status != AMIGA_REVIEWED → 400 error

PBT test file: tests/pbt/test_property15_download.py (Task 30.4)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.audit import AuditEvent, AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineExtractionStatus, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.models.voucher import VoucherPDF, VoucherTemplate

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types to SQLite-compatible equivalents
# (mirrors conftest.py approach used across the test suite)
# ---------------------------------------------------------------------------
import app.models.audit as _audit_module
import app.models.request as _req_module

_req_module.InvoiceRequest.__table__.c["flags"].type = __import__("sqlalchemy").JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = __import__("sqlalchemy").JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FAKE_PDF_BYTES = b"%PDF-1.4 fake pdf bytes"
_DISCLAIMER = (
    "This voucher is prepared for client review purposes only. "
    "Payment authorization remains with the client."
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
async def db_session():
    """Fresh in-memory SQLite async session per test."""
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
    Insert:
      - Client
      - CLIENT_PIC user (the downloader)
      - Separate uploader user
      - Active ClientAssignment
      - InvoiceRequest in AMIGA_REVIEWED status
      - Draft, HeaderFields, LineItem, PaymentDetails
      - Active VoucherTemplate
      - VoucherPDF record (simulating a previously generated PDF)

    Returns a dict of the key objects for use in tests.
    """
    now = datetime.now(timezone.utc)

    client = Client(client_id=uuid.uuid4(), client_name="Acme Corp", is_active=True)
    db_session.add(client)

    pic_user = User(
        user_id=uuid.uuid4(),
        username="pic_user",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(pic_user)

    uploader = User(
        user_id=uuid.uuid4(),
        username="uploader_user",
        role=UserRole.INTAKE_SUPPORT,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(uploader)

    await db_session.flush()

    assignment = ClientAssignment(
        assignment_id=uuid.uuid4(),
        user_id=pic_user.user_id,
        client_id=client.client_id,
        is_active=True,
        assigned_at=now,
        revoked_at=None,
    )
    db_session.add(assignment)

    request = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client.client_id,
        uploader_id=uploader.user_id,
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=RequestStatus.AMIGA_REVIEWED,
        flags=[],
        invoice_file_key="s3://bucket/inv.pdf",
        invoice_page_count=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(request)
    await db_session.flush()

    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=now,
    )
    db_session.add(draft)
    await db_session.flush()

    header = HeaderFields(
        header_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        vendor_name="PT Maju Bersama",
        invoice_number="INV-001",
        invoice_date=date.today(),
        currency="IDR",
        subtotal=Decimal("1000000.00"),
        total_amount=Decimal("1110000.00"),
        tax_amount=Decimal("110000.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    db_session.add(header)

    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Consulting",
        quantity=Decimal("1"),
        unit="day",
        unit_price=Decimal("1000000.00"),
        line_total=Decimal("1000000.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    db_session.add(line_item)

    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="9876543210",
        bank_name="Bank Mandiri",
        payment_reference="REF-001",
    )
    db_session.add(payment)

    template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version="v1.0",
        template_content="<html><body>{{ vendor_name }}</body></html>",
        is_active=True,
        created_by=None,
        created_at=now,
    )
    db_session.add(template)

    # The VoucherPDF record — simulates a previously generated PDF
    voucher_pdf = VoucherPDF(
        voucher_pdf_id=uuid.uuid4(),
        request_id=request.request_id,
        file_key=f"vouchers/{request.request_id}/voucher.pdf",
        generated_at=now,
        generated_by=pic_user.user_id,
        template_version="v1.0",
        disclaimer_hash="a" * 64,
        template_hash="b" * 64,
    )
    db_session.add(voucher_pdf)

    await db_session.commit()

    return {
        "client": client,
        "pic_user": pic_user,
        "uploader": uploader,
        "assignment": assignment,
        "request": request,
        "draft": draft,
        "voucher_pdf": voucher_pdf,
    }


def _build_app(db_session: AsyncSession, current_user: User) -> FastAPI:
    """Build a minimal FastAPI app with only the voucher router, with DI overridden."""
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
# Req 16.4 — Status guard: status != AMIGA_REVIEWED is blocked
# ===========================================================================


class TestDownloadStatusGuard:
    """
    Req 16.4: A download request for a Request that is not in AMIGA_REVIEWED
    status SHALL be denied with an error stating the voucher is not yet available.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_status",
        [
            RequestStatus.QUEUED,
            RequestStatus.PROCESSING,
            RequestStatus.READY_FOR_REVIEW,
            RequestStatus.EXCEPTION,
            RequestStatus.REJECTED,
        ],
    )
    async def test_non_amiga_reviewed_returns_400(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
        bad_status: RequestStatus,
    ):
        """All non-AMIGA_REVIEWED and non-SENT_TO_CLIENT statuses must return 400."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        req.status = bad_status
        await db_session.commit()

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 400, (
            f"Expected 400 for status {bad_status.value}, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_status",
        [
            RequestStatus.QUEUED,
            RequestStatus.PROCESSING,
            RequestStatus.READY_FOR_REVIEW,
            RequestStatus.EXCEPTION,
            RequestStatus.REJECTED,
        ],
    )
    async def test_status_guard_error_message_mentions_independent_review(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
        bad_status: RequestStatus,
    ):
        """The error detail must reference that the review is incomplete (Req 16.4)."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        req.status = bad_status
        await db_session.commit()

        app = _build_app(db_session, user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        detail = resp.json().get("detail", "").lower()
        assert "independent review" in detail or "not yet available" in detail, (
            f"Expected error message referencing unavailability, got: {detail!r}"
        )


# ===========================================================================
# Req 16.3 — Authorization guard: missing ClientAssignment → 403, no client data
# ===========================================================================


class TestDownloadAuthorizationGuard:
    """
    Req 16.3: A user without an active ClientAssignment for the request's client
    SHALL be denied with 403 and the response must contain NO client data.
    """

    @pytest.mark.asyncio
    async def test_no_client_assignment_returns_403(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """A user with no ClientAssignment for the client must get HTTP 403."""
        req: InvoiceRequest = seeded_db["request"]

        # Create a separate CLIENT_PIC user with no assignment
        now = datetime.now(timezone.utc)
        unauthorized_user = User(
            user_id=uuid.uuid4(),
            username="unassigned_pic",
            role=UserRole.CLIENT_PIC,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(unauthorized_user)
        await db_session.commit()

        app = _build_app(db_session, unauthorized_user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_no_client_assignment_response_contains_no_client_data(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.3: The 403 response body must not contain any client data
        (client name, client_id, request details, etc.).
        """
        req: InvoiceRequest = seeded_db["request"]
        client_obj: Client = seeded_db["client"]

        now = datetime.now(timezone.utc)
        unauthorized_user = User(
            user_id=uuid.uuid4(),
            username="unassigned_pic2",
            role=UserRole.CLIENT_PIC,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(unauthorized_user)
        await db_session.commit()

        app = _build_app(db_session, unauthorized_user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        body = str(resp.json())
        # The response must not expose client name or client_id
        assert client_obj.client_name not in body, (
            "403 response must not contain client name"
        )
        assert str(client_obj.client_id) not in body, (
            "403 response must not contain client_id"
        )

    @pytest.mark.asyncio
    async def test_revoked_assignment_returns_403(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.3 / 18.3: Revoking a ClientAssignment must deny the user on
        their very next request.
        """
        req: InvoiceRequest = seeded_db["request"]
        assignment: ClientAssignment = seeded_db["assignment"]
        user: User = seeded_db["pic_user"]

        # Revoke the assignment
        assignment.is_active = False
        assignment.revoked_at = datetime.now(timezone.utc)
        await db_session.commit()

        app = _build_app(db_session, user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 403, (
            "A user with a revoked ClientAssignment must receive 403"
        )


# ===========================================================================
# Req 16.1 + 16.2 — First download: SENT_TO_CLIENT transition and audit events
# ===========================================================================


class TestFirstDownload:
    """
    Req 16.1: First download by an authorized Client_PIC must:
      - Write exactly one VOUCHER_DOWNLOADED AuditEvent
      - Write a STATUS_CHANGED (AMIGA_REVIEWED → SENT_TO_CLIENT) AuditEvent
    Req 16.2: Request status must transition to SENT_TO_CLIENT.
    """

    @pytest.mark.asyncio
    async def test_first_download_returns_200_with_pdf_content_type(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """First download must return 200 with application/pdf content type."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200
        assert "application/pdf" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_first_download_returns_pdf_bytes(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """The response body must contain the actual PDF bytes fetched from S3."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.content == _FAKE_PDF_BYTES

    @pytest.mark.asyncio
    async def test_first_download_returns_attachment_disposition(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """Content-Disposition must be 'attachment' with the correct filename."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        disposition = resp.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert str(req.request_id) in disposition

    @pytest.mark.asyncio
    async def test_first_download_transitions_status_to_sent_to_client(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.2: After the first download, the Request status must be SENT_TO_CLIENT.
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200

        # Reload from DB
        result = await db_session.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == req.request_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.SENT_TO_CLIENT, (
            f"Expected SENT_TO_CLIENT, got {refreshed.status}"
        )

    @pytest.mark.asyncio
    async def test_first_download_writes_voucher_downloaded_audit_event(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.1: First download must write exactly one VOUCHER_DOWNLOADED AuditEvent.
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            f"Expected exactly 1 VOUCHER_DOWNLOADED event, got {len(events)}"
        )

    @pytest.mark.asyncio
    async def test_first_download_audit_event_records_actor_id(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """The VOUCHER_DOWNLOADED AuditEvent must capture the downloader's user_id."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
            )
        )
        event = result.scalars().first()
        assert event.actor_id == user.user_id

    @pytest.mark.asyncio
    async def test_first_download_writes_status_changed_audit_event(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.2: First download must also write a STATUS_CHANGED AuditEvent
        recording the AMIGA_REVIEWED → SENT_TO_CLIENT transition.
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.STATUS_CHANGED,
            )
        )
        events = result.scalars().all()
        assert len(events) >= 1, "A STATUS_CHANGED AuditEvent must be written on download"

        # Verify the event records the correct transition
        status_event = events[-1]
        payload = status_event.payload
        assert payload.get("from_status") == RequestStatus.AMIGA_REVIEWED.value
        assert payload.get("to_status") == RequestStatus.SENT_TO_CLIENT.value


# ===========================================================================
# Req 16.1 — Idempotency: second download does NOT create duplicate events
# ===========================================================================


class TestIdempotentDownload:
    """
    Req 16.1: Exactly one SENT_TO_CLIENT event must appear in the AuditTrail,
    regardless of how many times the download endpoint is called.
    """

    @pytest.mark.asyncio
    async def test_second_download_returns_200(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """The download endpoint must succeed on the second call too."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                client.get(f"/requests/{req.request_id}/voucher-pdf")
                resp2 = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp2.status_code == 200

    @pytest.mark.asyncio
    async def test_second_download_does_not_duplicate_voucher_downloaded_event(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Req 16.1: Calling the download endpoint twice must result in exactly ONE
        VOUCHER_DOWNLOADED AuditEvent (idempotent — no duplicates).
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                client.get(f"/requests/{req.request_id}/voucher-pdf")
                client.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            f"Expected exactly 1 VOUCHER_DOWNLOADED event after 2 calls, got {len(events)}"
        )

    @pytest.mark.asyncio
    async def test_second_download_does_not_duplicate_status_changed_event(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """
        Calling the download endpoint twice must not produce two STATUS_CHANGED
        AMIGA_REVIEWED→SENT_TO_CLIENT events.
        """
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                client.get(f"/requests/{req.request_id}/voucher-pdf")
                client.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.STATUS_CHANGED,
            )
        )
        events = result.scalars().all()
        # All STATUS_CHANGED events should be from the first download only
        sent_events = [
            e for e in events
            if e.payload.get("to_status") == RequestStatus.SENT_TO_CLIENT.value
        ]
        assert len(sent_events) == 1, (
            f"Expected exactly 1 SENT_TO_CLIENT STATUS_CHANGED event, got {len(sent_events)}"
        )

    @pytest.mark.asyncio
    async def test_third_download_still_only_one_sent_to_client_event(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """Idempotency holds for any number of repeat downloads."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]

        app = _build_app(db_session, user)
        with patch("app.routes.voucher.file_store") as mock_store:
            mock_store.download = AsyncMock(return_value=_FAKE_PDF_BYTES)
            with TestClient(app) as client:
                for _ in range(3):
                    client.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            f"Expected exactly 1 VOUCHER_DOWNLOADED event after 3 downloads, got {len(events)}"
        )


# ===========================================================================
# Missing VoucherPDF record → 404
# ===========================================================================


class TestMissingVoucherPdf:
    """
    When no VoucherPDF record exists for the request, the download endpoint
    must return 404 with an appropriate message.
    """

    @pytest.mark.asyncio
    async def test_no_voucher_pdf_record_returns_404(
        self,
        db_session: AsyncSession,
        seeded_db: dict,
    ):
        """If no VoucherPDF row exists for the request, return 404."""
        req: InvoiceRequest = seeded_db["request"]
        user: User = seeded_db["pic_user"]
        voucher_pdf: VoucherPDF = seeded_db["voucher_pdf"]

        # Remove the existing VoucherPDF record
        await db_session.delete(voucher_pdf)
        await db_session.commit()

        app = _build_app(db_session, user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 404
        assert "no voucher pdf" in resp.json().get("detail", "").lower()
