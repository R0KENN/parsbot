import os
import re
import logging
import subprocess
import tempfile

import requests

from config import (
    REDDIT_USER_AGENT, REDDIT_LIMIT,
    REDDIT_DEFAULT_SORT, REDDIT_DEFAULT_PERIOD,
    MAX_FILE_SIZE,
)

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": REDDIT_USER_AGENT}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def is_reddit_url(url: str) -> bool:
    """Определяет, что ссылка ведёт на сабреддит."""
    return "reddit.com/r/" in url.lower()


def _subreddit_name(url: str) -> str | None:
    """Достаёт имя сабреддита из ссылки вида https://reddit.com/r/pics."""
    m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
    return m.group(1) if m else None


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"],
                       capture_output=True, check=True)
        return True
    except Exception:
        return False


def _fetch_listing(subreddit: str, sort: str, period: str) -> list:
    """Берёт ленту сабреддита через публичный JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": REDDIT_LIMIT, "raw_json": 1}
    # период t= нужен только для сортировки top
    if sort == "top":
        params["t"] = period
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return [child["data"] for child in data["data"]["children"]]


def _extract_media_from_post(post: dict) -> list:
    """
    Возвращает список словарей: {"id":, "type": "photo"/"video", "url":, "permalink":}
    Обрабатывает: прямые картинки, галереи, reddit-видео.
    """
    items = []
    post_id = post.get("name") or post.get("id")
    permalink = "https://www.reddit.com" + post.get("permalink", "")

    # 1) Reddit-видео (v.redd.it)
    if post.get("is_video") and post.get("media", {}).get("reddit_video"):
        # для видео отдаём ссылку на сам пост — качать будем через yt-dlp
        items.append({
            "id": post_id,
            "type": "video",
            "url": permalink,
            "permalink": permalink,
        })
        return items

    # 2) Галерея (несколько фото в посте)
    if post.get("is_gallery") and post.get("media_metadata"):
        for i, (mid, meta) in enumerate(post["media_metadata"].items()):
            try:
                # s.u — ссылка на оригинал; иногда в meta["s"]["gif"]
                src = meta.get("s", {})
                link = src.get("u") or src.get("gif")
                if not link:
                    continue
                link = link.replace("&amp;", "&")
                items.append({
                    "id": f"{post_id}_{i}",
                    "type": "photo",
                    "url": link,
                    "permalink": permalink,
                })
            except Exception:
                logger.exception("Ошибка разбора элемента галереи")
        return items

    # 3) Прямая картинка (i.redd.it и т.п.)
    url = post.get("url_overridden_by_dest") or post.get("url", "")
    if url.split("?")[0].lower().endswith(IMAGE_EXT):
        items.append({
            "id": post_id,
            "type": "photo",
            "url": url,
            "permalink": permalink,
        })

    return items


def _download_image(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()
        name = os.path.basename(url.split("?")[0]) or "img.jpg"
        path = os.path.join(tempfile.gettempdir(), f"rd_{name}")
        downloaded = 0
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > MAX_FILE_SIZE:
                    f.close()
                    os.remove(path)
                    return None
                f.write(chunk)
        return path
    except Exception:
        logger.exception("Ошибка скачивания картинки: %s", url)
        return None


def _download_video(post_url: str) -> str | None:
    """
    Качает reddit-видео через yt-dlp. Если есть ffmpeg — со звуком,
    иначе yt-dlp отдаст видео без звука.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp не установлен — видео скачать нельзя")
        return None

    out_template = os.path.join(tempfile.gettempdir(), "rd_%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/b" if _has_ffmpeg() else "b",
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(post_url, download=True)
            path = ydl.prepare_filename(info)
            # после merge расширение может стать .mp4
            if not os.path.exists(path):
                base = os.path.splitext(path)[0]
                for ext in (".mp4", ".mkv", ".webm"):
                    if os.path.exists(base + ext):
                        path = base + ext
                        break
            if os.path.exists(path) and os.path.getsize(path) <= MAX_FILE_SIZE:
                return path
            return None
    except Exception:
        logger.exception("Ошибка скачивания видео: %s", post_url)
        return None


def fetch_new_media(subreddit_url: str, seen: list,
                    sort: str = None, period: str = None) -> list:
    """
    Совместима по смыслу со scraper.fetch_new_media, плюс sort/period.
    Возвращает список (uid, путь_к_файлу) для новых медиа.
    """
    sort = sort or REDDIT_DEFAULT_SORT
    period = period or REDDIT_DEFAULT_PERIOD

    name = _subreddit_name(subreddit_url)
    if not name:
        raise ValueError("Не удалось распознать сабреддит в ссылке")

    posts = _fetch_listing(name, sort, period)
    seen_set = set(seen)

    all_items = []
    for post in posts:
        all_items.extend(_extract_media_from_post(post))

    new_items = [it for it in all_items if it["id"] not in seen_set]

    result = []
    for it in new_items:
        if it["type"] == "photo":
            path = _download_image(it["url"])
        else:
            path = _download_video(it["url"])
        if path:
            result.append((it["id"], path))
    return result
