"""Client-only refinement, server tax calculations and submission-gated approval."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import select
from app.models.request import RequestStatus
from app.models.tax_assignment import TaxAssignment
from app.models.audit import FieldRevision
from tests.test_manual_workflow import seed, client_for
from tests.test_approval import db_session  # noqa: F401


async def refinement_seed(db):
    data = await seed(db)
    data["request"].status = RequestStatus.READY_FOR_REFINEMENT
    await db.commit()
    async with await client_for(db, data["uploader"]) as client:
        detail = (await client.get(f'/requests/{data["request"].request_id}')).json()
    return data, detail["draft"]["line_items"][0]["line_item_id"]


async def test_refine_tax_submit_and_approve_workflow(db_session):
    data, item_id = await refinement_seed(db_session)
    rid = data["request"].request_id
    async with await client_for(db_session, data["reviewer"]) as client:
        assert (await client.get("/requests")).json() == []
        with patch("app.routes.requests.generate_approved_voucher", AsyncMock()) as generate:
            assert (await client.post(f"/requests/{rid}/approve")).status_code == 409
            generate.assert_not_awaited()
    async with await client_for(db_session, data["uploader"]) as client:
        response = await client.put(
            f"/requests/{rid}/items/{item_id}/tax", json={"profile": "PPH23_SERVICES", "effect": "ADD"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["tax_assignment"]["amount"] == "0.20"
        response = await client.put(
            f"/requests/{rid}/fields", json={"field_path": "line_items.0.quantity", "new_value": "3"}
        )
        assert response.status_code == 200, response.text
        detail = (await client.get(f"/requests/{rid}")).json()
        assert detail["draft"]["line_items"][0]["line_total"] == "30.0000"
        assert detail["tax_assignments"][item_id]["amount"] == "0.6000"
        assert detail["totals"]["voucher_total"] == "30.6000"
        response = await client.post(f"/requests/{rid}/submit-review")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "READY_FOR_REVIEW"
        assert (
            await client.put(
                f"/requests/{rid}/fields", json={"field_path": "header.vendor_name", "new_value": "After submission"}
            )
        ).status_code == 409
        assert (await client.put(f"/requests/{rid}/items/{item_id}/tax", json={"profile": "NO_TAX"})).status_code == 409
    async with await client_for(db_session, data["reviewer"]) as client:
        assert len((await client.get("/requests")).json()) == 1
        renderer = MagicMock()
        renderer.HTML.return_value.write_pdf.return_value = b"%PDF-tax"
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
        ):
            response = await client.post(f"/requests/{rid}/approve")
            assert response.status_code == 200, response.text
        html = renderer.HTML.call_args.kwargs["string"]
        assert "PPh 23 2% (biaya pajak)" in html
        assert "Rp 30,60" in html
        assert "99" not in html.split('<table class="header">')[1].split("</table>")[0]
    assert data["request"].status == RequestStatus.AMIGA_REVIEWED
    assert (await db_session.execute(select(FieldRevision))).scalars().all()


async def test_manual_base_and_withholding_are_explicit(db_session):
    data, item_id = await refinement_seed(db_session)
    rid = data["request"].request_id
    async with await client_for(db_session, data["uploader"]) as client:
        response = await client.put(
            f"/requests/{rid}/items/{item_id}/tax",
            json={"profile": "PPH21_MANUAL", "rate_percent": "5.25", "base_amount": "100", "effect": "DEDUCT"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tax_assignment"]["amount"] == "5.25"
        assert (
            await client.put(
                f"/requests/{rid}/fields", json={"field_path": "line_items.0.line_total", "new_value": "20"}
            )
        ).status_code == 200
        detail = (await client.get(f"/requests/{rid}")).json()
        assert detail["totals"]["voucher_total"] == "14.7500"
        assert detail["tax_assignments"][item_id]["base_amount"] == "100.0000"
        assert (await client.post(f"/requests/{rid}/submit-review")).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"profile": "PPH21_MANUAL"},
        {"profile": "PPH21_MANUAL", "rate_percent": "NaN"},
        {"profile": "PPH21_MANUAL", "rate_percent": "-1"},
        {"profile": "PPH21_MANUAL", "rate_percent": "101"},
        {"profile": "PPH23_SERVICES", "rate_percent": "5"},
        {"profile": "PPN_NON_LUXURY", "base_amount": "-1"},
        {"profile": "PPH25"},
        {"profile": "BAD"},
    ],
)
async def test_invalid_tax_never_persists(db_session, body):
    data, item_id = await refinement_seed(db_session)
    async with await client_for(db_session, data["uploader"]) as client:
        response = await client.put(f'/requests/{data["request"].request_id}/items/{item_id}/tax', json=body)
        assert response.status_code == 422, response.text
    assert not (await db_session.execute(select(TaxAssignment))).scalars().all()


async def test_item_scope_and_role_guards(db_session):
    data, item_id = await refinement_seed(db_session)
    rid = data["request"].request_id
    async with await client_for(db_session, data["reviewer"]) as client:
        assert (
            await client.put(f"/requests/{rid}/items/{item_id}/tax", json={"profile": "PPH23_SERVICES"})
        ).status_code == 409
        assert (await client.post(f"/requests/{rid}/submit-review")).status_code == 403
    async with await client_for(db_session, data["uploader"]) as client:
        assert (
            await client.put(f"/requests/{rid}/items/{uuid.uuid4()}/tax", json={"profile": "PPH23_SERVICES"})
        ).status_code == 404
        assert (
            await client.put(f"/requests/{rid}/items/{item_id}/tax", json={"profile": "PPN_NON_LUXURY"})
        ).status_code == 200
        tax = (await client.get(f"/requests/{rid}")).json()["tax_assignments"][item_id]
        assert tax["rate_percent"] == "11.000000"
        assert (await client.put(f"/requests/{rid}/items/{item_id}/tax", json={"profile": "NO_TAX"})).status_code == 200
        assert (await client.get(f"/requests/{rid}")).json()["tax_assignments"] == {}


@pytest.mark.parametrize(
    "state",
    [RequestStatus.QUEUED, RequestStatus.PROCESSING, RequestStatus.READY_FOR_REFINEMENT, RequestStatus.EXCEPTION],
)
async def test_finance_cannot_modify_or_decide_before_review_submission(db_session, state):
    data = await seed(db_session)
    req = data["request"]
    req.status = state
    rid = req.request_id
    await db_session.commit()
    async with await client_for(db_session, data["reviewer"]) as client:
        with patch("app.routes.requests.generate_approved_voucher", AsyncMock()) as generate:
            assert (await client.post(f"/requests/{rid}/approve")).status_code == 409
            assert (
                await client.post(f"/requests/{rid}/reject", json={"rejection_reason": "Not yet submitted"})
            ).status_code == 409
            assert (
                await client.put(
                    f"/requests/{rid}/fields",
                    json={"field_path": "header.vendor_name", "new_value": "Premature change"},
                )
            ).status_code == 409
            generate.assert_not_awaited()
