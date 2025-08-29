# services/cache.py
from __future__ import annotations
import json, os, time, threading
from typing import Any, Tuple

_CACHE_FILE = os.path.join("data", "cache.json")
_LOCK = threading.Lock()
_DEFAULT_TTL = 300  # 5 minutes

def _load() -> dict:
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(obj: dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def get(key: str) -> Any:
    with _LOCK:
        data = _load()
        item = data.get(key)
        if not item:
            return None
        value, expires_at = item
        if expires_at and time.time() > expires_at:
            # expiré
            data.pop(key, None)
            _save(data)
            return None
        return value

def set(key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
    with _LOCK:
        data = _load()
        exp = time.time() + ttl if ttl > 0 else None
        data[key] = (value, exp)
        _save(data)

def invalidate_prefix(prefix: str) -> None:
    with _LOCK:
        data = _load()
        keys = [k for k in data if k.startswith(prefix)]
        for k in keys:
            data.pop(k, None)
        _save(data)
