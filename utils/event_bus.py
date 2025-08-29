# utils/event_bus.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Dict, Optional
import threading
import weakref

# ──────────────────────────────────────────────────────────────────────────────
# Implémentation
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _Listener:
    ref: weakref.ReferenceType
    once: bool
    priority: int  # plus grand = appelé en premier

    def callback(self) -> Optional[Callable]:
        return self.ref()  # None si garbage collected


class EventBus:
    """
    Bus d'événements minimaliste mais robuste :
      - Thread-safe
      - Pas de fuites mémoire (weakref sur les callbacks)
      - Priorités (plus grand d'abord)
      - .once() pour un abonnement auto-désinscrit après 1 appel
      - Wildcards simples: "topic:*" écoute tous les sous-événements de "topic:"
      - Option UI: emit(use_ui=True) poste les callbacks via utils.ui_queue.post (si dispo)

    Convention de nommage d'événements : "domain:action" (ex: "notion:page_updated").
    Wildcards : "notion:*" recevra "notion:page_updated", "notion:cache_invalidated", etc.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._listeners: Dict[str, List[_Listener]] = {}

    # ── Subscriptions ─────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable, *, priority: int = 0) -> None:
        """S'abonner (faible rétention mémoire)."""
        self._add_listener(event, callback, once=False, priority=priority)

    def once(self, event: str, callback: Callable, *, priority: int = 0) -> None:
        """S'abonner pour un seul déclenchement."""
        self._add_listener(event, callback, once=True, priority=priority)

    def off(self, event: str, callback: Callable) -> None:
        """Se désabonner."""
        with self._lock:
            lst = self._listeners.get(event)
            if not lst:
                return
            to_remove = []
            for l in lst:
                cb = l.callback()
                if cb is None or cb is callback:
                    to_remove.append(l)
            if to_remove:
                for l in to_remove:
                    lst.remove(l)
            if not lst:
                self._listeners.pop(event, None)

    # ── Emission ──────────────────────────────────────────────────────────────

    def emit(self, event: str, *args, use_ui: bool = False, **kwargs) -> None:
        """
        Émettre un événement.
        - use_ui=True → tente de dispatcher via utils.ui_queue.post (sinon fallback sync).
        - Les callbacks sont appelés par ordre de priorité décroissante.
        - Les abonnements 'once' sont retirés après appel.
        """
        targets = self._collect_targets(event)
        if not targets:
            return

        if use_ui:
            try:
                from utils.ui_queue import post  # import paresseux
            except Exception:
                post = None
        else:
            post = None

        # Appel dans l'ordre (priorité ↓). On copie pour stabilité si ça modifie.
        for listener in list(targets):
            cb = listener.callback()
            if cb is None:
                self._safe_remove(listener, event)
                continue

            def _run(c=cb, e=event, l=listener):
                try:
                    c(*args, **kwargs)
                except Exception:
                    # On isole les erreurs pour ne pas casser la diffusion
                    pass
                finally:
                    if l.once:
                        self.off(e, c)

            if post:
                try:
                    post(_run)  # garantit l'exécution sur le thread UI principal
                except Exception:
                    _run()      # fallback sync
            else:
                _run()

    # ── Utils ────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Désabonner tout le monde (tests / shutdown)."""
        with self._lock:
            self._listeners.clear()

    def listeners(self, event: str) -> Iterable[Callable]:
        """Retourne les callbacks vivants pour inspection/debug."""
        with self._lock:
            for l in self._listeners.get(event, []):
                cb = l.callback()
                if cb is not None:
                    yield cb

    # ── Internes ─────────────────────────────────────────────────────────────

    def _add_listener(self, event: str, callback: Callable, *, once: bool, priority: int) -> None:
        # weakref pour éviter de retenir des vues/objets Tk
        try:
            ref = weakref.ref(callback)
        except TypeError:
            # Certains callables exotiques ne sont pas weakref-able. On garde une ref forte.
            # (Rare; acceptable. Alternative: wrapper avec un objet weakref-able.)
            ref = _StrongRef(callback)  # type: ignore

        listener = _Listener(ref=ref, once=once, priority=priority)
        with self._lock:
            bucket = self._listeners.setdefault(event, [])
            # éviter les doublons exacts
            for l in bucket:
                if l.callback() is callback:
                    return
            bucket.append(listener)
            bucket.sort(key=lambda x: x.priority, reverse=True)

    def _collect_targets(self, event: str) -> List[_Listener]:
        with self._lock:
            exact = list(self._listeners.get(event, []))
            # Wildcards "prefix:*"
            wildcards = []
            prefix = event.split(":", 1)[0] + ":*"
            if prefix in self._listeners and event != prefix:
                wildcards = list(self._listeners[prefix])
            # Filtrer ceux déjà GC, et renvoyer une liste combinée (priorité ↓)
            merged = [l for l in exact + wildcards if l.callback() is not None]
            merged.sort(key=lambda x: x.priority, reverse=True)
            return merged

    def _safe_remove(self, listener: _Listener, event: str) -> None:
        with self._lock:
            lst = self._listeners.get(event)
            if not lst:
                return
            try:
                lst.remove(listener)
            except ValueError:
                pass
            if not lst:
                self._listeners.pop(event, None)


class _StrongRef:
    """Fallback pour callables non weakref-able (rare). Interface compatible weakref.ref."""
    __slots__ = ("_obj",)

    def __init__(self, obj):
        self._obj = obj

    def __call__(self):
        return self._obj


# ──────────────────────────────────────────────────────────────────────────────
# Instance globale (simple à importer)
# ──────────────────────────────────────────────────────────────────────────────

_bus = EventBus()

# API fonctionnelle simple (compatible avec ton ancien code)
def on(event: str, callback: Callable, *, priority: int = 0) -> None:
    _bus.on(event, callback, priority=priority)

def once(event: str, callback: Callable, *, priority: int = 0) -> None:
    _bus.once(event, callback, priority=priority)

def off(event: str, callback: Callable) -> None:
    _bus.off(event, callback)

def emit(event: str, *args, use_ui: bool = False, **kwargs) -> None:
    _bus.emit(event, *args, use_ui=use_ui, **kwargs)

def clear() -> None:
    _bus.clear()

def listeners(event: str) -> Iterable[Callable]:
    return _bus.listeners(event)
