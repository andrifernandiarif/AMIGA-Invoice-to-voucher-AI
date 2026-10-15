"""Telegram routing is tested without contacting Telegram."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from telegram_bot import bot


def update(chat_type="private"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=123, type=chat_type),
        effective_message=SimpleNamespace(text="status INV-001?", reply_text=AsyncMock()),
    )


def session_factory(user):
    db = AsyncMock()
    db.get.return_value = user
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=db)
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    return session


async def test_mapped_private_message_uses_rag_and_returns_plain_text():
    user = SimpleNamespace(user_id=uuid.uuid4(), is_active=True)
    message = update()
    with (
        patch.object(bot.settings, "TELEGRAM_USER_MAP", {"123": str(user.user_id)}),
        patch.object(bot, "AsyncSessionLocal", session_factory(user)),
        patch.object(bot.rag_service, "ask", AsyncMock(return_value="INV-001 is approved.")) as ask,
    ):
        await bot.message(message, None)
    ask.assert_awaited_once()
    message.effective_message.reply_text.assert_awaited_once_with("INV-001 is approved.", parse_mode=None)


async def test_unmapped_and_group_messages_never_reach_rag():
    user = SimpleNamespace(user_id=uuid.uuid4(), is_active=True)
    for message, mapping in [(update(), {}), (update("group"), {"123": str(user.user_id)})]:
        with (
            patch.object(bot.settings, "TELEGRAM_USER_MAP", mapping),
            patch.object(bot, "AsyncSessionLocal", session_factory(user)),
            patch.object(bot.rag_service, "ask", AsyncMock()) as ask,
        ):
            await bot.message(message, None)
        ask.assert_not_awaited()
        assert "not linked" in message.effective_message.reply_text.call_args.args[0]


async def test_start_and_long_answer_and_service_failure():
    user = SimpleNamespace(user_id=uuid.uuid4(), is_active=True)
    message = update()
    with (
        patch.object(bot.settings, "TELEGRAM_USER_MAP", {"123": str(user.user_id)}),
        patch.object(bot, "AsyncSessionLocal", session_factory(user)),
        patch.object(bot.rag_service, "ask", AsyncMock(return_value="a" * 4500)),
    ):
        await bot.start(message, None)
        message.effective_message.reply_text.reset_mock()
        await bot.message(message, None)
        assert message.effective_message.reply_text.await_count == 3
    with (
        patch.object(bot, "AsyncSessionLocal", session_factory(user)),
        patch.object(bot, "mapped_user", AsyncMock(return_value=user)),
        patch.object(bot.rag_service, "ask", AsyncMock(side_effect=RuntimeError("unavailable"))),
    ):
        message.effective_message.reply_text.reset_mock()
        await bot.message(message, None)
        assert "temporarily unavailable" in message.effective_message.reply_text.call_args.args[0]
