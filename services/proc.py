# services/proc.py
from __future__ import annotations
import threading
from multiprocessing import get_context, Process
import logging
from typing import Callable, Set

log = logging.getLogger(__name__)
_ctx = get_context("spawn")  # safe sous Windows / Tk
_gate = threading.Lock()     # exclusivité côté parent (pas 2 jobs en parallèle)
_children: Set[Process] = set()
_stop = False

def run_proc_exclusive(label: str, fn: Callable[[], None]) -> None:
    """
    Lance `fn` dans un SOUS-PROCESSUS, en exclusif (côté parent).
    - Ne fait AUCUN appel Tk/Numpy/PIL dans le parent.
    - Le child importe ce qu'il veut, exécute, puis exit.
    """
    global _stop
    if _stop:
        return
    with _gate:
        p = _ctx.Process(target=_wrap, args=(label, fn), daemon=True)
        p.start()
        _children.add(p)

def _wrap(label: str, fn: Callable[[], None]) -> None:
    # Contexte minimal du child (logs indépendants)
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(f"proc[{label}]")
    try:
        log.info("start")
        fn()
        log.info("done")
    except Exception:
        log.exception("failed")
    finally:
        # rien à retourner : le parent ne bloque pas
        pass

def shutdown_procs(timeout: float = 0.5) -> None:
    """Tente d'arrêter proprement les sous-processus encore vivants à la fermeture."""
    global _stop
    _stop = True
    dead = []
    for p in list(_children):
        if not p.is_alive():
            dead.append(p)
            continue
        try:
            p.join(timeout=timeout)
            if p.is_alive():
                p.terminate()
        except Exception:
            pass
        finally:
            dead.append(p)
    for p in dead:
        _children.discard(p)
