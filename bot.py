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



def cleanup_temp(max_age_hours: float = 6.0):
    """
    Удаляет старые временные медиафайлы бота из системного Temp.
    Чистит файлы с префиксами sc_ / rd_ / mb_, которые старше max_age_hours.
    Свежие (возможно, ещё качаются/отправляются) — не трогаем.
    """
    import tempfile
    import time

    tmp = tempfile.gettempdir()
    prefixes = ("sc_", "rd_", "mb_")
    now = time.time()
    max_age = max_age_hours * 3600

    removed = 0
    try:
        for name in os.listdir(tmp):
            if not name.startswith(prefixes):
                continue
            path = os.path.join(tmp, name)
            try:
                if not os.path.isfile(path):
                    continue
                if now - os.path.getmtime(path) < max_age:
                    continue  # слишком свежий — мог ещё использоваться
                os.remove(path)
                removed += 1
            except (PermissionError, FileNotFoundError):
                # файл занят или уже удалён — пропускаем
                pass
            except Exception:
                pass
    except Exception:
        logging.warning("Не удалось почистить временную папку %s", tmp)
        return

    if removed:
        logging.info("Очистка Temp: удалено старых файлов — %s", removed)
    else:
        logging.info("Очистка Temp: старых файлов не найдено")

from logging.handlers import RotatingFileHandler

_log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_log_dir, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(_log_dir, "bot.log"),
    maxBytes=5 * 1024 * 1024,   # 5 МБ на файл
    backupCount=5,              # хранить 5 старых файлов
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),   # консоль
        _file_handler,             # файл logs/bot.log
    ],
)


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота / меню"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])


async def main():
    health_check()
    cleanup_temp()

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
