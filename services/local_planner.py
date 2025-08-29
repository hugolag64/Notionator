# services/local_planner.py
from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

DEFAULT_PATH = os.path.join("data", "planner.json")


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


class LocalPlanner:
    """
    Planificateur local persistant des révisions PREVISIONNELLES, par jour.
    Structure JSON :
    {
      "YYYY-MM-DD": [
        {"id": "...", "title": "...", "is_college": false, "item_num": 123, "done": false}
      ]
    }
    """
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        _ensure_dir(self.path)
        self._data: Dict[str, List[Dict]] = {}
        self._load()

    # ------------- I/O -------------
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f) or {}
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------- Utils -------------
    def _key(self, d) -> str:
        if isinstance(d, datetime):
            return d.date().isoformat()
        if isinstance(d, date):
            return d.isoformat()
        return str(d)

    # ------------- API publique -------------
    def list_for(self, d) -> List[Dict]:
        """Liste des éléments planifiés pour la date."""
        items = list(self._data.get(self._key(d), []))
        # tri optionnel: non faits d'abord
        items.sort(key=lambda x: bool(x.get("done", False)))
        return items

    def add(self, d, course: Dict) -> None:
        """
        Ajoute (ou fusionne) un cours prévisionnel pour la date.
        Pas d'impact sur Notion.
        """
        k = self._key(d)
        items = self._data.setdefault(k, [])
        cid = course.get("id")
        idx = next((i for i, x in enumerate(items) if x.get("id") == cid), None)
        payload = {
            "id": cid,
            "title": course.get("title"),
            "is_college": bool(course.get("is_college", False)),
            "item_num": course.get("item_num"),
            "done": bool(course.get("done", False)),
        }
        if idx is None:
            items.append(payload)
        else:
            # merge en conservant "done" le cas échéant
            payload["done"] = items[idx].get("done", False) or payload["done"]
            items[idx] = payload
        self._save()

    def remove(self, d, course_id: str) -> None:
        """Retire un cours du prévisionnel pour la date (si besoin)."""
        k = self._key(d)
        items = self._data.get(k, [])
        new_items = [x for x in items if x.get("id") != course_id]
        if len(new_items) != len(items):
            self._data[k] = new_items
            self._save()

    def set_done(self, d, course_id: str, done: bool) -> None:
        """Marque/unmarque comme fait pour la date."""
        k = self._key(d)
        items = self._data.get(k, [])
        changed = False
        for x in items:
            if x.get("id") == course_id:
                if x.get("done") != bool(done):
                    x["done"] = bool(done)
                    changed = True
                break
        if changed:
            self._save()

    def get_done(self, d, course_id: str) -> bool:
        k = self._key(d)
        items = self._data.get(k, [])
        for x in items:
            if x.get("id") == course_id:
                return bool(x.get("done", False))
        return False
