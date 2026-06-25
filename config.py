import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Создай файл .env с BOT_TOKEN=...")

# Путь к файлу-хранилищу
STORAGE_PATH = os.path.join(os.path.dirname(__file__), "data", "storage.json")

# Лимит файлов за один проход. Защищает от ситуации, когда на огромной
# галерее качается всё, а seen обрезается и старое присылается заново.
MAX_FILES_PER_RUN = 200

# Сколько URL держать в seen на каждый сайт. Должно быть заметно больше,
# чем MAX_FILES_PER_RUN, иначе дедупликация ненадёжна.
SEEN_LIMIT = 5000

# Пауза между скачиваниями файлов с сайта (секунды) — бережём чужой/свой сервер
DOWNLOAD_DELAY = 1.0

# Пауза между ОТПРАВКАМИ в Telegram (секунды) — защита от flood limit.
SEND_DELAY = 1.5

# Каждые сколько файлов слать сообщение о прогрессе
PROGRESS_EVERY = 25

# Telegram-лимит на размер видео/документа для бота (~50 МБ)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Отдельный лимит на фото через send_photo (~10 МБ).
# Файлы крупнее шлём как документ, чтобы Telegram не отверг их.
MAX_PHOTO_SIZE = 10 * 1024 * 1024

# --- Reddit ---
# User-Agent ОБЯЗАТЕЛЕН и должен быть осмысленным, иначе Reddit режет анонимные запросы.
# Формат, который рекомендует Reddit: platform:appname:version (by /u/username)
REDDIT_USER_AGENT = "python:mediabot:1.0 (by /u/Upset_Magazine_9974)"

# Сколько постов забирать за один проход
REDDIT_LIMIT = 50

# Какую ленту брать: new / hot / top
# Дефолтная лента, если у сайта не задана
REDDIT_DEFAULT_SORT = "new"
REDDIT_DEFAULT_PERIOD = "day"

# Папка для временных видеофайлов
import tempfile as _tempfile
REDDIT_TMP = _tempfile.gettempdir()
