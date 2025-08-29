# services/dnd_batcher.py
from __future__ import annotations

class DropBatcher:
    """
    Regroupe les drops arrivés dans une courte fenêtre (ex: 250ms),
    puis appelle un callback avec {course_id: [files...]}.
    """
    def __init__(self, tk_widget, on_flush, delay_ms: int = 250):
        self.widget = tk_widget
        self.on_flush = on_flush
        self.delay_ms = delay_ms
        self._after_id = None
        self._buffer = []  # list[(course_id, path)]

    def add(self, course_id: str, files: list[str]):
        self._buffer.extend((course_id, f) for f in files)
        try:
            if self._after_id:
                self.widget.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = self.widget.after(self.delay_ms, self._flush)

    def _flush(self):
        self._after_id = None
        items = self._buffer
        self._buffer = []
        grouped = {}
        for cid, path in items:
            grouped.setdefault(cid, []).append(path)
        try:
            self.on_flush(grouped)
        except Exception:
            # on ne fait pas planter l'UI
            pass
