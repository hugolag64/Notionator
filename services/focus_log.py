# services/focus_log.py
from __future__ import annotations
import os
import json
import threading
from datetime import date, timedelta
from typing import List, Dict, Tuple

# Fichier d'agrégat quotidien : une ligne par date
LOG_FILE = os.path.join("data", "focus_log.json")

# Verrou pour éviter les écritures concurrentes
_LOCK = threading.Lock()


# ------------------------------ I/O bas niveau ------------------------------
def _ensure_file() -> None:
    """Crée le fichier JSON vide si inexistant."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def _load_rows() -> List[Dict]:
    """Charge la liste brute de dicts [{date, minutes}, ...]."""
    _ensure_file()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _rows_to_map(rows: List[Dict]) -> Dict[str, int]:
    """Agrège toutes les entrées par date -> minutes (déduplication)."""
    m: Dict[str, int] = {}
    for r in rows:
        try:
            d = str(r.get("date", "")).strip()
            minutes = int(r.get("minutes", 0) or 0)
        except Exception:
            continue
        if not d or minutes <= 0:
            continue
        m[d] = m.get(d, 0) + minutes
    return m


def _map_to_rows(m: Dict[str, int]) -> List[Dict]:
    """Transforme le dict trié par date croissante en liste de dicts."""
    rows: List[Dict] = []
    for d in sorted(m.keys()):
        rows.append({"date": d, "minutes": int(m[d])})
    return rows


def _atomic_save_rows(rows: List[Dict]) -> None:
    """Écrit le fichier de manière atomique pour éviter la corruption."""
    try:
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LOG_FILE)
    except Exception:
        # On évite toute exception bloquante pour l'app
        pass


# ------------------------------ API publique ------------------------------
def log_minutes(minutes: int) -> None:
    """
    Ajoute des minutes de focus pour aujourd’hui (>=1).
    Cumule si une entrée existe déjà pour la date du jour.
    Écriture atomique + déduplication.
    """
    try:
        minutes = int(minutes)
    except Exception:
        return
    if minutes <= 0:
        return

    today = date.today().isoformat()

    with _LOCK:
        rows = _load_rows()
        m = _rows_to_map(rows)
        m[today] = m.get(today, 0) + minutes
        _atomic_save_rows(_map_to_rows(m))


def get_today_minutes() -> int:
    """Retourne les minutes déjà loguées aujourd’hui."""
    rows = _load_rows()
    m = _rows_to_map(rows)
    return int(m.get(date.today().isoformat(), 0))


def get_last_days(n: int = 7) -> List[Tuple[date, int]]:
    """
    Retourne une liste [(date_obj, minutes)] pour les n derniers jours,
    en incluant les jours à 0 minute.
    """
    rows = _load_rows()
    m = _rows_to_map(rows)

    today = date.today()
    days: List[Tuple[date, int]] = []
    for i in range(n - 1, -1, -1):
        d = today - timedelta(days=i)
        days.append((d, int(m.get(d.isoformat(), 0))))
    return days


def get_week_stats() -> Dict[str, int]:
    """
    Stats des 7 derniers jours :
      {
        "total": minutes cumulées (7j),
        "avg":   moyenne journalière (entier),
        "today": minutes aujourd’hui
      }
    """
    last7 = get_last_days(7)
    total = sum(m for _, m in last7)
    avg = total // 7
    today_minutes = last7[-1][1] if last7 else 0
    return {"total": int(total), "avg": int(avg), "today": int(today_minutes)}


def get_total() -> int:
    """Retourne le cumul total historique (tous les jours)."""
    rows = _load_rows()
    m = _rows_to_map(rows)
    return int(sum(m.values()))


# ------------------------------ (optionnel) maintenance ------------------------------
def _compact() -> None:
    """
    Compacte le fichier en fusionnant d’éventuels doublons de date.
    Utile si d’anciennes versions ont généré plusieurs entrées/jour.
    """
    with _LOCK:
        rows = _load_rows()
        m = _rows_to_map(rows)
        _atomic_save_rows(_map_to_rows(m))
