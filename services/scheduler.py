import asyncio
import os

# Замки на каждый (user_id, site_id), чтобы один сайт не проверялся
# одновременно планировщиком и кнопкой «Проверить сейчас».
_site_locks: dict = {}

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
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
    """Отправляет один файл с обработкой flood limit и сетевых сбоев."""
    is_video = path.lower().endswith(VIDEO_EXT)
    # показываем в чате плашку «отправляет видео / фото / документ…»
    too_big_for_photo_pre = (
        os.path.exists(path) and os.path.getsize(path) > MAX_PHOTO_SIZE
    )
    try:
        if is_video:
            await bot.send_chat_action(user_id, "upload_video")
        elif too_big_for_photo_pre:
            await bot.send_chat_action(user_id, "upload_document")
        else:
            await bot.send_chat_action(user_id, "upload_photo")
    except Exception:
        pass
    # Фото крупнее лимита Telegram отверг бы — шлём документом.
    too_big_for_photo = (
        os.path.exists(path) and os.path.getsize(path) > MAX_PHOTO_SIZE
    )
    net_retries = 0
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
        except TelegramNetworkError:
            # временный сетевой сбой — пара повторов с нарастающей паузой
            net_retries += 1
            if net_retries > 3:
                return False
            await asyncio.sleep(2 * net_retries)
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


async def _send_album(bot, user_id, batch, on_sent=None):
    """
    Шлёт пачку медиа одним альбомом (до ALBUM_SIZE штук).
    batch — список путей к файлам.
    Возвращает список путей, которые реально были отправлены.
    Крупные фото (> MAX_PHOTO_SIZE) в альбом не кладём — Telegram их отвергнет,
    их шлём отдельно документом через _send_one.
    """
    media = []
    big_photos = []
    for path in batch:
        is_video = path.lower().endswith(VIDEO_EXT)
        too_big_photo = (
            not is_video
            and os.path.exists(path)
            and os.path.getsize(path) > MAX_PHOTO_SIZE
        )
        if too_big_photo:
            big_photos.append(path)
            continue
        file = FSInputFile(path)
        if is_video:
            media.append(InputMediaVideo(media=file))
        else:
            media.append(InputMediaPhoto(media=file))

    sent = []

    # крупные фото — по одному документом
    for path in big_photos:
        if await _send_one(bot, user_id, path):
            sent.append(path)
        await asyncio.sleep(SEND_DELAY)

    if not media:
        return sent

    # альбом из одного элемента Telegram не принимает — шлём как одиночный
    if len(media) == 1:
        path = next(p for p in batch if p not in big_photos)
        if await _send_one(bot, user_id, path):
            sent.append(path)
        return sent


    # плашка «отправляет видео…» для альбома
    has_video = any(isinstance(m, InputMediaVideo) for m in media)
    try:
        await bot.send_chat_action(
            user_id, "upload_video" if has_video else "upload_photo")
    except Exception:
        pass

    net_retries = 0
    while True:
        try:
            await bot.send_media_group(user_id, media)
            album_paths = [p for p in batch if p not in big_photos]
            if on_sent:
                for i in range(1, len(album_paths) + 1):
                    on_sent(len(sent) + i)
            return sent + album_paths
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramNetworkError:
            net_retries += 1
            if net_retries > 3:
                for path in batch:
                    if path in big_photos:
                        continue
                    if await _send_one(bot, user_id, path):
                        sent.append(path)
                        if on_sent:
                            on_sent(len(sent))
                    await asyncio.sleep(SEND_DELAY)
                return sent
            await asyncio.sleep(2 * net_retries)
        except Exception:
            for path in batch:
                if path in big_photos:
                    continue
                if await _send_one(bot, user_id, path):
                    sent.append(path)
                await asyncio.sleep(SEND_DELAY)
            return sent


async def run_site_check(bot, user_id: int, site_id: str):
    """Проверяет один сайт с живым многостадийным прогресс-баром."""
    site = storage.get_site(user_id, site_id)
    if site is None:
        return

    # не даём двум проверкам одного сайта идти одновременно
    lock_key = f"{user_id}_{site_id}"
    lock = _site_locks.setdefault(lock_key, asyncio.Lock())
    if lock.locked():
        try:
            await bot.send_message(
                user_id, "⏳ Этот сайт уже проверяется, дождись окончания.")
        except Exception:
            pass
        return

    async with lock:
        await _run_site_check_inner(bot, user_id, site_id, site)


async def _run_site_check_inner(bot, user_id: int, site_id: str, site: dict):

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

    _spin = {"i": 0}
    _frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def render() -> str:
        stage = state["stage"]
        done, total = state["done"], state["total"]
        sp = _frames[_spin["i"] % len(_frames)]
        _spin["i"] += 1
        foot = f"\n\n📡 {url}"
        if stage == "search":
            return f"{sp} 🔍 Ищу новые медиа…" + foot
        if stage in ("search_done", "download"):
            if total == 0:
                return "🔍 Поиск завершён.\nНовых медиа нет." + foot
            return (f"{sp} ⬇️ Скачиваю\n{_progress_bar(done, total)}" + foot)
        if stage == "upload":
            return (f"{sp} ⬆️ Отправляю в Telegram\n"
                    f"{_progress_bar(done, total)}" + foot)
        return "✅ Готово!" + foot

    # фоновая задача-художник: раз в секунду перерисовывает сообщение
    async def painter():
        while state["active"]:
            await _safe_edit(status, render(), cache)
            await asyncio.sleep(1.0)

    paint_task = asyncio.create_task(painter())

    # --- поиск + скачивание (в потоке, с прогрессом) ---
    try:
        limit = site.get("limit", 200)
        if reddit.is_reddit_url(url):
            new_media = await asyncio.to_thread(
                reddit.fetch_new_media, url, site["seen"],
                site.get("sort", "new"), site.get("period", "day"),
                on_progress, limit)
        else:
            new_media = await asyncio.to_thread(
                scraper.fetch_new_media, url, site["seen"], on_progress, limit)
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
    base_done = 0  # сколько уже отправлено в предыдущих пачках

    def on_sent(_n=1):
        # дёргается после каждого отправленного файла — двигает прогресс-бар
        state["done"] = min(base_done + _n, total)

    for start in range(0, total, ALBUM_SIZE):
        chunk = new_media[start:start + ALBUM_SIZE]
        paths = [p for (_u, p) in chunk]

        sent_paths = await _send_album(bot, user_id, paths, on_sent=on_sent)
        sent_set = set(sent_paths)
        sent_count += len(sent_paths)
        base_done += len(chunk)
        state["done"] = min(base_done, total)

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
        final += (
            f"\n⚠️ Не отправлено {skipped} "
            f"(превышен лимит Telegram 50 МБ или ошибка отправки)"
        )
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
