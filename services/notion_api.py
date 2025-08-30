from __future__ import annotations
import threading, time
from typing import Any, Dict, List, Optional
from notion_client import Client
from .cache import TTLCache

class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = burst
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.rate)
                self.tokens = 0
                self.last = time.monotonic()
            self.tokens -= 1

def backoff(max_attempts=5, base=0.3):
    def deco(fn):
        def wrap(*a, **k):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*a, **k)
                except Exception:
                    if attempt == max_attempts:
                        raise
                    time.sleep(base * (2 ** (attempt - 1)))
        return wrap
    return deco

class NotionAPI:
    _instance: Optional["NotionAPI"] = None
    _lock = threading.Lock()

    def __new__(cls, token: str):
        with cls._lock:
            if not cls._instance:
                cls._instance = super().__new__(cls)
                cls._instance._init_singleton(token)
            return cls._instance

    def _init_singleton(self, token: str):
        self.client = Client(auth=token)
        self.bucket = TokenBucket(rate_per_sec=3, burst=3)   # adapte si besoin
        self.cache = TTLCache(ttl_seconds=60)                # 60s pour courses/UE, ajustable

    @backoff()
    def _call(self, fn, *a, **k):
        self.bucket.acquire()
        return fn(*a, **k)

    def query_db(self, database_id: str, **kwargs) -> Dict[str, Any]:
        cache_key = f"db:{database_id}:{hash(frozenset(kwargs.items()))}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        res = self._call(self.client.databases.query, database_id=database_id, **kwargs)
        self.cache.set(cache_key, res)
        return res

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> None:
        self._call(self.client.pages.update, page_id=page_id, properties=properties)
