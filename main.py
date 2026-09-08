from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers import build_router, setup_bot_commands
from src.bot.middlewares import AdminOnlyMiddleware
from src.config import get_settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient
from src.services.monitor import MonitorService
from src.services.notifier import NotificationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    await db.init()
    await db.ensure_admins(settings)

    parser = AfishaClient()
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.middleware(AdminOnlyMiddleware(settings))
    dispatcher.callback_query.middleware(AdminOnlyMiddleware(settings))
    dispatcher.include_router(build_router(settings, db, parser))

    notifier = NotificationService(bot, db, settings)
    monitor = MonitorService(bot, db, settings, parser, notifier)

    await setup_bot_commands(bot, settings)
    monitor.start()

    try:
        logger.info("Бот запущен")
        await dispatcher.start_polling(bot)
    finally:
        await monitor.stop()
        await parser.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
