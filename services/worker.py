# services/worker.py
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# État global & executors
# ──────────────────────────────────────────────────────────────────────────────

_STOP = threading.Event()

# I/O réseau / disque, appels API, parsing léger (pas d'UI ici)
_IO_EXEC = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="io"
)

# CPU "léger" (hash/pages pdf rapides, petites conversions) — rester raisonnable
_CPU_EXEC = ThreadPoolExecutor(
    max_workers=max(1, min(2, (os.cpu_count() or 2) - 1)),
    thread_name_prefix="cpu",
)

# Exécution SÉRIALISÉE par clé (ex. "notion", "quick_summary", etc.)
_SERIAL_EXEC: Dict[str, ThreadPoolExecutor] = {}
_SERIAL_LOCK = threading.Lock()

def _get_serial_exec(key: str) -> ThreadPoolExecutor:
    with _SERIAL_LOCK:
        exec_ = _SERIAL_EXEC.get(key)
        if exec_ is None:
            exec_ = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"serial:{key}")
            _SERIAL_EXEC[key] = exec_
        return exec_

# ──────────────────────────────────────────────────────────────────────────────
# Dispatch UI (main thread) — robuste, avec fallback
# ──────────────────────────────────────────────────────────────────────────────
# Si tu as déjà utils.ui_queue.post, on l'utilisera automatiquement.
# Sinon, tu peux appeler install_ui_pump(root) pour une pompe d'événements interne.

_UI_ROOT: Optional[tk.Misc] = None
_UI_QUEUE: "queue.Queue[Callable[[], None]]" = queue.Queue()
_UI_PUMP_INSTALLED = False

def install_ui_pump(root: tk.Misc, interval_ms: int = 16) -> None:
    """
    Installe une pompe d'événements qui exécute les callbacks UI dans le thread Tk.
    À appeler APRÈS la création de la fenêtre principale.
    """
    global _UI_ROOT, _UI_PUMP_INSTALLED
    _UI_ROOT = root
    if _UI_PUMP_INSTALLED:
        return
    _UI_PUMP_INSTALLED = True

    def _pump():
        try:
            while True:
                cb = _UI_QUEUE.get_nowait()
                try:
                    cb()
                except Exception:
                    log.exception("Erreur dans callback UI")
                finally:
                    _UI_QUEUE.task_done()
        except queue.Empty:
            pass
        if not _STOP.is_set() and _UI_ROOT is not None:
            _UI_ROOT.after(interval_ms, _pump)

    root.after(interval_ms, _pump)

def _post_ui(cb: Callable[[], Any]) -> None:
    """
    Poste une fonction à exécuter sur le thread UI principal.
    Stratégie:
      1) utils.ui_queue.post si dispo
      2) pompe interne (_UI_QUEUE) si install_ui_pump(root) a été appelée
      3) en dernier recours, tentative via .after sur _UI_ROOT si présent
      4) sinon: exécute immédiatement (risque de ne pas être sur le main thread)
    """
    try:
        # Option 1 : pipeline externe si présent
        from utils.ui_queue import post  # type: ignore
        try:
            post(cb)
            return
        except Exception:
            log.debug("utils.ui_queue.post a échoué, on tente la pompe interne.", exc_info=True)
    except Exception:
        # utils.ui_queue non présent → on passe à la pompe interne
        pass

    # Option 2 : pompe interne
    if _UI_ROOT is not None:
        try:
            _UI_QUEUE.put_nowait(cb)
            return
        except Exception:
            log.exception("Impossible de poster sur la file UI interne")

        # Option 3 : fallback direct .after (au cas où la file serait HS)
        try:
            _UI_ROOT.after(0, cb)
            return
        except Exception:
            log.debug("fallback _UI_ROOT.after a échoué", exc_info=True)

    # Option 4 : dernier recours (exécution immédiate — pas idéal pour Tk)
    try:
        cb()
    except Exception:
        log.exception("Erreur dans callback UI (fallback)")

def call_ui(cb: Optional[Callable[..., Any]], *args, **kwargs) -> None:
    """Appelle `cb(*args, **kwargs)` sur le thread UI (silencieux si cb=None)."""
    if cb is None:
        return
    _post_ui(lambda: cb(*args, **kwargs))

# ──────────────────────────────────────────────────────────────────────────────
# Soumissions sécurisées
# ──────────────────────────────────────────────────────────────────────────────

def _wrap(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    """Wrapper standard: log des exceptions côté worker + chrono simple."""
    t0 = time.perf_counter()
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception("Tâche background en erreur")
        raise
    finally:
        dt = (time.perf_counter() - t0) * 1000
        log.debug("Task %s(…): %.1f ms", getattr(fn, "__name__", "<anon>"), dt)

def run_io(fn: Callable[..., Any], *args, **kwargs) -> Optional[Future]:
    """
    Soumet une tâche I/O (aucun accès Tk dedans).
    Retourne Future ou None si le pool est stoppé.
    """
    if _STOP.is_set():
        return None
    return _IO_EXEC.submit(_wrap, fn, args, kwargs)

def run_cpu(fn: Callable[..., Any], *args, **kwargs) -> Optional[Future]:
    """
    Soumet une tâche CPU 'légère' (toujours sans UI).
    Pour des traitements intensifs (OCR lourd, gros NLP), préférer un process séparé.
    """
    if _STOP.is_set():
        return None
    return _CPU_EXEC.submit(_wrap, fn, args, kwargs)

def run_serial(key: str, fn: Callable[..., Any], *args, **kwargs) -> Optional[Future]:
    """
    Soumet une tâche dans une file SÉRIALISÉE identifiée par `key`.
    Exemple: run_serial("notion", do_update, page_id, props)
    Garantit l'absence de concurrence pour une même clé.
    """
    if _STOP.is_set():
        return None
    exec_ = _get_serial_exec(key)
    return exec_.submit(_wrap, fn, args, kwargs)

# ──────────────────────────────────────────────────────────────────────────────
# Chaînage pratique
# ──────────────────────────────────────────────────────────────────────────────

def then(
    fut: Future,
    on_success: Optional[Callable[[Any], Any]] = None,
    on_error: Optional[Callable[[BaseException], Any]] = None,
    *,
    use_ui: bool = True,
) -> Future:
    """
    Attache des callbacks à un Future.
    - on_success(result) appelé si ok
    - on_error(exc) appelé si erreur (si absent: log)
    - use_ui=True → callbacks postés sur le thread UI
    Renvoie le même Future (pour chaînage).
    """
    def _cb(_f: Future):
        try:
            res = _f.result()
        except BaseException as e:
            if on_error:
                if use_ui:
                    call_ui(on_error, e)
                else:
                    try:
                        on_error(e)
                    except Exception:
                        log.exception("Erreur dans on_error")
            else:
                log.exception("Future error", exc_info=e)
            return
        if on_success:
            if use_ui:
                call_ui(on_success, res)
            else:
                try:
                    on_success(res)
                except Exception:
                    log.exception("Erreur dans on_success")

    fut.add_done_callback(_cb)
    return fut

def then_finally(
    fut: Future,
    on_finally: Optional[Callable[[], Any]],
    *,
    use_ui: bool = True,
) -> Future:
    """
    Appelle on_finally() quelle que soit l'issue du Future.
    """
    def _cb(_f: Future):
        try:
            _f.result()
        except BaseException:
            pass
        if on_finally:
            if use_ui:
                call_ui(on_finally)
            else:
                try:
                    on_finally()
                except Exception:
                    log.exception("Erreur dans on_finally")
    fut.add_done_callback(_cb)
    return fut

# ──────────────────────────────────────────────────────────────────────────────
# Décorateurs pratiques
# ──────────────────────────────────────────────────────────────────────────────

def bg_io(fn: Callable[..., Any]) -> Callable[..., Optional[Future]]:
    """
    Décorateur: exécute la fonction en arrière-plan (pool I/O).
    Retourne le Future de soumission.
    """
    @wraps(fn)
    def _inner(*a, **k) -> Optional[Future]:
        return run_io(fn, *a, **k)
    return _inner

def bg_cpu(fn: Callable[..., Any]) -> Callable[..., Optional[Future]]:
    """
    Décorateur: exécute la fonction en arrière-plan (pool CPU léger).
    Retourne le Future de soumission.
    """
    @wraps(fn)
    def _inner(*a, **k) -> Optional[Future]:
        return run_cpu(fn, *a, **k)
    return _inner

def bg_serial(key: str) -> Callable[[Callable[..., Any]], Callable[..., Optional[Future]]]:
    """
    Décorateur: exécute la fonction dans une file SÉRIALISÉE par `key`.
    Utile pour éviter les accès concurrents à Notion, par ex.
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Optional[Future]]:
        @wraps(fn)
        def _inner(*a, **k) -> Optional[Future]:
            return run_serial(key, fn, *a, **k)
        return _inner
    return deco

# ──────────────────────────────────────────────────────────────────────────────
# Arrêt propre
# ──────────────────────────────────────────────────────────────────────────────

def shutdown(wait: bool = False) -> None:
    """
    À appeler au quit (avant destroy de l'app).
    - wait=True si vous voulez attendre la fin des tâches en cours.
    """
    _STOP.set()
    # Ferme les exécuteurs sérialisés
    with _SERIAL_LOCK:
        for key, exec_ in list(_SERIAL_EXEC.items()):
            try:
                exec_.shutdown(wait=wait, cancel_futures=not wait)
            except TypeError:
                exec_.shutdown(wait=wait)
        _SERIAL_EXEC.clear()

    # Ferme CPU/IO
    try:
        _IO_EXEC.shutdown(wait=wait, cancel_futures=not wait)
    except TypeError:
        # compat Python <3.9 (cancel_futures absent)
        _IO_EXEC.shutdown(wait=wait)
    try:
        _CPU_EXEC.shutdown(wait=wait, cancel_futures=not wait)
    except TypeError:
        _CPU_EXEC.shutdown(wait=wait)
