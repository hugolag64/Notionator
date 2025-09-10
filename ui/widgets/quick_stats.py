# ui/widgets/quick_stats.py
from __future__ import annotations

import os, json, threading, traceback
from datetime import datetime, timedelta, date
from typing import Tuple, Dict, Optional, Callable, List
import customtkinter as ctk

from ui.styles import COLORS, FONT
from services.notion_client import get_notion_client
from services.local_planner import LocalPlanner
from config import TO_DO_DATABASE_ID
from utils.event_bus import on, off  # bus d’événements

# ---- focus store (source officielle) + fallback robustes ----
_HAS_FOCUS_STORE = False
try:
    # API attendue: add_minutes(int), minutes_today() -> int, total_minutes(days:int) -> int
    from services import focus_store  # type: ignore
    _HAS_FOCUS_STORE = True
except Exception:
    focus_store = None  # type: ignore


# -------------------------- helpers --------------------------
def _c(key: str, default: str = "#FFFFFF") -> str:
    try:
        return COLORS.get(key, default)
    except Exception:
        return default

def _font(name: str, *, default=("SF Pro Text", 13), display=False, semibold=False, bold=False):
    try:
        if isinstance(FONT, dict):
            if name in FONT:
                return FONT[name]
            if display:
                return ("SF Pro Display", 28, "bold")
            if bold:
                return ("SF Pro Text", 14, "bold")
            if semibold:
                return ("SF Pro Text", 13, "semibold")
            return default
        return FONT
    except Exception:
        if display:
            return ("Helvetica", 28, "bold")
        if bold:
            return ("Helvetica", 14, "bold")
        if semibold:
                return ("Helvetica", 13, "bold")
        return default


# -------------------------- Tile --------------------------
class _StatTile(ctk.CTkFrame):
    def __init__(self, parent, title: str, icon: str = ""):
        self._shadow = ctk.CTkFrame(parent, fg_color=_c("bg_card_shadow", "#E7E8EB"), corner_radius=16)
        self._shadow.grid_propagate(False)
        self._shadow.grid(row=0, column=0, sticky="nsew", padx=2, pady=6)

        super().__init__(parent, fg_color=_c("bg_card", "#FFFFFF"), corner_radius=16)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=0)
        self.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(self, fg_color="transparent", height=30)
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        self.icon = ctk.CTkLabel(
            hdr, text=icon, font=_font("emoji", default=("Segoe UI Emoji", 14)),
            text_color=_c("text_secondary", "#667085")
        )
        self.icon.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.title = ctk.CTkLabel(
            hdr, text=title, text_color=_c("text_secondary", "#667085"), font=_font("title_sm", semibold=True)
        )
        self.title.grid(row=0, column=1, sticky="w")

        self.value = ctk.CTkLabel(
            self, text="—", font=_font("display_lg", display=True), text_color=_c("text_primary", "#101828")
        )
        self.value.grid(row=1, column=0, sticky="w", padx=14, pady=(6, 0))

        self.bar = ctk.CTkProgressBar(self, height=10, corner_radius=6, fg_color=_c("bg_light", "#F2F4F7"))
        self.bar.set(0.0)
        self.bar.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 0))
        self.bar.grid_remove()

        self.sub = ctk.CTkLabel(
            self, text="", text_color=_c("text_muted", "#98A2B3"), font=_font("body_sm"), justify="left"
        )
        self.sub.grid(row=3, column=0, sticky="w", padx=14, pady=(6, 12))

        def _on_resize(_evt=None):
            try:
                self.sub.configure(wraplength=max(80, self.winfo_width() - 28))
            except Exception:
                pass
        self.bind("<Configure>", _on_resize)
        _on_resize()

        accent = ctk.CTkFrame(self, fg_color=_c("card_hairline", "#EEF1F5"), height=1)
        accent.grid(row=0, column=0, sticky="ew")
        accent.lower()

    def set(self, main: str, sub: str = ""):
        self.value.configure(text=main)
        self.sub.configure(text=sub)

    def set_loading(self, text: str = "Mise à jour…"):
        self.value.configure(text="…")
        self.sub.configure(text=text)

    def set_progress(self, ratio: float):
        ratio = max(0.0, min(1.0, float(ratio)))
        self.bar.grid()
        self.bar.set(ratio)


# -------------------------- Widget --------------------------
class QuickStatsWidget(ctk.CTkFrame):

    def __init__(self, parent, notion=None, planner: Optional[LocalPlanner] = None):
        super().__init__(parent, fg_color="transparent")

        self.notion = notion or self._inherit_notion() or get_notion_client()
        self.planner = planner or self._inherit_planner() or LocalPlanner()
        self.data_manager = self._inherit_dm()

        # File UI thread-safe (drainée par after() sur le main thread)
        self._ui_lock = threading.Lock()
        self._ui_queue: List[Callable[[], None]] = []
        self.after(60, self._drain_ui)

        for r in (0, 1):
            self.grid_rowconfigure(r, weight=1, uniform="rows")
        for c in (0, 1):
            self.grid_columnconfigure(c, weight=1, uniform="cols")

        self.t_progress = _StatTile(self, "Progression du jour", icon="✅")
        self.t_progress._shadow.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.t_progress.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.t_today = _StatTile(self, "Révisions aujourd’hui", icon="📚")
        self.t_today._shadow.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.t_today.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.t_actions = _StatTile(self, "Actions à faire", icon="🧩")
        self.t_actions._shadow.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.t_actions.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        self.t_week = _StatTile(self, "Temps de révision (7j)", icon="⏱️")
        self.t_week._shadow.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        self.t_week.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        self._loading = False
        self.refresh()

        # ---- LIVE UPDATE (bus + compat événements Tk) ----
        try:
            self.bind_all("<<TodoChanged>>",     self._on_todo_changed)
            self.bind_all("<<PlannerChanged>>",  self._on_planner_changed)
            self.bind_all("<<FocusLogged>>",     lambda e: self.reload_async(delay_ms=0))
        except Exception:
            pass

        on("todo.changed",       self._bus_todo_changed)
        on("revisions.changed",  self._bus_revisions_changed)
        on("stats.changed",      self._bus_stats_changed)

    # Nettoyage
    def destroy(self):
        try:
            off("todo.changed",      self._bus_todo_changed)
            off("revisions.changed", self._bus_revisions_changed)
            off("stats.changed",     self._bus_stats_changed)
        finally:
            return super().destroy()

    # ---------- queue UI ----------
    def _post_ui(self, fn: Callable[[], None]):
        with self._ui_lock:
            self._ui_queue.append(fn)

    def _drain_ui(self):
        try:
            batch: List[Callable[[], None]] = []
            with self._ui_lock:
                if self._ui_queue:
                    batch, self._ui_queue = self._ui_queue, []
            for fn in batch:
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
        finally:
            try:
                self.after(60, self._drain_ui)
            except Exception:
                pass

    # ---------- helpers d’intégration ----------
    def _today(self) -> date:
        return datetime.now().date()

    def _walk_ancestors(self, depth: int = 6):
        w = self
        for _ in range(depth):
            w = getattr(w, "master", None)
            if not w:
                break
            yield w

    def _inherit_planner(self) -> Optional[LocalPlanner]:
        for w in self._walk_ancestors():
            try:
                if hasattr(w, "upcoming") and getattr(w.upcoming, "planner", None):
                    return w.upcoming.planner
            except Exception:
                continue
        return None

    def _inherit_notion(self):
        for w in self._walk_ancestors():
            try:
                if hasattr(w, "notion") and w.notion:
                    return w.notion
            except Exception:
                continue
        return None

    def _inherit_dm(self):
        for w in self._walk_ancestors():
            try:
                if hasattr(w, "data_manager") and w.data_manager:
                    return w.data_manager
            except Exception:
                continue
        return None

    # ---------- LIVE (sans I/O) ----------
    def _instant_progress_from_todo(self) -> Tuple[int, int]:
        for w in self._walk_ancestors():
            tb = getattr(w, "todo_by_date", None)
            if tb and hasattr(tb, "_notion_vars"):
                vars_map: Dict[str, ctk.BooleanVar] = getattr(tb, "_notion_vars", {}) or {}
                total = len(vars_map)
                done = sum(1 for v in vars_map.values() if bool(v.get()))
                return done, total
        return (0, 0)

    def _instant_reviews_today(self) -> Tuple[int, int]:
        d = self._today()
        try:
            planned = self.planner.list_for(d) or []
        except Exception:
            planned = []
        total = len(planned)
        done = sum(1 for x in planned if x.get("done"))
        return (done, total)

    # ---------- Handlers (compat Tk) ----------
    def _on_todo_changed(self, _e=None):
        d, t = self._instant_progress_from_todo()
        self._apply_progress_tile(d, t)
        self.reload_async(delay_ms=200)

    def _on_planner_changed(self, _e=None):
        d, t = self._instant_reviews_today()
        self.t_today.set(f"{d}/{t}", "révisions faites/planifiées")
        self.reload_async(delay_ms=120)

    # ---------- Handlers (bus) ----------
    def _bus_todo_changed(self, *_):
        self._on_todo_changed()

    def _bus_revisions_changed(self, *_):
        self._on_planner_changed()

    def _bus_stats_changed(self, *_):
        self.reload_async(delay_ms=0)

    # ---------- calculs (I/O possibles) ----------
    def _progression_du_jour(self) -> Tuple[int, int]:
        live = self._instant_progress_from_todo()
        if sum(live) > 0:
            return live
        try:
            _, checks = self.notion.get_today_todo_checkboxes(TO_DO_DATABASE_ID)
        except Exception:
            checks = {}
        total = len(checks or {})
        done = sum(1 for v in (checks or {}).values() if v)
        return (done, total)

    def _revisions_aujourdhui(self) -> Tuple[int, int]:
        live = self._instant_reviews_today()
        if sum(live) > 0:
            return live
        d = self._today()
        try:
            due = self.notion.get_courses_due_on(d)
        except Exception:
            due = []
        due_ids = {x.get("id") for x in due if x.get("id")}
        try:
            planned = self.planner.list_for(d)
        except Exception:
            planned = []
        planned_ids = {x.get("id") for x in planned if x.get("id")}
        total = len(due_ids | planned_ids)
        done = sum(1 for x in planned if x.get("done"))
        return (done, total)

    # ---- Focus 7j : source officielle focus_store + doubles fallbacks ----
    def _focus_minutes_7d_and_today(self) -> Tuple[int, int]:
        """
        Retourne (total_minutes_sur_7j, minutes_aujourdhui).
        Ordre de priorité:
          1) services.focus_store (si présent)
          2) data/focus_sessions.jsonl  (lignes: {"ts": epoch|iso, "duration_min": int})
             → on ne retient ce fichier que s'il contient AU MOINS une ligne valide
          3) data/focus_log.json        (liste:  {"date": "YYYY-MM-DD", "minutes": int})
        """
        # 1) Store officiel
        if _HAS_FOCUS_STORE and focus_store:
            try:
                total = int(focus_store.total_minutes(days=7))
                today = int(focus_store.minutes_today())
                return total, today
            except Exception:
                pass

        # 2) JSONL (sessions)
        total = 0
        today_min = 0
        found_any = False
        now = datetime.now()
        since = now - timedelta(days=7)
        jsonl_path = os.path.join("data", "focus_sessions.jsonl")
        if os.path.exists(jsonl_path):
            try:
                with open(jsonl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        ts = obj.get("ts")
                        dur = int(obj.get("duration_min", 0) or 0)
                        if isinstance(ts, (int, float)):
                            dt = datetime.fromtimestamp(ts)
                        elif isinstance(ts, str):
                            try:
                                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            except Exception:
                                continue
                        else:
                            continue
                        found_any = True
                        if dt >= since:
                            total += dur
                        if dt.date() == now.date():
                            today_min += dur
                if found_any:
                    return total, today_min
            except Exception:
                traceback.print_exc()

        # 3) JSON simple (legacy)
        total = 0
        today_min = 0
        j_path = os.path.join("data", "focus_log.json")
        if os.path.exists(j_path):
            try:
                rows = json.load(open(j_path, "r", encoding="utf-8")) or []
                for r in rows:
                    try:
                        d = datetime.fromisoformat(str(r.get("date")))
                        m = int(r.get("minutes", 0) or 0)
                        if d >= since:
                            total += m
                        if d.date() == now.date():
                            today_min += m
                    except Exception:
                        continue
            except Exception:
                traceback.print_exc()
        return total, today_min

    # ---------- rendering ----------
    def _apply_progress_tile(self, done: int, total: int):
        pct = int(round(100 * done / total)) if total else 0
        self.t_progress.set(f"{pct}%", f"{done} / {total} cochées")
        self.t_progress.set_progress(pct / 100.0)

    def refresh(self):
        try:
            d, t = self._progression_du_jour()
            self._apply_progress_tile(d, t)

            r_done, r_total = self._revisions_aujourdhui()
            self.t_today.set(f"{r_done}/{r_total}", "révisions faites/planifiées")

            cnt = self._pending_actions() or {}
            a = int(cnt.get("pdf_missing", 0) or 0)
            b = int(cnt.get("summary_missing", 0) or 0)
            c = int(cnt.get("anki_missing", 0) or 0)
            self.t_actions.set(str(a + b + c), f"PDF • Résumé • Anki\n{a} • {b} • {c}")

            minutes, today_min = self._focus_minutes_7d_and_today()
            h, m = divmod(minutes, 60)
            main = f"{h}h{m:02d}" if minutes >= 60 else f"{minutes} min"
            avg = round(minutes / 7) if minutes else 0
            self.t_week.set(main, f"Total : {minutes} min • ∅/j : {avg} min • aujourd’hui : {today_min} min")

            cap = 3 * 60
            self.t_week.set_progress(min(1.0, minutes / cap))
        except Exception:
            traceback.print_exc()

    # ---- Actions à faire : calcul local depuis le cache ----
    def _pending_actions_local(self) -> Dict[str, int]:
        counters = {"pdf_missing": 0, "summary_missing": 0, "anki_missing": 0}
        courses: List[Dict] = []
        try:
            if self.data_manager and hasattr(self.data_manager, "get_parsed_courses"):
                courses = self.data_manager.get_parsed_courses(mode="college") or []
        except Exception:
            courses = []

        for c in courses:
            try:
                if not bool(c.get("pdf_ok", False)):
                    counters["pdf_missing"] += 1
                if not bool(c.get("resume_college_ok", False)):
                    counters["summary_missing"] += 1
                if not bool(c.get("anki_college_ok", False)):
                    counters["anki_missing"] += 1
            except Exception:
                continue
        return counters

    def _pending_actions(self) -> Dict[str, int]:
        try:
            if hasattr(self.notion, "get_pending_actions_counters"):
                res = self.notion.get_pending_actions_counters() or {}
                if any((res.get("pdf_missing"), res.get("summary_missing"), res.get("anki_missing"))):
                    return res
        except Exception:
            pass
        return self._pending_actions_local()

    # ---------- async ----------
    def reload_async(self, delay_ms: int = 0):
        if self._loading:
            return
        self._loading = True
        try:
            self.t_progress.set_loading()
            self.t_today.set_loading()
            self.t_week.set_loading()
        except Exception:
            pass

        def _start_worker():
            def worker():
                try:
                    d, t = self._progression_du_jour()
                    r_done, r_total = self._revisions_aujourdhui()
                    cnt = self._pending_actions() or {}
                    a = int(cnt.get("pdf_missing", 0) or 0)
                    b = int(cnt.get("summary_missing", 0) or 0)
                    c = int(cnt.get("anki_missing", 0) or 0)
                    minutes, today_min = self._focus_minutes_7d_and_today()
                    h, m = divmod(minutes, 60)
                    main = f"{h}h{m:02d}" if minutes >= 60 else f"{minutes} min"
                    avg = round(minutes / 7) if minutes else 0
                    cap = 3 * 60

                    def apply():
                        try:
                            self._apply_progress_tile(d, t)
                            self.t_today.set(f"{r_done}/{r_total}", "révisions faites/planifiées")
                            self.t_actions.set(str(a + b + c), f"PDF • Résumé • Anki\n{a} • {b} • {c}")
                            self.t_week.set(main, f"Total : {minutes} min • ∅/j : {avg} min • aujourd’hui : {today_min} min")
                            self.t_week.set_progress(min(1.0, minutes / cap))
                        finally:
                            self._loading = False

                    self._post_ui(apply)
                except Exception:
                    traceback.print_exc()
                    self._post_ui(lambda: setattr(self, "_loading", False))

            threading.Thread(target=worker, daemon=True).start()

        if delay_ms and delay_ms > 0:
            try:
                self.after(delay_ms, _start_worker)
            except Exception:
                _start_worker()
        else:
            _start_worker()
