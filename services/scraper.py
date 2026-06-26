import os
import re
import time
import logging
import tempfile
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

import requests
import yt_dlp
from bs4 import BeautifulSoup


from config import MAX_FILES_PER_RUN, DOWNLOAD_DELAY, MAX_FILE_SIZE

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXT = (".mp4", ".webm", ".mov")
MEDIA_EXT = IMAGE_EXT + VIDEO_EXT

# Суффикс размера в имени превью: ..._300px.jpg, ..._150px.png и т.п.
# Убираем его, чтобы получить ссылку на оригинал.
_SIZE_SUFFIX_RE = re.compile(r"_\d+px(?=\.[a-zA-Z0-9]+$)")

def _thumb_to_original(url: str) -> str:
    """
    Превращает ссылку превью в ссылку оригинала, убирая размерный суффикс.
    Пример: ..._0072_300px.jpg -> ..._0072.jpg
    Если суффикса нет — возвращает ссылку как есть.
    """
    base = url.split("?")[0]
    return _SIZE_SUFFIX_RE.sub("", base)


def _render_page_html(page_url: str) -> str:
    """Открывает страницу в браузере, прокручивает до конца и возвращает HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(page_url, wait_until="networkidle", timeout=60000)

        prev_height = 0
        for _ in range(50):  # предохранитель от бесконечного скролла
            page.mouse.wheel(0, 20000)
            page.wait_for_timeout(1500)
            height = page.evaluate("document.body.scrollHeight")
            if height == prev_height:
                break
            prev_height = height

        html = page.content()
        browser.close()
        return html


def find_media_urls(page_url: str) -> list:
    """
    Собирает превью со страницы галереи и преобразует их в ссылки
    на оригиналы, убирая размерный суффикс из имени файла.
    """
    html = _render_page_html(page_url)
    soup = BeautifulSoup(html, "html.parser")

    urls = []
    seen = set()

    for img in soup.find_all("img"):
        # источник превью может лежать в разных атрибутах
        src = None
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            val = img.get(attr)
            if val and val.split("?")[0].lower().endswith(IMAGE_EXT):
                src = val
                break
        if not src:
            continue

        full_thumb = urljoin(page_url, src)
        original = _thumb_to_original(full_thumb)

        if original not in seen:
            seen.add(original)
            urls.append(original)

    # На всякий случай добавим прямые видео со страницы
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src")
        if src:
            v = urljoin(page_url, src)
            if v.split("?")[0].lower().endswith(VIDEO_EXT) and v not in seen:
                seen.add(v)
                urls.append(v)

    return urls

def download_video_with_ytdlp(page_url: str) -> str | None:
    """
    Пробует скачать видео со страницы через yt-dlp.
    Работает для сайтов, которые yt-dlp поддерживает (их сотни).
    Возвращает путь к файлу или None, если видео нет/не скачалось.
    """
    import hashlib
    tmp = tempfile.gettempdir()
    uniq = hashlib.md5(page_url.encode()).hexdigest()[:8]
    out_tmpl = os.path.join(tmp, f"sc_{uniq}_%(id)s.%(ext)s")

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

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(page_url, download=True)
            path = ydl.prepare_filename(info)
            if not os.path.exists(path):
                base, _ = os.path.splitext(path)
                if os.path.exists(base + ".mp4"):
                    path = base + ".mp4"
        if os.path.exists(path) and os.path.getsize(path) <= MAX_FILE_SIZE:
            return path
        if os.path.exists(path):
            os.remove(path)
        return None
    except Exception:
        # это нормально: если на странице нет видео, yt-dlp бросает ошибку
        logger.info("yt-dlp не нашёл видео на странице: %s", page_url)
        return None

def download_file(url: str) -> str | None:
    """Скачивает файл во временную папку. Возвращает путь или None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        size = int(resp.headers.get("Content-Length", 0))
        if size and size > MAX_FILE_SIZE:
            return None

        import hashlib
        name = os.path.basename(urlparse(url).path) or "file"
        uniq = hashlib.md5(url.encode()).hexdigest()[:8]
        path = os.path.join(tempfile.gettempdir(), f"mb_{uniq}_{name}")

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
        logger.exception("Ошибка при скачивании файла: %s", url)
        return None


def fetch_new_media(page_url: str, seen: list, on_progress=None) -> list:
    """
    Возвращает список (url, путь_к_файлу) для новых медиа.
    on_progress(stage, done, total) — необязательный callback прогресса.
    """
    def report(stage, done, total):
        if on_progress:
            on_progress(stage, done, total)

    report("search", 0, 0)
    seen_set = set(seen)
    result = []

    # Сначала пробуем скачать видео со САМОЙ страницы через yt-dlp
    if page_url not in seen_set:
        video_path = download_video_with_ytdlp(page_url)
        if video_path:
            result.append((page_url, video_path))

    # Потом собираем картинки (старая логика)
    all_urls = find_media_urls(page_url)
    new_urls = [u for u in all_urls if u not in seen_set]
    if MAX_FILES_PER_RUN:
        new_urls = new_urls[:MAX_FILES_PER_RUN]

    total = len(new_urls)
    report("search_done", total, total)

    for i, url in enumerate(new_urls, start=1):
        path = download_file(url)
        if path:
            result.append((url, path))
        report("download", i, total)
        time.sleep(DOWNLOAD_DELAY)
    return result