import os
import shutil

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from keyboards.inline import main_menu, hours_choice
from services import storage
from config import REDDIT_COOKIES_PATH, SITE_COOKIES_PATH

router = Router()


class AddSite(StatesGroup):
    waiting_url = State()
    waiting_hours = State()
    waiting_limit = State()
    waiting_sort = State()
    waiting_period = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я скачиваю медиа с сайтов по расписанию.\n\n"
        "Добавь сайт, выбери интервал — и я буду присылать новые фото и видео.",
        reply_markup=main_menu(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_menu())

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.", reply_markup=main_menu())
        return
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=main_menu())

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    sites = storage.list_sites(message.from_user.id)
    total_seen = sum(len(s.get("seen", [])) for s in sites)
    lines = [
        "📊 Статистика:",
        f"Сайтов отслеживается: {len(sites)}",
        f"Всего скачано медиа: {total_seen}",
    ]
    if sites:
        lines.append("")
        for s in sites:
            url = s["url"][:40] + ("…" if len(s["url"]) > 40 else "")
            lines.append(f"• {url} — каждые {s['hours']}ч, "
                         f"скачано {len(s.get('seen', []))}")
    await message.answer("\n".join(lines))


@router.message(Command("health"))
async def cmd_health(message: Message):
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    rd_cookies = os.path.exists(REDDIT_COOKIES_PATH)
    site_cookies = os.path.exists(SITE_COOKIES_PATH)
    try:
        import yt_dlp
        ytdlp_ver = getattr(yt_dlp.version, "__version__", "?")
    except Exception:
        ytdlp_ver = "не установлен"

    def mark(ok):
        return "✅" if ok else "❌"

    text = (
        "🩺 Состояние бота:\n\n"
        f"{mark(ffmpeg_ok)} ffmpeg\n"
        f"{mark(ffprobe_ok)} ffprobe (для сжатия видео)\n"
        f"{mark(rd_cookies)} cookies Reddit\n"
        f"{mark(site_cookies)} cookies сайтов\n"
        f"📦 yt-dlp: {ytdlp_ver}"
    )
    await message.answer(text)

# Шаг 1: пользователь прислал ссылку
@router.message(StateFilter(AddSite.waiting_url))
async def receive_url(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Пришли ссылку текстом (URL целиком).")
        return
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        await message.answer("❌ Это не похоже на ссылку. Пришли URL целиком.")
        return
    await state.update_data(url=url)
    await state.set_state(AddSite.waiting_hours)
    await message.answer("⏱ Как часто проверять сайт?",
                         reply_markup=hours_choice())
