"""Work ownership, invoice identities, durable indexing and renderer-owned notices."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
from app.models.request import InvoiceRequest, RequestStatus
from app.models.indexing_job import IndexingJob
from app.services.advisory_validation import validate_request
from app.services.indexing_worker import enqueue_indexing, process_next
from app.services.pdf_service import render_html_pdf, add_authorization_footer
from app.services.workflow import finish_extraction
from app.validation.duplicate import DuplicateDetector
from app.validation.completeness import CompletenessValidator
from tests.test_approval import db_session  # noqa: F401
from tests.test_manual_workflow import seed, client_for


def sources(*identities):
    return [{"header": {"vendor_name": vendor, "invoice_number": number, "total_amount": "10"}}
            for vendor, number in identities]


async def set_identity(db, request_id, entries):
    draft = await db.scalar(select(Draft).where(Draft.request_id == request_id))
    draft.source_invoices = sources(*entries)
    header = await db.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
    header.vendor_name, header.invoice_number = entries[0]
    await db.flush()


async def clone(db, req, entries, *, rejected=False, other_client=False):
    now = datetime.now(UTC)
    client_id = req.client_id
    if other_client:
        from app.models.client import Client
        client = Client(client_name="Foreign client", is_active=True)
        db.add(client)
        await db.flush()
        client_id = client.client_id
    other = InvoiceRequest(request_id=uuid.uuid4(), client_id=client_id, uploader_id=req.uploader_id,
        request_type="standard", channel="upload", request_date=now.date(),
        status=RequestStatus.REJECTED if rejected else RequestStatus.READY_FOR_REVIEW,
        invoice_file_key="test", flags=[], created_at=now, updated_at=now)
    db.add(other)
    await db.flush()
    draft = Draft(request_id=other.request_id, ai_label_visible=True, extraction_status=ExtractionStatus.COMPLETE,
                  source_invoices=sources(*entries))
    db.add(draft)
    await db.flush()
    db.add(HeaderFields(draft_id=draft.draft_id, vendor_name=entries[0][0], invoice_number=entries[0][1]))
    await db.flush()
    return other


@pytest.mark.parametrize("target,existing,match", [
    ([("Vendor", "A")], [("Vendor", "A"), ("Vendor", "B")], True),
    ([("Vendor", "A"), ("Vendor", "B")], [("Vendor", "A")], True),
    ([("Vendor", "A"), ("Vendor", "B")], [("Vendor", "B"), ("Vendor", "C")], True),
    ([(" VENDOR  ltd ", " inv  1 ")], [("vendor ltd", "INV 1")], True),
    ([("Vendor", "A")], [("Different vendor", "A")], False),
])
async def test_source_duplicates_both_directions(db_session, target, existing, match):
    data = await seed(db_session)
    req = data["request"]
    await set_identity(db_session, req.request_id, target)
    other = await clone(db_session, req, existing)
    header = await db_session.scalar(select(HeaderFields).join(Draft).where(Draft.request_id == req.request_id))
    found = await DuplicateDetector.detect(req.request_id, header.vendor_name, header.invoice_number, db_session)
    assert bool(found) == match
    if match:
        assert len(found) == 1 and found[0].request_id == other.request_id
        assert found[0].matched_sources
        await validate_request(db_session, req)
        await db_session.commit()  # Source dates/amounts are JSON-safe in flags and audit events.
        assert "POSSIBLE_DUPLICATE" in req.flags


@pytest.mark.parametrize("rejected,other_client", [(True, False), (False, True)])
async def test_duplicate_rejection_and_client_isolation(db_session, rejected, other_client):
    data = await seed(db_session)
    req = data["request"]
    await set_identity(db_session, req.request_id, [("Vendor", "A")])
    await clone(db_session, req, [("Vendor", "A")], rejected=rejected, other_client=other_client)
    assert await DuplicateDetector.detect(req.request_id, "Vendor", "A", db_session) == []


async def test_single_invoice_uses_edited_identity_not_stale_snapshot(db_session):
    data = await seed(db_session)
    req = data["request"]
    await set_identity(db_session, req.request_id, [("Old", "OLD")])
    await clone(db_session, req, [("New", "NEW")])
    assert await DuplicateDetector.detect(req.request_id, "New", "NEW", db_session)
    assert not await DuplicateDetector.detect(req.request_id, "Old", "OLD", db_session)


async def test_extraction_workers_share_flags_and_preserve_submitted_stage(db_session):
    from worker.jobs.extraction import _mark_ready_for_manual_review
    data = await seed(db_session)
    req = data["request"]
    req.status, req.workflow_stage = RequestStatus.PROCESSING, None
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == req.request_id))
    await finish_extraction(db_session, req)
    flags, issues = req.flags[:], req.validation_issues[:]
    assert req.status == RequestStatus.EXCEPTION and req.workflow_stage == "REFINEMENT"
    req.status, req.workflow_stage = RequestStatus.PROCESSING, None
    await _mark_ready_for_manual_review(db_session, req.request_id, draft)
    assert (req.flags, req.validation_issues) == (flags, issues)
    assert req.status == RequestStatus.EXCEPTION and req.workflow_stage == "REFINEMENT"
    req.workflow_stage = "FINANCE_REVIEW"
    await _mark_ready_for_manual_review(db_session, req.request_id, draft)
    assert req.workflow_stage == "FINANCE_REVIEW"


async def test_zero_is_valid_and_blank_required_values_are_missing(db_session):
    data = await seed(db_session)
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == data["request"].request_id))
    item = await db_session.scalar(select(LineItem).where(LineItem.draft_id == draft.draft_id))
    header = await db_session.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
    header.invoice_date, header.currency = datetime.now(UTC).date(), "USD"
    item.quantity = item.unit_price = item.line_total = 0
    item.unit = "pcs"
    db_session.add(PaymentDetails(draft_id=draft.draft_id, bank_name="Bank", bank_account_number="0", payment_reference="0"))
    await db_session.flush()
    assert (await CompletenessValidator.validate(draft, db_session)).is_valid
    item.description = "  \t "
    await db_session.flush()
    assert "line_items[1].description" in (await CompletenessValidator.validate(draft, db_session)).missing_fields


async def test_submitted_missing_draft_cannot_approve(db_session):
    data = await seed(db_session)
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == data["request"].request_id))
    rid = data["request"].request_id
    await db_session.delete(draft)
    await db_session.commit()
    async with await client_for(db_session, data["reviewer"]) as client:
        response = await client.post(f'/requests/{data["request"].request_id}/approve')
    assert response.status_code == 409 and "usable draft" in response.json()["detail"]
    assert await db_session.get(IndexingJob, rid) is None


async def test_indexing_retry_restart_and_no_connection_during_embedding(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    await enqueue_indexing(db_session, rid)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    active = []
    @asynccontextmanager
    async def tracked():
        async with factory() as db:
            active.append(db)
            try:
                yield db
            finally:
                active.remove(db)
    async def embedding(texts):
        assert not active, "Remote embedding must not hold a session or transaction"
        return [[.1] * 1024 for _ in texts]
    with patch("app.services.embedding_service.embed_chunks", AsyncMock(side_effect=RuntimeError("offline"))):
        assert await process_next(tracked)
    await db_session.refresh(await db_session.get(IndexingJob, rid))
    job = await db_session.get(IndexingJob, rid)
    assert job.status == "PENDING" and job.attempts == 1 and job.last_error == "RuntimeError"
    # Simulate a process dying after claiming a job.
    job.status, job.started_at = "RUNNING", datetime.now(UTC) - timedelta(minutes=6)
    await db_session.commit()
    with patch("app.services.embedding_service.embed_chunks", side_effect=embedding), \
         patch("app.services.vector_store.upsert_chunks", AsyncMock()) as publish:
        assert await process_next(tracked)
        assert not await process_next(tracked)
    await db_session.refresh(job)
    assert job.status == "DONE" and job.attempts == 2
    publish.assert_awaited_once()


async def test_reenqueue_during_embedding_discards_stale_job(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    await enqueue_indexing(db_session, rid)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async def embedding(texts):
        async with factory() as db:
            await enqueue_indexing(db, rid)
            await db.commit()
        return [[.1] * 1024 for _ in texts]
    with patch("app.services.embedding_service.embed_chunks", side_effect=embedding):
        assert await process_next(factory)
    job = await db_session.get(IndexingJob, rid)
    await db_session.refresh(job)
    assert job.status == "PENDING" and job.revision == 2
    from app.models.chunk_embedding import ChunkEmbedding
    assert (await db_session.scalars(select(ChunkEmbedding))).all() == []


def test_notice_cannot_be_hidden_by_custom_template():
    pdf = render_html_pdf('<style>* {display:none}</style><p>Hidden custom template</p>')
    stamped = add_authorization_footer(pdf, "Otorisasi pembayaran tetap menjadi tanggung jawab klien.")
    for page in PdfReader(BytesIO(stamped)).pages:
        assert "Otorisasi pembayaran tetap" in page.extract_text()


async def test_flags_clear_without_losing_work_ownership(db_session):
    data = await seed(db_session)
    req = data["request"]
    req.status, req.workflow_stage = RequestStatus.READY_FOR_REFINEMENT, "REFINEMENT"
    await validate_request(db_session, req)
    assert req.status == RequestStatus.EXCEPTION
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == req.request_id))
    header = await db_session.scalar(select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
    item = await db_session.scalar(select(LineItem).where(LineItem.draft_id == draft.draft_id))
    header.total_amount, header.tax_amount, item.unit = 10, 0, "pcs"
    header.invoice_date, header.currency = datetime.now(UTC).date(), "USD"
    db_session.add(PaymentDetails(draft_id=draft.draft_id, bank_account_number="123", bank_name="Bank", payment_reference="INV-1"))
    await validate_request(db_session, req)
    assert req.flags == [] and req.status == RequestStatus.READY_FOR_REFINEMENT
    req.workflow_stage = "FINANCE_REVIEW"
    item.description = " "
    await validate_request(db_session, req)
    assert req.status == RequestStatus.EXCEPTION and req.workflow_stage == "FINANCE_REVIEW"
    await db_session.commit()
    async with await client_for(db_session, data["reviewer"]) as http:
        response = await http.put(f'/requests/{req.request_id}/fields', json={"field_path":"line_items.0.description","new_value":"Corrected service"})
        assert response.status_code == 200
    assert req.flags == [] and req.status == RequestStatus.READY_FOR_REVIEW


async def test_assigned_system_admin_cannot_make_finance_corrections(db_session):
    from app.models.user import UserRole
    data = await seed(db_session)
    data["reviewer"].role = UserRole.SYSTEM_ADMIN
    await db_session.commit()
    rid = data["request"].request_id
    async with await client_for(db_session, data["reviewer"]) as http:
        response = await http.put(f'/requests/{rid}/fields', json={"field_path":"header.vendor_name","new_value":"Forbidden"})
        assert response.status_code == 403
        assert (await http.post(f'/requests/{rid}/approve')).status_code == 403


async def test_indexing_exhaustion_is_durable_and_explicit_reenqueue_resets_attempts(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    await enqueue_indexing(db_session, rid)
    job = await db_session.get(IndexingJob, rid)
    job.attempts = 4
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    with patch("app.services.embedding_service.embed_chunks", AsyncMock(side_effect=RuntimeError("offline"))):
        await process_next(factory)
    await db_session.refresh(job)
    assert job.status == "FAILED" and job.attempts == 5
    await enqueue_indexing(db_session, rid)
    assert job.status == "PENDING" and job.attempts == 0 and job.last_error is None


async def test_combined_total_does_not_hide_source_arithmetic_issue(db_session):
    from app.validation.engine import ValidationEngine
    data = await seed(db_session)
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == data["request"].request_id))
    draft.source_invoices = [
        {"header":{"vendor_name":"Vendor", "invoice_number":"A", "subtotal":"10", "total_amount":"99"},
         "line_items":[{"sequence_number":1,"line_total":"10"}]},
        {"header":{"vendor_name":"Vendor", "invoice_number":"B", "subtotal":"10", "total_amount":"10"},
         "line_items":[{"sequence_number":1,"line_total":"10"}]},
    ]
    flags = await ValidationEngine.run(data["request"].request_id, draft, db_session)
    calc = next(flag for flag in flags if flag.flag_type == "CALC_DISCREPANCY")
    assert any(issue["field"] == "source_invoices[1].total_amount" and issue["invoice_number"] == "A"
               for issue in calc.details["discrepancies"])
