# services/serial_executor.py
from __future__ import annotations
import threading, queue, traceback

class SerialExecutor:
    """Exécute les tâches l’une après l’autre (un seul thread daemon)."""
    def __init__(self, name: str = "serial-worker"):
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._t = threading.Thread(target=self._run, name=name, daemon=True)
        self._t.start()

    def submit(self, fn, *args, **kwargs):
        self._q.put((fn, args, kwargs))

    def _run(self):
        while True:
            fn, args, kwargs = self._q.get()
            try:
                fn(*args, **kwargs)
            except Exception:
                traceback.print_exc()
            finally:
                self._q.task_done()
