import json
import os
import threading
from config import STORAGE_PATH

_lock = threading.Lock()


def _ensure_file():
    os.makedirs(os.path.dirname(STORAGE_PATH), exist_ok=True)
    if not os.path.exists(STORAGE_PATH):
        with open(STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({"users": {}}, f)


def _load():
    _ensure_file()
    with open(STORAGE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_user(user_id: int) -> dict:
    """Возвращает данные пользователя, создавая их при необходимости."""
    user_id = str(user_id)
    with _lock:
        data = _load()
        if user_id not in data["users"]:
            data["users"][user_id] = {
                "sites": [],          # список {"url":..., "hours": int, "seen": [...]}
            }
            _save(data)
        return data["users"][user_id]


def add_site(user_id: int, url: str, hours: int):
    user_id = str(user_id)
    with _lock:
        data = _load()
        data["users"].setdefault(user_id, {"sites": []})
        data["users"][user_id]["sites"].append({
            "url": url,
            "hours": hours,
            "seen": [],
        })
        _save(data)


def remove_site(user_id: int, index: int) -> bool:
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        if 0 <= index < len(sites):
            sites.pop(index)
            _save(data)
            return True
        return False


def list_sites(user_id: int) -> list:
    return get_user(user_id).get("sites", [])


def mark_seen(user_id: int, index: int, urls: list):
    user_id = str(user_id)
    with _lock:
        data = _load()
        sites = data["users"].get(user_id, {}).get("sites", [])
        if 0 <= index < len(sites):
            existing = sites[index]["seen"]
            existing_set = set(existing)
            for u in urls:
                if u not in existing_set:
                    existing.append(u)
                    existing_set.add(u)
            # не даём списку расти бесконечно — оставляем последние 1000 по порядку
            sites[index]["seen"] = existing[-1000:]
            _save(data)


def all_users() -> dict:
    """Все пользователи и их сайты — нужно планировщику при старте."""
    with _lock:
        return _load()["users"]
