from __future__ import annotations
import threading, time
from typing import Dict, Any, List, Tuple, Callable

class NotionBatcher:
    def __init__(self, apply_fn: Callable[[List[Tuple[str, Dict[str, Any]]]], None],
                 max_batch: int = 25, max_delay: float = 0.6):
        self.apply_fn = apply_fn
        self.max_batch = max_batch
        self.max_delay = max_delay
        self.buf: List[Tuple[str, Dict[str, Any]]] = []
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None

    def _flush_locked(self):
        if not self.buf: return
        batch = self.buf[:]
        self.buf.clear()
        self.lock.release()
        try:
            self.apply_fn(batch)
        finally:
            self.lock.acquire()

    def _arm_timer(self):
        if self.timer: return
        self.timer = threading.Timer(self.max_delay, self.flush)
        self.timer.daemon = True
        self.timer.start()

    def update_later(self, page_id: str, props: Dict[str, Any]) -> None:
        with self.lock:
            self.buf.append((page_id, props))
            if len(self.buf) >= self.max_batch:
                self._flush_locked()
            else:
                self._arm_timer()

    def flush(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            self._flush_locked()
