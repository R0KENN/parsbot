import asyncio
import os

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    SEND_DELAY, PROGRESS_EVERY, MAX_PHOTO_SIZE, ALBUM_SIZE,
)
from services import storage, scraper, reddit

# Задачи держим в памяти. При перезапуске они заново создаются из
# storage.json через reschedule_all() — это и есть наша персистентность.
scheduler = AsyncIOScheduler()

VIDEO_EXT = (".mp4", ".webm", ".mov")



def _safe_remove(path: str, attempts: int = 5, delay: float = 0.5) -> None:
    """
    Безопасно удаляет файл. На Windows файл может быть ещё занят
    (WinError 32) — повторяем несколько раз, потом просто пропускаем.
    """
    import time
    for _ in range(attempts):
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
            return
        except PermissionError:
            time.sleep(delay)
        except FileNotFoundError:
            return
        except Exception:
            return

async def _send_one(bot, user_id, path):
    """Отправляет один файл с обработкой flood limit."""
    is_video = path.lower().endswith(VIDEO_EXT)
    # Фото крупнее лимита Telegram отверг бы — шлём документом.
    too_big_for_photo = (
        os.path.exists(path) and os.path.getsize(path) > MAX_PHOTO_SIZE
    )
    while True:
        try:
            file = FSInputFile(path)
            if is_video:
                await bot.send_video(user_id, file)
            elif too_big_for_photo:
                await bot.send_document(user_id, file)
            else:
                await bot.send_photo(user_id, file)
            return True
        except TelegramRetryAfter as e:
            # Telegram просит подождать N секунд — ждём и пробуем снова
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            return False


def _progress_bar(done: int, total: int, width: int = 12) -> str:
    """Рисует прогресс-бар вида ▰▰▰▰▱▱▱▱ 50% (12/24)."""
    if total <= 0:
        return "▱" * width + " 0%"
    ratio = min(done / total, 1.0)
    filled = int(ratio * width)
    bar = "▰" * filled + "▱" * (width - filled)
    return f"{bar} {int(ratio * 100)}% ({done}/{total})"


async def _safe_edit(status, text: str, cache: dict):
    """Редактирует сообщение, гася ошибки Telegram о повторе/частоте."""
    if text == cache.get("last"):
        return
    try:
        await status.edit_text(text)
        cache["last"] = text
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
    except Exception:
        pass


async def _send_album(bot, user_id, batch):
    """
    Шлёт пачку медиа одним альбомом (до ALBUM_SIZE штук).
    batch — список путей к файлам.
    Возвращает список путей, которые реально были отправлены.
    """
    media = []
    for path in batch:
        file = FSInputFile(path)
        if path.lower().endswith(VIDEO_EXT):
            media.append(InputMediaVideo(media=file))
        else:
            media.append(InputMediaPhoto(media=file))

    while True:
        try:
            await bot.send_media_group(user_id, media)
            return list(batch)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            # альбом не ушёл целиком — пробуем по одному, чтобы не терять всё
            sent = []
            for path in batch:
                if await _send_one(bot, user_id, path):
                    sent.append(path)
                await asyncio.sleep(SEND_DELAY)
            return sent


async def run_site_check(bot, user_id: int, site_id: str):
    """Проверяет один сайт с живым многостадийным прогресс-баром."""
    site = storage.get_site(user_id, site_id)
    if site is None:
        return

    url = site["url"]
    cache = {"last": None}

    # общее состояние, которое обновляет фоновый поток
    state = {"stage": "search", "done": 0, "total": 0, "active": True}

    def on_progress(stage, done, total):
        state["stage"] = stage
        state["done"] = done
        state["total"] = total

    # отдельное закреплённое сообщение — только прогресс-бар
    status = await bot.send_message(user_id, "🔍 Запускаю проверку…")
    try:
        await bot.pin_chat_message(
            user_id, status.message_id, disable_notification=True)
        pinned = True
    except Exception:
        pinned = False

    def render() -> str:
        stage = state["stage"]
        done, total = state["done"], state["total"]
        foot = f"\n\n📡 {url}"
        if stage == "search":
            return "🔍 Ищу новые медиа…" + foot
        if stage in ("search_done", "download"):
            if total == 0:
                return "🔍 Поиск завершён.\nНовых медиа нет." + foot
            return (f"⬇️ Скачивание\n{_progress_bar(done, total)}" + foot)
        if stage == "upload":
            return (f"⬆️ Выгрузка в Telegram\n{_progress_bar(done, total)}"
                    + foot)
        return "✅ Готово!" + foot

    # фоновая задача-художник: раз в секунду перерисовывает сообщение
    async def painter():
        while state["active"]:
            await _safe_edit(status, render(), cache)
            await asyncio.sleep(1.0)

    paint_task = asyncio.create_task(painter())

    # --- поиск + скачивание (в потоке, с прогрессом) ---
    try:
        if reddit.is_reddit_url(url):
            new_media = await asyncio.to_thread(
                reddit.fetch_new_media, url, site["seen"],
                site.get("sort", "new"), site.get("period", "day"),
                on_progress)
        else:
            new_media = await asyncio.to_thread(
                scraper.fetch_new_media, url, site["seen"], on_progress)
    except PermissionError:
        state["active"] = False
        paint_task.cancel()
        await _safe_edit(status, f"📡 {url}\n\n⚠️ robots.txt запрещает доступ",
                         cache)
        if pinned:
            try:
                await bot.unpin_chat_message(user_id, status.message_id)
            except Exception:
                pass
        return
    except Exception as e:
        state["active"] = False
        paint_task.cancel()
        await _safe_edit(status, f"📡 {url}\n\n⚠️ Ошибка: {e}", cache)
        if pinned:
            try:
                await bot.unpin_chat_message(user_id, status.message_id)
            except Exception:
                pass
        return

    total = len(new_media)

    if not total:
        state["stage"] = "search_done"
        state["done"] = state["total"] = 0
        state["active"] = False
        paint_task.cancel()
        await _safe_edit(status, f"📡 {url}\n\n🔍 Новых медиа нет.", cache)
        if pinned:
            try:
                await bot.unpin_chat_message(user_id, status.message_id)
            except Exception:
                pass
        return

    # --- выгрузка альбомами с прогрессом ---
    state["stage"] = "upload"
    state["total"] = total
    state["done"] = 0

    sent_count = 0
    for start in range(0, total, ALBUM_SIZE):
        chunk = new_media[start:start + ALBUM_SIZE]
        paths = [p for (_u, p) in chunk]

        sent_paths = await _send_album(bot, user_id, paths)
        sent_set = set(sent_paths)
        sent_count += len(sent_paths)
        state["done"] = min(start + len(chunk), total)

        await asyncio.sleep(0.3)  # дать Telegram отпустить файлы перед удалением

        for (u, p) in chunk:
            if p in sent_set:
                storage.mark_seen(user_id, site_id, [u])

        for (_u, p) in chunk:
            _safe_remove(p)

        await asyncio.sleep(SEND_DELAY)

    # --- завершение ---
    state["stage"] = "done"
    state["active"] = False
    paint_task.cancel()

    skipped = total - sent_count
    final = (
        f"✅ Готово!\n"
        f"{_progress_bar(total, total)}\n"
        f"Отправлено {sent_count} из {total}"
    )
    if skipped > 0:
        final += f"\n⚠️ Пропущено {skipped} (слишком большие или ошибка)"
    final += f"\n\n📡 {url}"
    await _safe_edit(status, final, cache)

    if pinned:
        try:
            await bot.unpin_chat_message(user_id, status.message_id)
        except Exception:
            pass

def schedule_site(bot: Bot, user_id: int, site_id: str, hours: int):
    job_id = f"{user_id}_{site_id}"
    scheduler.add_job(
        run_site_check,
        "interval",
        hours=hours,
        args=[bot, user_id, site_id],
        id=job_id,
        replace_existing=True,
        max_instances=1,   # не запускать вторую проверку, пока идёт первая
        coalesce=True,     # пропущенные срабатывания объединять в одно
    )


def unschedule_site(user_id: int, site_id: str):
    """Снимает задачу одного сайта."""
    job_id = f"{user_id}_{site_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def reschedule_all(bot: Bot):
    """При старте бота восстанавливаем задачи из хранилища."""
    users = storage.all_users()
    for uid, info in users.items():
        for site in info.get("sites", []):
            schedule_site(bot, int(uid), site["id"], site["hours"])


def start():
    if not scheduler.running:
        scheduler.start()
