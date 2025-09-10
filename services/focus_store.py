from __future__ import annotations
import os, json
from datetime import date, datetime, timedelta
from typing import List, Dict

_LOG = os.path.join("data", "focus_log.json")
os.makedirs(os.path.dirname(_LOG), exist_ok=True)

def _load() -> List[Dict]:
    try:
        if not os.path.exists(_LOG):
            return []
        with open(_LOG, "r", encoding="utf-8") as f:
            data = json.load(f) or []
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []

def _dump(rows: List[Dict]) -> None:
    tmp = _LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _LOG)

def add_minutes(minutes: int, day_iso: str | None = None) -> None:
    """Ajoute des minutes au jour donné (ISO YYYY-MM-DD). Merge s’il existe déjà une entrée du jour."""
    if minutes <= 0:
        return
    day = day_iso or date.today().isoformat()
    rows = _load()

    # merge by day
    for r in rows:
        if r.get("date") == day:
            r["minutes"] = int(r.get("minutes", 0)) + int(minutes)
            _dump(rows)
            return

    rows.append({"date": day, "minutes": int(minutes)})
    _dump(rows)

def minutes_on(day: date | str) -> int:
    d = day if isinstance(day, str) else day.isoformat()
    total = 0
    for r in _load():
        if r.get("date") == d:
            total += int(r.get("minutes", 0))
    return int(total)

def minutes_today() -> int:
    return minutes_on(date.today())

def minutes_this_week(monday_first: bool = True) -> int:
    """Additionne de lundi à aujourd’hui (inclus)."""
    today = date.today()
    weekday = today.weekday()  # lundi=0
    start = today - timedelta(days=weekday if monday_first else (weekday + 1) % 7)
    days = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]
    total = 0
    for r in _load():
        if r.get("date") in days:
            total += int(r.get("minutes", 0))
    return int(total)
