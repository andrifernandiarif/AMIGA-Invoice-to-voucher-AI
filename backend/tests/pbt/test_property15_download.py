# Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
#
# For any Request whose status is not AMIGA_REVIEWED:
#   - Both Voucher PDF generation AND download requests SHALL be denied.
#
# For any number of download attempts on an AMIGA_REVIEWED Request by an
# authorized Client_PIC, exactly one VOUCHER_DOWNLOADED AuditEvent SHALL
# appear in the AuditTrail and the Request status SHALL be SENT_TO_CLIENT.
#
# For any Client_PIC without an active ClientAssignment for the request's
# client, download SHALL be denied (403) and the response body must contain
# no client data.
#
# **Validates: Requirements 15.3, 16.1, 16.2, 16.3, 16.4**

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
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
# ---------------------------------------------------------------------------
import app.models.audit as _audit_module
import app.models.request as _req_module
import sqlalchemy as _sa

_req_module.InvoiceRequest.__table__.c["flags"].type = _sa.JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = _sa.JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FAKE_PDF = b"%PDF-1.4 test"

# All statuses that are NOT AMIGA_REVIEWED
_NON_AMIGA_REVIEWED_STATUSES = [
    RequestStatus.QUEUED,
    RequestStatus.PROCESSING,
    RequestStatus.READY_FOR_REVIEW,
    RequestStatus.EXCEPTION,
    RequestStatus.REJECTED,
]


# ===========================================================================
# Async DB + fixture helpers (sync-wrapped for Hypothesis compatibility)
# ===========================================================================


def _make_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


import asyncio


def _run(coro):
    """Run a coroutine synchronously — needed for Hypothesis @given tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _create_test_db():
    """Create in-memory SQLite DB and return (engine, session_factory)."""
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _seed_scenario(
    factory,
    *,
    request_status: RequestStatus,
    has_assignment: bool,
    has_voucher_pdf: bool = True,
):
    """
    Seed a minimal scenario in the DB. Returns (session, objects_dict).
    The session is yielded from the factory and is the caller's responsibility
    to close.
    """
    now = datetime.now(timezone.utc)

    session = factory()
    await session.__aenter__()

    client = Client(client_id=uuid.uuid4(), client_name="Test Client", is_active=True)
    session.add(client)

    pic_user = User(
        user_id=uuid.uuid4(),
        username=f"pic_{uuid.uuid4().hex[:6]}",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(pic_user)

    uploader = User(
        user_id=uuid.uuid4(),
        username=f"uploader_{uuid.uuid4().hex[:6]}",
        role=UserRole.INTAKE_SUPPORT,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(uploader)

    await session.flush()

    if has_assignment:
        assignment = ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=pic_user.user_id,
            client_id=client.client_id,
            is_active=True,
            assigned_at=now,
            revoked_at=None,
        )
        session.add(assignment)

    request = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client.client_id,
        uploader_id=uploader.user_id,
        request_type="standard",
        channel="email",
        request_date=date.today(),
        status=request_status,
        flags=[],
        invoice_file_key="s3://bucket/inv.pdf",
        invoice_page_count=1,
        created_at=now,
        updated_at=now,
    )
    session.add(request)
    await session.flush()

    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=request.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
        extracted_at=now,
    )
    session.add(draft)
    await session.flush()

    if has_voucher_pdf:
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
        session.add(voucher_pdf)

    await session.commit()

    return session, {
        "client": client,
        "pic_user": pic_user,
        "request": request,
    }


def _build_app(session: AsyncSession, current_user: User) -> FastAPI:
    from app.dependencies import get_current_user, get_db
    from app.routes.voucher import router as voucher_router

    app = FastAPI()
    app.include_router(voucher_router)

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


# ===========================================================================
# Property 15a: Non-AMIGA_REVIEWED status → download denied
# ===========================================================================


class TestProperty15StatusGate:
    """
    Property 15a: For any Request whose status is not AMIGA_REVIEWED (and not
    SENT_TO_CLIENT), the download endpoint SHALL deny the request with an error.
    """

    @given(status=st.sampled_from(_NON_AMIGA_REVIEWED_STATUSES))
    @settings(max_examples=50, deadline=None)  # Database/HTTP assertions; timing is measured separately.
    def test_non_amiga_reviewed_download_is_denied(self, status: RequestStatus):
        """
        # Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
        For any non-AMIGA_REVIEWED status, the download endpoint SHALL return a 400 error.
        **Validates: Requirements 15.3, 16.4**
        """

        async def _run_scenario():
            engine, factory = await _create_test_db()
            try:
                session, objs = await _seed_scenario(
                    factory,
                    request_status=status,
                    has_assignment=True,
                    has_voucher_pdf=True,
                )
                try:
                    app = _build_app(session, objs["pic_user"])
                    with patch("app.routes.voucher.file_store") as mock_store:
                        mock_store.download = AsyncMock(return_value=_FAKE_PDF)
                        with TestClient(app) as client:
                            resp = client.get(
                                f"/requests/{objs['request'].request_id}/voucher-pdf"
                            )
                    return resp.status_code
                finally:
                    await session.__aexit__(None, None, None)
            finally:
                await engine.dispose()

        status_code = _run(_run_scenario())
        assert status_code == 400, (
            f"Expected 400 for status {status.value}, got {status_code}"
        )


# ===========================================================================
# Property 15b: Exactly one SENT_TO_CLIENT event for N downloads
# ===========================================================================


class TestProperty15IdempotentSentToClient:
    """
    Property 15b: For any number N ≥ 1 of download attempts on an AMIGA_REVIEWED
    request by an authorized Client_PIC, exactly one VOUCHER_DOWNLOADED AuditEvent
    SHALL appear in the AuditTrail.
    """

    @given(num_downloads=st.integers(min_value=1, max_value=5))
    @settings(max_examples=25, deadline=None)  # Database/HTTP assertions; timing is measured separately.
    def test_exactly_one_voucher_downloaded_event_for_n_downloads(
        self, num_downloads: int
    ):
        """
        # Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
        For N download attempts by an authorized Client_PIC, exactly one
        VOUCHER_DOWNLOADED event SHALL appear in the AuditTrail.
        **Validates: Requirements 16.1**
        """

        async def _run_scenario():
            engine, factory = await _create_test_db()
            try:
                session, objs = await _seed_scenario(
                    factory,
                    request_status=RequestStatus.AMIGA_REVIEWED,
                    has_assignment=True,
                    has_voucher_pdf=True,
                )
                try:
                    app = _build_app(session, objs["pic_user"])
                    with patch("app.routes.voucher.file_store") as mock_store:
                        mock_store.download = AsyncMock(return_value=_FAKE_PDF)
                        with TestClient(app) as client:
                            for _ in range(num_downloads):
                                client.get(
                                    f"/requests/{objs['request'].request_id}/voucher-pdf"
                                )

                    # Count VOUCHER_DOWNLOADED events
                    result = await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.request_id == objs["request"].request_id,
                            AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
                        )
                    )
                    return len(result.scalars().all())
                finally:
                    await session.__aexit__(None, None, None)
            finally:
                await engine.dispose()

        event_count = _run(_run_scenario())
        assert event_count == 1, (
            f"Expected exactly 1 VOUCHER_DOWNLOADED event after {num_downloads} "
            f"downloads, got {event_count}"
        )

    @given(num_downloads=st.integers(min_value=1, max_value=5))
    @settings(max_examples=25, deadline=None)  # Database/HTTP assertions; timing is measured separately.
    def test_request_status_is_sent_to_client_after_n_downloads(
        self, num_downloads: int
    ):
        """
        # Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
        After any number of downloads, the Request status SHALL be SENT_TO_CLIENT.
        **Validates: Requirements 16.2**
        """

        async def _run_scenario():
            engine, factory = await _create_test_db()
            try:
                session, objs = await _seed_scenario(
                    factory,
                    request_status=RequestStatus.AMIGA_REVIEWED,
                    has_assignment=True,
                    has_voucher_pdf=True,
                )
                try:
                    app = _build_app(session, objs["pic_user"])
                    with patch("app.routes.voucher.file_store") as mock_store:
                        mock_store.download = AsyncMock(return_value=_FAKE_PDF)
                        with TestClient(app) as client:
                            for _ in range(num_downloads):
                                client.get(
                                    f"/requests/{objs['request'].request_id}/voucher-pdf"
                                )

                    # Reload request status
                    result = await session.execute(
                        select(InvoiceRequest).where(
                            InvoiceRequest.request_id == objs["request"].request_id
                        )
                    )
                    return result.scalar_one().status
                finally:
                    await session.__aexit__(None, None, None)
            finally:
                await engine.dispose()

        final_status = _run(_run_scenario())
        assert final_status == RequestStatus.SENT_TO_CLIENT, (
            f"Expected SENT_TO_CLIENT after {num_downloads} downloads, "
            f"got {final_status}"
        )


# ===========================================================================
# Property 15c: Unauthorized Client_PIC → 403, no client data
# ===========================================================================


class TestProperty15UnauthorizedDownload:
    """
    Property 15c: A Client_PIC without an active ClientAssignment for the
    request's client SHALL be denied (403) and the response body must contain
    no client data.
    """

    @given(dummy=st.just(None))
    @settings(max_examples=10, deadline=None)  # Database/HTTP assertions; timing is measured separately.
    def test_no_assignment_returns_403(self, dummy):
        """
        # Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
        A Client_PIC with no active ClientAssignment SHALL receive 403.
        **Validates: Requirements 16.3**
        """

        async def _run_scenario():
            engine, factory = await _create_test_db()
            try:
                session, objs = await _seed_scenario(
                    factory,
                    request_status=RequestStatus.AMIGA_REVIEWED,
                    has_assignment=False,  # <-- no assignment
                    has_voucher_pdf=True,
                )
                try:
                    app = _build_app(session, objs["pic_user"])
                    with patch("app.routes.voucher.file_store") as mock_store:
                        mock_store.download = AsyncMock(return_value=_FAKE_PDF)
                        with TestClient(app) as client:
                            resp = client.get(
                                f"/requests/{objs['request'].request_id}/voucher-pdf"
                            )
                    return resp.status_code, resp.json(), objs["client"]
                finally:
                    await session.__aexit__(None, None, None)
            finally:
                await engine.dispose()

        status_code, body, client_obj = _run(_run_scenario())
        # Must be denied
        assert status_code == 403, f"Expected 403, got {status_code}"
        # Response body must not expose client data
        body_str = str(body)
        assert client_obj.client_name not in body_str, (
            "403 response must not contain the client's name"
        )
        assert str(client_obj.client_id) not in body_str, (
            "403 response must not contain the client_id"
        )

    @given(dummy=st.just(None))
    @settings(max_examples=10, deadline=None)  # Database/HTTP assertions; timing is measured separately.
    def test_no_assignment_does_not_create_audit_events(self, dummy):
        """
        # Feature: invoice-to-voucher, Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event
        A denied download (no assignment) must not write any AuditEvents.
        **Validates: Requirements 16.3**
        """

        async def _run_scenario():
            engine, factory = await _create_test_db()
            try:
                session, objs = await _seed_scenario(
                    factory,
                    request_status=RequestStatus.AMIGA_REVIEWED,
                    has_assignment=False,
                    has_voucher_pdf=True,
                )
                try:
                    app = _build_app(session, objs["pic_user"])
                    with patch("app.routes.voucher.file_store") as mock_store:
                        mock_store.download = AsyncMock(return_value=_FAKE_PDF)
                        with TestClient(app) as client:
                            client.get(
                                f"/requests/{objs['request'].request_id}/voucher-pdf"
                            )

                    result = await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.request_id == objs["request"].request_id,
                            AuditEvent.event_type == AuditEventType.VOUCHER_DOWNLOADED,
                        )
                    )
                    return len(result.scalars().all())
                finally:
                    await session.__aexit__(None, None, None)
            finally:
                await engine.dispose()

        event_count = _run(_run_scenario())
        assert event_count == 0, (
            f"Unauthorized download must not write audit events, got {event_count}"
        )
