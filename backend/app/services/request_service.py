import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.request_repository import request_repository


class RequestIdGenerationError(Exception):
    """Raised when a unique Request ID cannot be generated after max retries."""


MAX_ID_RETRIES = 5


async def generate_request_id(db: AsyncSession) -> uuid.UUID:
    """
    Generate a unique UUID for a new Request.
    Retries up to MAX_ID_RETRIES times on the astronomically unlikely collision.
    Raises RequestIdGenerationError after exhausting retries.
    """
    for attempt in range(1, MAX_ID_RETRIES + 1):
        candidate = uuid.uuid4()
        existing = await request_repository.get_by_id(db, candidate)
        if existing is None:
            return candidate

    raise RequestIdGenerationError(
        "Request creation failed: ID generation failed after "
        f"{MAX_ID_RETRIES} attempts. Please retry."
    )
