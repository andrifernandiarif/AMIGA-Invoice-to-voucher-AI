"""Live retrieval, named targeting, audit redaction, pagination and scope regressions."""
# ruff: noqa: F811 - shared pytest fixture

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.audit import AuditEvent, AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineExtractionStatus, LineItem
from app.models.request import InvoiceRequest
from app.models.user import UserRole
from app.services import chat_context, rag_service
from tests.test_approval import db_session  # noqa: F401
from tests.test_manual_workflow import seed


@pytest.fixture(autouse=True)
def model_calls():
    with (
        patch.object(rag_service.embedding_service, "embed", AsyncMock(return_value=[1.0])) as embed,
        patch.object(rag_service.vector_store, "similarity_search", AsyncMock(return_value=[])),
        patch.object(rag_service.answer_generator, "generate", AsyncMock(return_value="Grounded answer.")) as generate,
    ):
        yield embed, generate


async def clone_request(db, original, *, client_id=None, invoice_number="INV-2"):
    values = {c.name: getattr(original, c.name) for c in InvoiceRequest.__table__.columns}
    values.update(request_id=uuid.uuid4(), client_id=client_id or original.client_id, voucher_number=None)
    req = InvoiceRequest(**values)
    db.add(req)
    draft = Draft(request_id=req.request_id, extraction_status=ExtractionStatus.COMPLETE)
    db.add(draft)
    await db.flush()
    db.add(HeaderFields(draft_id=draft.draft_id, invoice_number=invoice_number, vendor_name="PRIVATE VENDOR"))
    await db.flush()
    return req


@pytest.mark.parametrize(
    "role", ["CLIENT_PIC", "INTAKE_SUPPORT", "FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN", "SUPER_ADMIN"]
)
async def test_roles_only_receive_live_authorized_context(db_session, model_calls, role):
    data = await seed(db_session)
    user = data["reviewer"]
    user.role = UserRole(role)
    result = await rag_service.ask("Status of invoice INV-1?", user, db_session)
    embed, generate = model_calls
    embed.assert_not_awaited()
    records = generate.call_args.args[1]
    assert "READY_FOR_REVIEW" in records[0]
    assert "INV-1" in records[0]
    assert ("audit_timeline" in records[0]) == (role != "INTAKE_SUPPORT")
    assert result.sources[0]["request_id"] == str(data["request"].request_id)


@pytest.mark.parametrize(
    "question",
    [
        "Details for company Secret Ltd?",
        "Status of INV-999?",
        'Tell me about "Secret Ltd"',
        "Compare company ACME and company Secret Ltd",
        "Status of ACME and Secret Ltd?",
    ],
)
async def test_unmatched_target_denied_without_model_or_foreign_data(db_session, model_calls, question):
    data = await seed(db_session)
    company = Client(client_name="Secret Ltd", client_code="SECRET")
    db_session.add(company)
    await db_session.flush()
    await clone_request(db_session, data["request"], client_id=company.client_id, invoice_number="INV-999")
    result = await rag_service.ask(question, data["reviewer"], db_session)
    assert str(result) == rag_service.DENIED
    model_calls[0].assert_not_awaited()
    model_calls[1].assert_not_awaited()
    assert not result.sources


async def test_missing_and_revoked_assignment_deny_all_context(db_session, model_calls):
    data = await seed(db_session)
    assignment = await db_session.scalar(
        select(ClientAssignment).where(ClientAssignment.user_id == data["reviewer"].user_id)
    )
    assignment.is_active = False
    await db_session.flush()
    assert str(await rag_service.ask("company ACME", data["reviewer"], db_session)) == rag_service.DENIED
    with pytest.raises(HTTPException) as exc:
        await rag_service.ask("status?", data["reviewer"], db_session, data["request"].request_id)
    assert exc.value.status_code == 404
    model_calls[1].assert_not_awaited()


async def test_revocation_during_context_loading_is_rechecked(db_session, model_calls):
    data = await seed(db_session)
    original = rag_service.request_context

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        assignment = await db_session.scalar(
            select(ClientAssignment).where(ClientAssignment.user_id == data["reviewer"].user_id)
        )
        assignment.is_active = False
        await db_session.flush()
        return result

    with patch.object(rag_service, "request_context", revoke):
        result = await rag_service.ask("status?", data["reviewer"], db_session, data["request"].request_id)
    assert str(result) == rag_service.DENIED
    model_calls[1].assert_not_awaited()


@pytest.mark.parametrize("condition", ["inactive", "password", "role"])
async def test_account_guards_precede_retrieval(db_session, model_calls, condition):
    data = await seed(db_session)
    user = data["reviewer"]
    if condition == "inactive":
        user.is_active = False
    elif condition == "password":
        user.must_change_password = True
    else:
        user.role = UserRole.USER_ADMIN
    with pytest.raises(HTTPException) as exc:
        await rag_service.ask("status?", user, db_session)
    assert exc.value.status_code == 403
    model_calls[0].assert_not_awaited()
    model_calls[1].assert_not_awaited()


async def test_company_without_invoices_and_embedding_outage(db_session, model_calls):
    data = await seed(db_session)
    company = Client(client_name="Empty Company", client_code="EMPTY")
    db_session.add(company)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            user_id=data["reviewer"].user_id, client_id=company.client_id, assigned_at=datetime.now(UTC), is_active=True
        )
    )
    await db_session.flush()
    await rag_service.ask("Tell me about company EMPTY", data["reviewer"], db_session)
    assert "Empty Company" in model_calls[1].call_args.args[1][0]
    model_calls[0].side_effect = RuntimeError("SDK private detail")
    await rag_service.ask("What are my current statuses?", data["reviewer"], db_session)
    assert "INV-1" in model_calls[1].call_args.args[1][0]


async def test_duplicate_invoice_number_requires_scope(db_session, model_calls):
    data = await seed(db_session)
    await clone_request(db_session, data["request"], invoice_number="INV-1")
    result = await rag_service.ask("invoice INV-1", data["reviewer"], db_session)
    assert "Several accessible requests" in result
    model_calls[1].assert_not_awaited()
    await rag_service.ask("invoice INV-1", data["reviewer"], db_session, data["request"].request_id)
    model_calls[1].assert_awaited_once()


async def test_audit_is_chronological_allowlisted_and_live(db_session, model_calls):
    data = await seed(db_session)
    now = datetime.now(UTC)
    for kind, offset, payload in [
        (AuditEventType.STATUS_CHANGED, 1, {"from": "QUEUED", "to": "PROCESSING", "token": "SECRET_TOKEN"}),
        (AuditEventType.REQUEST_CREATED, 0, {"password": "SECRET_PASSWORD"}),
        (AuditEventType.USER_MODIFIED, 2, {"name": "PRIVATE_ADMIN"}),
        (AuditEventType.PROCESSING_FAILED, 3, {"error": "PRIVATE_ERROR"}),
    ]:
        db_session.add(
            AuditEvent(
                request_id=data["request"].request_id,
                event_type=kind,
                payload=payload,
                occurred_at=now + timedelta(seconds=offset),
            )
        )
    await db_session.flush()
    await rag_service.ask("Progress of invoice INV-1?", data["reviewer"], db_session)
    text = model_calls[1].call_args.args[1][0]
    context = json.loads(text)
    assert [e["event"] for e in context["audit_timeline"]] == ["REQUEST_CREATED", "STATUS_CHANGED", "PROCESSING_FAILED"]
    assert context["audit_timeline"][1]["to"] == "PROCESSING"
    assert "SECRET" not in text and "PRIVATE" not in text
    assert "independent reviewer" in context["next_required_action"]
    assert "completeness" in context["audit_coverage"]


async def test_large_invoice_pages_and_all_item_totals(db_session, model_calls):
    data = await seed(db_session)
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == data["request"].request_id))
    for index in range(45):
        db_session.add(
            LineItem(
                draft_id=draft.draft_id,
                sequence_number=100 + index,
                description=f"Paged item {index}",
                extraction_status=LineExtractionStatus.COMPLETE,
                quantity=1,
                unit_price=2,
                line_total=2,
            )
        )
    await db_session.flush()
    first = await rag_service.ask("invoice INV-1", data["reviewer"], db_session)
    page_one = json.loads(model_calls[1].call_args.args[1][0])
    assert len(page_one["line_items"]) == chat_context.PAGE_SIZE
    assert first.has_more_items
    second = await rag_service.ask("invoice INV-1", data["reviewer"], db_session, item_page=1)
    page_two = json.loads(model_calls[1].call_args.args[1][0])
    assert not second.has_more_items
    assert page_one["calculated_totals_all_items"] == page_two["calculated_totals_all_items"]
    assert not {i["sequence_number"] for i in page_one["line_items"]} & {
        i["sequence_number"] for i in page_two["line_items"]
    }


async def test_terminal_extraction_failure_records_transition_in_same_transaction(db_session):
    from contextlib import asynccontextmanager

    from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from app.models.request import RequestStatus
    from app.services import extraction_worker

    data = await seed(db_session)
    data["request"].status = RequestStatus.PROCESSING
    job = ExtractionJob(
        request_id=data["request"].request_id,
        status=ExtractionJobStatus.IN_PROGRESS,
        retry_count=extraction_worker.MAX_RETRIES - 1,
        created_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    @asynccontextmanager
    async def session():
        yield db_session

    with patch.object(extraction_worker, "AsyncSessionLocal", session):
        await extraction_worker._handle_failure(job, "Private provider failure")
    await db_session.refresh(data["request"])
    assert data["request"].status == RequestStatus.EXCEPTION
    events = list(
        (await db_session.scalars(select(AuditEvent).where(AuditEvent.request_id == data["request"].request_id))).all()
    )
    transition = next(e for e in events if e.event_type == AuditEventType.STATUS_CHANGED)
    assert transition.payload == {"from": "PROCESSING", "to": "EXCEPTION"}
    assert any(e.event_type == AuditEventType.PROCESSING_FAILED for e in events)


async def test_super_admin_can_retrieve_without_assignment(db_session, model_calls):
    data = await seed(db_session)
    user = data["reviewer"]
    user.role = UserRole.SUPER_ADMIN
    assignment = await db_session.scalar(select(ClientAssignment).where(ClientAssignment.user_id == user.user_id))
    assignment.is_active = False
    await db_session.flush()
    result = await rag_service.ask("invoice INV-1", user, db_session, data["request"].request_id)
    assert result.sources[0]["request_id"] == str(data["request"].request_id)
    model_calls[1].assert_awaited_once()


async def test_company_code_disambiguates_shared_invoice_numbers(db_session, model_calls):
    data = await seed(db_session)
    other = Client(client_name="Other Company", client_code="OTHER")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            user_id=data["reviewer"].user_id, client_id=other.client_id, is_active=True, assigned_at=datetime.now(UTC)
        )
    )
    await clone_request(db_session, data["request"], client_id=other.client_id, invoice_number="INV-1")
    result = await rag_service.ask("company ACME and invoice INV-1", data["reviewer"], db_session)
    assert str(result) == "Grounded answer."
    assert "Other Company" not in "".join(model_calls[1].call_args.args[1])
    other.client_name = data["client"].client_name
    await db_session.flush()
    result = await rag_service.ask("company Acme Corp", data["reviewer"], db_session)
    assert "Several accessible companies" in result
