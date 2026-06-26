import os
import re
import logging
import tempfile
import time

import requests
import yt_dlp
from playwright.sync_api import sync_playwright

from services.http import make_session, DEFAULT_TIMEOUT

from config import (
    REDDIT_LIMIT,
    REDDIT_DEFAULT_SORT, REDDIT_DEFAULT_PERIOD,
    MAX_FILE_SIZE, REDDIT_COOKIES_PATH,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.reddit.com/",
    "Connection": "keep-alive",
}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")

_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = make_session(HEADERS["User-Agent"])
    return _SESSION

def is_reddit_url(url: str) -> bool:
    return "reddit.com/r/" in url.lower()


def _subreddit_name(url: str) -> str | None:
    m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
    return m.group(1) if m else None


_REDDIT_HOSTS = (
    "https://old.reddit.com",
    "https://www.reddit.com",
    "https://reddit.com",
)


def _fetch_via_browser(subreddit: str, sort: str, period: str) -> list:
    import json

    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={REDDIT_LIMIT}&raw_json=1"
    if sort == "top":
        url += f"&t={period}"

    logger.info("Playwright: открываю %s", url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        _state = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "reddit_state.json")
        _has_state = os.path.exists(_state)
        logger.info("Playwright: файл сессии %s",
                    "НАЙДЕН" if _has_state else "НЕ найден")
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="en-US",
            storage_state=_state if _has_state else None,
        )
        page = context.new_page()
        try:
            resp = page.goto(url, timeout=60000, wait_until="domcontentloaded")
            status = resp.status if resp else "нет ответа"
            logger.info("Playwright: статус ответа = %s", status)
            body = page.inner_text("body")
        finally:
            browser.close()

    preview = body[:200].replace("\n", " ")
    logger.info("Playwright: начало ответа: %s", preview)

    try:
        data = json.loads(body)
    except Exception:
        raise RuntimeError(
            "Reddit вернул не JSON (вероятно страницу блокировки/капчи). "
            f"Начало ответа: {preview}"
        )

    posts = [c["data"] for c in data["data"]["children"]]
    logger.info("Playwright: получено постов = %s", len(posts))
    return posts


def _fetch_listing(subreddit: str, sort: str, period: str) -> list:
    params = {"limit": REDDIT_LIMIT, "raw_json": 1}
    if sort == "top":
        params["t"] = period

    last_error = None

    for host in _REDDIT_HOSTS:
        url = f"{host}/r/{subreddit}/{sort}.json"
        for attempt in range(3):
            try:
                resp = _session().get(url, headers=HEADERS,
                                      params=params, timeout=DEFAULT_TIMEOUT)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 5))
                    logger.warning("429 от %s, ждём %s сек", host, wait)
                    time.sleep(wait + 1)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return [c["data"] for c in data["data"]["children"]]
            except Exception as e:
                last_error = e
                logger.warning("Не вышло с %s: %s", host, e)
                break

    logger.warning("Все зеркала вернули блок, пробуем через Playwright…")
    try:
        return _fetch_via_browser(subreddit, sort, period)
    except Exception as e:
        raise RuntimeError(
            f"Reddit заблокировал и обычные запросы (последняя ошибка: "
            f"{last_error}), и браузер. Ошибка браузера: {e}"
        )


def _extract_media_from_post(post: dict) -> list:
    items = []
    post_id = post.get("name") or post.get("id")
    permalink = "https://www.reddit.com" + post.get("permalink", "")

    # 1) Reddit-видео — качаем через yt-dlp по permalink
    if post.get("is_video") and post.get("media", {}).get("reddit_video"):
        items.append({
            "id": post_id,
            "type": "video",
            "url": permalink,
            "permalink": permalink,
        })
        return items

    # 2) Галерея
    if post.get("is_gallery") and post.get("media_metadata"):
        for mid, meta in post["media_metadata"].items():
            try:
                src = meta.get("s", {})
                link = src.get("u") or src.get("gif")
                if not link:
                    continue
                link = link.replace("&amp;", "&")
                items.append({
                    "id": f"{post_id}_{mid}",
                    "type": "photo",
                    "url": link,
                    "permalink": permalink,
                })
            except Exception:
                logger.exception("Ошибка разбора элемента галереи")
        return items

    # 3) Прямая картинка
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
    img_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = _session().get(url, headers=img_headers,
                              timeout=DEFAULT_TIMEOUT,
                              stream=True, allow_redirects=True)
        if "reddit.com/media" in resp.url:
            logger.warning("Картинка ушла в редирект на media: %s", url)
            return None
        resp.raise_for_status()
        import hashlib
        name = os.path.basename(url.split("?")[0]) or "img.jpg"
        uniq = hashlib.md5(url.encode()).hexdigest()[:8]
        path = os.path.join(tempfile.gettempdir(), f"rd_{uniq}_{name}")
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
    Качает reddit-видео через yt-dlp по ссылке на пост.
    yt-dlp сам находит видео+аудио и склеивает их (нужен ffmpeg).
    """
    tmp = tempfile.gettempdir()
    out_tmpl = os.path.join(tmp, "rd_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "http_headers": {"User-Agent": HEADERS["User-Agent"]},
    }

    from services.http import apply_cookies
    apply_cookies(ydl_opts, REDDIT_COOKIES_PATH)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(post_url, download=True)
            if not info:
                logger.warning("yt-dlp не вернул инфо по видео: %s", post_url)
                return None
            path = ydl.prepare_filename(info)
            if not os.path.exists(path):
                base, _ = os.path.splitext(path)
                if os.path.exists(base + ".mp4"):
                    path = base + ".mp4"
        if os.path.exists(path) and os.path.getsize(path) <= MAX_FILE_SIZE:
            return path
        # больше лимита — пробуем сжать (если включено)
        if os.path.exists(path):
            from config import COMPRESS_BIG_VIDEOS
            if COMPRESS_BIG_VIDEOS:
                from services.transcode import compress_video
                smaller = compress_video(path, MAX_FILE_SIZE)
                if smaller:
                    os.remove(path)
                    return smaller
            os.remove(path)
        logger.warning("Видео превысило лимит размера: %s", post_url)
        return None
    except Exception:
        logger.exception("Ошибка скачивания видео: %s", post_url)
        return None


def fetch_new_media(subreddit_url: str, seen: list,
                    sort: str = None, period: str = None,
                    on_progress=None, limit: int = None) -> list:
    def report(stage, done, total):
        if on_progress:
            on_progress(stage, done, total)

    sort = sort or REDDIT_DEFAULT_SORT
    period = period or REDDIT_DEFAULT_PERIOD

    name = _subreddit_name(subreddit_url)
    if not name:
        raise ValueError("Не удалось распознать сабреддит в ссылке")

    report("search", 0, 0)
    posts = _fetch_listing(name, sort, period)
    seen_set = set(seen)

    all_items = []
    for post in posts:
        all_items.extend(_extract_media_from_post(post))

    new_items = [it for it in all_items if it["id"] not in seen_set]
    # limit: None/<=0 -> все; N -> первые N
    if limit and limit > 0:
        new_items = new_items[:limit]
    total = len(new_items)
    report("search_done", total, total)

    result = []
    for i, it in enumerate(new_items, start=1):
        if it["type"] == "photo":
            path = _download_image(it["url"])
        else:
            path = _download_video(it["permalink"])
        if path:
            result.append((it["id"], path))
        report("download", i, total)
    return result
