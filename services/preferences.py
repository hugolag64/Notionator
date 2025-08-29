# services/preferences.py
from __future__ import annotations
import json
import os
from typing import Any, Dict

_PREF_PATH = os.path.join("data", "preferences.json")
_DEFAULTS: Dict[str, Any] = {
    "theme": "system",  # "light" | "dark" | "system"
}

def _ensure_dir():
    os.makedirs(os.path.dirname(_PREF_PATH), exist_ok=True)

def load_prefs() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.exists(_PREF_PATH):
        return _DEFAULTS.copy()
    try:
        with open(_PREF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # backfill defaults
        for k, v in _DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return _DEFAULTS.copy()

def save_prefs(prefs: Dict[str, Any]) -> None:
    _ensure_dir()
    with open(_PREF_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)

def get(key: str, default: Any = None) -> Any:
    prefs = load_prefs()
    return prefs.get(key, default)

def set(key: str, value: Any) -> None:
    prefs = load_prefs()
    prefs[key] = value
    save_prefs(prefs)
