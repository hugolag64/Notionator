# utils/ui_queue.py
from __future__ import annotations

import queue
import logging
import threading
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# État global
# ──────────────────────────────────────────────────────────────────────────────

_UIQ: "queue.Queue[Callable[[], Any]]" = queue.Queue()
_ROOT = None                   # objet Tk/CTk
_UI_THREAD_ID: Optional[int] = None
_STOP = threading.Event()
_INTERVAL_MS = 33              # ~30 FPS par défaut
_MAX_BATCH_PER_TICK = 200      # évite de bloquer l'UI si la file est pleine


# ──────────────────────────────────────────────────────────────────────────────
# API publique (compat descendante)
# ──────────────────────────────────────────────────────────────────────────────

def post(fn: Callable[[], Any]) -> None:
    """
    Planifie une MAJ UI (fn sans argument) depuis n’importe quel thread.
    Si post est appelé depuis le thread UI et qu'un root existe, exécute immédiatement.
    """
    if _STOP.is_set():
        return
    if _is_ui_thread() and _ROOT is not None:
        try:
            fn()
        except Exception:
            log.exception("UI task failed (direct exec)")
        return
    _UIQ.put(fn)
    _kick()

def call(fn: Callable[..., Any], *args, **kwargs) -> None:
    """
    Variante pratique: curry les args → post(lambda: fn(*args, **kwargs)).
    """
    def _bound():
        return fn(*args, **kwargs)
    post(_bound)

def install(app, *, fps: Optional[int] = None, interval_ms: Optional[int] = None) -> None:
    """
    À appeler 1x après création de la fenêtre principale (dans main).
    Compatible avec ton ancienne signature: install(app)
    - fps: nombre de drains par seconde (prioritaire sur interval_ms)
    - interval_ms: intervalle personnalisé entre drains si fourni
    """
    global _ROOT, _UI_THREAD_ID, _INTERVAL_MS
    _ROOT = app
    _UI_THREAD_ID = threading.get_ident()

    if fps and fps > 0:
        _INTERVAL_MS = max(1, int(1000 / fps))
    elif interval_ms is not None:
        _INTERVAL_MS = max(1, int(interval_ms))

    # premier tick
    try:
        _ROOT.after(_INTERVAL_MS, _drain)
    except Exception:
        # si after échoue (root pas prêt), on réessaye au prochain post()
        pass

def shutdown() -> None:
    """
    À appeler au quit (avant destroy).
    Vide la file et arrête le scheduler.
    """
    _STOP.set()
    try:
        while not _UIQ.empty():
            _UIQ.get_nowait()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Internes
# ──────────────────────────────────────────────────────────────────────────────

def _kick() -> None:
    """
    Programme un drain au prochain tick si possible.
    Idempotent côté Tk (plusieurs after() rapprochés ne posent pas de souci).
    """
    if _ROOT is None or _STOP.is_set():
        return
    try:
        _ROOT.after(_INTERVAL_MS, _drain)
    except Exception:
        # root peut être en destruction : on tente un drain immédiat
        _drain()

def _drain() -> None:
    """
    Exécuté sur le thread UI: vide la file par petits batches pour ne pas geler l'UI.
    """
    if _STOP.is_set() or _ROOT is None:
        return

    processed = 0
    while processed < _MAX_BATCH_PER_TICK:
        try:
            fn = _UIQ.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception:
            log.exception("UI task failed")
        processed += 1

    # Re-planifie le prochain tick tant que l'app vit
    try:
        _ROOT.after(_INTERVAL_MS, _drain)
    except Exception:
        # root détruit : on stoppe silencieusement
        _STOP.set()

def _is_ui_thread() -> bool:
    try:
        return threading.get_ident() == _UI_THREAD_ID
    except Exception:
        return False
