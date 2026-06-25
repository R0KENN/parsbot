import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Создай файл .env с BOT_TOKEN=...")

# Путь к файлу-хранилищу
STORAGE_PATH = os.path.join(os.path.dirname(__file__), "data", "storage.json")

# Снимаем жёсткий лимит на количество файлов за проход.
# None = скачивать всё, что найдено.
MAX_FILES_PER_RUN = None

# Пауза между скачиваниями файлов с сайта (секунды) — бережём чужой/свой сервер
DOWNLOAD_DELAY = 1.0

# Пауза между ОТПРАВКАМИ в Telegram (секунды) — защита от flood limit.
# ~1.5 сек безопасно для длинных рассылок в один чат.
SEND_DELAY = 1.5

# Каждые сколько файлов слать сообщение о прогрессе
PROGRESS_EVERY = 25

# Telegram-лимит на размер файла для обычного бота (~50 МБ)
MAX_FILE_SIZE = 50 * 1024 * 1024
