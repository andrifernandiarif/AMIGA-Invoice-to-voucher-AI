"""Disconnected sockets, read-only retries, and transaction boundaries around extraction."""

import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import dependencies
from app.database import create_database_engine, is_database_unavailable, read_authenticated_user
from app.models.draft import Draft
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus
from app.models.request import RequestStatus
from app.providers.bedrock import BedrockExtractionProvider, _build_nova_request
from app.services import extraction_worker
from app.storage import S3FileStore
from tests.test_approval import db_session  # noqa: F401
from tests.test_combined_extraction import invoices
from tests.test_manual_workflow import seed

# ruff: noqa: F811


def disconnected():
    return DBAPIError("SELECT 1", {}, ConnectionError("closed in the middle of operation"), connection_invalidated=True)


def test_postgres_engine_checks_idle_connections_and_preserves_url_options():
    with patch("app.database.create_async_engine") as create:
        create_database_engine(
            "postgresql+asyncpg://user:password@localhost/db?ssl=require&prepared_statement_cache_size=0"
        )
    url = create.call_args.args[0]
    args = create.call_args.kwargs
    assert url.query == {"prepared_statement_cache_size": "0"}
    assert args["pool_pre_ping"] and args["pool_recycle"] > 0
    assert args["connect_args"]["timeout"] > 0 and args["connect_args"]["command_timeout"] > 0
    assert "ssl" in args["connect_args"]


async def test_authentication_read_reconnects_once_and_uses_live_user():
    db, repo = AsyncMock(), SimpleNamespace(find_by_id=AsyncMock(side_effect=[disconnected(), "live-user"]))
    user_id = uuid.uuid4()
    assert await read_authenticated_user(db, user_id, repo) == "live-user"
    assert repo.find_by_id.await_count == 2
    db.rollback.assert_awaited_once()


async def test_authentication_never_retries_non_connection_errors():
    db, repo = AsyncMock(), SimpleNamespace(find_by_id=AsyncMock(side_effect=IntegrityError("query", {}, ValueError())))
    with pytest.raises(IntegrityError):
        await read_authenticated_user(db, uuid.uuid4(), repo)
    assert repo.find_by_id.await_count == 1
    db.rollback.assert_not_awaited()


async def test_persistent_disconnect_is_a_retryable_503_without_replaying_endpoint():
    db = AsyncMock()
    repo = SimpleNamespace(find_by_id=AsyncMock(side_effect=disconnected()))
    endpoint = MagicMock()

    @asynccontextmanager
    async def factory():
        yield db

    app = FastAPI()

    @app.get("/requests")
    async def requests(session=Depends(dependencies.get_db)):
        await read_authenticated_user(session, uuid.uuid4(), repo)
        endpoint()
        return []

    with patch.object(dependencies, "AsyncSessionLocal", factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/requests")
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    assert "temporarily unavailable" in response.json()["detail"]
    assert repo.find_by_id.await_count == 2
    endpoint.assert_not_called()


async def test_disconnect_during_write_rolls_back_without_replaying_write():
    db = AsyncMock()
    write = AsyncMock(side_effect=disconnected())

    @asynccontextmanager
    async def factory():
        yield db

    app = FastAPI()

    @app.post("/requests")
    async def upload(session=Depends(dependencies.get_db)):
        await write(session)

    with patch.object(dependencies, "AsyncSessionLocal", factory):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/requests")
    assert response.status_code == 503
    write.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


def test_programming_errors_are_not_misreported_as_database_outages():
    assert not is_database_unavailable(IntegrityError("INSERT", {}, ValueError("bad input")))
    assert not is_database_unavailable(ValueError("invalid invoice"))


def test_nova_pdf_names_follow_bedrock_constraints():
    # The service rejects underscore names, even for otherwise valid PDFs.
    body = _build_nova_request([(b"pdf-one", "application/pdf"), (b"pdf-two", "application/pdf")])
    documents = [block["document"] for block in body["messages"][0]["content"] if "document" in block]
    assert len(documents) == 2
    for document in documents:
        assert re.fullmatch(r"[a-zA-Z0-9 ()\[\]-]+", document["name"])
    assert len({d["name"] for d in documents}) == 2


async def test_streaming_s3_download_does_not_block_event_loop_and_closes_body():
    loop_thread = threading.get_ident()
    threads = []
    store = object.__new__(S3FileStore)
    store.bucket = "test-bucket"
    body = MagicMock()

    def read():
        threads.append(threading.get_ident())
        return b"pdf"

    body.read.side_effect = read
    store._client = MagicMock()
    store._client.get_object.return_value = {"Body": body}
    assert await store.download("invoice.pdf") == b"pdf"
    assert threads and threads[0] != loop_thread
    body.close.assert_called_once()


async def test_multi_pdf_worker_releases_sessions_during_storage_and_model_io(db_session):
    data = await seed(db_session)
    req = data["request"]
    # Existing fixture draft is deliberately removed to test real persistence, not skip it.
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == req.request_id))
    from sqlalchemy import delete

    from app.models.invoice_fields import HeaderFields, LineItem

    await db_session.execute(delete(LineItem).where(LineItem.draft_id == draft.draft_id))
    await db_session.execute(delete(HeaderFields).where(HeaderFields.draft_id == draft.draft_id))
    await db_session.delete(draft)
    req.status = RequestStatus.PROCESSING
    job = ExtractionJob(
        request_id=req.request_id, status=ExtractionJobStatus.IN_PROGRESS, retry_count=0, created_at=datetime.now(UTC)
    )
    db_session.add(job)
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    active = 0

    @asynccontextmanager
    async def factory():
        nonlocal active
        async with session_factory() as session:
            active += 1
            try:
                yield session
            finally:
                active -= 1

    provider = object.__new__(BedrockExtractionProvider)
    provider._is_nova, provider.model_id = True, "amazon.nova-lite-v1:0"
    provider._client = MagicMock()
    sources = list(invoices())

    def converse(**kwargs):
        assert active == 0
        name = kwargs["messages"][0]["content"][0]["document"]["name"]
        assert "_" not in name
        data_bytes = kwargs["messages"][0]["content"][0]["document"]["source"]["bytes"]
        extracted = sources[0] if data_bytes == b"one" else sources[1]
        return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": extracted.model_dump_json()}]}}}

    provider._client.converse.side_effect = converse

    async def download(key):
        assert active == 0
        return b"one" if key == "one.pdf" else b"two"

    with (
        patch.object(extraction_worker, "AsyncSessionLocal", factory),
        patch.object(
            extraction_worker,
            "_load_evidence_sources",
            AsyncMock(return_value=[("one.pdf", "application/pdf"), ("two.pdf", "application/pdf")]),
        ),
        patch.object(extraction_worker.file_store, "download", download),
        patch.object(extraction_worker, "get_extraction_provider", return_value=provider),
        patch.object(extraction_worker, "_handle_failure", AsyncMock()) as failure,
    ):
        await extraction_worker._process_job(job)
    failure.assert_not_awaited()
    await db_session.refresh(req)
    await db_session.refresh(job)
    assert req.status == RequestStatus.READY_FOR_REFINEMENT
    assert job.status == ExtractionJobStatus.DONE
    draft = await db_session.scalar(select(Draft).where(Draft.request_id == req.request_id))
    assert len(draft.source_invoices) == 2
    assert sum(float(i["header"]["total_amount"]) for i in draft.source_invoices) == 4497.30
