# services/notification_center.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Optional
from datetime import datetime
import itertools
import threading

Level = str  # "info" | "success" | "warning" | "error"
Category = str  # "pomodoro" | "notion" | "sync" | "reminder" | ...

@dataclass
class NotificationAction:
    label: str
    callback: Optional[Callable[[], None]] = None

@dataclass
class Notification:
    id: int
    title: str
    message: str
    level: Level = "info"
    category: Category = "general"
    created_at: datetime = field(default_factory=datetime.now)
    read: bool = False
    sticky: bool = False
    actions: List[NotificationAction] = field(default_factory=list)

class NotificationCenter:
    """Singleton simple: publish/subscribe + store mémoire."""
    _instance: "NotificationCenter" | None = None
    _id_counter = itertools.count(1)
    _lock = threading.Lock()

    def __init__(self):
        self._subs: List[Callable[[Notification], None]] = []
        self._notifications: List[Notification] = []

    @classmethod
    def instance(cls) -> "NotificationCenter":
        with cls._lock:
            if cls._instance is None:
                cls._instance = NotificationCenter()
        return cls._instance

    # ---- API publique ----
    def notify(
        self,
        title: str,
        message: str,
        *,
        level: Level = "info",
        category: Category = "general",
        sticky: bool = False,
        actions: Optional[List[NotificationAction]] = None,
    ) -> Notification:
        nid = next(self._id_counter)
        n = Notification(
            id=nid,
            title=title,
            message=message,
            level=level,
            category=category,
            sticky=sticky,
            actions=actions or [],
        )
        self._notifications.insert(0, n)  # plus récent en tête
        # broadcast
        for cb in list(self._subs):
            try:
                cb(n)
            except Exception:
                pass
        return n

    def subscribe(self, callback: Callable[[Notification], None]) -> Callable[[], None]:
        self._subs.append(callback)
        def _unsub():
            if callback in self._subs:
                self._subs.remove(callback)
        return _unsub

    # ---- Gestion liste ----
    def all(self) -> List[Notification]:
        return list(self._notifications)

    def mark_read(self, nid: int, read: bool = True):
        for n in self._notifications:
            if n.id == nid:
                n.read = read
                return

    def clear_non_sticky(self):
        self._notifications = [n for n in self._notifications if n.sticky]

    def clear_all(self):
        self._notifications.clear()
