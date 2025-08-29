from __future__ import annotations
import threading, time
from typing import Callable, Optional

class DebouncedJob:
    def __init__(self, wait_s: float, fn: Callable[[], None]):
        self.wait_s = wait_s
        self.fn = fn
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def trigger(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.wait_s, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self):
        try:
            self.fn()
        finally:
            with self._lock:
                self._timer = None

def run_after(delay_s: float, fn: Callable[[], None]):
    t = threading.Timer(delay_s, fn)
    t.daemon = True
    t.start()
    return t
