# services/worker.py
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
from typing import Any, Callable, Optional

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


# ──────────────────────────────────────────────────────────────────────────────
# Helpers UI (post sur le thread principal uniquement)
# ──────────────────────────────────────────────────────────────────────────────

def _post_ui(fn: Callable[[], Any]) -> None:
    """
    Poste une fonction à exécuter sur le thread UI principal.
    Ne lève pas si utils.ui_queue n'est pas dispo (fallback: exécute sync).
    """
    try:
        from utils.ui_queue import post  # import paresseux
        post(fn)
    except Exception:
        try:
            fn()
        except Exception:
            log.exception("Erreur dans callback UI")


def call_ui(cb: Optional[Callable[..., Any]], *args, **kwargs) -> None:
    """Appelle `cb(*args, **kwargs)` sur le thread UI (silencieux si cb=None)."""
    if cb is None:
        return
    _post_ui(lambda: cb(*args, **kwargs))


# ──────────────────────────────────────────────────────────────────────────────
# Soumissions sécurisées
# ──────────────────────────────────────────────────────────────────────────────

def _wrap(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    """Wrapper standard: log des exceptions côté worker."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception("Background task failed")
        raise


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


# ──────────────────────────────────────────────────────────────────────────────
# Arrêt propre
# ──────────────────────────────────────────────────────────────────────────────

def shutdown(wait: bool = False) -> None:
    """
    À appeler au quit (avant destroy de l'app).
    - wait=True si vous voulez attendre la fin des tâches en cours.
    """
    _STOP.set()
    try:
        _IO_EXEC.shutdown(wait=wait, cancel_futures=not wait)
    except TypeError:
        # compat Python <3.9 (cancel_futures absent)
        _IO_EXEC.shutdown(wait=wait)
    try:
        _CPU_EXEC.shutdown(wait=wait, cancel_futures=not wait)
    except TypeError:
        _CPU_EXEC.shutdown(wait=wait)
