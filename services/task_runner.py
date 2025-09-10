# services/task_runner.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, Future
from queue import Queue, Empty
import threading, time

class TaskRunner:
    def __init__(self, max_workers: int = 8):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._results = Queue()

    def submit(self, fn, *args, **kwargs) -> Future:
        def wrapper():
            try:
                res = fn(*args, **kwargs)
                self._results.put((None, res))
            except Exception as e:
                self._results.put((e, None))
        return self._executor.submit(wrapper)

    def poll(self, on_result, on_error=lambda e: None):
        """Appeler régulièrement depuis Tkinter (après 100–200 ms)."""
        try:
            while True:
                e, r = self._results.get_nowait()
                if e: on_error(e)
                else: on_result(r)
        except Empty:
            pass
