import os
import re
import logging
import subprocess
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

from config import (
    REDDIT_LIMIT,
    REDDIT_DEFAULT_SORT, REDDIT_DEFAULT_PERIOD,
    MAX_FILE_SIZE, REDDIT_COOKIES_PATH,
)

logger = logging.getLogger(__name__)

# Reddit режет запросы с "питоновским" User-Agent и без браузерных заголовков.
# Поэтому притворяемся обычным браузером — это главное лекарство от 403 Blocked.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.reddit.com/",
    "Connection": "keep-alive",
}

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

# Несколько зеркал Reddit. Пробуем по очереди — если один домен блокирует,
# другой часто отдаёт данные. old.reddit.com обычно самый "мягкий".
_REDDIT_HOSTS = (
    "https://old.reddit.com",
    "https://www.reddit.com",
    "https://reddit.com",
)

def _fetch_via_browser(subreddit: str, sort: str, period: str) -> list:
    """
    Запасной способ: открываем JSON-страницу Reddit настоящим браузером
    через Playwright. Reddit не считает его ботом, поэтому 403 обходится.
    """
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

    # покажем первые символы ответа, чтобы понять, что прислал Reddit
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
    """
    Берёт ленту сабреддита через публичный JSON, без OAuth.
    Перебирает зеркала и делает повтор при 429 (слишком много запросов).
    """
    params = {"limit": REDDIT_LIMIT, "raw_json": 1}
    # период t= нужен только для сортировки top
    if sort == "top":
        params["t"] = period

    last_error = None

    for host in _REDDIT_HOSTS:
        url = f"{host}/r/{subreddit}/{sort}.json"
        # до 3 попыток на каждый домен (на случай 429)
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=HEADERS,
                                    params=params, timeout=30)
                if resp.status_code == 429:
                    # Reddit просит подождать — ждём и пробуем снова
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
                break  # этот домен не отвечает — пробуем следующий

    # обычные запросы заблокированы — пробуем настоящий браузер
    logger.warning("Все зеркала вернули блок, пробуем через Playwright…")
    try:
        return _fetch_via_browser(subreddit, sort, period)
    except Exception as e:
        raise RuntimeError(
            f"Reddit заблокировал и обычные запросы (последняя ошибка: "
            f"{last_error}), и браузер. Ошибка браузера: {e}"
        )


def _extract_media_from_post(post: dict) -> list:
    """
    Возвращает список словарей: {"id":, "type": "photo"/"video", "url":, "permalink":}
    Обрабатывает: прямые картинки, галереи, reddit-видео.
    """
    items = []
    post_id = post.get("name") or post.get("id")
    permalink = "https://www.reddit.com" + post.get("permalink", "")

    # 1) Reddit-видео (v.redd.it) — берём прямую ссылку на видеопоток
    if post.get("is_video") and post.get("media", {}).get("reddit_video"):
        rv = post["media"]["reddit_video"]
        fallback = rv.get("fallback_url", "")
        if fallback:
            items.append({
                "id": post_id,
                "type": "video",
                "url": fallback,  # полный URL с токеном — нужен для fallback-скачивания
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
                    "id": f"{post_id}_{mid}",
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
    # Для i.redd.it / preview.redd.it НЕ нужен Referer на reddit.com —
    # иначе Reddit редиректит на www.reddit.com/media и отдаёт 403.
    img_headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=img_headers, timeout=60,
                            stream=True, allow_redirects=True)
        # если всё же редиректнуло на страницу-блокировку reddit.com — стоп
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


import xml.etree.ElementTree as ET


def _download_to(url: str, path: str, referer: str = None) -> bool:
    """Качает один файл по прямой ссылке. True — успех.
    Для v.redd.it нужен корректный Referer и БЕЗ сжатия (br ломает mp4)."""
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        # НЕ просим br/gzip для бинарного файла — иначе поток может побиться
        "Accept-Encoding": "identity",
        "Referer": referer or "https://www.reddit.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=120, stream=True)
        if resp.status_code != 200:
            logger.warning("HTTP %s при скачивании %s", resp.status_code, url)
            return False
        downloaded = 0
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > MAX_FILE_SIZE:
                    f.close()
                    os.remove(path)
                    logger.warning("Файл превысил лимит размера: %s", url)
                    return False
                f.write(chunk)
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception:
        logger.exception("Ошибка скачивания файла: %s", url)
        return False


# Пространство имён в манифесте MPEG-DASH
_MPD_NS = {"mpd": "urn:mpeg:dash:schema:mpd:2011"}


def _parse_dash_playlist(base: str, permalink: str):
    """
    Скачивает и разбирает DASHPlaylist.mpd сабреддит-видео.
    Возвращает (video_name, audio_name|None) — РЕАЛЬНЫЕ имена дорожек,
    взятые из манифеста, а не угаданные.
    base — https://v.redd.it/<id>
    """
    mpd_url = f"{base}/DASHPlaylist.mpd"
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/dash+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": permalink or "https://www.reddit.com/",
    }
    try:
        resp = requests.get(mpd_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logger.warning("MPD недоступен (HTTP %s): %s",
                           resp.status_code, mpd_url)
            return None, None
        root = ET.fromstring(resp.content)
    except Exception:
        logger.exception("Не удалось разобрать DASHPlaylist.mpd: %s", mpd_url)
        return None, None

    best_video = None
    best_video_h = -1
    best_audio = None
    best_audio_br = -1

    for adapt in root.iter("{urn:mpeg:dash:schema:mpd:2011}AdaptationSet"):
        ctype = (adapt.get("contentType") or adapt.get("mimeType") or "").lower()
        for rep in adapt.findall("mpd:Representation", _MPD_NS):
            # имя файла дорожки лежит в BaseURL
            base_url_el = rep.find("mpd:BaseURL", _MPD_NS)
            if base_url_el is None or not base_url_el.text:
                continue
            name = base_url_el.text.strip()
            mime = (rep.get("mimeType") or ctype).lower()

            if "video" in mime or "video" in ctype:
                h = int(rep.get("height") or 0)
                if h > best_video_h:
                    best_video_h = h
                    best_video = name
            elif "audio" in mime or "audio" in ctype:
                br = int(rep.get("bandwidth") or 0)
                if br > best_audio_br:
                    best_audio_br = br
                    best_audio = name

    return best_video, best_audio


def _download_video(video_url: str, permalink: str = None) -> str | None:
    """
    Качает reddit-видео самым надёжным способом: читает DASHPlaylist.mpd,
    берёт оттуда реальные имена видео- и аудиодорожек, качает их и
    склеивает через ffmpeg. Не зависит от угадывания имён и от cookies.
    video_url — прямая ссылка вида https://v.redd.it/<id>/DASH_720.mp4
    """
    try:
        # базовый адрес вида https://v.redd.it/<id>
        base = video_url.rsplit("/", 1)[0]
        vid_id = base.rsplit("/", 1)[-1]

        tmp = tempfile.gettempdir()
        video_path = os.path.join(tmp, f"rd_{vid_id}_v.mp4")
        audio_path = os.path.join(tmp, f"rd_{vid_id}_a.mp4")
        out_path = os.path.join(tmp, f"rd_{vid_id}.mp4")

        # 1) читаем манифест и узнаём реальные имена дорожек
        v_name, a_name = _parse_dash_playlist(base, permalink)

        # 2) определяем URL видеодорожки
        if v_name:
            video_track_url = f"{base}/{v_name}"
        else:
            # манифест не прочитался — используем fallback_url как есть
            video_track_url = video_url

        # 3) качаем видеодорожку (Referer = ссылка на пост!)
        if not _download_to(video_track_url, video_path, referer=permalink):
            logger.warning("Не удалось скачать видеопоток: %s", video_track_url)
            return None

        # 4) качаем аудиодорожку, если она есть в манифесте
        audio_ok = False
        if a_name:
            audio_ok = _download_to(f"{base}/{a_name}", audio_path,
                                    referer=permalink)

        # 5) есть аудио и есть ffmpeg — склеиваем
        if audio_ok and _has_ffmpeg():
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                     "-c", "copy", "-loglevel", "error", out_path],
                    check=True,
                )
                for p in (video_path, audio_path):
                    if os.path.exists(p):
                        os.remove(p)
                if os.path.exists(out_path) and \
                        os.path.getsize(out_path) <= MAX_FILE_SIZE:
                    return out_path
                return None
            except Exception:
                logger.exception("Ошибка склейки ffmpeg для %s", video_url)
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                return video_path if os.path.exists(video_path) else None

        # 6) аудио нет (немое видео) или нет ffmpeg — отдаём только видео
        if os.path.exists(audio_path):
            os.remove(audio_path)
        if os.path.exists(video_path) and \
                os.path.getsize(video_path) <= MAX_FILE_SIZE:
            return video_path
        return None
    except Exception:
        logger.exception("Ошибка скачивания видео: %s", video_url)
        return None


def fetch_new_media(subreddit_url: str, seen: list,
                    sort: str = None, period: str = None,
                    on_progress=None) -> list:
    """
    Возвращает список (uid, путь_к_файлу) для новых медиа.
    on_progress(stage, done, total) — необязательный callback прогресса.
    """
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
    total = len(new_items)
    report("search_done", total, total)

    result = []
    for i, it in enumerate(new_items, start=1):
        if it["type"] == "photo":
            path = _download_image(it["url"])
        else:
            path = _download_video(it["url"], it.get("permalink"))
        if path:
            result.append((it["id"], path))
        report("download", i, total)
    return result
