# utils/debounce.py
from __future__ import annotations
import threading, time
from functools import wraps
from typing import Callable

def debounce(wait_ms: int):
    def decorator(fn: Callable):
        timer = None
        lock = threading.Lock()

        @wraps(fn)
        def wrapper(*args, **kwargs):
            nonlocal timer
            def run():
                time.sleep(wait_ms / 1000.0)
                with lock:
                    # si personne n'a relancé
                    if timer is t:
                        fn(*args, **kwargs)

            with lock:
                t = threading.Thread(target=run, daemon=True)
                timer = t
                t.start()
        return wrapper
    return decorator
