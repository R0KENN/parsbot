import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    from requests.packages.urllib3.util.retry import Retry


def make_session(user_agent: str = None) -> requests.Session:
    """Session с ретраями и бэкоффом на 429/5xx."""
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        backoff_factor=1.5,          # 0, 1.5, 3, 6 сек
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10,
                          pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if user_agent:
        session.headers.update({"User-Agent": user_agent})
    return session


# Таймаут (connect, read) по умолчанию
DEFAULT_TIMEOUT = (10, 60)

import os as _os


def apply_cookies(ydl_opts: dict, cookies_path: str = None) -> dict:
    """
    Добавляет в ydl_opts cookies: либо файл (если существует),
    либо извлечение из браузера (COOKIES_FROM_BROWSER).
    """
    from config import COOKIES_FROM_BROWSER
    if cookies_path and _os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path
    elif COOKIES_FROM_BROWSER:
        # формат yt-dlp: (browser_name,) или (browser, profile, ...)
        ydl_opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    return ydl_opts
