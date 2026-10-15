"""Run separately: python -m telegram_bot.bot (from backend/)."""

import logging
import uuid
from fastapi import HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from app.config import settings
from app.dependencies import AsyncSessionLocal, engine
from app.models.user import User
from app.services import rag_service

logger = logging.getLogger(__name__)


async def mapped_user(update, db):
    chat = update.effective_chat
    if not chat or chat.type != "private":
        return None
    mapped = settings.TELEGRAM_USER_MAP.get(str(chat.id))
    if not mapped:
        return None
    try:
        user = await db.get(User, uuid.UUID(mapped))
    except ValueError:
        return None
    return user if user and user.is_active else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    async with AsyncSessionLocal() as db:
        user = await mapped_user(update, db)
    await update.effective_message.reply_text(
        "Ask a question about invoices and vouchers assigned to your account."
        if user
        else "This private chat is not linked to an active account. Contact your administrator.",
        parse_mode=None,
    )


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    incoming = update.effective_message
    if not incoming or not incoming.text:
        return
    try:
        async with AsyncSessionLocal() as db:
            user = await mapped_user(update, db)
            if not user:
                await incoming.reply_text(
                    "This private chat is not linked to an active account. Contact your administrator.", parse_mode=None
                )
                return
            answer = str(await rag_service.ask(incoming.text, user, db))
    except HTTPException as exc:
        answer = str(exc.detail)
    except Exception as exc:
        logger.warning("Telegram question failed (%s)", type(exc).__name__)
        answer = "The answer service is temporarily unavailable. Please try again."
    # Telegram's message limit is 4096 characters; use a smaller UTF-16-safe
    # budget as astral characters count as two units on Telegram.
    for offset in range(0, len(answer), 2000):
        await incoming.reply_text(answer[offset : offset + 2000], parse_mode=None)


async def on_error(update, context):
    # SDK exceptions may contain token-bearing request URLs; log only the type.
    logger.warning("Telegram update failed (%s)", type(context.error).__name__)


async def shutdown(application):
    await engine.dispose()


def build_application():
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN before starting the Telegram bot.")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).post_shutdown(shutdown).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    return app


def main():
    logging.basicConfig(level=logging.WARNING)
    # httpx request logs include token-bearing Telegram URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_application().run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
