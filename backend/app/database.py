"""Connection lifecycle shared by the API and extraction workers."""

import asyncio
import logging
import ssl
from socket import gaierror

from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)
DATABASE_UNAVAILABLE = "The database is temporarily unavailable. Please try again shortly."


def create_database_engine(database_url=None, *, null_pool=False):
    url = make_url(database_url or settings.DATABASE_URL)
    connect_args = {}
    options = {"echo": False, "pool_pre_ping": True, "pool_recycle": settings.DB_POOL_RECYCLE_SECONDS}
    if url.drivername == "postgresql+asyncpg":
        connect_args.update(
            timeout=settings.DB_CONNECT_TIMEOUT_SECONDS, command_timeout=settings.DB_COMMAND_TIMEOUT_SECONDS
        )
        if url.query.get("ssl") == "require" or url.query.get("sslmode") == "require":
            # Preserve the existing SSL policy, but keep unrelated query options.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = context
            url = url.difference_update_query(["ssl", "sslmode"])
        if not null_pool:
            options.update(pool_timeout=10, pool_use_lifo=True)
    if null_pool:
        # RQ opens a new event loop for each job; never share asyncpg connections across loops.
        options["poolclass"] = NullPool
    return create_async_engine(url, connect_args=connect_args, **options)


def is_database_unavailable(exc):
    if isinstance(exc, TimeoutError | ConnectionError | PoolTimeout | gaierror):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {50, 51, 54, 60, 61, 64, 65, 101, 104, 110, 111, 113}
    if isinstance(exc, DBAPIError):
        state = getattr(exc.orig, "sqlstate", "") or ""
        return exc.connection_invalidated or state.startswith("08") or state in {"57P01", "57P02", "57P03"}
    return False


async def rollback_safely(session):
    try:
        await session.rollback()
    except Exception as exc:
        # A dead socket must not replace the original error with a rollback traceback.
        logger.warning("Database rollback failed (%s)", type(exc).__name__)


async def read_authenticated_user(db, user_id, repository):
    """Retry only the initial read, before the endpoint can perform any writes."""
    for attempt in range(2):
        try:
            return await repository.find_by_id(db, user_id)
        except Exception as exc:
            if not is_database_unavailable(exc) or attempt:
                raise
            await rollback_safely(db)
            logger.warning("Retrying initial authentication read after a database disconnect")
            await asyncio.sleep(0.1)
