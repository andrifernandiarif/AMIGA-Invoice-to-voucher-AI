"""Integration coverage for workspace scoping, typed edits and revision persistence."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.dependencies import get_current_user, get_db
from app.models.audit import FieldRevision
from app.models.draft import Draft, ExtractionStatus
from app.models.invoice_fields import HeaderFields, LineItem, LineExtractionStatus
from app.routes.workspace import router
from tests.test_approval import _seed_base, db_session  # noqa: F401


async def http_client(db, user):
    app = FastAPI()
    app.include_router(router)

    async def database():
        yield db
        await db.commit()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def seed(db):
    data = await _seed_base(db, with_draft=False)
    draft = Draft(
        draft_id=uuid.uuid4(),
        request_id=data["request"].request_id,
        extraction_status=ExtractionStatus.COMPLETE,
        ai_label_visible=True,
    )
    db.add(draft)
    await db.flush()
    db.add(HeaderFields(draft_id=draft.draft_id, vendor_name="Vendor", subtotal=10))
    db.add(
        LineItem(
            draft_id=draft.draft_id,
            sequence_number=1,
            description="First",
            quantity=1,
            unit_price=10,
            line_total=10,
            extraction_status=LineExtractionStatus.COMPLETE,
        )
    )
    await db.commit()
    return data


async def test_request_list_detail_and_revisions_are_scoped(db_session):
    data = await seed(db_session)
    async with await http_client(db_session, data["reviewer"]) as client:
        response = await client.get("/requests")
        assert response.status_code == 200
        assert response.json()[0]["client_name"] == "Acme Corp"
        detail = await client.get(f"/requests/{data['request'].request_id}")
        assert detail.json()["draft"]["header_fields"]["subtotal"] == "10.0000"
    async with await http_client(db_session, data["uploader"]) as client:
        assert (await client.get("/requests")).json() == []
        assert (await client.get(f"/requests/{data['request'].request_id}")).status_code == 403
        assert (await client.get(f"/requests/{data['request'].request_id}/revisions")).status_code == 403


async def test_edit_zero_based_line_index_tracks_revision_once(db_session):
    data = await seed(db_session)
    async with await http_client(db_session, data["reviewer"]) as client:
        path = f"/requests/{data['request'].request_id}/fields"
        body = {"field_path": "line_items.0.description", "new_value": "Corrected"}
        response = await client.put(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True
        assert (await client.put(path, json=body)).json()["changed"] is False
    revisions = (await db_session.execute(select(FieldRevision))).scalars().all()
    assert len(revisions) == 1
    assert revisions[0].old_value == "First"
    assert revisions[0].new_value == "Corrected"
    assert revisions[0].actor_id == data["reviewer"].user_id


@pytest.mark.parametrize(
    "path,value",
    [
        ("header.header_id", "bad"),
        ("header.subtotal", "NaN"),
        ("header.invoice_date", "tomorrow"),
        ("header.currency", "TOOLONG"),
        ("line_items.9.description", "bad"),
    ],
)
async def test_invalid_edit_rejected_without_revision(db_session, path, value):
    data = await seed(db_session)
    async with await http_client(db_session, data["reviewer"]) as client:
        response = await client.put(
            f"/requests/{data['request'].request_id}/fields", json={"field_path": path, "new_value": value}
        )
        assert response.status_code == 422
    assert not (await db_session.execute(select(FieldRevision))).scalars().all()
