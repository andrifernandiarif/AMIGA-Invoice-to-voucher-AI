# backend/app/dependencies.py

import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import decode_token
from app.database import (
    DATABASE_UNAVAILABLE,
    create_database_engine,
    is_database_unavailable,
    read_authenticated_user,
    rollback_safely,
)
from app.models.user import User
from app.repositories.request_repository import request_repository as _req_repo
from app.repositories.user_repository import user_repository

# ── Database engine & session factory ─────────────────────────────────────────
logger = logging.getLogger(__name__)
engine = create_database_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session, rolling back on unhandled exceptions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await rollback_safely(session)
            if is_database_unavailable(exc):
                logger.warning("Database temporarily unavailable (%s)", type(exc).__name__)
                raise HTTPException(503, DATABASE_UNAVAILABLE, headers={"Retry-After": "1"}) from exc
            raise


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate the Bearer JWT and return the authenticated User ORM object.
    Raises HTTPException(401) if the token is invalid or the user is not found/active.
    """
    payload = decode_token(token)  # raises 401 on bad token

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await read_authenticated_user(db, user_id, user_repository)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("ver", 0) != (user.token_version or 0):
        raise HTTPException(401, "Session expired. Sign in again.")
    if user.must_change_password and request.url.path not in ("/auth/me", "/auth/change-password"):
        raise HTTPException(403, "Change your temporary password before continuing.")
    return user


# ── Authorization dependencies ────────────────────────────────────────────────


def require_role(*roles: str):
    """
    FastAPI dependency factory. Returns a dependency that checks the
    authenticated user's role against the allowed set.

    Usage:
        @router.post("/approve", dependencies=[Depends(require_role("FINANCE_REVIEWER"))])

    Raises HTTPException(403) if the user's role is not in `roles`.
    """

    async def _check_role(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.value != "SUPER_ADMIN" and current_user.role.value not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have authority to perform this action.",
            )
        return current_user

    return _check_role


def require_client_assignment(request_id: uuid.UUID):
    """
    FastAPI dependency factory. Returns a dependency that verifies the
    authenticated user holds an active ClientAssignment for the client
    that owns the given request_id.

    Performs a direct DB lookup on every call — no caching.
    Returns nothing if the check passes (used as a guard dependency).

    Raises HTTPException(403) with no client data if the check fails.
    """

    async def _check_assignment(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        req = await _req_repo.get_by_id(db, request_id)
        if req is None:
            # Don't reveal whether the request exists for another client
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access Requests for this client.",
            )
        active = await user_repository.is_assignment_active(db, current_user.user_id, req.client_id)
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access Requests for this client.",
            )

    return _check_assignment
