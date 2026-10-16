"""Organization controls exercised through HTTP and real persisted password/session state."""
# ruff: noqa: F811

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.dependencies import get_db
from app.models.user import User, UserRole
from app.models.request import InvoiceRequest, RequestStatus
from app.models.client import Client
from app.models.audit import AuditEvent
from app.routes.auth import router as auth_router, _attempts
from app.routes.users import router as users_router
from app.routes.clients import router as clients_router
from app.routes.workspace import router as workspace_router
from app.routes.operations import router as operations_router
from app.routes.requests import router as requests_router
from app.routes.voucher import router as voucher_router
from app.routes.refinement import router as refinement_router
from app.services.passwords import hash_password
from app.auth import create_access_token
from app.permissions import ROLE_PERMISSIONS, ALL_PERMISSIONS
from app.repositories.user_repository import user_repository
from scripts.manage_accounts import seed_demo
from tests.test_approval import db_session  # noqa: F401
from tests.test_manual_workflow import seed, valid_pdf

PASSWORD = "Temporary-Password-2026"


async def account(db, role, temporary=False):
    now = datetime.now(timezone.utc)
    user = User(
        username=f"{role.lower()}_{uuid.uuid4().hex[:8]}",
        role=UserRole(role),
        is_active=True,
        full_name=f"Test {role}",
        job_title=role,
        created_at=now,
        updated_at=now,
        password_hash=hash_password(PASSWORD),
        must_change_password=temporary,
    )
    db.add(user)
    await db.commit()
    return user


async def http(db):
    app = FastAPI()
    for router in (
        auth_router,
        users_router,
        clients_router,
        workspace_router,
        operations_router,
        requests_router,
        voucher_router,
        refinement_router,
    ):
        app.include_router(router)

    async def database():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            # Each HTTP call has its own session in production. Restore shared test objects after rollback.
            for obj in list(db.identity_map.values()):
                await db.refresh(obj)
            raise

    app.dependency_overrides[get_db] = database
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def bearer(user):
    return {"Authorization": "Bearer " + create_access_token(user.user_id, user.role.value, user.token_version or 0)}


async def test_login_change_reset_and_session_revocation(db_session):
    _attempts.clear()
    user = await account(db_session, "CLIENT_PIC", temporary=True)
    admin = await account(db_session, "SUPER_ADMIN")
    async with await http(db_session) as client:
        assert (await client.post("/auth/token", data={"username": user.username})).status_code == 422
        assert (
            await client.post("/auth/token", data={"username": user.username, "password": "incorrect"})
        ).status_code == 401
        login = await client.post("/auth/token", data={"username": user.username, "password": PASSWORD})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        me = await client.get("/auth/me", headers=headers)
        assert me.json()["must_change_password"] and "upload" in me.json()["permissions"]
        assert "password_hash" not in me.json()
        assert (await client.get("/requests", headers=headers)).status_code == 403
        changed = await client.post(
            "/auth/change-password",
            headers=headers,
            json={"current_password": PASSWORD, "new_password": "Changed-Password-2026"},
        )
        assert changed.status_code == 200
        assert (await client.get("/auth/me", headers=headers)).status_code == 401
        login = await client.post("/auth/token", data={"username": user.username, "password": "Changed-Password-2026"})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        assert (await client.get("/requests", headers=headers)).status_code == 200
        reset = await client.post(
            f"/users/{user.user_id}/reset-password", headers=bearer(admin), json={"temporary_password": PASSWORD}
        )
        assert reset.status_code == 200
        assert (await client.get("/auth/me", headers=headers)).status_code == 401
        assert user.password_hash.startswith("$argon2id$")
        events = (await db_session.scalars(select(AuditEvent))).all()
        assert all(PASSWORD not in str(e.payload) for e in events)


async def test_login_throttle_is_bounded(db_session):
    _attempts.clear()
    async with await http(db_session) as client:
        for _ in range(10):
            assert (
                await client.post("/auth/token", data={"username": "unknown", "password": "bad"})
            ).status_code == 401
        assert (await client.post("/auth/token", data={"username": "unknown", "password": "bad"})).status_code == 429
    _attempts.clear()


@pytest.mark.parametrize("role", [*ROLE_PERMISSIONS, "SUPER_ADMIN"])
async def test_effective_permissions_and_user_management_guard(db_session, role):
    user = await account(db_session, role)
    async with await http(db_session) as client:
        me = (await client.get("/auth/me", headers=bearer(user))).json()
        expected = ALL_PERMISSIONS if role == "SUPER_ADMIN" else ROLE_PERMISSIONS[role]
        assert set(me["permissions"]) == expected
        response = await client.post(
            "/users",
            headers=bearer(user),
            json={
                "username": "created",
                "full_name": "Created Person",
                "job_title": "Employee",
                "role": "CLIENT_PIC",
                "temporary_password": PASSWORD,
                "assignments": [],
            },
        )
        assert response.status_code == (201 if role in ("SUPER_ADMIN", "SYSTEM_ADMIN") else 403)


async def test_delegated_admin_cannot_escalate_or_manage_admins(db_session):
    admin = await account(db_session, "SYSTEM_ADMIN")
    root = await account(db_session, "SUPER_ADMIN")
    user = await account(db_session, "CLIENT_PIC")
    async with await http(db_session) as client:
        for role in ("SUPER_ADMIN", "SYSTEM_ADMIN"):
            assert (
                await client.patch(f"/users/{user.user_id}/role", headers=bearer(admin), json={"role": role})
            ).status_code == 403
        for path, payload in (("role", {"role": "CLIENT_PIC"}), ("status", {"is_active": False})):
            assert (
                await client.patch(f"/users/{root.user_id}/{path}", headers=bearer(admin), json=payload)
            ).status_code == 403
        assert (
            await client.post(
                f"/users/{root.user_id}/reset-password", headers=bearer(admin), json={"temporary_password": PASSWORD}
            )
        ).status_code == 403
        assert (
            await client.patch(f"/users/{root.user_id}/status", headers=bearer(root), json={"is_active": False})
        ).status_code == 409
        assert (
            await client.patch(f"/users/{root.user_id}/role", headers=bearer(root), json={"role": "CLIENT_PIC"})
        ).status_code == 409
        assert (
            await client.patch(f"/users/{user.user_id}/role", headers=bearer(admin), json={"role": "FINANCE_REVIEWER"})
        ).status_code == 200


async def test_scoped_assignments_allocation_deadlines_and_revocation(db_session):
    data = await seed(db_session)
    lead = await account(db_session, "SERVICE_LEAD")
    root = await account(db_session, "SUPER_ADMIN")
    await user_repository.add_assignment(db_session, lead.user_id, data["client"].client_id)
    other = Client(client_id=uuid.uuid4(), client_name="Other company", is_active=True)
    db_session.add(other)
    await db_session.commit()
    req = data["request"]
    req.due_date = date.today() - timedelta(days=2)
    await db_session.commit()
    async with await http(db_session) as client:
        for actor in (lead, root):
            response = await client.patch(
                f"/requests/{req.request_id}/allocation",
                headers=bearer(actor),
                json={"assigned_reviewer": str(data["reviewer"].user_id)},
            )
            assert response.status_code == 200 and response.json()["overdue"]
        assert (
            await client.patch(
                f"/requests/{req.request_id}/allocation",
                headers=bearer(lead),
                json={"assigned_reviewer": str(data["uploader"].user_id)},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/users/{data['reviewer'].user_id}/assignments",
                headers=bearer(lead),
                json={"client_id": str(other.client_id)},
            )
        ).status_code == 403
        assert len((await client.get("/requests?overdue_only=true", headers=bearer(lead))).json()) == 1
        assert (await client.get(f"/requests?client_id={other.client_id}", headers=bearer(lead))).json() == []
        assert (
            await client.delete(f"/users/{data['reviewer'].user_id}/assignments/{req.client_id}", headers=bearer(root))
        ).status_code == 204
        assert (await client.get(f"/requests/{req.request_id}", headers=bearer(data["reviewer"]))).status_code == 403
        # The same account's existing token observes its role changes immediately.
        old = bearer(data["uploader"])
        await client.patch(
            f"/users/{data['uploader'].user_id}/role", headers=bearer(root), json={"role": "INTAKE_SUPPORT"}
        )
        assert (await client.get(f"/requests/{req.request_id}/history", headers=old)).status_code == 403


async def test_super_admin_self_approval_keeps_warnings_and_audit(db_session):
    data = await seed(db_session)
    user = data["uploader"]
    user.role = UserRole.SUPER_ADMIN
    await db_session.commit()
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = valid_pdf()
    async with await http(db_session) as client:
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
        ):
            response = await client.post(f"/requests/{data['request'].request_id}/approve", headers=bearer(user))
        assert response.status_code == 200, response.text
        assert "MISSING_EVIDENCE" in data["request"].flags
        assert "Persetujuan pihak klien" in renderer.HTML.call_args.kwargs["string"]
        timeline = await client.get(f"/requests/{data['request'].request_id}/history?limit=1", headers=bearer(user))
        assert timeline.status_code == 200 and timeline.json()["next_offset"] == 1
        all_events = await client.get(f"/requests/{data['request'].request_id}/history", headers=bearer(user))
        approved = next(e for e in all_events.json()["items"] if e["event_type"] == "APPROVED")
        assert approved["payload"]["outstanding_issues"] and approved["actor_name"] == user.full_name


async def test_demo_seed_is_idempotent_and_passwords_are_temporary(db_session):
    first = await seed_demo(db_session)
    assert len(first) == 8
    await db_session.commit()
    second = await seed_demo(db_session)
    assert second == []
    users = (await db_session.scalars(select(User))).all()
    assert all(u.must_change_password and u.password_hash.startswith("$argon2id$") for u in users)


async def test_metrics_percentiles_and_breach_threshold(db_session):
    root = await account(db_session, "SUPER_ADMIN")
    async with await http(db_session) as client:
        for duration in (100, 2000, 2001):
            assert (
                await client.post(
                    "/telemetry/page-load", headers=bearer(root), json={"page": "dashboard", "duration_ms": duration}
                )
            ).status_code == 201
        metrics = (await client.get("/telemetry/metrics", headers=bearer(root))).json()[0]
        assert metrics["p50"] <= metrics["p90"] <= metrics["p99"]
        assert metrics["breaches"] == 1 and metrics["breach_rate"] == pytest.approx(100 / 3)
        breaches = (await client.get("/telemetry/breaches", headers=bearer(root))).json()
        assert len(breaches) == 1 and breaches[0]["threshold_ms"] == 2000


async def test_duplicate_warnings_are_confined_to_client(db_session):
    from app.models.draft import Draft, ExtractionStatus
    from app.models.invoice_fields import HeaderFields
    from app.services.advisory_validation import validate_request

    data = await seed(db_session)
    req = data["request"]
    header = await db_session.scalar(select(HeaderFields).join(Draft).where(Draft.request_id == req.request_id))
    other = Client(client_id=uuid.uuid4(), client_name="Separate tenant", is_active=True)
    db_session.add(other)
    await db_session.flush()
    clone = InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=other.client_id,
        uploader_id=req.uploader_id,
        request_type="standard",
        channel="upload",
        request_date=date.today(),
        status=RequestStatus.READY_FOR_REVIEW,
        flags=[],
        invoice_file_key="other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(clone)
    await db_session.flush()
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=clone.request_id,
        ai_label_visible=True,
        extraction_status=ExtractionStatus.COMPLETE,
    )
    db_session.add(draft)
    await db_session.flush()
    db_session.add(
        HeaderFields(draft_id=draft.draft_id, vendor_name=header.vendor_name, invoice_number=header.invoice_number)
    )
    await validate_request(db_session, req)
    assert "POSSIBLE_DUPLICATE" not in req.flags
    clone.client_id = req.client_id
    await validate_request(db_session, req)
    assert "POSSIBLE_DUPLICATE" in req.flags
    clone.status = RequestStatus.REJECTED
    await validate_request(db_session, req)
    assert "POSSIBLE_DUPLICATE" not in req.flags


async def test_upload_extract_submit_flagged_approve_download_history(db_session):
    from app.models.extraction_job import ExtractionJob
    from app.models.invoice_fields import HeaderFields
    from app.models.draft import Draft
    from app.services.extraction_worker import _process_job
    from app.schemas.extraction import ExtractionResult, HeaderFieldsSchema, LineItemSchema

    data = await seed(db_session)
    # Duplicate invoice identity guarantees all three issue types on the new request.
    source_header = await db_session.scalar(
        select(HeaderFields).join(Draft).where(Draft.request_id == data["request"].request_id)
    )
    async with await http(db_session) as client:
        with patch("app.routes.requests.file_store.upload", AsyncMock()):
            response = await client.post(
                "/requests",
                headers=bearer(data["uploader"]),
                data={
                    "client_id": str(data["client"].client_id),
                    "request_date": "2026-10-15",
                    "voucher_purpose": "Acceptance test",
                    "approvers": '[{"full_name":"Client Manager","job_title":"Manager"}]',
                },
                files=[("files", ("invoice.pdf", valid_pdf(), "application/pdf"))],
            )
        assert response.status_code == 201, response.text
        request_id = uuid.UUID(response.json()["request_id"])
        job = await db_session.scalar(select(ExtractionJob).where(ExtractionJob.request_id == request_id))

        class Context:
            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *args):
                return False

        extracted = ExtractionResult(
            header=HeaderFieldsSchema(
                vendor_name=source_header.vendor_name,
                invoice_number=source_header.invoice_number,
                subtotal=10,
                total_amount=99,
                currency="IDR",
            ),
            line_items=[
                LineItemSchema(
                    sequence_number=i, description=f"Item {i}", quantity=1, unit="each", unit_price=10, line_total=10
                )
                for i in range(1, 4)
            ],
            payment_details=None,
        )
        provider = MagicMock()
        provider.extract = AsyncMock(return_value=extracted)
        with (
            patch("app.services.extraction_worker.AsyncSessionLocal", return_value=Context()),
            patch("app.services.extraction_worker.get_extraction_provider", return_value=provider),
            patch("app.services.extraction_worker.file_store.download", AsyncMock(return_value=valid_pdf())),
        ):
            await _process_job(job)
        detail = (await client.get(f"/requests/{request_id}", headers=bearer(data["uploader"]))).json()
        assert detail["status"] == "EXCEPTION" and detail["workflow_stage"] == "REFINEMENT"
        assert len(detail["draft"]["line_items"]) == 3
        assert set(detail["flags"]) == {"MISSING_EVIDENCE", "POSSIBLE_DUPLICATE", "CALC_DISCREPANCY"}
        assert (
            await client.post(f"/requests/{request_id}/submit-review", headers=bearer(data["uploader"]))
        ).status_code == 200
        renderer = MagicMock()
        renderer.HTML.return_value.write_pdf.return_value = valid_pdf()
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
        ):
            approved = await client.post(f"/requests/{request_id}/approve", headers=bearer(data["reviewer"]))
        assert approved.status_code == 200, approved.text
        with patch("app.routes.voucher.file_store.download", AsyncMock(return_value=valid_pdf())):
            assert (
                await client.get(f"/requests/{request_id}/voucher-pdf", headers=bearer(data["uploader"]))
            ).status_code == 200
        timeline = (await client.get(f"/requests/{request_id}/history", headers=bearer(data["uploader"]))).json()[
            "items"
        ]
        kinds = {e["event_type"] for e in timeline}
        assert {"REQUEST_CREATED", "FLAG_APPLIED", "APPROVED", "VOUCHER_GENERATED", "VOUCHER_DOWNLOADED"} <= kinds
        assert next(e for e in timeline if e["event_type"] == "APPROVED")["payload"]["outstanding_issues"]
        assert (await client.get(f"/requests/{request_id}", headers=bearer(data["uploader"]))).json()[
            "status"
        ] == "SENT_TO_CLIENT"


async def test_system_admin_can_manage_accounts_but_client_records_stay_scoped(db_session):
    data = await seed(db_session)
    admin = await account(db_session, "SYSTEM_ADMIN")
    async with await http(db_session) as client:
        response = await client.post(
            "/users",
            headers=bearer(admin),
            json={
                "username": "not_created",
                "full_name": "No Escalation",
                "job_title": "Test",
                "role": "FINANCE_REVIEWER",
                "temporary_password": PASSWORD,
                "assignments": [{"client_id": str(data["client"].client_id)}],
            },
        )
        assert response.status_code == 201
        assert (await client.get("/requests", headers=bearer(admin))).json() == []
        assert (await client.get(f"/requests/{data['request'].request_id}", headers=bearer(admin))).status_code == 403


async def test_deactivation_revokes_existing_sessions(db_session):
    root = await account(db_session, "SUPER_ADMIN")
    user = await account(db_session, "CLIENT_PIC")
    headers = bearer(user)
    async with await http(db_session) as client:
        assert (await client.get("/auth/me", headers=headers)).status_code == 200
        assert (
            await client.patch(f"/users/{user.user_id}/status", headers=bearer(root), json={"is_active": False})
        ).status_code == 200
        assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_user_admin_consolidation_preserves_credentials_and_expires_sessions(db_session):
    from sqlalchemy import update
    user = await account(db_session, "USER_ADMIN")
    username, password_hash = user.username, user.password_hash
    old_session = bearer(user)
    # Exercise the account-data operation used by migration 0014.
    await db_session.execute(update(User).where(User.role == UserRole.USER_ADMIN).values(
        role=UserRole.SYSTEM_ADMIN, token_version=User.token_version + 1))
    await db_session.commit()
    await db_session.refresh(user)
    assert user.username == username and user.password_hash == password_hash
    async with await http(db_session) as client:
        assert (await client.get("/auth/me", headers=old_session)).status_code == 401
        current = await client.get("/auth/me", headers=bearer(user))
        assert current.status_code == 200 and current.json()["role"] == "SYSTEM_ADMIN"
        assert "users" in current.json()["permissions"] and "approve" not in current.json()["permissions"]


async def test_document_timing_metrics_stay_client_scoped(db_session):
    from app.models.telemetry import TelemetryRecord, MetricType
    from app.models.client_assignment import ClientAssignment
    data = await seed(db_session)
    actor = await account(db_session, "SYSTEM_ADMIN")
    db_session.add(ClientAssignment(user_id=actor.user_id, client_id=data["client"].client_id,
                                   is_active=True, assigned_at=datetime.now(timezone.utc)))
    for subject in (str(data["request"].request_id), str(uuid.uuid4())):
        db_session.add(TelemetryRecord(record_id=uuid.uuid4(), metric_type=MetricType.EXTRACTION_DOCUMENT_DURATION,
            subject_id=f"{subject}/document/1", duration_ms=1000, page_count=1, is_breach=False,
            breach_threshold_ms=15000, measured_at=datetime.now(timezone.utc)))
    await db_session.commit()
    async with await http(db_session) as client:
        response = await client.get("/telemetry/metrics", headers=bearer(actor))
    assert response.status_code == 200
    documents = next(row for row in response.json() if row["metric_type"] == "EXTRACTION_DOCUMENT_DURATION")
    assert documents["count"] == 1


@pytest.mark.parametrize("role", ["SYSTEM_ADMIN", "SUPER_ADMIN", "SERVICE_LEAD", "FINANCE_REVIEWER", "CLIENT_PIC"])
async def test_company_removal_preserves_history_and_revokes_assignments(db_session, role):
    from app.models.client_assignment import ClientAssignment

    data = await seed(db_session)
    actor = await account(db_session, role)
    company_id = data["request"].client_id
    async with await http(db_session) as client:
        response = await client.delete(f"/clients/{company_id}", headers=bearer(actor))
        if role not in ("SYSTEM_ADMIN", "SUPER_ADMIN"):
            assert response.status_code == 403
            assert (await db_session.get(Client, company_id)).is_active
            return
        assert response.status_code == 204
        assert not (await db_session.get(Client, company_id)).is_active
        assert await db_session.get(InvoiceRequest, data["request"].request_id) is not None
        assignments = (await db_session.scalars(select(ClientAssignment).where(ClientAssignment.client_id == company_id))).all()
        assert assignments and all(not a.is_active and a.revoked_at for a in assignments)
        assert company_id not in [uuid.UUID(c["client_id"]) for c in (await client.get("/clients/all", headers=bearer(actor))).json()]
        assert (await client.delete(f"/clients/{company_id}", headers=bearer(actor))).status_code == 204
        events = (await db_session.scalars(select(AuditEvent))).all()
        assert sum(e.payload.get("action") == "COMPANY_REMOVED" for e in events) == 1
        assert (await client.delete(f"/clients/{uuid.uuid4()}", headers=bearer(actor))).status_code == 404


async def test_cannot_remove_own_account_even_with_another_super_admin(db_session):
    actor = await account(db_session, "SUPER_ADMIN")
    await account(db_session, "SUPER_ADMIN")
    async with await http(db_session) as client:
        response = await client.patch(f"/users/{actor.user_id}/status", headers=bearer(actor), json={"is_active": False})
        assert response.status_code == 409
        assert actor.is_active
