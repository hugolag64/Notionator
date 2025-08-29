# services/profiler.py
from __future__ import annotations
import time
import threading
import atexit
import json
import os
from collections import defaultdict, namedtuple
from contextlib import contextmanager

_ProfileEntry = namedtuple("_ProfileEntry", "count total min max last_error")

class _Aggregator:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, _ProfileEntry] = {}

    def add(self, name: str, duration: float, error: bool):
        with self._lock:
            e = self._data.get(name)
            if e is None:
                self._data[name] = _ProfileEntry(
                    count=1, total=duration, min=duration, max=duration,
                    last_error=bool(error)
                )
            else:
                self._data[name] = _ProfileEntry(
                    count=e.count + 1,
                    total=e.total + duration,
                    min=min(e.min, duration),
                    max=max(e.max, duration),
                    last_error=bool(error)
                )

    def snapshot(self) -> dict[str, _ProfileEntry]:
        with self._lock:
            return dict(self._data)

class Profiler:
    def __init__(self):
        self.enabled = True
        self._agg = _Aggregator()
        self._base_t0 = time.perf_counter()
        self._report_written = False
        atexit.register(self._render_at_exit)

    @contextmanager
    def span(self, name: str):
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        err = False
        try:
            yield
        except Exception:
            err = True
            raise
        finally:
            dt = time.perf_counter() - t0
            self._agg.add(name, dt, err)

    def profiled(self, name: str):
        def deco(func):
            def wrapper(*args, **kwargs):
                if not self.enabled:
                    return func(*args, **kwargs)
                t0 = time.perf_counter()
                err = False
                try:
                    return func(*args, **kwargs)
                except Exception:
                    err = True
                    raise
                finally:
                    dt = time.perf_counter() - t0
                    self._agg.add(name, dt, err)
            return wrapper
        return deco

    def disable(self):
        self.enabled = False

    def enable(self):
        self.enabled = True

    # --- Reporting ---------------------------------------------------------

    def _render_at_exit(self):
        # Évite double rendu si appelé manuellement
        if self._report_written:
            return
        self._report_written = True
        self.render_report()

    def render_report(self, save_path: str | None = None):
        snap = self._agg.snapshot()
        if not snap:
            return

        # Tri par total décroissant
        rows = []
        total_runtime = time.perf_counter() - self._base_t0
        for name, e in snap.items():
            rows.append({
                "span": name,
                "count": e.count,
                "total_ms": round(e.total * 1000, 2),
                "avg_ms": round((e.total / e.count) * 1000, 2),
                "min_ms": round(e.min * 1000, 2),
                "max_ms": round(e.max * 1000, 2),
                "error": e.last_error,
            })
        rows.sort(key=lambda r: (-r["total_ms"], -r["count"]))

        # Affichage console (sobre, lisible)
        print("\n┌────────────────────────────────────────────────────────────────────────────┐")
        print("│                         PROFILAGE – RÉCAPITULATIF                          │")
        print("└────────────────────────────────────────────────────────────────────────────┘")
        print(f"Temps total exécution: {total_runtime:0.2f}s")
        print(f"Spans mesurés: {len(rows)}\n")

        header = f'{"SPAN":38} {"N":>4} {"TOTAL(ms)":>10} {"AVG":>8} {"MIN":>8} {"MAX":>8} {"ERR":>4}'
        print(header)
        print("-" * len(header))
        for r in rows[:80]:  # limite affichage
            print(f'{r["span"][:38]:38} {r["count"]:>4} {r["total_ms"]:>10.2f} {r["avg_ms"]:>8.2f} {r["min_ms"]:>8.2f} {r["max_ms"]:>8.2f} {("Y" if r["error"] else ""):>4}')
        print()

        # Sauvegarde JSON pour post-analyse
        if save_path is None:
            os.makedirs("data", exist_ok=True)
            save_path = os.path.join("data", "profiler_last.json")
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump({
                    "generated_at": time.time(),
                    "total_runtime_s": total_runtime,
                    "rows": rows,
                }, f, ensure_ascii=False, indent=2)
            print(f"[profilage] Rapport sauvegardé → {save_path}")
        except Exception as e:
            print(f"[profilage] Impossible d’écrire le rapport: {e}")

# Instance globale
profiler = Profiler()

# Helpers de commodité
span = profiler.span
profiled = profiler.profiled
enable = profiler.enable
disable = profiler.disable
render_report = profiler.render_report
