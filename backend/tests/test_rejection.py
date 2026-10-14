"""
Tests for the Finance Reviewer rejection endpoint (Task 26.1).

POST /requests/{request_id}/reject

Requirements tested:
  - Req 13.1: Rejection requires a reason of 1–500 characters; 0 or >500 chars
              returns 422.
  - Req 13.2: Valid rejection transitions status to REJECTED and records the
              reason, reviewer identity, and UTC timestamp in the Audit Trail.
  - Req 13.3: The rejection reason is visible to an authorized Client_PIC on
              the Request detail endpoint.
  - Req 13.4: The Finance Reviewer who uploaded the Request cannot reject it
              (self-rejection, 403).
  Additional guards:
  - Non-FINANCE_REVIEWER role is rejected with 403.
  - Request in a non-rejectable status (e.g. AMIGA_REVIEWED) returns 409.
  - Request in EXCEPTION status can be rejected (Req 13.1 — both statuses).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

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
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific column types to SQLite-compatible equivalents.
# ---------------------------------------------------------------------------
_req_module.InvoiceRequest.__table__.c["flags"].type = JSON()
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].type = JSON()
_audit_module.AuditEvent.__table__.c["payload"].server_default = None


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


def _make_user(role: UserRole, username_suffix: str = "") -> User:
    """Build an in-memory (non-persisted) User with the given role."""
    now = datetime.now(timezone.utc)
    suffix = username_suffix or uuid.uuid4().hex[:6]
    return User(
        user_id=uuid.uuid4(),
        username=f"{role.value.lower()}_{suffix}",
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


async def _seed_base(
    db: AsyncSession,
    *,
    request_status: RequestStatus = RequestStatus.READY_FOR_REVIEW,
    request_flags: list[str] | None = None,
) -> dict:
    """
    Seed the minimum data needed for rejection tests:
      - one Client
      - one uploader (CLIENT_PIC)
      - one reviewer (FINANCE_REVIEWER) assigned to the client
      - one InvoiceRequest in the given status

    Returns a dict of the key objects.
    """
    now = datetime.now(timezone.utc)

    client = Client(client_id=uuid.uuid4(), client_name="Acme Corp", is_active=True)
    db.add(client)

    uploader = User(
        user_id=uuid.uuid4(),
        username="uploader_pic",
        role=UserRole.CLIENT_PIC,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(uploader)

    reviewer = User(
        user_id=uuid.uuid4(),
        username="finance_reviewer",
        role=UserRole.FINANCE_REVIEWER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(reviewer)

    await db.flush()

    # Assign reviewer to client
    assignment = ClientAssignment(
        assignment_id=uuid.uuid4(),
        user_id=reviewer.user_id,
        client_id=client.client_id,
        is_active=True,
        assigned_at=now,
        revoked_at=None,
    )
    db.add(assignment)

    request = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client.client_id,
        uploader_id=uploader.user_id,
        request_type="standard",
        channel="upload",
        request_date=date.today(),
        status=request_status,
        flags=request_flags or [],
        invoice_file_key="s3://bucket/invoice.pdf",
        invoice_page_count=1,
        created_at=now,
        updated_at=now,
    )
    db.add(request)

    await db.commit()

    return {
        "client": client,
        "uploader": uploader,
        "reviewer": reviewer,
        "request": request,
    }


def _build_app(db_session: AsyncSession, current_user: User) -> FastAPI:
    """
    Minimal FastAPI app with only the requests router, DB session and
    current user both overridden so no real credentials are needed.
    """
    from app.dependencies import get_current_user, get_db
    from app.routes.requests import router as requests_router

    test_app = FastAPI()
    test_app.include_router(requests_router)

    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_user] = lambda: current_user
    return test_app


# ===========================================================================
# Req 13.1 — Rejection reason length validation
# ===========================================================================


class TestRejectionReasonLengthValidation:
    """
    Req 13.1: The Finance Reviewer must supply a rejection reason of 1–500 chars.
    Empty or >500-char reasons are rejected with 422.
    """

    @pytest.mark.asyncio
    async def test_reject_empty_reason_returns_422(self, db_session: AsyncSession):
        """Empty string reason must return 422 (Pydantic min_length constraint)."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": ""},
            )

        assert resp.status_code == 422, (
            f"Expected 422 for empty reason, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_reject_oversized_reason_returns_422(self, db_session: AsyncSession):
        """A reason longer than 500 characters must return 422."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "x" * 501},
            )

        assert resp.status_code == 422, (
            f"Expected 422 for 501-char reason, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_reject_missing_reason_returns_422(self, db_session: AsyncSession):
        """Omitting rejection_reason entirely must return 422."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={},
            )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_one_char_reason_accepted(self, db_session: AsyncSession):
        """A single-character reason is the minimum acceptable length."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "X"},
            )

        assert resp.status_code == 200, (
            f"Expected 200 for 1-char reason, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_reject_500_char_reason_accepted(self, db_session: AsyncSession):
        """A 500-character reason is exactly at the maximum boundary."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "y" * 500},
            )

        assert resp.status_code == 200, (
            f"Expected 200 for 500-char reason, got {resp.status_code}: {resp.text}"
        )


# ===========================================================================
# Req 13.4 — Self-rejection is rejected
# ===========================================================================


class TestSelfRejectionRejected:
    """
    Req 13.4: WHEN a Finance_Reviewer attempts to reject a Request whose
    uploader_id matches their own user_id, the system SHALL reject the action
    with a message stating self-rejection is not permitted.
    """

    @pytest.mark.asyncio
    async def test_reject_self_rejection_returns_403(self, db_session: AsyncSession):
        """Finance Reviewer who uploaded the Request is rejected with 403."""
        now = datetime.now(timezone.utc)
        client = Client(client_id=uuid.uuid4(), client_name="SelfCo", is_active=True)
        db_session.add(client)

        # The reviewer is also the uploader
        reviewer = User(
            user_id=uuid.uuid4(),
            username="self_reviewer_reject",
            role=UserRole.FINANCE_REVIEWER,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(reviewer)
        await db_session.flush()

        db_session.add(ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=reviewer.user_id,
            client_id=client.client_id,
            is_active=True,
            assigned_at=now,
        ))

        request = InvoiceRequest(
            request_id=uuid.uuid4(),
            client_id=client.client_id,
            uploader_id=reviewer.user_id,  # ← same as current_user
            request_type="standard",
            channel="upload",
            request_date=date.today(),
            status=RequestStatus.READY_FOR_REVIEW,
            flags=[],
            invoice_file_key="s3://bucket/self_reject.pdf",
            invoice_page_count=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(request)
        await db_session.commit()

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Duplicate submission"},
            )

        assert resp.status_code == 403
        detail = resp.json().get("detail", "").lower()
        assert "self-rejection" in detail or "self rejection" in detail, (
            f"Expected self-rejection message, got: {detail!r}"
        )

    @pytest.mark.asyncio
    async def test_reject_self_rejection_status_unchanged(self, db_session: AsyncSession):
        """Request status must remain READY_FOR_REVIEW after a self-rejection attempt."""
        now = datetime.now(timezone.utc)
        client = Client(client_id=uuid.uuid4(), client_name="SelfCo2", is_active=True)
        db_session.add(client)

        reviewer = User(
            user_id=uuid.uuid4(),
            username="self_reviewer_reject2",
            role=UserRole.FINANCE_REVIEWER,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(reviewer)
        await db_session.flush()

        db_session.add(ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=reviewer.user_id,
            client_id=client.client_id,
            is_active=True,
            assigned_at=now,
        ))

        request = InvoiceRequest(
            request_id=uuid.uuid4(),
            client_id=client.client_id,
            uploader_id=reviewer.user_id,
            request_type="standard",
            channel="upload",
            request_date=date.today(),
            status=RequestStatus.READY_FOR_REVIEW,
            flags=[],
            invoice_file_key="s3://bucket/self_reject2.pdf",
            invoice_page_count=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(request)
        await db_session.commit()

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Duplicate"},
            )

        result = await db_session.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == request.request_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.READY_FOR_REVIEW


# ===========================================================================
# Non-FINANCE_REVIEWER role is rejected
# ===========================================================================


class TestWrongRoleRejected:
    """
    Users without the FINANCE_REVIEWER role cannot reject Requests.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_role",
        [UserRole.CLIENT_PIC, UserRole.SERVICE_LEAD, UserRole.INTAKE_SUPPORT, UserRole.SYSTEM_ADMIN],
    )
    async def test_reject_wrong_role_returns_403(
        self, db_session: AsyncSession, bad_role: UserRole
    ):
        """Non-FINANCE_REVIEWER roles are rejected with 403."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        non_reviewer = _make_user(bad_role, f"bad_{bad_role.value.lower()}")

        app = _build_app(db_session, non_reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Wrong role test"},
            )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_role",
        [UserRole.CLIENT_PIC, UserRole.SERVICE_LEAD, UserRole.INTAKE_SUPPORT, UserRole.SYSTEM_ADMIN],
    )
    async def test_reject_wrong_role_no_status_change(
        self, db_session: AsyncSession, bad_role: UserRole
    ):
        """Request status must remain READY_FOR_REVIEW when wrong-role user attempts rejection."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        non_reviewer = _make_user(bad_role, f"bad2_{bad_role.value.lower()}")

        app = _build_app(db_session, non_reviewer)
        with TestClient(app) as client_http:
            client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Wrong role test"},
            )

        result = await db_session.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == request.request_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.READY_FOR_REVIEW


# ===========================================================================
# Wrong status returns 409
# ===========================================================================


class TestWrongStatusRejected:
    """
    Requests not in READY_FOR_REVIEW or EXCEPTION status cannot be rejected.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_status",
        [
            RequestStatus.QUEUED,
            RequestStatus.PROCESSING,
            RequestStatus.AMIGA_REVIEWED,
            RequestStatus.SENT_TO_CLIENT,
        ],
    )
    async def test_reject_wrong_status_returns_409(
        self, db_session: AsyncSession, bad_status: RequestStatus
    ):
        """Requests in non-rejectable statuses return 409."""
        seeded = await _seed_base(db_session, request_status=bad_status)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Status test"},
            )

        assert resp.status_code == 409, (
            f"Expected 409 for status {bad_status.value}, got {resp.status_code}"
        )


# ===========================================================================
# Req 13.1 & 13.2 — Valid rejection from READY_FOR_REVIEW and EXCEPTION status
# ===========================================================================


class TestValidRejectionTransition:
    """
    Req 13.1 / 13.2: A Finance Reviewer (not the uploader) can reject a
    Request in READY_FOR_REVIEW or EXCEPTION status. Status transitions to
    REJECTED and the Audit Trail records the reason, reviewer identity, and
    UTC timestamp.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "initial_status",
        [RequestStatus.READY_FOR_REVIEW, RequestStatus.EXCEPTION],
    )
    async def test_reject_valid_transitions_to_rejected(
        self, db_session: AsyncSession, initial_status: RequestStatus
    ):
        """Successful rejection returns 200 and status moves to REJECTED."""
        seeded = await _seed_base(db_session, request_status=initial_status)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Invoice does not match PO."},
            )

        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        result = await db_session.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == request.request_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.REJECTED, (
            f"Expected REJECTED status after rejection, got {refreshed.status}"
        )

    @pytest.mark.asyncio
    async def test_reject_valid_response_body(self, db_session: AsyncSession):
        """Successful rejection returns JSON with status='rejected' and request_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Missing required attachments."},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "rejected"
        assert body.get("request_id") == str(request.request_id)

    @pytest.mark.asyncio
    async def test_reject_valid_creates_rejected_audit_event(self, db_session: AsyncSession):
        """A REJECTED AuditEvent must be persisted after successful rejection."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Calculation error too large."},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.REJECTED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            "Exactly one REJECTED audit event must be recorded on rejection"
        )

    @pytest.mark.asyncio
    async def test_reject_valid_audit_event_records_reviewer_identity(
        self, db_session: AsyncSession
    ):
        """REJECTED AuditEvent actor_id must be the reviewer's user_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Vendor not approved."},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.REJECTED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        assert event.actor_id == reviewer.user_id, (
            "REJECTED AuditEvent.actor_id must be the reviewer's user_id"
        )

    @pytest.mark.asyncio
    async def test_reject_valid_audit_event_payload_contains_reason_and_reviewer_id(
        self, db_session: AsyncSession
    ):
        """REJECTED AuditEvent payload must include the rejection reason and reviewer_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]
        reason = "Amount exceeds purchase order by more than 10%."

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": reason},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.REJECTED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        payload = event.payload
        assert "reason" in payload, "REJECTED AuditEvent payload must contain 'reason'"
        assert payload["reason"] == reason
        assert "reviewer_id" in payload, "REJECTED AuditEvent payload must contain 'reviewer_id'"
        assert payload["reviewer_id"] == str(reviewer.user_id)

    @pytest.mark.asyncio
    async def test_reject_valid_audit_event_has_timestamp(self, db_session: AsyncSession):
        """REJECTED AuditEvent must have a non-null occurred_at timestamp."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": "Insufficient documentation."},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.REJECTED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        assert event.occurred_at is not None, (
            "REJECTED AuditEvent must record a UTC timestamp"
        )

    @pytest.mark.asyncio
    async def test_reject_valid_persists_rejection_reason_on_request(
        self, db_session: AsyncSession
    ):
        """Rejection reason must be persisted on the InvoiceRequest row (Req 13.3)."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]
        reason = "Tax codes not aligned with current regulations."

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/reject",
                json={"rejection_reason": reason},
            )

        assert resp.status_code == 200

        result = await db_session.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == request.request_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.rejection_reason == reason, (
            f"Expected rejection_reason '{reason}', got {refreshed.rejection_reason!r}"
        )
