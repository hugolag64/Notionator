# services/exclusive.py
from __future__ import annotations
import threading, logging

log = logging.getLogger(__name__)
_GATE = threading.Lock()

def run_exclusive(label: str, fn, *args, **kwargs):
    """
    Exécute fn en 'section critique' pour éviter que deux grosses tâches I/O
    (Notion/Drive/HTTP) se chevauchent.
    """
    _GATE.acquire()
    log.debug("Exclusive start: %s", label)
    try:
        return fn(*args, **kwargs)
    finally:
        log.debug("Exclusive end: %s", label)
        _GATE.release()
