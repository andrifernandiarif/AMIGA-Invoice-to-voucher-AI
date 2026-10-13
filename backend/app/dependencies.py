# backend/app/dependencies.py

import uuid
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import decode_token
from app.config import settings
from app.models.user import User
from app.repositories.request_repository import request_repository as _req_repo
from app.repositories.user_repository import user_repository

# ── Database engine & session factory ─────────────────────────────────────────
# NeonDB/asyncpg requires ssl — pass via connect_args when URL contains ssl=require
_db_url = settings.DATABASE_URL
_connect_args = {}
if "ssl=require" in _db_url or "sslmode=require" in _db_url:
    # asyncpg wants ssl in connect_args, not the query string
    import ssl as _ssl
    _ssl_ctx = _ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE
    _connect_args = {"ssl": _ssl_ctx}
    # strip ssl params from URL so asyncpg doesn't get confused
    _db_url = _db_url.split("?")[0]

engine = create_async_engine(_db_url, echo=False, future=True, connect_args=_connect_args)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session, rolling back on unhandled exceptions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
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

    user = await user_repository.find_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
        if current_user.role.value not in roles:
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
        active = await user_repository.is_assignment_active(
            db, current_user.user_id, req.client_id
        )
        if not active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to access Requests for this client.",
            )

    return _check_assignment
