import json
import os
import threading
import uuid

from config import STORAGE_PATH, SEEN_LIMIT

_lock = threading.Lock()


def _ensure_file():
    os.makedirs(os.path.dirname(STORAGE_PATH), exist_ok=True)
    if not os.path.exists(STORAGE_PATH):
        with open(STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({"users": {}}, f)


def _migrate(data: dict) -> bool:
    """
    Приводит старые записи к актуальному формату: добавляет недостающие
    поля id / sort / period / seen. Возвращает True, если что-то изменилось.
    """
    changed = False
    for user in data.get("users", {}).values():
        for site in user.get("sites", []):
            if "id" not in site:
                site["id"] = uuid.uuid4().hex
                changed = True
            if "sort" not in site:
                site["sort"] = "new"
                changed = True
            if "period" not in site:
                site["period"] = "day"
                changed = True
            if "seen" not in site:
                site["seen"] = []
                changed = True
            if "limit" not in site:
                site["limit"] = 200   # дефолт для старых записей
                changed = True
    return changed


def _load():
    _ensure_file()
    with open(STORAGE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if _migrate(data):
        _save(data)
    return data


def _save(data):
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(user_id: int) -> dict:
    """Возвращает данные пользователя, создавая их при необходимости."""
    user_id = str(user_id)
    with _lock:
        data = _load()
        if user_id not in data["users"]:
            data["users"][user_id] = {"sites": []}
            _save(data)
        return data["users"][user_id]


def add_site(user_id: int, url: str, hours: int,
             sort: str = "new", period: str = "day",
             limit: int = 200) -> str:
    """Добавляет сайт/сабреддит и возвращает его id."""
    user_id = str(user_id)
    site_id = uuid.uuid4().hex
    with _lock:
        data = _load()
        data["users"].setdefault(user_id, {"sites": []})
        data["users"][user_id]["sites"].append({
            "id": site_id,
            "url": url,
            "hours": hours,
            "sort": sort,        # new / hot / top — для reddit
            "period": period,    # hour/day/week/month/year/all — для top
            "limit": limit,      # 0 = без ограничения (все по очереди)
            "seen": [],
        })
        _save(data)
    return site_id


def remove_site(user_id: int, site_id: str) -> bool:
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        for i, site in enumerate(sites):
            if site["id"] == site_id:
                sites.pop(i)
                _save(data)
                return True
        return False


def list_sites(user_id: int) -> list:
    return get_user(user_id).get("sites", [])


def get_site(user_id: int, site_id: str) -> dict | None:
    for site in list_sites(user_id):
        if site["id"] == site_id:
            return site
    return None

def update_limit(user_id: int, site_id: str, limit: int) -> bool:
    """Меняет лимит медиа за раз у конкретного сайта. 0 = все."""
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        for site in sites:
            if site["id"] == site_id:
                site["limit"] = limit
                _save(data)
                return True
        return False

def update_hours(user_id: int, site_id: str, hours: int) -> bool:
    """Меняет интервал проверки (в часах) у конкретного сайта."""
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        for site in sites:
            if site["id"] == site_id:
                site["hours"] = hours
                _save(data)
                return True
        return False

def mark_seen(user_id: int, site_id: str, urls: list):
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        for site in sites:
            if site["id"] != site_id:
                continue
            existing = site.get("seen", [])
            existing_set = set(existing)
            for u in urls:
                if u not in existing_set:
                    existing.append(u)
                    existing_set.add(u)
            # не даём списку расти бесконечно
            site["seen"] = existing[-SEEN_LIMIT:]
            _save(data)
            return


def all_users() -> dict:
    """Все пользователи и их сайты — нужно планировщику при старте."""
    with _lock:
        return _load()["users"]
