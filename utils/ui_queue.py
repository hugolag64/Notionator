# utils/ui_queue.py
from __future__ import annotations
import queue, logging
log = logging.getLogger(__name__)

_UIQ: "queue.Queue[callable]" = queue.Queue()

def post(fn):
    """Planifie une MAJ UI (fn sans argument) depuis n’importe quel thread."""
    _UIQ.put(fn)

def install(app):
    """À appeler 1x après création de la fenêtre principale (dans main)."""
    def _drain():
        try:
            while True:
                fn = _UIQ.get_nowait()
                try:
                    fn()
                except Exception:
                    log.exception("UI task failed")
        except queue.Empty:
            pass
        app.after(33, _drain)  # ~30 FPS
    app.after(33, _drain)
