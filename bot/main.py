#!/usr/bin/env python3
"""Telegram Broadcast Manager Bot entry point."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from loguru import logger
from telegram.constants import ParseMode
from telegram.ext import Application

import account_manager as am
import broadcaster
import config
import database as db
import handlers


def _setup_logging() -> None:
    logger.remove()
    serialize = os.getenv("LOG_JSON", "0") == "1"
    logger.add(
        sys.stderr,
        level=os.getenv("LOG_LEVEL", "INFO"),
        colorize=not serialize,
        serialize=serialize,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        os.path.join(config.LOGS_DIR, "bot.log"),
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        encoding="utf-8",
    )


async def _post_init(app: Application) -> None:
    await db.init_db()
    await am.load_all_clients()

    async def _notify_owner(owner_user_id: int, text: str) -> None:
        try:
            await app.bot.send_message(
                chat_id=owner_user_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("Could not notify owner {}: {}", owner_user_id, exc)

    broadcaster.set_notify(_notify_owner)
    logger.info("Bot initialised with {} admin(s)", len(config.ADMIN_IDS))


async def _post_shutdown(app: Application) -> None:
    await broadcaster.stop_all()
    await am.disconnect_all()
    logger.info("Bot shut down cleanly")


def main() -> None:
    _setup_logging()

    if os.getenv("RAILWAY_ENVIRONMENT_ID") and not os.getenv("DATABASE_URL"):
        logger.error("DATABASE_URL is required on Railway. Attach a PostgreSQL database.")
        sys.exit(1)
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set.")
        sys.exit(1)
    if not config.API_ID or not config.API_HASH:
        logger.error("API_ID / API_HASH are required. Get them from https://my.telegram.org")
        sys.exit(1)
    if not config.ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty; no admin panel access will be available.")

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    handlers.register_all(app)
    logger.info("Starting polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
