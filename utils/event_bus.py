# utils/event_bus.py
from __future__ import annotations

from typing import Callable, Dict, Set

_subs: Dict[str, Set[Callable]] = {}

def on(event: str, callback: Callable) -> None:
    """S'abonner à un événement (ex: 'todo.changed')."""
    _subs.setdefault(event, set()).add(callback)

def off(event: str, callback: Callable) -> None:
    """Se désabonner."""
    if event in _subs:
        _subs[event].discard(callback)

def emit(event: str, *args, **kwargs) -> None:
    """Émettre un événement. Les callbacks sont appelés de façon synchrone (thread UI)."""
    for cb in list(_subs.get(event, ())):
        try:
            cb(*args, **kwargs)
        except Exception:
            # On isole les erreurs pour ne pas casser la diffusion
            pass
