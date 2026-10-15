"""The simplified upload → manual review → automatic voucher → downloads workflow."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, inspect

from app.dependencies import get_current_user, get_db
from app.models.audit import AuditEvent, AuditEventType
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft, ExtractionStatus
from app.models.evidence_file import EvidenceFile
from app.models.invoice_fields import HeaderFields, LineItem, LineExtractionStatus
from app.models.request import RequestStatus
from app.models.user import UserRole
from app.models.voucher import VoucherPDF, VoucherTemplate
from app.routes.users import router as users_router
from app.routes.clients import router as clients_router
from app.routes.admin import router as admin_router
from app.routes.refinement import router as refinement_router
from app.routes.requests import router as requests_router
from app.routes.voucher import router as voucher_router
from app.routes.workspace import router as workspace_router
from tests.test_approval import _seed_base, db_session  # noqa: F401


async def client_for(db, user):
    app = FastAPI()
    for router in (
        workspace_router,
        requests_router,
        voucher_router,
        admin_router,
        refinement_router,
        users_router,
        clients_router,
    ):
        app.include_router(router)

    async def database():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = database
    user_id = inspect(user).identity[0]

    async def authenticated_user():
        from app.models.user import User

        current = await db.get(User, user_id)
        await db.refresh(current)
        return current

    app.dependency_overrides[get_current_user] = authenticated_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def seed(db):
    data = await _seed_base(db, request_flags=["MISSING_EVIDENCE", "POSSIBLE_DUPLICATE", "CALC_DISCREPANCY"])
    now = datetime.now(timezone.utc)
    data["uploader"].full_name = "Rina Setiawan"
    data["uploader"].job_title = "Admin"
    data["client"].client_code = "ACME"
    data["request"].voucher_purpose = "Biaya operasional"
    data["request"].approvers = [{"full_name": "Budi Santoso", "job_title": "BOD"}]
    db.add(
        ClientAssignment(
            user_id=data["uploader"].user_id, client_id=data["client"].client_id, is_active=True, assigned_at=now
        )
    )
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=data["request"].request_id,
        extraction_status=ExtractionStatus.PARTIAL,
        ai_label_visible=True,
    )
    db.add(draft)
    await db.flush()
    db.add(
        HeaderFields(
            draft_id=draft.draft_id,
            vendor_name="Manual review vendor",
            invoice_number="INV-1",
            subtotal=10,
            total_amount=99,
            tax_amount=3,
            currency="IDR",
        )
    )
    db.add(
        LineItem(
            draft_id=draft.draft_id,
            sequence_number=1,
            description="Service",
            quantity=1,
            unit_price=10,
            line_total=10,
            extraction_status=LineExtractionStatus.COMPLETE,
        )
    )
    for n in (1, 2):
        db.add(
            EvidenceFile(
                evidence_id=uuid.uuid4(),
                request_id=data["request"].request_id,
                file_key=f'evidence/{data["request"].request_id}/{n}.pdf',
                original_filename=f"Invoice {n}.pdf",
                content_type="application/pdf",
                file_size_bytes=20,
                upload_order=n,
                uploaded_at=now,
            )
        )
    await db.commit()
    return data


async def test_accept_generates_voucher_and_downloads_are_separate(db_session):
    data = await seed(db_session)
    request_id = data["request"].request_id
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = b"%PDF-voucher"
    with (
        patch("app.routes.voucher.weasyprint", renderer),
        patch("app.routes.voucher.file_store.upload", AsyncMock()) as upload,
        patch("app.routes.voucher.file_store.download", AsyncMock(return_value=b"%PDF-evidence-or-voucher")),
    ):
        async with await client_for(db_session, data["reviewer"]) as client:
            response = await client.post(f"/requests/{request_id}/approve")
            assert response.status_code == 200, response.text
            assert response.json()["voucher"]["voucher_pdf_id"]
            upload.assert_awaited_once()
            assert "Rp 99" in renderer.HTML.call_args.kwargs["string"]
            records = (await db_session.execute(select(VoucherPDF))).scalars().all()
            assert len(records) == 1
            assert records[0].generated_by == data["reviewer"].user_id
            assert data["request"].status == RequestStatus.AMIGA_REVIEWED
            assert data["request"].flags == []
            # Separate calls do not overwrite the approved voucher after a template edit.
            assert (await client.post(f"/requests/{request_id}/voucher-pdf")).status_code == 201
            assert upload.await_count == 1
        async with await client_for(db_session, data["uploader"]) as client:
            for n in (1, 2):
                response = await client.get(f"/evidence/evidence/{request_id}/{n}.pdf?download=true")
                assert response.status_code == 200
                assert f"Invoice%20{n}.pdf" in response.headers["content-disposition"]
            assert data["request"].status == RequestStatus.AMIGA_REVIEWED
            preview = await client.get(f"/requests/{request_id}/voucher-pdf?preview=true")
            assert preview.status_code == 200
            assert preview.headers["content-disposition"].startswith("inline")
            assert data["request"].status == RequestStatus.AMIGA_REVIEWED
            response = await client.get(f"/requests/{request_id}/voucher-pdf")
            assert response.status_code == 200
            assert response.content == b"%PDF-evidence-or-voucher"
            assert data["request"].status == RequestStatus.SENT_TO_CLIENT
            assert (await client.get(f"/requests/{request_id}/voucher-preview")).status_code == 200
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert not any(e.event_type == AuditEventType.FLAG_APPLIED for e in events)
    assert any(e.event_type == AuditEventType.APPROVED for e in events)
    assert any(e.event_type == AuditEventType.VOUCHER_GENERATED for e in events)


async def test_pdf_failure_rolls_back_acceptance(db_session):
    data = await seed(db_session)
    request_id = data["request"].request_id
    reviewer = data["reviewer"]
    async with await client_for(db_session, reviewer) as client:
        renderer = MagicMock()
        renderer.HTML.return_value.write_pdf.side_effect = RuntimeError("Rendering failed")
        with patch("app.routes.voucher.weasyprint", renderer):
            response = await client.post(f"/requests/{request_id}/approve")
            assert response.status_code == 500
    req = await db_session.get(type(data["request"]), request_id)
    assert req.status == RequestStatus.READY_FOR_REVIEW
    assert not (await db_session.execute(select(VoucherPDF))).scalars().all()
    events = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert not any(e.event_type in (AuditEventType.APPROVED, AuditEventType.VOUCHER_GENERATED) for e in events)


async def test_rejection_never_generates_a_voucher(db_session):
    data = await seed(db_session)
    request_id = data["request"].request_id
    async with await client_for(db_session, data["reviewer"]) as client:
        with patch("app.routes.requests.generate_approved_voucher", AsyncMock()) as generate:
            response = await client.post(
                f"/requests/{request_id}/reject",
                json={"rejection_reason": "Invoice amount does not match the evidence."},
            )
            assert response.status_code == 200
            generate.assert_not_awaited()
    assert data["request"].status == RequestStatus.REJECTED
    assert not (await db_session.execute(select(VoucherPDF))).scalars().all()


async def test_template_versions_accept_real_jinja_and_keep_old_versions(db_session):
    data = await seed(db_session)
    admin = data["reviewer"]
    admin.role = UserRole.SYSTEM_ADMIN
    await db_session.commit()
    async with await client_for(db_session, admin) as client:
        response = await client.get("/admin/templates")
        assert response.status_code == 200
        template = response.json()["default_template"]
        response = await client.post("/admin/templates", data={"version": "custom-v1", "template_content": template})
        assert response.status_code == 201, response.text
        response = await client.post(
            "/admin/templates",
            data={"version": "custom-v2", "template_content": template.replace("VOUCHER PENGELUARAN", "MY VOUCHER")},
        )
        assert response.status_code == 201
        rows = (await client.get("/admin/templates")).json()["templates"]
        assert len(rows) == 2
        assert [t["version"] for t in rows if t["is_active"]] == ["custom-v2"]
        assert "MY VOUCHER" in rows[0]["template_content"]
        response = await client.post("/admin/templates", data={"version": "bad", "template_content": "{{ broken "})
        assert response.status_code == 422


async def test_extraction_does_not_run_discrepancy_validators(db_session):
    from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from app.schemas.extraction import ExtractionResult
    from app.services.extraction_worker import _process_job

    data = await seed(db_session)
    req = data["request"]
    req.status = RequestStatus.PROCESSING
    now = datetime.now(timezone.utc)
    job = ExtractionJob(
        job_id=uuid.uuid4(),
        request_id=req.request_id,
        status=ExtractionJobStatus.IN_PROGRESS,
        retry_count=0,
        created_at=now,
        started_at=now,
    )
    db_session.add(job)
    await db_session.commit()

    class SessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    provider = MagicMock()
    provider.extract = AsyncMock(return_value=ExtractionResult(header=None, line_items=[], payment_details=None))
    with (
        patch("app.services.extraction_worker.AsyncSessionLocal", return_value=SessionContext()),
        patch("app.services.extraction_worker.get_extraction_provider", return_value=provider),
        patch("app.services.extraction_worker.file_store.download", AsyncMock(return_value=b"%PDF-evidence")),
        patch(
            "app.validation.engine.run_validation", AsyncMock(side_effect=AssertionError("Flags must not run"))
        ) as validate,
        patch("app.services.extraction_worker._handle_failure", AsyncMock()) as failure,
    ):
        await _process_job(job)
        validate.assert_not_awaited()
        failure.assert_not_awaited()
    await db_session.refresh(req)
    await db_session.refresh(job)
    assert req.status == RequestStatus.READY_FOR_REFINEMENT
    assert req.flags == []
    assert job.status == ExtractionJobStatus.DONE


async def test_client_upload_persists_and_extracts_all_evidence_files(db_session):
    from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
    from app.services.extraction_worker import _fetch_evidence_files

    data = await seed(db_session)
    async with await client_for(db_session, data["uploader"]) as client:
        with patch("app.routes.requests.file_store.upload", AsyncMock()) as upload:
            response = await client.post(
                "/requests",
                data={
                    "client_id": str(data["client"].client_id),
                    "request_date": "2026-10-14",
                    "voucher_purpose": "Biaya operasional",
                    "approvers": '[{"full_name":"Budi Santoso","job_title":"BOD"}]',
                },
                files=[
                    ("files", ("invoice.pdf", b"%PDF-invoice", "application/pdf")),
                    ("files", ("receipt.png", b"PNG-receipt", "image/png")),
                ],
            )
            assert response.status_code == 201, response.text
            assert upload.await_count == 2
    request_id = uuid.UUID(response.json()["request_id"])
    rows = (
        (
            await db_session.execute(
                select(EvidenceFile).where(EvidenceFile.request_id == request_id).order_by(EvidenceFile.upload_order)
            )
        )
        .scalars()
        .all()
    )
    assert [r.original_filename for r in rows] == ["invoice.pdf", "receipt.png"]
    assert [r.file_size_bytes for r in rows] == [12, 11]
    async with await client_for(db_session, data["reviewer"]) as client:
        detail = await client.get(f"/requests/{request_id}")
        assert len(detail.json()["evidence_files"]) == 2
        assert detail.json()["voucher_purpose"] == "Biaya operasional"
        assert detail.json()["approvers"] == [{"full_name": "Budi Santoso", "job_title": "BOD"}]
        assert detail.json()["voucher_number"] is None
    with patch(
        "app.services.extraction_worker.file_store.download", AsyncMock(side_effect=[b"%PDF-invoice", b"PNG-receipt"])
    ):
        assert await _fetch_evidence_files(db_session, request_id) == [
            (b"%PDF-invoice", "application/pdf"),
            (b"PNG-receipt", "image/png"),
        ]
    job = await db_session.scalar(select(ExtractionJob).where(ExtractionJob.request_id == request_id))
    assert job.status == ExtractionJobStatus.PENDING


async def test_legacy_exception_requires_client_submission_before_acceptance(db_session):
    data = await seed(db_session)
    req = data["request"]
    req.status = RequestStatus.EXCEPTION
    await db_session.commit()
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = b"%PDF-voucher"
    async with await client_for(db_session, data["reviewer"]) as client:
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
        ):
            response = await client.post(f"/requests/{req.request_id}/approve")
    assert response.status_code == 409, response.text
    await db_session.refresh(req)
    assert req.status == RequestStatus.EXCEPTION


async def test_voucher_information_and_item_date_can_be_saved(db_session):
    data = await seed(db_session)
    async with await client_for(db_session, data["reviewer"]) as client:
        for path, value in [
            ("header.voucher_name", "Nama pemohon"),
            ("line_items.0.item_date", "2026-10-14"),
        ]:
            response = await client.put(
                f'/requests/{data["request"].request_id}/fields', json={"field_path": path, "new_value": value}
            )
            assert response.status_code == 200, response.text
        detail = (await client.get(f'/requests/{data["request"].request_id}')).json()["draft"]
        assert detail["header_fields"]["voucher_name"] == "Nama pemohon"
        assert detail["line_items"][0]["item_date"] == "2026-10-14"


async def test_regeneration_keeps_original_pdf_history(db_session):
    data = await seed(db_session)
    req = data["request"]
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = b"%PDF-new-layout"
    async with await client_for(db_session, data["reviewer"]) as client:
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()) as upload,
        ):
            assert (await client.post(f"/requests/{req.request_id}/approve")).status_code == 200
            first = await db_session.scalar(select(VoucherPDF))
            req.status = RequestStatus.SENT_TO_CLIENT
            await db_session.commit()
            response = await client.post(f"/requests/{req.request_id}/voucher-pdf?regenerate=true")
            assert response.status_code == 201, response.text
            assert upload.await_count == 2
            records = (await db_session.execute(select(VoucherPDF))).scalars().all()
            assert len(records) == 2
            assert first.voucher_pdf_id != uuid.UUID(response.json()["voucher_pdf_id"])
            assert req.status == RequestStatus.SENT_TO_CLIENT
