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

from services.http import make_session, DEFAULT_TIMEOUT


from config import MAX_FILES_PER_RUN, DOWNLOAD_DELAY, MAX_FILE_SIZE, SITE_COOKIES_PATH

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"}

_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = make_session(HEADERS["User-Agent"])
    return _SESSION

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m3u8", ".mpd")
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


# Расширения/типы, по которым ловим видео в сетевых запросах
_NET_VIDEO_HINTS = (".mp4", ".webm", ".mov", ".m3u8", ".mpd")


def _render_page_html(page_url: str):
    """
    Открывает страницу в браузере, прокручивает до конца.
    Возвращает (html, sniffed_video_urls) — sniffed_video_urls это
    ссылки на видео, пойманные в сетевых запросах самой страницы.
    """
    sniffed = []
    sniffed_set = set()

    def _on_response(resp):
        try:
            u = resp.url
            ct = (resp.headers or {}).get("content-type", "").lower()
            low = u.split("?")[0].lower()
            is_video = low.endswith(_NET_VIDEO_HINTS) or "video/" in ct
            if is_video and u not in sniffed_set:
                sniffed_set.add(u)
                sniffed.append(u)
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.on("response", _on_response)
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
        return html, sniffed


def find_media_urls(page_url: str) -> list:
    """
    Собирает превью со страницы галереи и преобразует их в ссылки
    на оригиналы, убирая размерный суффикс из имени файла.
    """
    html, _sniffed = _render_page_html(page_url)
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

    # Прямые видео со страницы — смотрим больше атрибутов
    for tag in soup.find_all(["video", "source"]):
        for attr in ("src", "data-src", "data-video", "data-mp4"):
            src = tag.get(attr)
            if not src:
                continue
            v = urljoin(page_url, src)
            if v.split("?")[0].lower().endswith(VIDEO_EXT) and v not in seen:
                seen.add(v)
                urls.append(v)

    # Ссылки <a>, ведущие прямо на видеофайл
    for a in soup.find_all("a", href=True):
        v = urljoin(page_url, a["href"])
        if v.split("?")[0].lower().endswith(VIDEO_EXT) and v not in seen:
            seen.add(v)
            urls.append(v)

    return urls

def find_post_links(page_url: str, html: str = None) -> list:
    """
    Собирает со страницы ссылки на отдельные посты/видео того же домена.
    Эти ссылки потом по одной прогоняются через yt-dlp.
    """
    if html is None:
        html, _ = _render_page_html(page_url)
    soup = BeautifulSoup(html, "html.parser")

    base_host = urlparse(page_url).netloc
    links = []
    seen = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(page_url, a["href"])
        host = urlparse(full).netloc
        # только тот же сайт и не сама стартовая страница
        if host != base_host:
            continue
        if full.split("#")[0].rstrip("/") == page_url.split("#")[0].rstrip("/"):
            continue
        if full in seen:
            continue
        seen.add(full)
        links.append(full)

    return links

def _resolve_ytdlp_path(ydl, entry) -> str | None:
    """По info-словарю одного видео возвращает реальный путь к файлу."""
    path = ydl.prepare_filename(entry)
    if not os.path.exists(path):
        base, _ = os.path.splitext(path)
        if os.path.exists(base + ".mp4"):
            path = base + ".mp4"
    if os.path.exists(path):
        if os.path.getsize(path) <= MAX_FILE_SIZE:
            return path
        from config import COMPRESS_BIG_VIDEOS
        if COMPRESS_BIG_VIDEOS:
            from services.transcode import compress_video
            smaller = compress_video(path, MAX_FILE_SIZE)
            if smaller:
                os.remove(path)
                return smaller
        os.remove(path)  # слишком большое для Telegram
        logger.info("Видео превысило лимит размера: %s", path)
    return None


def download_videos_with_ytdlp(page_url: str, allow_playlist: bool = True) -> list:
    """
    Скачивает через yt-dlp ВСЕ видео по ссылке.
    Если ссылка — плейлист/страница со списком, качает каждое видео.
    Возвращает список путей к скачанным файлам (может быть пустым).
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
        "noplaylist": not allow_playlist,
        "ignoreerrors": True,
        "retries": 3,
        "http_headers": {"User-Agent": HEADERS["User-Agent"]},
    }
    if MAX_FILES_PER_RUN:
        ydl_opts["playlistend"] = MAX_FILES_PER_RUN
    from services.http import apply_cookies
    apply_cookies(ydl_opts, SITE_COOKIES_PATH)

    paths = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(page_url, download=True)
            if not info:
                return []

            # Плейлист / страница со списком видео
            if isinstance(info, dict) and info.get("entries"):
                for entry in info["entries"]:
                    if not entry:
                        continue
                    p = _resolve_ytdlp_path(ydl, entry)
                    if p:
                        paths.append(p)
            else:
                # Одиночное видео
                p = _resolve_ytdlp_path(ydl, info)
                if p:
                    paths.append(p)
        return paths
    except Exception:
        logger.info("yt-dlp не нашёл видео на странице: %s", page_url)
        return paths


def download_video_with_ytdlp(page_url: str) -> str | None:
    """Совместимость: возвращает ОДИН путь (первый), как раньше."""
    res = download_videos_with_ytdlp(page_url, allow_playlist=False)
    return res[0] if res else None

def download_file(url: str) -> str | None:
    """Скачивает файл во временную папку. Возвращает путь или None."""
    try:
        resp = _session().get(url, headers=HEADERS,
                              timeout=DEFAULT_TIMEOUT, stream=True)
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


def fetch_new_media(page_url: str, seen: list, on_progress=None,
                    limit: int = None) -> list:
    """
    Возвращает список (url, путь_к_файлу) для новых медиа.
    on_progress(stage, done, total) — необязательный callback прогресса.
    """
    def report(stage, done, total):
        if on_progress:
            on_progress(stage, done, total)

    # limit: None -> берём из конфига; 0 -> без ограничения (все); N -> N
    if limit is None:
        limit = MAX_FILES_PER_RUN
    eff_limit = None if limit == 0 else limit

    report("search", 0, 0)
    seen_set = set(seen)
    result = []

    # Рендерим страницу один раз и переиспользуем HTML + пойманные видео
    html, sniffed_videos = _render_page_html(page_url)

    # 1) Пробуем все видео с самой страницы через yt-dlp (включая плейлисты)
    if page_url not in seen_set:
        for idx, vpath in enumerate(download_videos_with_ytdlp(page_url)):
            # уникальный ключ на каждое видео, чтобы seen не схлопывал их в одно
            result.append((f"{page_url}#v{idx}", vpath))
        seen_set.add(page_url)

    # 2) Прямые медиа (картинки + прямые видеофайлы)
    all_urls = find_media_urls(page_url)

    # 2b) Видео, пойманные из сетевых запросов браузера
    for v in sniffed_videos:
        if v not in all_urls:
            all_urls.append(v)

    # 3) Ссылки на отдельные посты — каждую пробуем через yt-dlp
    post_links = find_post_links(page_url, html)
    new_posts = [u for u in post_links if u not in seen_set]
    if eff_limit:
        new_posts = new_posts[:eff_limit]

    new_urls = [u for u in all_urls if u not in seen_set]
    if eff_limit:
        new_urls = new_urls[:eff_limit]

    total = len(new_urls) + len(new_posts)
    report("search_done", total, total)

    done = 0

    # Скачиваем прямые файлы
    for url in new_urls:
        done += 1
        if url.split("?")[0].lower().endswith(VIDEO_EXT):
            path = download_video_with_ytdlp(url) or download_file(url)
        else:
            path = download_file(url)
        if path:
            result.append((url, path))
        report("download", done, total)
        time.sleep(DOWNLOAD_DELAY)

    # Прогоняем ссылки постов через yt-dlp (видео внутри постов)
    for url in new_posts:
        done += 1
        path = download_video_with_ytdlp(url)
        if path:
            result.append((url, path))
        report("download", done, total)
        time.sleep(DOWNLOAD_DELAY)

    return result