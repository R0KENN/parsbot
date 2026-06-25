import asyncio
import logging
import os
import shutil

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN, REDDIT_COOKIES_PATH
from handlers import commands, callbacks
from services import scheduler


def health_check():
    """Проверяет окружение и предупреждает о возможных проблемах."""
    if shutil.which("ffmpeg") is None:
        logging.warning(
            "ffmpeg не найден в PATH — видео Reddit будут без звука "
            "или не скачаются. Установите ffmpeg.")
    else:
        logging.info("ffmpeg найден — ок")

    if os.path.exists(REDDIT_COOKIES_PATH):
        logging.info("Файл cookies Reddit найден — ок")
    else:
        logging.warning(
            "Файл cookies Reddit не найден (%s) — видео могут требовать "
            "авторизации. См. инструкцию по reddit_cookies.txt",
            REDDIT_COOKIES_PATH)

logging.basicConfig(level=logging.INFO)


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота / меню"),
        BotCommand(command="menu", description="Главное меню"),
    ])


async def main():
    health_check()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)

    await set_commands(bot)

    scheduler.start()
    scheduler.reschedule_all(bot)

    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
