import asyncio
import os

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import SEND_DELAY, PROGRESS_EVERY, MAX_PHOTO_SIZE
from services import storage, scraper, reddit

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

    # одно сообщение, которое будем редактировать как живой прогресс
    status = await bot.send_message(
        user_id,
        f"📥 {site['url']}\n"
        f"Найдено {total} новых медиа.\n"
        f"{_progress_bar(0, total)}\n"
        f"Отправлено 0 из {total}"
    )

    sent_count = 0
    last_text = None
    for i, (url, path) in enumerate(new_media, start=1):
        ok = await _send_one(bot, user_id, path)
        if ok:
            sent_count += 1
            # отмечаем сразу, чтобы при сбое не качать заново
            storage.mark_seen(user_id, site_id, [url])

        if os.path.exists(path):
            os.remove(path)

        # обновляем прогресс не на каждом файле, а раз в PROGRESS_EVERY,
        # и обязательно на самом последнем — иначе упрёмся во flood limit
        if i % PROGRESS_EVERY == 0 or i == total:
            new_text = (
                f"📥 {site['url']}\n"
                f"Найдено {total} новых медиа.\n"
                f"{_progress_bar(i, total)}\n"
                f"Отправлено {sent_count} из {i}"
            )
            # Telegram ругается, если текст не изменился — пропускаем такой случай
            if new_text != last_text:
                try:
                    await status.edit_text(new_text)
                    last_text = new_text
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except Exception:
                    pass

        await asyncio.sleep(SEND_DELAY)

    # финальное состояние сообщения
    try:
        await status.edit_text(
            f"✅ Готово!\n"
            f"📥 {site['url']}\n"
            f"{_progress_bar(total, total)}\n"
            f"Отправлено {sent_count} из {total}"
        )
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
