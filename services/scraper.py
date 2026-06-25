import os
import time
import logging
import tempfile
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

import requests
from bs4 import BeautifulSoup

from config import MAX_FILES_PER_RUN, DOWNLOAD_DELAY, MAX_FILE_SIZE

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXT = (".mp4", ".webm", ".mov")
MEDIA_EXT = IMAGE_EXT + VIDEO_EXT

# Слова, по которым видно, что это миниатюра, а не оригинал
_THUMB_HINTS = ("thumb", "icon", "logo", "avatar", "sprite", "/small", "_small", "preview")

# Сколько ждать появления оригинала после клика (мс)
_CLICK_WAIT_MS = 8000


def _robots_allowed(url: str) -> bool:
    """
    Проверяем robots.txt. Логика:
    - файла нет (404) или он пустой -> разрешено;
    - файл есть -> уважаем его правила;
    - сервер недоступен / иная ошибка -> разрешено.
    """
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        resp = requests.get(robots_url, headers=HEADERS, timeout=10)

        if resp.status_code == 404 or not resp.text.strip():
            return True
        if resp.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp.can_fetch(HEADERS["User-Agent"], url)
        return True
    except Exception:
        return True


def _looks_like_thumb(url: str) -> bool:
    low = url.lower()
    return any(hint in low for hint in _THUMB_HINTS)


def _is_media_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(MEDIA_EXT)


def _scroll_to_bottom(page):
    """Прокручивает страницу до конца, чтобы подгрузились все превью."""
    prev_height = 0
    for _ in range(50):  # предохранитель от бесконечного скролла
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(1500)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height


def _collect_thumb_selectors(page) -> int:
    """
    Возвращает количество кликабельных превью на странице.
    Используем селектор, который ловит и <a> с картинками, и сами <img>.
    """
    return page.locator("a:has(img), a > img, .gallery img, img").count()


def find_media_urls(page_url: str) -> list:
    """
    Открывает галерею в браузере, кликает по каждому превью
    и перехватывает URL появившегося полноразмерного фото/видео.
    """
    if not _robots_allowed(page_url):
        raise PermissionError("robots.txt запрещает доступ к этой странице")

    found_urls = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_page(user_agent=HEADERS["User-Agent"])

        # --- Перехват сети: ловим все медиа-ответы, которые грузит браузер ---
        captured = []

        def _on_response(response):
            url = response.url
            ctype = response.headers.get("content-type", "")
            is_media = (
                ctype.startswith("image/")
                or ctype.startswith("video/")
                or _is_media_url(url)
            )
            if is_media and not _looks_like_thumb(url):
                captured.append(url)

        context.on("response", _on_response)

        context.goto(page_url, wait_until="networkidle", timeout=60000)
        _scroll_to_bottom(context)

        # Все превью на странице
        thumbs = context.locator("a:has(img), .gallery a, .thumb, img")
        count = thumbs.count()
        logger.info("Найдено превью на странице: %s", count)

        if MAX_FILES_PER_RUN:
            count = min(count, MAX_FILES_PER_RUN)

        for i in range(count):
            try:
                # запоминаем длину буфера до клика, чтобы взять только новое
                before = len(captured)

                thumb = thumbs.nth(i)
                thumb.scroll_into_view_if_needed(timeout=5000)
                thumb.click(timeout=5000)

                # ждём, пока браузер подгрузит оригинал
                context.wait_for_timeout(_CLICK_WAIT_MS // 4)

                # 1) пробуем взять то, что реально загрузилось по сети
                new_loads = captured[before:]
                original = None
                if new_loads:
                    # самый "тяжёлый" по URL обычно и есть оригинал
                    original = max(new_loads, key=len)

                # 2) запасной вариант — крупное изображение в лайтбоксе
                if not original:
                    original = _read_lightbox_image(context, page_url)

                if original:
                    full = urljoin(page_url, original)
                    if full not in seen:
                        seen.add(full)
                        found_urls.append(full)

                # закрываем лайтбокс, если открылся (Esc обычно закрывает)
                context.keyboard.press("Escape")
                context.wait_for_timeout(500)

            except Exception:
                logger.exception("Ошибка при клике по превью #%s", i)
                # пытаемся восстановиться: закрыть возможный лайтбокс
                try:
                    context.keyboard.press("Escape")
                except Exception:
                    pass
                continue

            time.sleep(DOWNLOAD_DELAY)

        browser.close()

    return found_urls


def _read_lightbox_image(page, page_url: str) -> str | None:
    """
    Запасной способ: читает src самого крупного изображения в открытом
    лайтбоксе/модалке, если перехват сети ничего не дал.
    """
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        candidates = []
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-full",
                         "data-zoom-image", "data-large"):
                src = img.get(attr)
                if src and _is_media_url(src) and not _looks_like_thumb(src):
                    candidates.append(urljoin(page_url, src))

        if not candidates:
            return None
        return max(candidates, key=len)
    except Exception:
        logger.exception("Ошибка при чтении лайтбокса")
        return None


def download_file(url: str) -> str | None:
    """Скачивает файл во временную папку. Возвращает путь или None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        size = int(resp.headers.get("Content-Length", 0))
        if size and size > MAX_FILE_SIZE:
            return None

        name = os.path.basename(urlparse(url).path) or "file"
        path = os.path.join(tempfile.gettempdir(), f"mb_{name}")

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


def fetch_new_media(page_url: str, seen: list) -> list:
    """
    Возвращает список (url, путь_к_файлу) для новых медиа.
    Ограничено MAX_FILES_PER_RUN.
    """
    all_urls = find_media_urls(page_url)
    seen_set = set(seen)
    new_urls = [u for u in all_urls if u not in seen_set]
    if MAX_FILES_PER_RUN:
        new_urls = new_urls[:MAX_FILES_PER_RUN]

    result = []
    for url in new_urls:
        path = download_file(url)
        if path:
            result.append((url, path))
        time.sleep(DOWNLOAD_DELAY)
    return result
