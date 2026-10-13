from redis import Redis
from rq import Queue

from app.config import settings


def get_redis_connection() -> Redis:
    """Return a Redis connection from REDIS_URL."""
    return Redis.from_url(settings.REDIS_URL)


def get_extraction_queue() -> Queue:
    """Return the RQ extraction queue."""
    return Queue("amiga-extraction", connection=get_redis_connection())
