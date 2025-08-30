# services/cache.py
from __future__ import annotations
import atexit
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Cache persistant léger (JSON) + mémoire, avec flush différé (écritures groupées)
# - get/set O(1) sur la mémoire, E/S disque amorties
# - TTL persistant (epoch seconds) pour survivre au redémarrage
# - Écritures atomiques (tmp + os.replace) pour éviter la corruption
# - API rétro-compatible : get, set, invalidate_prefix
# ──────────────────────────────────────────────────────────────────────────────

_CACHE_FILE = os.path.join("data", "cache.json")
_TMP_FILE = _CACHE_FILE + ".tmp"
_DEFAULT_TTL = 300  # secondes (5 min)
_FLUSH_DELAY = 0.4  # secondes : regrouper les écritures rapprochées
_MAX_ENTRIES = 5000  # limite douce pour éviter un cache JSON énorme

# Mémoire : key -> (value, expires_at_epoch | None)
_MEM: Dict[str, Tuple[Any, Optional[float]]] = {}

_LOCK = threading.Lock()
_DIRTY = False
_FLUSH_TIMER: Optional[threading.Timer] = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers bas niveau
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_parent_dir() -> None:
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)


def _load_from_disk() -> Dict[str, Tuple[Any, Optional[float]]]:
    if not os.path.exists(_CACHE_FILE):
        return {}
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Attendu : { key: [value, expires_at] }
        mem: Dict[str, Tuple[Any, Optional[float]]] = {}
        now = time.time()
        for k, pair in data.items():
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            val, exp = pair[0], pair[1]
            # Nettoie ce qui est expiré dès le chargement
            if exp is not None and exp <= now:
                continue
            mem[k] = (val, exp)
        return mem
    except Exception:
        # Si le JSON est corrompu, on repart proprement
        return {}


def _flush_to_disk_locked() -> None:
    """Écrit le cache en JSON de façon atomique. Appelé sous _LOCK."""
    global _DIRTY, _FLUSH_TIMER
    if not _DIRTY:
        return
    _ensure_parent_dir()

    # Compactage : enlève expirés + tronque si on dépasse MAX_ENTRIES
    now = time.time()
    to_dump: Dict[str, Tuple[Any, Optional[float]]] = {}
    for k, (v, exp) in _MEM.items():
        if exp is not None and exp <= now:
            continue
        to_dump[k] = (v, exp)

    if len(to_dump) > _MAX_ENTRIES:
        # Stratégie simple : garder les premières N entrées arbitrairement
        # (si besoin : améliorer avec LRU selon usage projet)
        cut = len(to_dump) - _MAX_ENTRIES
        for i, k in enumerate(list(to_dump.keys())):
            if i >= cut:
                break
            to_dump.pop(k, None)

    try:
        # Écriture atomique
        with open(_TMP_FILE, "w", encoding="utf-8") as f:
            json.dump(to_dump, f, ensure_ascii=False, indent=2)
        os.replace(_TMP_FILE, _CACHE_FILE)
        _DIRTY = False
    except Exception:
        # En cas d'échec, on réessaiera au prochain flush
        pass
    finally:
        if _FLUSH_TIMER:
            _FLUSH_TIMER = None


def _schedule_flush_locked() -> None:
    """Programme un flush groupé dans _FLUSH_DELAY (appel sous _LOCK)."""
    global _FLUSH_TIMER
    if _FLUSH_TIMER is not None:
        return
    _FLUSH_TIMER = threading.Timer(_FLUSH_DELAY, _flush)
    _FLUSH_TIMER.daemon = True
    _FLUSH_TIMER.start()


def _flush() -> None:
    with _LOCK:
        _flush_to_disk_locked()


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────

def get(key: str) -> Any:
    """Retourne la valeur si présente et non expirée, sinon None."""
    with _LOCK:
        item = _MEM.get(key)
        if not item:
            return None
        value, expires_at = item
        if expires_at is not None and time.time() > expires_at:
            # Expiré : supprime et planifie un flush
            _MEM.pop(key, None)
            global _DIRTY
            _DIRTY = True
            _schedule_flush_locked()
            return None
        return value


def set(key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
    """Set une valeur avec TTL (en secondes). ttl<=0 => pas d'expiration."""
    with _LOCK:
        exp = time.time() + ttl if ttl > 0 else None
        _MEM[key] = (value, exp)
        global _DIRTY
        _DIRTY = True
        _schedule_flush_locked()


def get_or_set(key: str, factory: Callable[[], Any], ttl: int = _DEFAULT_TTL) -> Any:
    """
    Récupère la valeur en cache ; sinon la calcule via `factory()`, la stocke puis la retourne.
    La factory est appelée hors verrou pour ne pas bloquer d'autres threads.
    """
    val = get(key)
    if val is not None:
        return val
    # Calcule hors verrou
    computed = factory()
    set(key, computed, ttl=ttl)
    return computed


def invalidate(key: str) -> None:
    """Supprime une entrée précise et planifie un flush."""
    with _LOCK:
        if key in _MEM:
            _MEM.pop(key, None)
            global _DIRTY
            _DIRTY = True
            _schedule_flush_locked()


def invalidate_prefix(prefix: str) -> None:
    """Supprime toutes les clés commençant par `prefix` et planifie un flush."""
    with _LOCK:
        removed = False
        for k in list(_MEM.keys()):
            if k.startswith(prefix):
                _MEM.pop(k, None)
                removed = True
        if removed:
            global _DIRTY
            _DIRTY = True
            _schedule_flush_locked()


def clear() -> None:
    """Vide complètement le cache (mémoire + disque)."""
    with _LOCK:
        _MEM.clear()
        try:
            if os.path.exists(_CACHE_FILE):
                os.remove(_CACHE_FILE)
        finally:
            global _DIRTY
            _DIRTY = False


def size() -> int:
    """Nombre d'entrées en mémoire (non expirées potentiellement)."""
    with _LOCK:
        return len(_MEM)


def set_default_ttl(seconds: int) -> None:
    """Change le TTL par défaut (affecte uniquement les prochains set)."""
    global _DEFAULT_TTL
    _DEFAULT_TTL = max(0, int(seconds))


# ──────────────────────────────────────────────────────────────────────────────
# Initialisation & shutdown
# ──────────────────────────────────────────────────────────────────────────────

# Chargement unique au premier import
with _LOCK:
    _MEM.update(_load_from_disk())

# Flush garanti à la fermeture du process
atexit.register(_flush)
