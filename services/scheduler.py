from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services import storage, scraper

scheduler = AsyncIOScheduler()


import asyncio
import os

from aiogram.exceptions import TelegramRetryAfter
from config import SEND_DELAY, PROGRESS_EVERY


async def _send_one(bot, user_id, path):
    """Отправляет один файл с обработкой flood limit."""
    file = FSInputFile(path)
    is_video = path.lower().endswith((".mp4", ".webm", ".mov"))
    while True:
        try:
            if is_video:
                await bot.send_video(user_id, file)
            else:
                await bot.send_photo(user_id, file)
            return True
        except TelegramRetryAfter as e:
            # Telegram просит подождать N секунд — ждём и пробуем снова
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            return False


async def run_site_check(bot, user_id: int, index: int):
    """Проверяет один сайт и отправляет новые медиа пользователю."""
    sites = storage.list_sites(user_id)
    if index >= len(sites):
        return
    site = sites[index]

    try:
        new_media = scraper.fetch_new_media(site["url"], site["seen"])
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
    await bot.send_message(
        user_id, f"📥 Найдено {total} новых медиа на {site['url']}. Отправляю…")

    sent_urls = []
    for i, (url, path) in enumerate(new_media, start=1):
        ok = await _send_one(bot, user_id, path)
        if ok:
            sent_urls.append(url)
            # отмечаем сразу, чтобы при сбое не качать заново
            storage.mark_seen(user_id, index, [url])

        if os.path.exists(path):
            os.remove(path)

        # прогресс
        if i % PROGRESS_EVERY == 0 and i < total:
            await bot.send_message(user_id, f"… {i} из {total}")

        # пауза между отправками — защита от flood limit
        await asyncio.sleep(SEND_DELAY)

    await bot.send_message(
        user_id,
        f"✅ Готово! Отправлено {len(sent_urls)} из {total} с {site['url']}")


def schedule_site(bot: Bot, user_id: int, index: int, hours: int):
    job_id = f"{user_id}_{index}"
    scheduler.add_job(
        run_site_check,
        "interval",
        hours=hours,
        args=[bot, user_id, index],
        id=job_id,
        replace_existing=True,
    )


def reschedule_all(bot: Bot):
    """При старте бота восстанавливаем задачи из хранилища."""
    users = storage.all_users()
    for uid, info in users.items():
        for i, site in enumerate(info.get("sites", [])):
            schedule_site(bot, int(uid), i, site["hours"])


def start():
    if not scheduler.running:
        scheduler.start()
