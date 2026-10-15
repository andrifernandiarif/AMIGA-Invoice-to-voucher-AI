"""RAG boundaries: payloads, chunk coverage, authorization, approval isolation."""
# ruff: noqa: F811 - imported shared pytest fixture

import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.dependencies import get_current_user, get_db
from app.models.request import InvoiceRequest, RequestStatus
from app.routes.chat import router as chat_router
from app.services import chunker, embedding_service, answer_generator, indexer, rag_service
from app.services.chunker import TextChunk
from app.services.rag_service import RagAnswer
from tests.test_manual_workflow import seed, client_for
from tests.test_approval import db_session  # noqa: F401


def vector():
    return [1.0] + [0.0] * 1023


async def test_chunker_mock_db_returns_four_populated_chunks():
    rid = uuid.uuid4()
    cid = uuid.uuid4()
    did = uuid.uuid4()
    iid = uuid.uuid4()
    from datetime import datetime, UTC

    req = SimpleNamespace(
        request_id=rid,
        client_id=cid,
        status=RequestStatus.AMIGA_REVIEWED,
        created_at=datetime.now(UTC),
        voucher_number="ACME-1",
        voucher_purpose="Office supplies",
        voucher_created_at=datetime.now(UTC),
        request_date=None,
        refinement_submitted_at=None,
    )
    from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails

    header = HeaderFields(
        vendor_name="Acme vendor", invoice_number="INV-001", currency="IDR", subtotal=100, total_amount=100
    )
    item = LineItem(
        line_item_id=iid, sequence_number=1, description="Paper", quantity=1, unit_price=100, line_total=100
    )
    db = AsyncMock()
    db.scalar.side_effect = [req, SimpleNamespace(draft_id=did), header, PaymentDetails(bank_name="Bank X")]
    db.scalars.side_effect = [MagicMock(all=lambda: [item]), MagicMock(all=lambda: [])]
    chunks = await chunker.chunk_request(rid, db)
    assert len(chunks) == 4
    assert {c.chunk_type for c in chunks} == {"HEADER", "LINE_ITEMS", "PAYMENT", "TAX_SUMMARY"}
    for chunk in chunks:
        assert chunk.chunk_text.strip()
        assert chunk.metadata["vendor_name"] == "Acme vendor"
        assert chunk.metadata["invoice_number"] == "INV-001"
        assert chunk.metadata["status"] == "AMIGA_REVIEWED"
        assert chunk.metadata["client_id"] == str(cid)


async def test_titan_payload_parsing_and_one_call_per_chunk():
    bedrock = MagicMock()
    bedrock.invoke_model.side_effect = lambda **kwargs: {
        "body": io.BytesIO(json.dumps({"embedding": vector()}).encode())
    }
    with patch("app.services.embedding_service.create_client", return_value=bedrock):
        result = await embedding_service.embed("what is the status?")
        batch = await embedding_service.embed_chunks(["one", "two"])
    assert len(result) == 1024 and len(batch) == 2
    assert bedrock.invoke_model.call_count == 3
    kwargs = bedrock.invoke_model.call_args_list[0].kwargs
    assert kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
    assert json.loads(kwargs["body"]) == {"inputText": "what is the status?", "dimensions": 1024, "normalize": True}


@pytest.mark.parametrize("bad", [[0.0] * 1024, [1.0] * 1536, [float("nan")] * 1024, [True] * 1024])
def test_invalid_vectors_are_rejected(bad):
    with pytest.raises(ValueError):
        embedding_service.validate_vector(bad)


async def test_nova_converse_contains_all_context_and_question():
    bedrock = MagicMock()
    bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "Invoice INV-001 is approved."}]}}}
    with patch("app.services.answer_generator.create_client", return_value=bedrock):
        answer = await answer_generator.generate("Status of INV-001?", ["Header chunk", "Payment chunk"])
    assert answer == "Invoice INV-001 is approved."
    kwargs = bedrock.converse.call_args.kwargs
    payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
    assert payload == {"question": "Status of INV-001?", "context_records": ["Header chunk", "Payment chunk"]}
    assert "only the provided context" in kwargs["system"][0]["text"]


async def test_indexer_calls_all_stages(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    chunks = [TextChunk("HEADER", "test", {})]
    with (
        patch.object(chunker, "chunk_request", AsyncMock(return_value=chunks)) as chunk,
        patch.object(embedding_service, "embed_chunks", AsyncMock(return_value=[vector()])) as embed,
        patch("app.services.vector_store.upsert_chunks", AsyncMock()) as store,
    ):
        assert await indexer.index_request(rid, db_session) == 1
    chunk.assert_awaited_once_with(rid, db_session)
    embed.assert_awaited_once_with(["test"])
    store.assert_awaited_once_with(rid, chunks, [vector()], db_session)


async def test_rag_removes_unauthorized_chunks_before_llm(db_session):
    data = await seed(db_session)
    rid = data["request"].request_id
    foreign_id = uuid.uuid4()
    chunks = [
        SimpleNamespace(request_id=rid, chunk_text="AUTHORIZED INVOICE"),
        SimpleNamespace(request_id=foreign_id, chunk_text="PRIVATE OTHER CLIENT"),
    ]
    with (
        patch.object(embedding_service, "embed", AsyncMock(return_value=vector())),
        patch("app.services.vector_store.similarity_search", AsyncMock(return_value=chunks)) as search,
        patch.object(answer_generator, "generate", AsyncMock(return_value="Approved.")) as generate,
    ):
        answer = await rag_service.ask("status?", data["reviewer"], db_session)
    assert str(answer) == "Approved." and answer.chunks_used == 1
    texts = generate.call_args.args[1]
    assert len(texts) == 1 and "AUTHORIZED" in texts[0]
    assert "PRIVATE OTHER CLIENT" not in "".join(texts)
    assert search.call_args.kwargs["user_id"] == data["reviewer"].user_id


async def test_rag_denies_request_scope_before_embedding(db_session):
    data = await seed(db_session)
    with patch.object(embedding_service, "embed", AsyncMock()) as embed:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as err:
            await rag_service.ask("status?", data["reviewer"], db_session, request_id=uuid.uuid4())
        assert err.value.status_code == 404
        embed.assert_not_awaited()


async def test_rag_empty_results_never_calls_llm(db_session):
    data = await seed(db_session)
    with (
        patch.object(embedding_service, "embed", AsyncMock(return_value=vector())),
        patch("app.services.vector_store.similarity_search", AsyncMock(return_value=[])),
        patch.object(answer_generator, "generate", AsyncMock()) as generate,
    ):
        answer = await rag_service.ask("status?", data["reviewer"], db_session)
    assert answer.chunks_used == 0
    generate.assert_not_awaited()


async def test_chat_jwt_required_and_response_shape(db_session):
    app = FastAPI()
    app.include_router(chat_router)

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/chat", json={"message": "status?"})).status_code == 401
        data = await seed(db_session)
        app.dependency_overrides[get_current_user] = lambda: data["reviewer"]
        with patch.object(rag_service, "ask", AsyncMock(return_value=RagAnswer("Approved.", 4))):
            response = await client.post("/chat", json={"message": "status?"})
        assert response.json() == {"answer": "Approved.", "chunks_used": 4}
        with patch.object(rag_service, "ask", AsyncMock(side_effect=RuntimeError("AWS error"))):
            assert (await client.post("/chat", json={"message": "status?"})).status_code == 503


@pytest.mark.parametrize("fail", [False, True])
async def test_approval_indexes_after_commit_and_failure_keeps_approval(db_session, fail):
    data = await seed(db_session)
    rid = data["request"].request_id
    renderer = MagicMock()
    renderer.HTML.return_value.write_pdf.return_value = b"%PDF-test"
    indexing = AsyncMock(side_effect=RuntimeError("Bedrock unavailable") if fail else None, return_value=4)
    async with await client_for(db_session, data["reviewer"]) as client:
        with (
            patch("app.routes.voucher.weasyprint", renderer),
            patch("app.routes.voucher.file_store.upload", AsyncMock()),
            patch("app.routes.requests.index_request", indexing),
        ):
            response = await client.post(f"/requests/{rid}/approve")
    assert response.status_code == 200, response.text
    indexing.assert_awaited_once_with(rid, db_session)
    req = await db_session.get(InvoiceRequest, rid)
    assert req.status == RequestStatus.AMIGA_REVIEWED
    assert req.voucher_number == "ACME-1"
