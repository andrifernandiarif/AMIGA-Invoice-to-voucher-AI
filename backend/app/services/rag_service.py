"""Single authorized entry point for web and Telegram questions."""

from fastapi import HTTPException
from sqlalchemy import select
from app.models.client_assignment import ClientAssignment
from app.models.request import InvoiceRequest
from app.services import answer_generator, embedding_service, vector_store


class RagAnswer(str):
    """String answer carrying the retrieval count for API callers."""

    chunks_used: int

    def __new__(cls, answer: str, chunks_used: int):
        obj = super().__new__(cls, answer)
        obj.chunks_used = chunks_used
        return obj


async def ask(question, user, db, request_id=None) -> str:
    question = question.strip()
    if not question or len(question) > 2000:
        raise HTTPException(422, "Enter a question between 1 and 2000 characters.")
    if not user.is_active:
        raise HTTPException(403, "This account is inactive.")
    authorized = (
        select(InvoiceRequest.request_id, InvoiceRequest.status)
        .join(ClientAssignment, ClientAssignment.client_id == InvoiceRequest.client_id)
        .where(ClientAssignment.user_id == user.user_id, ClientAssignment.is_active.is_(True))
    )
    if request_id is not None:
        # Do not reveal whether a request outside the user's scope exists.
        if not (await db.execute(authorized.where(InvoiceRequest.request_id == request_id))).first():
            raise HTTPException(404, "Request not found or not accessible.")
    vector = await embedding_service.embed(question)
    retrieved = await vector_store.similarity_search(vector, 8, db, filter_request_id=request_id, user_id=user.user_id)
    if not retrieved:
        return RagAnswer("I could not find accessible indexed information to answer that question.", 0)
    # Recheck the live relational assignment before any text reaches the LLM;
    # never trust metadata supplied by an embedding or a cached client ID.
    ids = list({chunk.request_id for chunk in retrieved})
    allowed = dict((await db.execute(authorized.where(InvoiceRequest.request_id.in_(ids)))).all())
    safe = [
        chunk
        for chunk in retrieved
        if chunk.request_id in allowed and (request_id is None or chunk.request_id == request_id)
    ]
    if not safe:
        return RagAnswer("I could not find accessible indexed information to answer that question.", 0)
    texts = [
        f"Current database status for request {chunk.request_id}: {allowed[chunk.request_id].value}\n{chunk.chunk_text}"
        for chunk in safe
    ]
    answer = await answer_generator.generate(question, texts)
    return RagAnswer(answer, len(texts))
