"""
Tests for Voucher Preview endpoint (Task 29.1).

GET /requests/{request_id}/voucher-preview

Requirements tested:
  - Req 14.1: Preview available for AMIGA_REVIEWED Requests only; returns all
              approved HeaderFields, LineItems, and PaymentDetails.
  - Req 14.2: Authorization_Disclaimer must be included in the response.
  - Req 14.3: FINANCE_REVIEWER and CLIENT_PIC with active ClientAssignment may
              access the preview; others are denied.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import (
    HeaderFields,
    LineExtractionStatus,
    LineItem,
    PaymentDetails,
)
from app.models.request import InvoiceRequest, RequestStatus
from app.models.tax_assignment import IndonesianTaxType, TaxAssignment
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types to SQLite-compatible equivalents
# ---------------------------------------------------------------------------
import app.models.audit as _audit_module
import app.models.request as _req_module

_req_module.InvoiceRequest.__table__.c["flags"].type = JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None

# The disclaimer configured in settings
_DISCLAIMER = (
    "Voucher ini disiapkan hanya untuk keperluan peninjauan oleh klien. "
    "Otorisasi pembayaran tetap menjadi tanggung jawab klien."
)


# ===========================================================================
# Fixtures
# ===========================================================================


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
async def seeded_db(db_session: AsyncSession):
    """
    Seed the DB with:
      - a Client
      - a CLIENT_PIC user + active ClientAssignment
      - a FINANCE_REVIEWER user + active ClientAssignment
      - an unassigned CLIENT_PIC user (no assignment)
      - an InvoiceRequest in AMIGA_REVIEWED status
      - a Draft with HeaderFields, two LineItems (one with TaxAssignment), PaymentDetails
    """
    now = datetime.now(timezone.utc)

    client = Client(client_id=uuid.uuid4(), client_name="PT Demo", is_active=True)
    db_session.add(client)

    # Uploader (different user to avoid constraint issues)
    uploader = User(
        user_id=uuid.uuid4(),
        username="uploader_x",
        role=UserRole.INTAKE_SUPPORT,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(uploader)

    # CLIENT_PIC with assignment
    pic = User(
        user_id=uuid.uuid4(),
        username="pic_alice",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(pic)

    # FINANCE_REVIEWER with assignment
    reviewer = User(
        user_id=uuid.uuid4(),
        username="reviewer_bob",
        role=UserRole.FINANCE_REVIEWER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(reviewer)

    # CLIENT_PIC without assignment
    unassigned_pic = User(
        user_id=uuid.uuid4(),
        username="pic_unassigned",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(unassigned_pic)

    await db_session.flush()

    # Assignments
    for user_obj in (pic, reviewer):
        db_session.add(ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=user_obj.user_id,
            client_id=client.client_id,
            is_active=True,
            assigned_at=now,
            revoked_at=None,
        ))

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
        invoice_number="INV-2024-007",
        invoice_date=date(2024, 7, 1),
        currency="IDR",
        subtotal=Decimal("2000000.00"),
        total_amount=Decimal("2220000.00"),
        tax_amount=Decimal("220000.00"),
        fee_amount=Decimal("0.00"),
        rounding_adj=Decimal("0.00"),
    )
    db_session.add(header)

    # LineItem 1 — will have a TaxAssignment
    line1 = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=1,
        description="Consulting services",
        quantity=Decimal("2"),
        unit="day",
        unit_price=Decimal("1000000.00"),
        line_total=Decimal("2000000.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    db_session.add(line1)

    # LineItem 2 — no TaxAssignment
    line2 = LineItem(
        line_item_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        sequence_number=2,
        description="Travel reimbursement",
        quantity=Decimal("1"),
        unit="lump sum",
        unit_price=Decimal("500000.00"),
        line_total=Decimal("500000.00"),
        extraction_status=LineExtractionStatus.COMPLETE,
    )
    db_session.add(line2)
    await db_session.flush()

    # TaxAssignment for line1 only
    tax = TaxAssignment(
        tax_assignment_id=uuid.uuid4(),
        line_item_id=line1.line_item_id,
        tax_type=IndonesianTaxType.PPN,
        tax_rate=Decimal("0.1100"),
        tax_amount=Decimal("220000.00"),
        assigned_at=now,
        assigned_by=reviewer.user_id,
    )
    db_session.add(tax)

    # PaymentDetails
    payment = PaymentDetails(
        payment_id=uuid.uuid4(),
        draft_id=draft.draft_id,
        bank_account_number="9876543210",
        bank_name="Bank Mandiri",
        payment_reference="REF-2024-007",
    )
    db_session.add(payment)

    await db_session.commit()

    return {
        "client": client,
        "pic": pic,
        "reviewer": reviewer,
        "unassigned_pic": unassigned_pic,
        "uploader": uploader,
        "request": request,
        "draft": draft,
        "header": header,
        "line1": line1,
        "line2": line2,
        "tax": tax,
        "payment": payment,
    }


def _build_app(db_session: AsyncSession, current_user: User) -> FastAPI:
    """Build a minimal FastAPI app with the voucher router and test overrides."""
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
# Req 14.1 — Status guard and field completeness
# ===========================================================================


class TestStatusGuardAndFields:
    """
    Req 14.1: Preview only available for AMIGA_REVIEWED Requests;
    must include all approved HeaderFields, LineItems, and PaymentDetails.
    """

    @pytest.mark.asyncio
    async def test_amiga_reviewed_returns_200(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """AMIGA_REVIEWED request must return HTTP 200."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200

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
    async def test_non_amiga_reviewed_returns_422(
        self, db_session: AsyncSession, seeded_db: dict, bad_status: RequestStatus
    ):
        """All non-AMIGA_REVIEWED statuses must be rejected with HTTP 422."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        req.status = bad_status
        await db_session.commit()

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 422, (
            f"Expected 422 for status {bad_status.value}, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_unknown_request_returns_404(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """A request_id that doesn't exist must return 404."""
        pic: User = seeded_db["pic"]
        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{uuid.uuid4()}/voucher-preview")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_response_contains_header_fields(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Req 14.1: Response must include all approved HeaderFields."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200
        body = resp.json()
        hf = body["header_fields"]
        assert hf is not None
        assert hf["vendor_name"] == "PT Maju Bersama"
        assert hf["invoice_number"] == "INV-2024-007"
        assert hf["currency"] == "IDR"

    @pytest.mark.asyncio
    async def test_response_contains_all_line_items_in_order(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Req 14.1: Response must include all LineItems ordered by sequence_number."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200
        items = resp.json()["line_items"]
        assert len(items) == 2
        assert items[0]["sequence_number"] == 1
        assert items[1]["sequence_number"] == 2
        assert items[0]["description"] == "Consulting services"
        assert items[1]["description"] == "Travel reimbursement"

    @pytest.mark.asyncio
    async def test_response_contains_payment_details(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Req 14.1: Response must include PaymentDetails."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200
        pd = resp.json()["payment_details"]
        assert pd is not None
        assert pd["bank_account_number"] == "9876543210"
        assert pd["bank_name"] == "Bank Mandiri"
        assert pd["payment_reference"] == "REF-2024-007"

    @pytest.mark.asyncio
    async def test_line_item_with_tax_assignment_includes_tax_fields(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Line items with a TaxAssignment must expose tax_type, tax_rate, tax_amount."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        items = resp.json()["line_items"]
        taxed_item = next(i for i in items if i["sequence_number"] == 1)
        assert taxed_item["tax_type"] == "PPN"
        assert taxed_item["tax_rate"] is not None
        assert taxed_item["tax_amount"] is not None

    @pytest.mark.asyncio
    async def test_line_item_without_tax_assignment_has_null_tax_fields(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Line items without a TaxAssignment must have null tax_type/rate/amount."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        items = resp.json()["line_items"]
        untaxed_item = next(i for i in items if i["sequence_number"] == 2)
        assert untaxed_item["tax_type"] is None
        assert untaxed_item["tax_rate"] is None
        assert untaxed_item["tax_amount"] is None

    @pytest.mark.asyncio
    async def test_response_includes_request_id_and_status(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """Response must echo back request_id and the current AMIGA_REVIEWED status."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        body = resp.json()
        assert body["request_id"] == str(req.request_id)
        assert body["status"] == "AMIGA_REVIEWED"


# ===========================================================================
# Req 14.2 — Authorization Disclaimer included
# ===========================================================================


class TestAuthorizationDisclaimer:
    """
    Req 14.2: The Authorization_Disclaimer must be included in the voucher preview.
    """

    @pytest.mark.asyncio
    async def test_response_contains_authorization_disclaimer(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """authorization_disclaimer field must be present and non-empty."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200
        body = resp.json()
        assert "authorization_disclaimer" in body
        assert len(body["authorization_disclaimer"]) > 0

    @pytest.mark.asyncio
    async def test_disclaimer_matches_configured_value(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """The disclaimer text must match the value from settings.AUTHORIZATION_DISCLAIMER."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.json()["authorization_disclaimer"] == _DISCLAIMER


# ===========================================================================
# Req 14.3 — Role and ClientAssignment authorization
# ===========================================================================


class TestAuthorization:
    """
    Req 14.3: FINANCE_REVIEWER and CLIENT_PIC with an active ClientAssignment
    may access the voucher preview; all other roles are denied.
    """

    @pytest.mark.asyncio
    async def test_finance_reviewer_with_assignment_can_access(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """FINANCE_REVIEWER with active ClientAssignment must receive HTTP 200."""
        req: InvoiceRequest = seeded_db["request"]
        reviewer: User = seeded_db["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_client_pic_with_assignment_can_access(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """CLIENT_PIC with active ClientAssignment must receive HTTP 200."""
        req: InvoiceRequest = seeded_db["request"]
        pic: User = seeded_db["pic"]

        app = _build_app(db_session, pic)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_client_pic_without_assignment_is_forbidden(
        self, db_session: AsyncSession, seeded_db: dict
    ):
        """CLIENT_PIC without a ClientAssignment for this client must receive HTTP 403."""
        req: InvoiceRequest = seeded_db["request"]
        unassigned: User = seeded_db["unassigned_pic"]

        app = _build_app(db_session, unassigned)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unauthorized_role",
        [UserRole.INTAKE_SUPPORT, UserRole.SERVICE_LEAD, UserRole.SYSTEM_ADMIN],
    )
    async def test_unauthorized_roles_are_denied(
        self, db_session: AsyncSession, seeded_db: dict, unauthorized_role: UserRole
    ):
        """Roles other than FINANCE_REVIEWER and CLIENT_PIC must receive HTTP 403."""
        req: InvoiceRequest = seeded_db["request"]
        now = datetime.now(timezone.utc)
        other_user = User(
            user_id=uuid.uuid4(),
            username=f"user_{unauthorized_role.value}",
            role=unauthorized_role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(other_user)
        await db_session.commit()

        app = _build_app(db_session, other_user)
        with TestClient(app) as client:
            resp = client.get(f"/requests/{req.request_id}/voucher-preview")

        assert resp.status_code == 403, (
            f"Role {unauthorized_role.value} should be denied access (expected 403, "
            f"got {resp.status_code})"
        )
