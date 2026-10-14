"""
Tests for the Finance Reviewer approval endpoint (Task 25.2).

POST /requests/{request_id}/approve

Requirements tested:
  - Req 12.1: Valid approval by a Finance Reviewer transitions status to
              AMIGA_REVIEWED and records approver identity + timestamp in the
              Audit Trail.
  - Req 12.2: Self-approval is rejected with 403 and a meaningful message.
  - Req 12.3: A user without the FINANCE_REVIEWER role is rejected with 403.
  - Req 12.4: A SYSTEM_ADMIN attempt is rejected AND an
              UNAUTHORIZED_APPROVAL_ATTEMPT AuditEvent is written.
  - Req 12.5: A Request in EXCEPTION status cannot be approved; returns
              409 with a message about unresolved exceptions.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

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
# Mirrors the approach used in conftest.py and test_voucher_pdf.py.
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
    Seed the minimum data needed for approval tests:
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
    Minimal FastAPI app with only the requests router, the DB session and
    current user both overridden so no real credentials are needed.
    """
    from app.dependencies import get_current_user, get_db
    from app.routes.requests import router as requests_router

    app = FastAPI()
    app.include_router(requests_router)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


# ===========================================================================
# Req 12.2 — Self-approval is rejected
# ===========================================================================


class TestSelfApprovalRejected:
    """
    Req 12.2: WHEN a Finance_Reviewer attempts to approve a Request whose
    uploader_id matches the Finance_Reviewer's user_id, the system SHALL reject
    with a message stating self-approval is not permitted.
    """

    @pytest.mark.asyncio
    async def test_approval_self_approval_rejected(
        self, db_session: AsyncSession
    ):
        """Finance Reviewer who uploaded the Request is rejected with 403."""
        now = datetime.now(timezone.utc)
        client = Client(client_id=uuid.uuid4(), client_name="SelfCo", is_active=True)
        db_session.add(client)

        # The reviewer is also the uploader
        reviewer = User(
            user_id=uuid.uuid4(),
            username="self_reviewer",
            role=UserRole.FINANCE_REVIEWER,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db_session.add(reviewer)
        await db_session.flush()

        # Assign reviewer to client
        db_session.add(ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=reviewer.user_id,
            client_id=client.client_id,
            is_active=True,
            assigned_at=now,
        ))

        # Request uploaded by the reviewer themselves
        request = InvoiceRequest(
            request_id=uuid.uuid4(),
            client_id=client.client_id,
            uploader_id=reviewer.user_id,   # ← same as current_user
            request_type="standard",
            channel="upload",
            request_date=date.today(),
            status=RequestStatus.READY_FOR_REVIEW,
            flags=[],
            invoice_file_key="s3://bucket/self.pdf",
            invoice_page_count=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(request)
        await db_session.commit()

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(
                f"/requests/{request.request_id}/approve"
            )

        assert resp.status_code == 403
        detail = resp.json().get("detail", "").lower()
        assert "self-approval" in detail or "self approval" in detail, (
            f"Expected self-approval rejection message, got: {detail!r}"
        )

    @pytest.mark.asyncio
    async def test_approval_self_approval_status_unchanged(
        self, db_session: AsyncSession
    ):
        """Request status must remain READY_FOR_REVIEW after a self-approval attempt."""
        now = datetime.now(timezone.utc)
        client = Client(client_id=uuid.uuid4(), client_name="SelfCo2", is_active=True)
        db_session.add(client)

        reviewer = User(
            user_id=uuid.uuid4(),
            username="self_reviewer2",
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
            invoice_file_key="s3://bucket/self2.pdf",
            invoice_page_count=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(request)
        await db_session.commit()

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == request.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.READY_FOR_REVIEW, (
            "Status must stay READY_FOR_REVIEW after rejected self-approval"
        )


# ===========================================================================
# Req 12.3 — Wrong role is rejected
# ===========================================================================


class TestWrongRoleRejected:
    """
    Req 12.3: WHEN a user without FINANCE_REVIEWER role attempts to approve,
    the system SHALL reject with a message about lacking authority.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_role",
        [UserRole.CLIENT_PIC, UserRole.SERVICE_LEAD, UserRole.INTAKE_SUPPORT],
    )
    async def test_approval_wrong_role_rejected(
        self, db_session: AsyncSession, bad_role: UserRole
    ):
        """Non-FINANCE_REVIEWER roles are rejected with 403."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        non_reviewer = _make_user(bad_role, f"bad_{bad_role.value.lower()}")

        app = _build_app(db_session, non_reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 403
        detail = resp.json().get("detail", "").lower()
        assert "authority" in detail, (
            f"Expected 'authority' in rejection message for role {bad_role.value}, "
            f"got: {detail!r}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_role",
        [UserRole.CLIENT_PIC, UserRole.SERVICE_LEAD, UserRole.INTAKE_SUPPORT],
    )
    async def test_approval_wrong_role_no_status_change(
        self, db_session: AsyncSession, bad_role: UserRole
    ):
        """Request status must remain unchanged when a wrong-role user tries to approve."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        non_reviewer = _make_user(bad_role, f"bad2_{bad_role.value.lower()}")

        app = _build_app(db_session, non_reviewer)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == request.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.READY_FOR_REVIEW


# ===========================================================================
# Req 12.5 — EXCEPTION status blocks approval
# ===========================================================================


class TestExceptionStatusBlocked:
    """
    Req 12.5: WHILE a Request is in EXCEPTION status, the system SHALL prevent
    approval and display an error message about unresolved exceptions.
    """

    @pytest.mark.asyncio
    async def test_approval_exception_status_blocked(
        self, db_session: AsyncSession
    ):
        """Request in EXCEPTION status returns 409 with an unresolved-exceptions message."""
        seeded = await _seed_base(
            db_session, request_status=RequestStatus.EXCEPTION
        )
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 409, (
            f"Expected 409 for EXCEPTION status request, got {resp.status_code}"
        )
        detail = resp.json().get("detail", "").lower()
        assert "exception" in detail or "unresolved" in detail, (
            f"Expected message about unresolved exceptions, got: {detail!r}"
        )

    @pytest.mark.asyncio
    async def test_approval_exception_status_unchanged(
        self, db_session: AsyncSession
    ):
        """Request status must remain EXCEPTION after blocked approval attempt."""
        seeded = await _seed_base(
            db_session, request_status=RequestStatus.EXCEPTION
        )
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == request.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.EXCEPTION

    @pytest.mark.asyncio
    async def test_approval_exception_flag_also_blocked(
        self, db_session: AsyncSession
    ):
        """
        A Request with an exception flag (e.g. CALCULATION_MISMATCH) but not yet
        in EXCEPTION status is also blocked.
        """
        seeded = await _seed_base(
            db_session,
            request_status=RequestStatus.READY_FOR_REVIEW,
            request_flags=["CALCULATION_MISMATCH"],
        )
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 409


# ===========================================================================
# Req 12.1 — Valid approval transitions to AMIGA_REVIEWED
# ===========================================================================


class TestValidApprovalTransition:
    """
    Req 12.1: A Finance Reviewer (not the uploader) approving a READY_FOR_REVIEW
    Request SHALL transition status to AMIGA_REVIEWED and record approver identity
    + timestamp in the Audit Trail.
    """

    @pytest.mark.asyncio
    async def test_approval_valid_transitions_to_amiga_reviewed(
        self, db_session: AsyncSession
    ):
        """Successful approval returns 200 and status moves to AMIGA_REVIEWED."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == request.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.AMIGA_REVIEWED, (
            f"Expected AMIGA_REVIEWED after approval, got {refreshed.status}"
        )

    @pytest.mark.asyncio
    async def test_approval_valid_creates_approved_audit_event(
        self, db_session: AsyncSession
    ):
        """An APPROVED AuditEvent must be persisted after successful approval."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.APPROVED,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            "Exactly one APPROVED audit event must be recorded on approval"
        )

    @pytest.mark.asyncio
    async def test_approval_valid_audit_event_records_approver_identity(
        self, db_session: AsyncSession
    ):
        """APPROVED AuditEvent must capture the approver's user_id as actor_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.APPROVED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        assert event.actor_id == reviewer.user_id, (
            "APPROVED AuditEvent.actor_id must be the approver's user_id"
        )

    @pytest.mark.asyncio
    async def test_approval_valid_audit_event_payload_contains_approver_id(
        self, db_session: AsyncSession
    ):
        """APPROVED AuditEvent payload must include approver_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.APPROVED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        payload = event.payload
        assert "approver_id" in payload, (
            "APPROVED AuditEvent payload must contain 'approver_id'"
        )
        assert payload["approver_id"] == str(reviewer.user_id)

    @pytest.mark.asyncio
    async def test_approval_valid_audit_event_has_timestamp(
        self, db_session: AsyncSession
    ):
        """APPROVED AuditEvent must have a non-null occurred_at timestamp."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.request_id == request.request_id,
                AuditEvent.event_type == AuditEventType.APPROVED,
            )
        )
        event = result.scalars().first()
        assert event is not None
        assert event.occurred_at is not None, (
            "APPROVED AuditEvent must record a UTC timestamp"
        )

    @pytest.mark.asyncio
    async def test_approval_valid_response_body(
        self, db_session: AsyncSession
    ):
        """Successful approval returns JSON with status='approved' and request_id."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]
        reviewer: User = seeded["reviewer"]

        app = _build_app(db_session, reviewer)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "approved"
        assert body.get("request_id") == str(request.request_id)


# ===========================================================================
# Req 12.4 — SYSTEM_ADMIN attempt is logged as UNAUTHORIZED_APPROVAL_ATTEMPT
# ===========================================================================


class TestSystemAdminAttemptLogged:
    """
    Req 12.4: WHEN the System_Admin attempts to approve a Request via the API,
    the system SHALL reject the action AND record the unauthorized attempt in
    the Audit Trail (UNAUTHORIZED_APPROVAL_ATTEMPT).
    """

    @pytest.mark.asyncio
    async def test_approval_system_admin_attempt_rejected(
        self, db_session: AsyncSession
    ):
        """SYSTEM_ADMIN approval attempt returns 403."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        sys_admin = _make_user(UserRole.SYSTEM_ADMIN, "sysadmin1")

        app = _build_app(db_session, sys_admin)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_approval_system_admin_attempt_logged(
        self, db_session: AsyncSession
    ):
        """
        Req 12.4: SYSTEM_ADMIN approval attempt must persist an
        UNAUTHORIZED_APPROVAL_ATTEMPT AuditEvent.
        """
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        sys_admin = _make_user(UserRole.SYSTEM_ADMIN, "sysadmin2")

        app = _build_app(db_session, sys_admin)
        with TestClient(app) as client_http:
            resp = client_http.post(f"/requests/{request.request_id}/approve")

        assert resp.status_code == 403

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.UNAUTHORIZED_APPROVAL_ATTEMPT,
                AuditEvent.request_id == request.request_id,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1, (
            "An UNAUTHORIZED_APPROVAL_ATTEMPT audit event must be recorded when "
            "a SYSTEM_ADMIN tries to approve"
        )

    @pytest.mark.asyncio
    async def test_approval_system_admin_audit_event_records_actor(
        self, db_session: AsyncSession
    ):
        """
        UNAUTHORIZED_APPROVAL_ATTEMPT AuditEvent must record the SYSTEM_ADMIN's
        user_id as actor_id.
        """
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        sys_admin = _make_user(UserRole.SYSTEM_ADMIN, "sysadmin3")

        app = _build_app(db_session, sys_admin)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.UNAUTHORIZED_APPROVAL_ATTEMPT,
                AuditEvent.request_id == request.request_id,
            )
        )
        event = result.scalars().first()
        assert event is not None
        assert event.actor_id == sys_admin.user_id, (
            "UNAUTHORIZED_APPROVAL_ATTEMPT AuditEvent.actor_id must be the "
            "SYSTEM_ADMIN's user_id"
        )

    @pytest.mark.asyncio
    async def test_approval_system_admin_audit_event_payload_contains_role(
        self, db_session: AsyncSession
    ):
        """
        UNAUTHORIZED_APPROVAL_ATTEMPT payload must include the attempted role
        so it is auditable.
        """
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        sys_admin = _make_user(UserRole.SYSTEM_ADMIN, "sysadmin4")

        app = _build_app(db_session, sys_admin)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.UNAUTHORIZED_APPROVAL_ATTEMPT,
                AuditEvent.request_id == request.request_id,
            )
        )
        event = result.scalars().first()
        assert event is not None
        payload = event.payload
        assert "role" in payload, (
            "UNAUTHORIZED_APPROVAL_ATTEMPT payload must contain 'role'"
        )
        assert payload["role"] == "SYSTEM_ADMIN"

    @pytest.mark.asyncio
    async def test_approval_system_admin_no_status_change(
        self, db_session: AsyncSession
    ):
        """Request status must not change when SYSTEM_ADMIN tries to approve."""
        seeded = await _seed_base(db_session)
        request: InvoiceRequest = seeded["request"]

        sys_admin = _make_user(UserRole.SYSTEM_ADMIN, "sysadmin5")

        app = _build_app(db_session, sys_admin)
        with TestClient(app) as client_http:
            client_http.post(f"/requests/{request.request_id}/approve")

        result = await db_session.execute(
            select(InvoiceRequest).where(
                InvoiceRequest.request_id == request.request_id
            )
        )
        refreshed = result.scalar_one()
        assert refreshed.status == RequestStatus.READY_FOR_REVIEW, (
            "Request status must stay READY_FOR_REVIEW after SYSTEM_ADMIN attempt"
        )
