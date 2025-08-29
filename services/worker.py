# services/worker.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import threading, logging

log = logging.getLogger(__name__)

_STOP = threading.Event()
_EXEC = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bg")

def run_io(fn, *args, **kwargs):
    """Soumet une tâche I/O lourde (aucun appel Tk à l’intérieur)."""
    if _STOP.is_set():
        return None
    return _EXEC.submit(_wrap, fn, args, kwargs)

def _wrap(fn, args, kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception("Background task failed")
        raise

def shutdown():
    """À appeler au quit (avant destroy)."""
    _STOP.set()
    _EXEC.shutdown(wait=False, cancel_futures=True)
