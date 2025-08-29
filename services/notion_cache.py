# services/notion_cache.py
from __future__ import annotations
import time
from typing import Any, Dict, Tuple, Optional

class InProcessTTLCache:
    """Cache ultra-léger en mémoire pour la durée d'un run."""
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        self._store: Dict[Tuple[str, Tuple], Tuple[float, Any]] = {}

    def get(self, key: Tuple[str, Tuple]) -> Optional[Any]:
        now = time.time()
        hit = self._store.get(key)
        if not hit:
            return None
        ts, value = hit
        if now - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: Tuple[str, Tuple], value: Any) -> None:
        self._store[key] = (time.time(), value)

    def memoize(self, namespace: str):
        """Décorateur simple: mettez un tuple d'args hashables."""
        def _wrap(fn):
            def _inner(*args, **kwargs):
                # kwargs figés en tuple trié pour hash stable
                k = (namespace, args + (("__KW__", tuple(sorted(kwargs.items()))),))
                got = self.get(k)
                if got is not None:
                    return got
                val = fn(*args, **kwargs)
                self.set(k, val)
                return val
            return _inner
        return _wrap
