"""Notifier agent entry point — runs Telegram bot with callback handler."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.state import init_db, log_event
from lib.logger import get_logger

logger = get_logger("notifier")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def main():
    init_db()

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — notifier cannot start")
        log_event("error", "notifier", "Missing TELEGRAM_BOT_TOKEN")
        # Keep alive for supervisord
        while True:
            await asyncio.sleep(60)

    log_event("startup", "notifier", "Notifier agent started")
    logger.info("Notifier agent started")

    from telegram.ext import Application, CallbackQueryHandler, CommandHandler
    from agents.notifier.bot import (
        handle_callback,
        handle_status,
        handle_cancel_all,
    )

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("cancel_all", handle_cancel_all))

    # Run polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.info("Telegram bot polling started")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
