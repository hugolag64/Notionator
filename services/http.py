# services/http.py
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Optional


class RateLimiter:
    """
    Token-bucket thread-safe, pensé pour limiter les appels Notion à ~3 req/s par défaut.

    - rate_per_sec: jetons ajoutés par seconde.
    - capacity: taille du seau (burst max). Par défaut: 2× rate.
    - acquire(n): bloque jusqu'à obtenir n jetons et les consomme.
    - try_acquire(n): tente de consommer sans bloquer, renvoie bool.

    Utilisation:
        rl = RateLimiter(3.0, 6)
        rl.acquire()  # bloque si nécessaire avant de laisser passer l’appel
    """
    def __init__(self, rate_per_sec: float = 3.0, capacity: Optional[int] = None):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec doit être > 0")
        self.rate = float(rate_per_sec)
        self.capacity = int(capacity if capacity is not None else max(1, int(round(self.rate * 2))))
        self._tokens = float(self.capacity)
        self._last = time.perf_counter()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    def _refill_unlocked(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def _wait_for_tokens_unlocked(self, n: float) -> None:
        # Attente active conditionnée, pour limiter les wakeups.
        while True:
            self._refill_unlocked()
            if self._tokens >= n:
                self._tokens -= n
                return
            # temps approx pour 1 jeton manquant
            missing = n - self._tokens
            wait_s = max(0.0, missing / self.rate)
            self._cv.wait(timeout=min(wait_s, 0.5))

    def acquire(self, n: int = 1) -> None:
        if n <= 0:
            return
        with self._cv:
            self._wait_for_tokens_unlocked(float(n))

    def try_acquire(self, n: int = 1) -> bool:
        if n <= 0:
            return True
        with self._cv:
            self._refill_unlocked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    @contextmanager
    def limit(self, n: int = 1):
        """Context manager pratique: with rl.limit(): call()"""
        self.acquire(n)
        try:
            yield
        finally:
            pass


# Optionnel: un petit helper No-Op si tu veux désactiver facilement le rate-limit.
class NoopLimiter:
    def acquire(self, n: int = 1) -> None:
        return

    def try_acquire(self, n: int = 1) -> bool:
        return True

    @contextmanager
    def limit(self, n: int = 1):
        yield
