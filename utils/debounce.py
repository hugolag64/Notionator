# utils/debounce.py
from __future__ import annotations

"""
Debounce util moderne et thread-safe.

Points clés
- Basé sur threading.Timer (annulation/replanification propre).
- Options : leading (appel immédiat), trailing (appel en fin de rafale), max_wait.
- Débounce par *clé* (ex : par instance self) via `key=...`.
- API pratique : wrapped.cancel(), wrapped.flush(), wrapped.is_pending().

Exemples
--------
@debounce(300)  # trailing-only par défaut
def on_change(text): ...

@debounce(250, leading=True, trailing=True)
def search(query): ...

# Débounce par instance (chaque vue a son timer séparé)
@debounce(400, key=lambda args, kwargs: id(args[0]))
def refresh(self): ...
"""

import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = ["debounce"]


class _State:
    __slots__ = (
        "timer",
        "last_call_ts",
        "last_invoke_ts",
        "pending_args",
        "pending_kwargs",
        "max_wait_timer",
    )

    def __init__(self) -> None:
        self.timer: Optional[threading.Timer] = None
        self.max_wait_timer: Optional[threading.Timer] = None
        self.last_call_ts: float = 0.0
        self.last_invoke_ts: float = 0.0
        self.pending_args: Tuple[Any, ...] = ()
        self.pending_kwargs: Dict[str, Any] = {}


def debounce(
    wait_ms: int,
    *,
    leading: bool = False,
    trailing: bool = True,
    key: Optional[Callable[[Tuple[Any, ...], Dict[str, Any]], Any]] = None,
    max_wait_ms: Optional[int] = None,
    dispatch: Optional[Callable[[Callable[[], None]], None]] = None,
):
    """
    Décorateur debounce.

    Params
    ------
    wait_ms : int
        Délai en millisecondes avant l'invocation trailing.
    leading : bool
        Appelle immédiatement à la première frappe du burst (optionnel).
    trailing : bool
        Appelle à la fin du burst (défaut True). Peut être combiné avec leading.
    key : callable(args, kwargs) -> hashable
        Clé de débounce. Par défaut, si la fonction est liée (premier arg = self),
        on isole par instance (id(self)), sinon clé globale (None).
    max_wait_ms : int | None
        Si fourni, garantit une exécution au plus tard toutes les `max_wait_ms`.
    dispatch : callable(cb) -> None | None
        Si fourni, utilise ce dispatcher pour exécuter la fonction (ex: poste sur le thread UI).

    Retour
    ------
    wrapped : Callable
        La fonction décorée possède :
          - .cancel()  : annule timers et appels en attente
          - .flush()   : exécute immédiatement l'appel différé si présent
          - .is_pending() -> bool : vrai si une exécution trailing est planifiée
    """
    wait_s = max(0, int(wait_ms)) / 1000.0
    max_wait_s = None if max_wait_ms is None else max(0, int(max_wait_ms)) / 1000.0

    # États par clé de débounce
    _states: Dict[Any, _State] = {}
    _lock = threading.Lock()

    def _default_key(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        # Si méthode liée : débounce par instance
        if args and hasattr(args[0], "__class__"):
            return id(args[0])
        return None  # clé globale

    key_fn = key or _default_key

    def _invoke(fn: Callable[..., Any], state: _State, *, use_pending: bool = True):
        """Exécute la fonction (via dispatch si fourni)."""
        def _call():
            if use_pending:
                fn(*state.pending_args, **state.pending_kwargs)
            else:
                fn()

        if dispatch:
            try:
                dispatch(_call)
            except Exception:
                # fallback direct si dispatch échoue
                _call()
        else:
            _call()

    def _schedule_trailing(fn: Callable[..., Any], k: Any, state: _State):
        """Programme l'appel trailing après wait_s à partir de maintenant."""
        def _fire():
            with _lock:
                # Si aucune nouvelle frappe depuis wait_s, on invoque
                now = time.monotonic()
                if now - state.last_call_ts >= wait_s:
                    state.timer = None
                    state.last_invoke_ts = now
                    _invoke(fn, state, use_pending=True)
                else:
                    # Du nouveau est arrivé pendant l’attente → replanifie
                    state.timer = threading.Timer(wait_s - (now - state.last_call_ts), _fire)
                    state.timer.daemon = True
                    state.timer.start()

        # (Re)planifie
        if state.timer is not None:
            state.timer.cancel()
        state.timer = threading.Timer(wait_s, _fire)
        state.timer.daemon = True
        state.timer.start()

    def _schedule_max_wait(fn: Callable[..., Any], k: Any, state: _State):
        if max_wait_s is None:
            return
        # Si un max_wait est déjà en cours, on le laisse (première frappe du burst)
        if state.max_wait_timer is not None:
            return

        def _fire_max():
            with _lock:
                state.max_wait_timer = None
                # Si rien n'est en attente → rien à faire
                if state.pending_args == () and state.pending_kwargs == {}:
                    return
                # Force une invocation avec les derniers args en attente
                state.last_invoke_ts = time.monotonic()
                # Annule le trailing courant pour éviter double invocation, il sera replanifié si besoin
                if state.timer is not None:
                    state.timer.cancel()
                    state.timer = None
                _invoke(fn, state, use_pending=True)

        state.max_wait_timer = threading.Timer(max_wait_s, _fire_max)
        state.max_wait_timer.daemon = True
        state.max_wait_timer.start()

    def decorator(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            nonlocal _states
            k = key_fn(args, kwargs)
            now = time.monotonic()

            with _lock:
                state = _states.get(k)
                if state is None:
                    state = _State()
                    _states[k] = state

                # Met à jour les "pending" à chaque frappe
                state.pending_args = args
                state.pending_kwargs = kwargs
                state.last_call_ts = now

                fired_leading = False

                # Leading : si pas de timer actif (début de burst) → invoque
                if leading and state.timer is None and (now - state.last_invoke_ts) >= wait_s:
                    state.last_invoke_ts = now
                    fired_leading = True
                    # Leading consomme les pending actuels
                    _invoke(fn, state, use_pending=True)
                    # On garde pending pour trailing si d'autres frappes arrivent

                # Trailing : programme (ou reprogramme) la fin de burst
                if trailing:
                    _schedule_trailing(fn, k, state)
                else:
                    # Pas de trailing → si leading non déclenché, on ne fait rien ici
                    pass

                # Max wait : garantit une exécution au plus tard toutes les max_wait_s
                if max_wait_s is not None:
                    _schedule_max_wait(fn, k, state)

            return None  # comportement debounce : pas de retour synchrone

        # Méthodes utilitaires sur le wrapper
        def cancel():
            with _lock:
                for st in _states.values():
                    if st.timer is not None:
                        st.timer.cancel()
                        st.timer = None
                    if st.max_wait_timer is not None:
                        st.max_wait_timer.cancel()
                        st.max_wait_timer = None
                    st.pending_args = ()
                    st.pending_kwargs = {}

        def flush():
            """Exécute immédiatement l'appel différé (par clé globale)."""
            with _lock:
                for st in _states.values():
                    if st.pending_args or st.pending_kwargs:
                        # Annule timers et invoque
                        if st.timer is not None:
                            st.timer.cancel()
                            st.timer = None
                        if st.max_wait_timer is not None:
                            st.max_wait_timer.cancel()
                            st.max_wait_timer = None
                        st.last_invoke_ts = time.monotonic()
                        _invoke(fn, st, use_pending=True)
                        st.pending_args = ()
                        st.pending_kwargs = {}

        def is_pending() -> bool:
            with _lock:
                return any(st.timer is not None for st in _states.values())

        wrapper.cancel = cancel  # type: ignore[attr-defined]
        wrapper.flush = flush    # type: ignore[attr-defined]
        wrapper.is_pending = is_pending  # type: ignore[attr-defined]
        return wrapper

    return decorator
