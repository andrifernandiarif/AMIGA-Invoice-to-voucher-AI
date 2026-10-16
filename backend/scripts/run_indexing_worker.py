"""Run the PostgreSQL indexing worker independently: python -m scripts.run_indexing_worker."""

import asyncio

from app.dependencies import engine
from app.services.indexing_worker import indexing_worker_loop


async def main():
    try:
        await indexing_worker_loop()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
