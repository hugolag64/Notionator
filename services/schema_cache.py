# services/schema_cache.py
from __future__ import annotations
import json, os, threading
from typing import Optional, Dict

_CACHE_FILE = os.path.join("data", "schema_cache.json")
_LOCK = threading.Lock()


def _load() -> Dict:
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: Dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def get_prop_id(db_id: str, prop_name: str) -> Optional[str]:
    with _LOCK:
        d = _load()
        return d.get(db_id, {}).get(prop_name)


def set_prop_id(db_id: str, prop_name: str, prop_id: str) -> None:
    with _LOCK:
        d = _load()
        d.setdefault(db_id, {})[prop_name] = prop_id
        _save(d)
