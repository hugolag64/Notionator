# services/daily_todo_generator.py
from __future__ import annotations

from datetime import datetime, timedelta
import os
import threading
import inspect
from typing import Dict, Optional, Tuple, List

from services.notion_client import get_notion_client
from services.profiler import profiled, span
from services.settings_store import settings
from config import TO_DO_DATABASE_ID

STATUS_TODO_TODAY = "En cours"
STATUS_TODO_FUTUR = "À faire"
STATUS_TODO_DONE  = "Terminé"

# --- Garde-fou process-wide ---
_RUN_ONCE = False
_RUN_LOCK = threading.Lock()


# --------- Verrou journalier disque (atomique) ----------
def _daily_lock_path(today_iso: str) -> str:
    os.makedirs("data", exist_ok=True)
    return os.path.join("data", f".todo.{today_iso}.lock")

def _acquire_daily_file_lock(today_iso: str):
    """
    Essaie de créer le fichier de lock du jour en mode O_EXCL.
    Retourne un descripteur si OK, sinon None (déjà pris).
    """
    path = _daily_lock_path(today_iso)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags)
        # Contexte informatif (pid, caller)
        try:
            caller = inspect.stack()[2].filename  # un cran au-dessus de generate()->caller
        except Exception:
            caller = "unknown"
        os.write(fd, f"pid={os.getpid()} caller={caller}\n".encode("utf-8"))
        return fd
    except FileExistsError:
        return None
    except Exception:
        return None

def _release_daily_file_lock(fd, today_iso: str):
    """
    Relâche le lock (on supprime le fichier, la persistance réelle est assurée par settings).
    """
    try:
        if fd is not None:
            os.close(fd)
    except Exception:
        pass
    try:
        os.remove(_daily_lock_path(today_iso))
    except Exception:
        pass


# --------- Settings (mémoire longue) ----------
def _already_generated_today_settings(today_iso: str) -> bool:
    try:
        last = str(settings.get("todo.last_generated_date", "")).strip()
        return last == today_iso
    except Exception:
        return False

def _mark_generated_today_settings(today_iso: str) -> None:
    try:
        settings.set("todo.last_generated_date", today_iso)
        settings.save()
    except Exception:
        # soft-fail : ne casse pas le flux si la persistance échoue
        pass


# --------- Utilitaires ---------
def date_fr(d: datetime) -> str:
    mois = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre"
    ]
    return f"{d.day} {mois[d.month - 1]} {d.year}"


class DailyToDoGenerator:
    """
    Aligne la fenêtre J-1..J+2 à chaque démarrage (idempotent, par date absolue) :
      - J-1   -> Terminé
      - J     -> En cours
      - J+1   -> À faire (créée si absente)
      - J+2   -> À faire (créée si absente)

    Optimisations clés :
      - Prefetch de la fenêtre en **1 seule query** (OR sur 4 dates)
      - Réutilisation du cache mémoire du NotionAPI (write-through)
      - Updates statut **no-op** évitées (gérées côté NotionAPI)
    """

    def __init__(self):
        self.notion = get_notion_client()
        self.now = datetime.today()
        self.today_str = self.now.strftime("%Y-%m-%d")

    # ------------------ Helpers Notion ------------------

    def _title_for(self, d: datetime) -> str:
        return f"📅 {date_fr(d)}"

    def _get_page_by_date(self, date_iso: str):
        # Utilise le cache interne du client si présent
        return self.notion.get_todo_page_by_date(TO_DO_DATABASE_ID, date_iso)

    def _set_status(self, page_id: str, status_name: str) -> None:
        try:
            # Le client évite déjà les updates no-op
            self.notion.set_todo_status(page_id, TO_DO_DATABASE_ID, status_name)
        except Exception:
            pass

    def _create_minimal_page(self, date_iso: str, status_name: str, title: str):
        page = self.notion.create_minimal_todo_page(TO_DO_DATABASE_ID, title, date_iso)
        if page:
            self._set_status(page["id"], status_name)
            print(f"[+] Page To-Do créée pour {date_iso} ({title})")
        else:
            print(f"[!] Échec création page To-Do pour {date_iso}")

    def _upsert_for(self, d: datetime, status_name: str, *, preload: Optional[dict] = None) -> None:
        """
        Même logique qu'avant, mais accepte une page préchargée (évite une requête supplémentaire).
        """
        date_iso = d.strftime("%Y-%m-%d")
        title = self._title_for(d)
        page = preload if preload is not None else self._get_page_by_date(date_iso)
        if page:
            self._set_status(page["id"], status_name)
            print(f"[=] Page {date_iso} trouvée → statut « {status_name} » appliqué")
        else:
            self._create_minimal_page(date_iso, status_name, title)

    def _mark_day_done(self, d: datetime) -> None:
        date_iso = d.strftime("%Y-%m-%d")
        page = self._get_page_by_date(date_iso)
        if page:
            self._set_status(page["id"], STATUS_TODO_DONE)
            print(f"[→] Page du {date_iso} marquée comme « {STATUS_TODO_DONE} »")
        else:
            print(f"[i] Aucune page à marquer en Terminé pour {date_iso}")

    def _prefetch_window(self) -> Dict[str, Optional[dict]]:
        """
        Précharge en **une seule requête** les pages To-Do pour: J-1, J, J+1, J+2
        Alimente aussi le cache interne du NotionAPI pour ces dates.
        Retourne un dict: { "Jm1": page|None, "J": page|None, "J1": page|None, "J2": page|None }
        """
        d_m1 = (self.now - timedelta(days=1)).strftime("%Y-%m-%d")
        d_0  = self.now.strftime("%Y-%m-%d")
        d_1  = (self.now + timedelta(days=1)).strftime("%Y-%m-%d")
        d_2  = (self.now + timedelta(days=2)).strftime("%Y-%m-%d")
        wanted = [d_m1, d_0, d_1, d_2]

        pages_by_date: Dict[str, Optional[dict]] = {k: None for k in ["Jm1", "J", "J1", "J2"]}

        # 1) Si déjà en cache côté client, on récupère sans requête
        cached_hits = {
            d: self.notion._cache_get_todo_page(TO_DO_DATABASE_ID, d)  # type: ignore[attr-defined]
            for d in wanted
        }
        if all(cached_hits.values()):
            return {
                "Jm1": cached_hits[d_m1],
                "J":   cached_hits[d_0],
                "J1":  cached_hits[d_1],
                "J2":  cached_hits[d_2],
            }

        # 2) Query unique OR[equals] sur les 4 dates
        date_prop = self.notion._get_prop_cached(TO_DO_DATABASE_ID, "Date", expected_type="date")  # type: ignore[attr-defined]
        or_filters = [{"property": date_prop, "date": {"equals": d}} for d in wanted]

        with span("notion.databases.query:todo_prefetch_window"):
            resp = self.notion.client.databases.query(  # accès direct client OK ici
                database_id=TO_DO_DATABASE_ID,
                filter={"or": or_filters},
                page_size=100,
            )

        # 3) Indexation par date (start[:10]) + write-through dans le cache interne
        found_map: Dict[str, dict] = {}
        for r in resp.get("results", []):
            props = r.get("properties", {}) or {}
            dval  = (props.get(date_prop, {}) or {}).get("date", {}) or {}
            iso   = (dval.get("start") or "")[:10]
            if iso:
                found_map[iso] = r
                try:
                    # write-through cache
                    self.notion._cache_set_todo_page(TO_DO_DATABASE_ID, iso, r)  # type: ignore[attr-defined]
                except Exception:
                    pass

        pages_by_date["Jm1"] = found_map.get(d_m1)
        pages_by_date["J"]   = found_map.get(d_0)
        pages_by_date["J1"]  = found_map.get(d_1)
        pages_by_date["J2"]  = found_map.get(d_2)
        return pages_by_date

    def _window_state(self) -> Tuple[Dict[str, Optional[dict]], bool]:
        """
        Récupère en une passe les pages J-1, J, J+1, J+2 et indique si la fenêtre J..J+2 est complète.
        """
        pages = self._prefetch_window()
        ok = bool(pages["J"] and pages["J1"] and pages["J2"])
        return pages, ok

    # ------------------ API principale ------------------

    @profiled("todo.generate")
    def generate(self, mark_yesterday_done: bool = True, origin: str = "unknown") -> None:
        """
        Exécute l’alignement To-Do du jour (une seule vraie exécution/jour):

          - Garde-fou process-wide (_RUN_ONCE)    → évite double appel dans le même process.
          - Verrou fichier atomique (YYYY-MM-DD)  → évite travaux concurrents multi-appels.
          - Settings (todo.last_generated_date)   → évite relancer sur démarrages suivants.

          Si les pages J..J+2 ne sont pas complètes, on ignore le settings et on régénère.
        """
        global _RUN_ONCE
        with _RUN_LOCK:
            if _RUN_ONCE:
                print("[·] Générateur déjà exécuté (process) — skip instantané.")
                return
            _RUN_ONCE = True

        # 0) Prefetch de fenêtre (J-1..J+2) en 1 requête
        with span("todo.window_probe"):
            pages, window_ok_first_probe = self._window_state()

        # 1) Skip si déjà fait aujourd'hui ET fenêtre complète
        if _already_generated_today_settings(self.today_str) and window_ok_first_probe:
            print("[·] Générateur déjà exécuté aujourd'hui (fenêtre OK) — skip.")
            return

        # 2) Verrou atomique disque : si un autre appel est en cours aujourd'hui → skip
        lock_fd = _acquire_daily_file_lock(self.today_str)
        if lock_fd is None:
            print("[·] Générateur: lock journalier déjà pris — skip.")
            return

        print(f"[todo.generate] start (origin={origin})")
        try:
            # 3) J-1 → Terminé (no-op si déjà bon)
            if mark_yesterday_done:
                with span("todo.mark_yesterday_done"):
                    self._mark_day_done(self.now - timedelta(days=1))

            # 4) Upsert J, J+1, J+2 (réutilise les pages préchargées, pas de requery)
            with span("todo.upsert_window"):
                self._upsert_for(self.now, STATUS_TODO_TODAY, preload=pages["J"])
                self._upsert_for(self.now + timedelta(days=1), STATUS_TODO_FUTUR, preload=pages["J1"])
                self._upsert_for(self.now + timedelta(days=2), STATUS_TODO_FUTUR, preload=pages["J2"])

            # 5) Marque comme généré (settings)
            _mark_generated_today_settings(self.today_str)
        finally:
            _release_daily_file_lock(lock_fd, self.today_str)
            print("[todo.generate] done")
