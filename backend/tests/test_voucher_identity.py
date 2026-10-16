"""End-to-end voucher metadata, account signatures and company numbering."""
# ruff: noqa: F811 - shared pytest fixture imported below

from tests.pdf_fixtures import pdf_bytes
import base64
import io
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from app.models.client import Client
from app.models.request import InvoiceRequest, RequestStatus
from app.models.user import UserRole
from app.services.voucher_identity import prepare_voucher_identity
from app.services.signatures import normalize_signature
from tests.test_manual_workflow import client_for, seed
from tests.test_approval import db_session  # noqa: F401


def signature():
    out = io.BytesIO()
    Image.new("RGBA", (80, 30), (0, 0, 0, 0)).save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def another_request(source, client_id=None):
    now = datetime.now(UTC)
    return InvoiceRequest(
        request_id=uuid.uuid4(),
        client_id=client_id or source.client_id,
        uploader_id=source.uploader_id,
        request_type="standard",
        channel="upload",
        request_date=source.request_date,
        status=RequestStatus.AMIGA_REVIEWED,
        flags=[],
        invoice_file_key="evidence/example.pdf",
        created_at=now,
        updated_at=now,
    )


async def test_company_sequence_and_regeneration_identity(db_session):
    data = await seed(db_session)
    req = data["request"]
    data["uploader"].signature_data = signature()
    await prepare_voucher_identity(db_session, req)
    first_time = req.voucher_created_at
    assert req.voucher_number == "ACME-1"
    data["uploader"].full_name = "Changed later"
    data["uploader"].signature_data = None
    await prepare_voucher_identity(db_session, req)
    assert req.voucher_number == "ACME-1" and req.voucher_created_at == first_time
    assert req.submitter_snapshot["full_name"] == "Rina Setiawan"
    assert req.submitter_snapshot["signature_data"].startswith("data:image/png;base64,")
    req2 = another_request(req)
    other_company = Client(client_id=uuid.uuid4(), client_name="Second Company", client_code="SECOND")
    db_session.add_all([req2, other_company])
    await db_session.flush()
    req3 = another_request(req, other_company.client_id)
    db_session.add(req3)
    await prepare_voucher_identity(db_session, req2)
    await prepare_voucher_identity(db_session, req3)
    assert req2.voucher_number == "ACME-2"
    assert req3.voucher_number == "SECOND-1"


async def test_pdf_failure_rolls_back_number_and_company_counter(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.side_effect = RuntimeError("renderer unavailable")
    async with await client_for(db_session, data["reviewer"]) as client:
        with patch("app.routes.voucher.weasyprint", renderer):
            assert (await client.post(f"/requests/{rid}/approve")).status_code == 500
        req = await db_session.get(InvoiceRequest, rid)
        assert req.voucher_number is None and req.voucher_created_at is None
        company = await db_session.get(Client, req.client_id)
        assert company.voucher_sequence == 0
        assert req.status == RequestStatus.READY_FOR_REVIEW


async def test_metadata_refinement_then_freeze_and_render_multiple_approvers(db_session):
    data = await seed(db_session)
    req = data["request"]
    rid = req.request_id
    req.status = RequestStatus.READY_FOR_REFINEMENT
    req.voucher_purpose = None
    req.approvers = None
    data["uploader"].signature_data = signature()
    await db_session.commit()
    metadata = {
        "voucher_purpose": "Biaya perjalanan",
        "approvers": [
            {"full_name": "Budi Santoso", "job_title": "BOD"},
            {"full_name": "Sari Dewi", "job_title": "Manager of PT X"},
        ],
    }
    async with await client_for(db_session, data["uploader"]) as client:
        assert (await client.post(f"/requests/{rid}/submit-review")).status_code == 422
        assert (await client.put(f"/requests/{rid}/voucher-metadata", json=metadata)).status_code == 200
        assert (await client.post(f"/requests/{rid}/submit-review")).status_code == 200
        assert (await client.put(f"/requests/{rid}/voucher-metadata", json=metadata)).status_code == 409
        detail = (await client.get(f"/requests/{rid}")).json()
        assert detail["approvers"] == metadata["approvers"]
        assert "signature_data" not in detail["submitter"]
    # Editing the account after submission must not change the signature under review.
    data["uploader"].full_name = "Changed after submission"
    data["uploader"].signature_data = None
    await db_session.commit()
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = pdf_bytes()
    async with await client_for(db_session, data["reviewer"]) as client:
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
        ):
            response = await client.post(f"/requests/{rid}/approve")
            assert response.status_code == 200, response.text
    rendered = renderer.HTML.call_args.kwargs["string"]
    assert "ACME-1" in rendered and "Biaya perjalanan" in rendered
    assert "Rina Setiawan" in rendered and "Changed after submission" not in rendered
    assert "Budi Santoso" in rendered and "Sari Dewi" in rendered
    assert "data:image/png;base64," in rendered
    assert rendered.count("<img") == 1  # external signatures remain empty


async def test_generated_header_fields_cannot_be_overridden(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    async with await client_for(db_session, data["reviewer"]) as client:
        for key in ["voucher_number", "voucher_period", "voucher_purpose"]:
            response = await client.put(
                f"/requests/{rid}/fields", json={"field_path": "header." + key, "new_value": "override"}
            )
            assert response.status_code == 422


def test_signature_normalization_rejects_urls_and_invalid_images():
    assert normalize_signature(signature()).startswith("data:image/png;base64,")
    for value in [
        "https://example.com/signature.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,bm90LWltYWdl",
    ]:
        with pytest.raises(Exception) as exc:
            normalize_signature(value)
        assert exc.value.status_code == 422


async def test_create_account_and_optional_signature_and_profile_privacy(db_session):
    from app.models.user import User

    data = await seed(db_session)
    admin = data["reviewer"]
    admin.role = UserRole.SUPER_ADMIN
    await db_session.commit()
    async with await client_for(db_session, admin) as client:
        result = await client.post(
            "/users",
            json={
                "username": "new_pic",
                "temporary_password": "New-Account-Password-2026",
                "full_name": "Nadia Putri",
                "job_title": "Finance Admin",
                "role": "CLIENT_PIC",
                "signature_data": signature(),
                "assignments": [{"client_id": str(data["client"].client_id)}],
            },
        )
        assert result.status_code == 201, result.text
        assert result.json()["full_name"] == "Nadia Putri"
        assert "signature_data" not in result.json()
    new = await db_session.get(User, uuid.UUID(result.json()["user_id"]))
    assert new.signature_data
    async with await client_for(db_session, new) as client:
        profile = await client.get("/users/me/profile")
        assert profile.json()["signature_data"]
        saved = await client.put(
            "/users/me/profile", json={"full_name": "Nadia Putri", "job_title": "Admin", "remove_signature": True}
        )
        assert saved.status_code == 200
        assert (await client.get("/users/me/profile")).json()["signature_data"] is None
        assert (await client.get(f"/users/{admin.user_id}")).status_code == 403
