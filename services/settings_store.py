# services/settings_store.py
from __future__ import annotations
import json, os
from typing import Any, Dict
from copy import deepcopy
from config import FOCUS_DEFAULTS

DATA_DIR = "data"
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

_DEFAULTS: Dict[str, Any] = {
    "appearance": {
        "theme": "light",          # light | dark | system (si tu veux plus tard)
        "font_scale": "medium",    # small | medium | large (placeholder si besoin)
    },
    "focus": {
        "work_min": int(FOCUS_DEFAULTS["WORK_MIN"]),
        "short_break_min": int(FOCUS_DEFAULTS["SHORT_BREAK_MIN"]),
        "long_break_min": int(FOCUS_DEFAULTS["LONG_BREAK_MIN"]),
        "sessions_before_long": int(FOCUS_DEFAULTS["SESSIONS_BEFORE_LONG"]),
        "launch_spotify": True,
        "spotify_url": FOCUS_DEFAULTS["SPOTIFY_URL"],
    },
    "notifications": {
        "center_popups": True,
        "sound": False,
    },
    "shortcuts": [
        {"label": "Ouvrir Notion", "url": "https://www.notion.so/"},
    ],
}

def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """merge b into a without mutating inputs"""
    out = deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out

class SettingsStore:
    def __init__(self):
        self._data: Dict[str, Any] = deepcopy(_DEFAULTS)
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                self._data = _deep_merge(_DEFAULTS, disk)
            except Exception:
                # fichier cassé → on garde defaults
                pass

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- API simple --
    def get(self, path: str, default: Any = None) -> Any:
        cur = self._data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, path: str, value: Any):
        parts = path.split(".")
        cur = self._data
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value

    def all(self) -> Dict[str, Any]:
        return deepcopy(self._data)

# Singleton pratique
settings = SettingsStore()
