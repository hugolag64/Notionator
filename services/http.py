# services/http.py
from __future__ import annotations
import time
from threading import Lock
import httpx

class RateLimiter:
    """Token bucket simple: ~3 req/s (cap 6)."""
    def __init__(self, rate_per_sec: float = 3.0, capacity: int = 6):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.ts = time.monotonic()
        self.lock = Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.ts
            self.ts = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1:
                sleep_for = (1 - self.tokens) / self.rate
                time.sleep(max(0, sleep_for))
                self.tokens = 0
            else:
                self.tokens -= 1

class Http:
    """Client httpx partagé + retry exponentiel sur erreurs réseau / 429."""
    _client: httpx.Client | None = None
    _rl = RateLimiter()

    @classmethod
    def client(cls) -> httpx.Client:
        if cls._client is None:
            cls._client = httpx.Client(timeout=20.0)
        return cls._client

    @classmethod
    def request(cls, method: str, url: str, **kw) -> httpx.Response:
        backoff = 0.25
        for _ in range(6):
            cls._rl.acquire()
            try:
                r = cls.client().request(method, url, **kw)
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                return r
            except (httpx.RequestError, httpx.HTTPStatusError):
                time.sleep(backoff)
                backoff = min(backoff * 2, 4.0)
        # Dernier essai (laisse remonter l’erreur si besoin)
        r = cls.client().request(method, url, **kw)
        r.raise_for_status()
        return r
