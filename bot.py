import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import BOT_TOKEN
from handlers import commands, callbacks
from services import scheduler

logging.basicConfig(level=logging.INFO)


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота / меню"),
    ])


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)

    await set_commands(bot)

    scheduler.reschedule_all(bot)
    scheduler.start()

    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
