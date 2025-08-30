# services/profiler.py
from __future__ import annotations
import atexit
import json
import os
import threading
import time
from contextlib import ContextDecorator
from dataclasses import dataclass, asdict
from functools import wraps
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# Profilage léger : spans horodatés, thread-safe, export JSON
# API:
#   enable(True|False)
#   with span("phase"):
#       ...
#   @profiled() / @profiled("nom")
#   render_report(path="data/profiler_last.json")
# ──────────────────────────────────────────────────────────────────────────────

_ENABLED = True
_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()

@dataclass
class _Event:
    name: str
    start_ms: float
    end_ms: float
    duration_ms: float
    thread: str

_EVENTS: List[_Event] = []

def enable(on: bool = True) -> None:
    """Active/désactive le profiler (non intrusif quand off)."""
    global _ENABLED
    _ENABLED = bool(on)

def _now_ms() -> float:
    return time.perf_counter() * 1000.0

class _Span(ContextDecorator):
    def __init__(self, name: str):
        self.name = name
        self._start_ms: float = 0.0
        self._end_ms: float = 0.0

    def __enter__(self):
        if not _ENABLED:
            return self
        self._start_ms = _now_ms()
        # pile par thread (optionnel mais utile si tu veux enrichir plus tard)
        stack = getattr(_THREAD_LOCAL, "stack", None)
        if stack is None:
            stack = []
            _THREAD_LOCAL.stack = stack
        stack.append(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if not _ENABLED:
            return False
        self._end_ms = _now_ms()
        dur = max(0.0, self._end_ms - self._start_ms)
        with _LOCK:
            _EVENTS.append(
                _Event(
                    name=self.name,
                    start_ms=self._start_ms,
                    end_ms=self._end_ms,
                    duration_ms=dur,
                    thread=threading.current_thread().name,
                )
            )
        try:
            stack = getattr(_THREAD_LOCAL, "stack", None)
            if stack:
                stack.pop()
        except Exception:
            pass
        # ne supprime pas l’exception éventuelle
        return False

def span(name: str) -> _Span:
    """Context manager de profilage."""
    return _Span(name)

def profiled(name: Optional[str] = None):
    """
    Décorateur de profilage :
        @profiled()           → span avec le nom de la fonction
        @profiled("custom")   → span "custom"
    """
    def decorator(fn):
        nm = name or fn.__name__
        @wraps(fn)
        def wrapper(*a, **k):
            if not _ENABLED:
                return fn(*a, **k)
            with span(nm):
                return fn(*a, **k)
        return wrapper
    return decorator

def render_report(path: str = os.path.join("data", "profiler_last.json")) -> Optional[str]:
    """Écrit le rapport JSON minimal des spans collectés. Renvoie le chemin écrit."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            payload: Dict[str, Any] = {
                "enabled": _ENABLED,
                "total_events": len(_EVENTS),
                "events": [asdict(e) for e in _EVENTS],
            }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None

def reset() -> None:
    """Vide les événements collectés (utile en tests)."""
    with _LOCK:
        _EVENTS.clear()

# Export automatique à la sortie du processus (best-effort)
atexit.register(render_report)
