"""Backfill isolates each request transaction and skips already indexed IDs in SQL."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from scripts import backfill_embeddings as script


async def test_backfill_commits_success_continues_failure_and_throttles():
    ids = [uuid.uuid4(), uuid.uuid4()]
    db = AsyncMock()
    db.scalars.return_value = MagicMock(all=lambda: ids)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.object(script, "AsyncSessionLocal", factory),
        patch.object(script, "index_request", AsyncMock(side_effect=[4, RuntimeError("AWS unavailable")])) as index,
        patch.object(script.asyncio, "sleep", AsyncMock()) as sleep,
    ):
        assert await script.backfill() == (1, 1)
    assert index.await_count == 2
    db.commit.assert_awaited_once()
    sleep.assert_awaited_once_with(0.1)
    query = str(db.scalars.call_args.args[0])
    assert "chunk_embeddings" in query and "count" in query


async def test_backfill_no_work_makes_no_model_calls():
    db = AsyncMock()
    db.scalars.return_value = MagicMock(all=lambda: [])
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.object(script, "AsyncSessionLocal", factory),
        patch.object(script, "index_request", AsyncMock()) as index,
    ):
        assert await script.backfill() == (0, 0)
    index.assert_not_awaited()
