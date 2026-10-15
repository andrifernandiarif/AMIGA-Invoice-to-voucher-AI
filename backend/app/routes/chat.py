"""JWT-authenticated, client-scoped retrieval chat."""

import logging
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db, require_role
from app.models.user import User
from app.services import rag_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    request_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    answer: str
    chunks_used: int


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(
        require_role("CLIENT_PIC", "INTAKE_SUPPORT", "FINANCE_REVIEWER", "SERVICE_LEAD", "SYSTEM_ADMIN")
    ),
):
    try:
        answer = await rag_service.ask(body.message, user, db, request_id=body.request_id)
    except HTTPException:
        raise
    except Exception as exc:
        # Do not log question text, retrieved records or credential-bearing SDK errors.
        logger.warning("Chat retrieval failed (%s)", type(exc).__name__)
        raise HTTPException(503, "The answer service is temporarily unavailable. Please try again.")
    return ChatResponse(answer=str(answer), chunks_used=getattr(answer, "chunks_used", 0))
