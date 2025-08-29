# services/boot.py
from __future__ import annotations
import os
import threading
from typing import Callable, Optional

# Flags via .env (0/1)
FAST_START = os.getenv("FAST_START", "1") == "1"
DEFER_RAG  = os.getenv("DEFER_RAG",  "1") == "1"
DEFER_NOTION_PREFETCH = os.getenv("DEFER_NOTION_PREFETCH", "1") == "1"

def _run(name: str, fn: Callable, *args,
         on_done: Optional[Callable[[float, Optional[Exception]], None]] = None,
         **kwargs):
    """Exécute fn dans un thread; on_done(duration, error) si fourni."""
    import time
    def _target():
        t0 = time.perf_counter()
        err = None
        try:
            fn(*args, **kwargs)
        except Exception as e:
            err = e
        dur = (time.perf_counter() - t0)
        if on_done:
            try:
                on_done(dur, err)
            except Exception:
                pass
    th = threading.Thread(target=_target, name=f"boot.{name}", daemon=True)
    th.start()
    return th

def kickoff_background_tasks(root=None, on_log: Optional[Callable[[str], None]] = None):
    """
    Lance les grosses étapes en arrière-plan (sans bloquer l’UI).
    - RAG (PDF) : seulement si DEFER_RAG=0, sinon laissé au bouton "Scanner les PDF".
    - Notion warm/prefetch : uniquement si des helpers existent; sinon no-op.
    """
    def log(msg: str):
        if on_log:
            try: on_log(msg)
            except Exception: pass
        else:
            print(msg)

    # ---------- Notion: helpers optionnels ----------
    warm_fn = None
    prefetch_fn = None
    try:
        # Ces helpers sont optionnels ; s'ils n'existent pas, on ignore proprement.
        from services.notion_client import warm_properties_cache as _warm
        warm_fn = _warm
    except Exception:
        log("[boot] warm_properties_cache non disponible (ok)")

    try:
        from services.notion_client import prefetch_today_and_courses as _prefetch
        prefetch_fn = _prefetch
    except Exception:
        log("[boot] prefetch_today_and_courses non disponible (ok)")

    if DEFER_NOTION_PREFETCH:
        log("[boot] Notion prefetch différé (aucune action obligatoire).")
        # On ne lance rien par défaut s'il n'y a pas de helpers.
        if warm_fn:
            _run("notion_warm", warm_fn, on_done=lambda d,e: log(
                f"[boot] warm_properties_cache terminé en {d:.1f}s" + (f" (err: {e})" if e else "")
            ))
        if prefetch_fn:
            _run("notion_prefetch_today", prefetch_fn, on_done=lambda d,e: log(
                f"[boot] prefetch_today_and_courses terminé en {d:.1f}s" + (f" (err: {e})" if e else "")
            ))
    else:
        # Même logique mais lancée tout de suite si dispo
        if warm_fn:
            log("[boot] Notion warm lancé…")
            _run("notion_warm", warm_fn)
        if prefetch_fn:
            log("[boot] Notion prefetch_today lancé…")
            _run("notion_prefetch_today", prefetch_fn)

    # ---------- RAG / FAISS ----------
    try:
        from services import local_search
    except Exception:
        log("[boot] services.local_search introuvable (RAG ignoré).")
        local_search = None

    if local_search is None:
        log("[boot] tâches de fond lancées ✔ (sans RAG)")
        return

    if DEFER_RAG:
        log("[boot] RAG (scan/index PDF) laissé au bouton 'Scanner les PDF'.")
    else:
        log("[boot] RAG scan → lancement en arrière-plan")
        _run("rag_scan", local_search.ensure_index_up_to_date, verbose=True)

    log("[boot] tâches de fond lancées ✔")
