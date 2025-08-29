# services/logic.py
from __future__ import annotations
from datetime import date, datetime
from typing import List, Dict, Any, Optional

from services.data_manager import DataManager
from services.logger import get_logger

logger = get_logger(__name__)


def _iso(d: date | datetime | str | None) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def courses_due_on_local(dm: DataManager, target: date) -> List[Dict[str, Any]]:
    """
    Retourne la liste des cours 'dus' à la date `target` en lisant EXCLUSIVEMENT
    le cache local de DataManager (dm.cache["courses"]), sans requête Notion.
    Champs attendus (adapter si tes clés diffèrent) :
      - row["next_review_date"] : "YYYY-MM-DD" (ou None)
      - row["archived"]         : bool
      - row["statut"]           : str (facultatif)
    """
    due = []
    target_iso = _iso(target)
    courses = dm.cache.get("courses", {})

    for cid, row in courses.items():
        if not isinstance(row, dict):
            continue
        if row.get("archived") is True:
            continue
        nr = row.get("next_review_date")
        if _iso(nr) == target_iso:
            due.append(row)

    logger.debug("[logic] courses_due_on_local(%s) -> %d cours", target_iso, len(due))
    return due


def ensure_due_courses(dm: DataManager, target: date) -> List[Dict[str, Any]]:
    """
    Helper : tente le local, si cache vide → fallback réseau (ancienne impl.).
    Appelle la fonction réseau existante si tu en as une (ex: dm.notion.courses_due_on(...)).
    """
    local = courses_due_on_local(dm, target)
    if local:
        return local

    # Fallback (optionnel) : uniquement si cache totalement froid
    try:
        if not dm.cache.get("courses"):
            logger.info("[logic] cache vide → fallback réseau courses_due_on(...)")
            # ↓ Remplace par ta méthode réseau si nécessaire (ou retourne [])
            return dm.notion.courses_due_on(target)  # si tu as déjà cette API
    except Exception as e:
        logger.warning("[logic] fallback réseau a échoué: %r", e)

    return local  # vide si rien
