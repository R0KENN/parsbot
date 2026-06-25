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


def _progress_bar(done: int, total: int, width: int = 10) -> str:
    """Рисует текстовый прогресс-бар вида ▓▓▓▓░░░░░░ 40%."""
    if total <= 0:
        return ""
    ratio = done / total
    filled = int(ratio * width)
    bar = "▓" * filled + "░" * (width - filled)
    return f"{bar} {int(ratio * 100)}%"


async def _send_album(bot, user_id, batch):
    """
    Шлёт пачку медиа одним альбомом (до ALBUM_SIZE штук).
    batch — список путей к файлам. Возвращает кол-во успешно отправленных.
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
            return len(batch)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            # альбом не ушёл целиком — пробуем по одному, чтобы не терять всё
            ok = 0
            for path in batch:
                if await _send_one(bot, user_id, path):
                    ok += 1
                await asyncio.sleep(SEND_DELAY)
            return ok


async def run_site_check(bot, user_id: int, site_id: str):
    """Проверяет один сайт и отправляет новые медиа пользователю."""
    site = storage.get_site(user_id, site_id)
    if site is None:
        return

    try:
        if reddit.is_reddit_url(site["url"]):
            new_media = await asyncio.to_thread(
                reddit.fetch_new_media, site["url"], site["seen"],
                site.get("sort", "new"), site.get("period", "day"))
        else:
            new_media = await asyncio.to_thread(
                scraper.fetch_new_media, site["url"], site["seen"])
    except PermissionError:
        await bot.send_message(
            user_id, f"⚠️ robots.txt запрещает доступ к {site['url']}")
        return
    except Exception as e:
        await bot.send_message(user_id, f"⚠️ Ошибка при {site['url']}: {e}")
        return

    if not new_media:
        return

    total = len(new_media)

    status = await bot.send_message(
        user_id,
        f"📥 {site['url']}\n"
        f"Найдено {total} новых медиа.\n"
        f"{_progress_bar(0, total)}\n"
        f"Отправлено 0 из {total}"
    )

    try:
        await bot.pin_chat_message(
            user_id, status.message_id, disable_notification=True)
        pinned = True
    except Exception:
        pinned = False

    sent_count = 0
    processed = 0
    last_text = None

    # шлём альбомами по ALBUM_SIZE
    for start in range(0, total, ALBUM_SIZE):
        chunk = new_media[start:start + ALBUM_SIZE]
        paths = [p for (_url, p) in chunk]

        ok = await _send_album(bot, user_id, paths)
        sent_count += ok
        processed += len(chunk)

        # отмечаем отправленными ровно столько, сколько ушло
        for (url, _p) in chunk[:ok]:
            storage.mark_seen(user_id, site_id, [url])

        # чистим временные файлы пачки
        for (_url, p) in chunk:
            if os.path.exists(p):
                os.remove(p)

        # обновляем прогресс
        new_text = (
            f"📥 {site['url']}\n"
            f"Найдено {total} новых медиа.\n"
            f"{_progress_bar(processed, total)}\n"
            f"Отправлено {sent_count} из {processed}"
        )
        if new_text != last_text:
            try:
                await status.edit_text(new_text)
                last_text = new_text
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                pass

        await asyncio.sleep(SEND_DELAY)

    skipped = total - sent_count
    final = (
        f"✅ Готово!\n"
        f"📥 {site['url']}\n"
        f"{_progress_bar(total, total)}\n"
        f"Отправлено {sent_count} из {total}"
    )
    if skipped > 0:
        final += f"\n⚠️ Пропущено {skipped} (слишком большие или ошибка)"
    try:
        await status.edit_text(final)
    except Exception:
        pass

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
