import os
import time
import tempfile
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

import requests
from bs4 import BeautifulSoup

from config import MAX_FILES_PER_RUN, DOWNLOAD_DELAY, MAX_FILE_SIZE

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXT = (".mp4", ".webm", ".mov")


def _robots_allowed(url: str) -> bool:
    """
    Проверяем robots.txt. Логика:
    - файла нет (404) или он пустой -> разрешено;
    - файл есть -> уважаем его правила;
    - сервер недоступен / иная ошибка -> разрешено (не блокируем зря).
    """
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        resp = requests.get(robots_url, headers=HEADERS, timeout=10)

        # Нет файла или он пустой — ограничений нет
        if resp.status_code == 404 or not resp.text.strip():
            return True

        # Файл есть — разбираем его правила
        if resp.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp.can_fetch(HEADERS["User-Agent"], url)

        # Любой другой ответ — не блокируем
        return True
    except Exception:
        # Сеть недоступна и т.п. — не мешаем работе
        return True


def _render_page_html(page_url: str) -> str:
    """Открывает страницу в браузере, прокручивает до конца и возвращает HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(page_url, wait_until="networkidle", timeout=60000)

        # Прокручиваем вниз, пока высота страницы не перестанет расти
        prev_height = 0
        for _ in range(50):  # предохранитель от бесконечного скролла
            page.mouse.wheel(0, 20000)
            page.wait_for_timeout(1500)  # ждём подгрузки
            height = page.evaluate("document.body.scrollHeight")
            if height == prev_height:
                break
            prev_height = height

        html = page.content()
        browser.close()
        return html


def _extract_full_image(page_url: str) -> str | None:
    """Заходит на страницу картинки и возвращает ссылку на полноразмерное изображение."""
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Берём самое большое изображение на странице:
        # сначала пробуем типичные места, где лежит оригинал.
        candidates = []

        # 1) ссылка <a> на сам файл изображения
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href.lower().split("?")[0].endswith(IMAGE_EXT):
                candidates.append(urljoin(page_url, href))

        # 2) все <img> на странице
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                src = img.get(attr)
                if src and src.lower().split("?")[0].endswith(IMAGE_EXT):
                    candidates.append(urljoin(page_url, src))

        if not candidates:
            return None
        # эвристика: самый длинный URL часто = оригинал, а не миниатюра.
        # но обычно первый <a> на файл — это и есть оригинал.
        return candidates[0]
    except Exception:
        return None


def find_media_urls(page_url: str) -> list:
    """
    Находит полноразмерные изображения, заходя на страницу каждой картинки.
    """
    if not _robots_allowed(page_url):
        raise PermissionError("robots.txt запрещает доступ к этой странице")

    html = _render_page_html(page_url)
    soup = BeautifulSoup(html, "html.parser")

    # Шаг 1: собираем ссылки на страницы отдельных картинок.
    page_links = []
    seen_links = set()
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(page_url, href)
        # ссылки на страницы картинок, а НЕ на сами файлы и не на внешние сайты
        if full.startswith(page_url.split("?")[0].rsplit("/", 1)[0]) \
                and not full.lower().split("?")[0].endswith(IMAGE_EXT + VIDEO_EXT) \
                and full not in seen_links:
            seen_links.add(full)
            page_links.append(full)

    # Шаг 2: на каждой странице картинки берём полноразмерное изображение.
    urls = []
    seen = set()
    for link in page_links:
        full_img = _extract_full_image(link)
        if full_img and full_img not in seen:
            seen.add(full_img)
            urls.append(full_img)
        time.sleep(DOWNLOAD_DELAY)  # пауза между заходами на страницы

    # Шаг 3: на всякий случай добавим прямые видео со страницы-галереи.
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src")
        if src:
            v = urljoin(page_url, src)
            if v.lower().split("?")[0].endswith(VIDEO_EXT) and v not in seen:
                seen.add(v)
                urls.append(v)

    return urls

def download_file(url: str) -> str | None:
    """Скачивает файл во временную папку. Возвращает путь или None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        size = int(resp.headers.get("Content-Length", 0))
        if size and size > MAX_FILE_SIZE:
            return None  # слишком большой для бота

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
