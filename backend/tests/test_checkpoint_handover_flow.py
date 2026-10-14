"""
Checkpoint 31 — Full handover flow end-to-end integration test.

Exercises the complete path:
  approve (Finance Reviewer)
    → generate PDF (Client PIC)
    → download twice (Client PIC)
    → verify exactly one SENT_TO_CLIENT transition in AuditTrail

Requirements validated:
  - Req 12.1: Approval transitions to AMIGA_REVIEWED
  - Req 15.6: VOUCHER_GENERATED AuditEvent written on PDF generation
  - Req 16.1: Exactly one VOUCHER_DOWNLOADED AuditEvent regardless of # of downloads
  - Req 16.2: Request status transitions to SENT_TO_CLIENT on first download
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models.audit as _audit_module
import app.models.request as _req_module
from app.models import Base
from app.models.audit import AuditEvent, AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineExtractionStatus, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole
from app.models.voucher import VoucherTemplate

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types to SQLite-compatible equivalents
# ---------------------------------------------------------------------------
_req_module.InvoiceRequest.__table__.c["flags"].type = JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FAKE_PDF = b"%PDF-1.4 fake-pdf-for-checkpoint-31"
_TEST_TEMPLATE = """\
<!DOCTYPE html><html><body>
<h1>VOUCHER</h1>
<p>Vendor: {{ vendor_name }}</p>
<p>{{ authorization_disclaimer }}</p>
</body></html>
"""


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def approval_generation_stub():
    with patch(
        "app.routes.requests.generate_approved_voucher", AsyncMock(return_value={"voucher_pdf_id": "test-voucher"})
    ):
        yield


@pytest.fixture
async def db_session():
    """Fresh in-memory SQLite async session for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def handover_scenario(db_session: AsyncSession) -> dict:
    """
    Seed the full handover scenario:
      - Client
      - Finance Reviewer (NOT the uploader to avoid self-approval)
      - Client PIC user with active ClientAssignment
      - Uploader (a separate INTAKE_SUPPORT user)
      - InvoiceRequest in READY_FOR_REVIEW status
      - Draft + HeaderFields + LineItem + PaymentDetails
      - Active VoucherTemplate

    Returns a dict of all key objects.
    """
    now = datetime.now(timezone.utc)

    client = Client(client_id=uuid.uuid4(), client_name="PT Checkpoint Corp", is_active=True)
    db_session.add(client)

    # Finance Reviewer — will approve the request
    reviewer = User(
        user_id=uuid.uuid4(),
        username="reviewer_chk",
        role=UserRole.FINANCE_REVIEWER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(reviewer)

    # Client PIC — will generate and download the PDF
    pic = User(
        user_id=uuid.uuid4(),
        username="pic_chk",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(pic)

    # Uploader — separate from reviewer (avoids self-approval block)
    uploader = User(
        user_id=uuid.uuid4(),
        username="uploader_chk",
        role=UserRole.INTAKE_SUPPORT,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(uploader)

    await db_session.flush()

    # Assign reviewer to client
    reviewer_assignment = ClientAssignment(
        assignment_id=uuid.uuid4(),
        user_id=reviewer.user_id,
        client_id=client.client_id,
        is_active=True,
        assigned_at=now,
        revoked_at=None,
    )
    db_session.add(reviewer_assignment)

    # Assign PIC to client
    pic_assignment = ClientAssignment(
        assignment_id=uuid.uuid4(),
        user_id=pic.user_id,
        client_id=client.client_id,
        is_active=True,
        assigned_at=now,
        revoked_at=None,
    )
    db_session.add(pic_assignment)

    # InvoiceRequest — starts in READY_FOR_REVIEW
    request = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client.client_id,
        uploader_id=uploader.user_id,
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="s3://bucket/inv_chk.pdf",
        invoice_page_count=2,
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
        invoice_number="INV-CHK-001",
        invoice_date=date.today(),
        currency="IDR",
        subtotal=Decimal("5000000.00"),
        total_amount=Decimal("5550000.00"),
        tax_amount=Decimal("550000.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    db_session.add(header)

    # LineItem
    line_item = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="IT Consulting",
        quantity=Decimal("5"),
        unit="day",
        unit_price=Decimal("1000000.00"),
        line_total=Decimal("5000000.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    db_session.add(line_item)

    # PaymentDetails
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="9876543210",
        bank_name="Bank Mandiri",
        payment_reference="REF-CHK-001",
    )
    db_session.add(payment)

    # Active VoucherTemplate
    template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version="v1.0",
        template_content=_TEST_TEMPLATE,
        is_active=True,
        created_by=None,
        created_at=now,
    )
    db_session.add(template)

    await db_session.commit()

    return {
        "client": client,
        "reviewer": reviewer,
        "pic": pic,
        "uploader": uploader,
        "request": request,
        "draft": draft,
    }


def _build_app(db_session: AsyncSession, current_user: User) -> FastAPI:
    """
    Minimal FastAPI app with both the requests and voucher routers,
    DI-overridden for a specific current_user and DB session.
    """
    from app.dependencies import get_current_user, get_db
    from app.routes.requests import router as requests_router
    from app.routes.voucher import router as voucher_router

    app = FastAPI()
    app.include_router(requests_router)
    app.include_router(voucher_router)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


# ===========================================================================
# Checkpoint 31 — Full handover flow
# ===========================================================================


class TestHandoverFlowCheckpoint:
    """
    End-to-end integration test for the full voucher handover flow:
      approve → generate PDF → download × 2 → verify SENT_TO_CLIENT (idempotent)
    """

    @pytest.mark.asyncio
    async def test_step1_approve_transitions_to_amiga_reviewed(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Step 1: POST /approve as Finance Reviewer → status becomes AMIGA_REVIEWED.
        Req 12.1
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            resp = http.post(f"/requests/{req.request_id}/approve")

        assert resp.status_code == 200, f"Approve failed: {resp.json()}"

        result = await db_session.execute(select(InvoiceRequest).where(InvoiceRequest.request_id == req.request_id))
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.AMIGA_REVIEWED

    @pytest.mark.asyncio
    async def test_step1_approve_writes_approved_audit_event(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """Approval must write an APPROVED AuditEvent. Req 12.1"""
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.APPROVED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, f"Expected 1 APPROVED event, got {len(events)}"

    @pytest.mark.asyncio
    async def test_step2_generate_pdf_returns_201(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Step 2: POST /voucher-pdf as Client PIC after approval → 201.
        Req 15.6
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        # First approve
        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        # Then generate PDF
        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                resp = http.post(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 201, f"PDF generation failed: {resp.json()}"

    @pytest.mark.asyncio
    async def test_step2_generate_pdf_writes_voucher_generated_event(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """VOUCHER_GENERATED AuditEvent must be written on PDF generation. Req 15.6"""
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_GENERATED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, f"Expected 1 VOUCHER_GENERATED event, got {len(events)}"

    @pytest.mark.asyncio
    async def test_step3_first_download_returns_pdf(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Step 3a: GET /voucher-pdf (first download) → 200 application/pdf.
        Req 16.1, 16.2
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                resp = http.get(f"/requests/{req.request_id}/voucher-pdf")

        assert resp.status_code == 200, f"First download failed: {resp.status_code}"
        assert "application/pdf" in resp.headers.get("content-type", "")
        assert resp.content == _FAKE_PDF

    @pytest.mark.asyncio
    async def test_step3_second_download_also_returns_200(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Step 3b: GET /voucher-pdf (second download) → still 200 (idempotent).
        Req 16.1
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")  # first
                resp2 = http.get(f"/requests/{req.request_id}/voucher-pdf")  # second

        assert resp2.status_code == 200, f"Second download failed: {resp2.status_code}"

    @pytest.mark.asyncio
    async def test_final_status_is_sent_to_client(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        After the full flow, request.status must be SENT_TO_CLIENT.
        Req 16.2
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")  # first download
                http.get(f"/requests/{req.request_id}/voucher-pdf")  # second download

        result = await db_session.execute(select(InvoiceRequest).where(InvoiceRequest.request_id == req.request_id))
        final = result.scalar_one()
        assert final.status == RequestStatus.SENT_TO_CLIENT, f"Expected SENT_TO_CLIENT, got {final.status}"

    @pytest.mark.asyncio
    async def test_exactly_one_voucher_downloaded_event_after_two_downloads(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Req 16.1: Exactly one VOUCHER_DOWNLOADED AuditEvent in AuditTrail,
        regardless of how many times the download endpoint is called.
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, f"Expected exactly 1 VOUCHER_DOWNLOADED event, got {len(events)}"

    @pytest.mark.asyncio
    async def test_exactly_one_sent_to_client_status_change_after_two_downloads(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        Req 16.1: Exactly one STATUS_CHANGED (AMIGA_REVIEWED → SENT_TO_CLIENT)
        event after two downloads — no duplicate transitions.
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == req.request_id,
                AuditEvent.event_type == AuditEventType.STATUS_CHANGED,
            )
        )
        all_status_changes = result.scalars().all()
        sent_to_client_changes = [
            e for e in all_status_changes if e.payload.get("to_status") == RequestStatus.SENT_TO_CLIENT.value
        ]
        assert len(sent_to_client_changes) == 1, (
            f"Expected exactly 1 SENT_TO_CLIENT STATUS_CHANGED event, " f"got {len(sent_to_client_changes)}"
        )

    @pytest.mark.asyncio
    async def test_full_audit_trail_has_expected_event_sequence(
        self,
        db_session: AsyncSession,
        handover_scenario: dict,
    ):
        """
        After the complete flow, the AuditTrail for the request must contain
        all of: APPROVED, VOUCHER_GENERATED, VOUCHER_DOWNLOADED, STATUS_CHANGED (×2).
        """
        req: InvoiceRequest = handover_scenario["request"]
        reviewer: User = handover_scenario["reviewer"]
        pic: User = handover_scenario["pic"]

        reviewer_app = _build_app(db_session, reviewer)
        with TestClient(reviewer_app) as http:
            http.post(f"/requests/{req.request_id}/approve")

        pic_app = _build_app(db_session, pic)
        mock_wp = MagicMock()
        mock_wp.HTML.return_value.write_pdf.return_value = _FAKE_PDF
        with (
            patch("app.routes.voucher.weasyprint", mock_wp),
            patch("app.routes.voucher.file_store") as mock_store,
        ):
            mock_store.upload = AsyncMock(return_value=None)
            mock_store.download = AsyncMock(return_value=_FAKE_PDF)
            with TestClient(pic_app) as http:
                http.post(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")
                http.get(f"/requests/{req.request_id}/voucher-pdf")

        result = await db_session.execute(select(AuditEvent).where(AuditEvent.request_id == req.request_id))
        events = result.scalars().all()
        event_types = {e.event_type for e in events}

        assert AuditEventType.APPROVED in event_types, "Missing APPROVED event"
        assert AuditEventType.VOUCHER_GENERATED in event_types, "Missing VOUCHER_GENERATED event"
        assert AuditEventType.VOUCHER_DOWNLOADED in event_types, "Missing VOUCHER_DOWNLOADED event"
        assert AuditEventType.STATUS_CHANGED in event_types, "Missing STATUS_CHANGED event"
