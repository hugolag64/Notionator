# ui/dashboard.py
from __future__ import annotations

import customtkinter as ctk
from typing import Dict, List
from datetime import datetime, date, timedelta
import threading
import traceback

from ui.ai_dialog import AIAnswerDialog
from services import local_search as rag
from ui.styles import COLORS, FONT
from services.notion_client import get_notion_client
from services.daily_todo_generator import DailyToDoGenerator
from services.local_planner import LocalPlanner          # ← prévisionnels (Prochaines révisions)
from services.local_todo_store import LocalTodoStore     # ← ajouts locaux To-Do par date
from config import TO_DO_DATABASE_ID
from ui.focus_mode import FocusMode                      # Pomodoro
from utils.event_bus import emit, on, off                # ← NEW: bus d’événements

# Widgets
from ui.widgets.quick_stats import QuickStatsWidget      # Statistiques 2×2 (centre bas)
from ui.widgets.backlog import BacklogWidget             # À rattraper (droite bas)

AI_PLACEHOLDER = "Recherche via ChatGPT Local"

# ---------- Helpers ----------
_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre"
]
def fmt_date_fr(d: date) -> str:
    return f"{_JOURS[d.weekday()].capitalize()} {d.day} {_MOIS[d.month-1]}"


# ---------- UI helpers ----------
class Card(ctk.CTkFrame):
    def __init__(self, parent, title: str = "", *, corner_radius: int = 12, padding: int = 16):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=corner_radius)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent", height=40)
        header.grid(row=0, column=0, sticky="ew", padx=padding, pady=(padding, 8))
        header.grid_propagate(False)

        # — Couleur du bouton CTk actuel (thème) —
        def _button_blue() -> str:
            tmp = ctk.CTkButton(self, text="")
            col = tmp.cget("fg_color")
            tmp.destroy()
            mode = ctk.get_appearance_mode().lower()
            if isinstance(col, tuple) and len(col) >= 2:
                return col[0] if mode == "light" else col[1]
            return col

        self.title_label = ctk.CTkLabel(
            header, text=title, font=("Helvetica", 16, "bold"),
            text_color=_button_blue()
        )
        self.title_label.pack(side="left")

        # corps de la carte = même fond que la carte
        self.body = ctk.CTkFrame(self, fg_color=COLORS["bg_card"])
        self.body.grid(row=1, column=0, sticky="nsew", padx=padding, pady=(0, padding))


# ---------- Widget : Prochaines révisions ----------
class UpcomingReviewsWidget(Card):
    def __init__(self, parent, notion=None):
        super().__init__(parent, title="Prochaines révisions")
        self.notion = notion or get_notion_client()
        self.planner = LocalPlanner()
        self._items_vars: Dict[str, ctk.BooleanVar] = {}
        self._current_date = datetime.now().date()

        # --- recherche: debounce + cache + annulation souple ---
        self._search_after_id = None          # id du after() courant
        self._search_seq = 0                  # numéro de requête pour ignorer les réponses obsolètes
        self._search_cache: Dict[str, list] = {}  # cache simple (clé = query normalisée)
        self._searching = False               # flag pour éviter re-entrance

        self._build()
        self.after(50, self.reload)

    # --- utilitaire: poster sur l'UI en toute sécurité ---
    def _ui(self, fn, delay_ms: int = 0):
        try:
            if delay_ms > 0:
                self.after(delay_ms, fn)
            else:
                self.after_idle(fn)
        except Exception:
            # en cas de fermeture rapide, on ignore
            pass

    def _build(self):
        self.body.grid_rowconfigure(0, weight=0)
        self.body.grid_rowconfigure(1, weight=0)
        self.body.grid_rowconfigure(2, weight=0)
        self.body.grid_rowconfigure(3, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        # (0) barre date
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for i in range(5):
            bar.grid_columnconfigure(i, weight=0)
        bar.grid_columnconfigure(2, weight=1)

        self.prev_btn = ctk.CTkButton(bar, text="◀", width=48, command=self._go_prev)
        self.prev_btn.grid(row=0, column=0, padx=(0, 8))

        self.date_lbl = ctk.CTkLabel(
            bar, text=self._date_fr(self._current_date),
            font=("Helvetica", 16, "bold"), text_color=COLORS["text_primary"]
        )
        self.date_lbl.grid(row=0, column=1, sticky="w")

        self.today_btn = ctk.CTkButton(bar, text="Aujourd'hui", width=120, command=self._go_today)
        self.today_btn.grid(row=0, column=3, padx=8)

        self.next_btn = ctk.CTkButton(bar, text="▶", width=48, command=self._go_next)
        self.next_btn.grid(row=0, column=4)

        # (1) recherche + ITEM + ajouter
        srch = ctk.CTkFrame(self.body, fg_color="transparent")
        srch.grid(row=1, column=0, sticky="ew")
        srch.grid_columnconfigure(0, weight=1)
        srch.grid_columnconfigure(1, weight=0)
        srch.grid_columnconfigure(2, weight=0)
        srch.grid_columnconfigure(3, weight=0)

        self.search_entry = ctk.CTkEntry(
            srch, placeholder_text="Rechercher un cours (Notion)…",
            height=32, fg_color=COLORS["bg_light"], text_color=COLORS["text_primary"]
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(2, 6))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        self.item_switch = ctk.CTkSwitch(srch, text="", width=44)
        self.item_switch.grid(row=0, column=1, padx=(0, 6))
        ctk.CTkLabel(srch, text="ITEM (Collège)", font=FONT,
                     text_color=COLORS["text_primary"]).grid(row=0, column=2, padx=(0, 10))

        self.add_btn = ctk.CTkButton(srch, text="Ajouter", width=84, height=30,
                                     command=self._add_placeholder_item)
        self.add_btn.grid(row=0, column=3, sticky="e")

        # (2) suggestions (100% GRID)
        self.suggest_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.suggest_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.suggest_frame.grid_columnconfigure(0, weight=1)
        self.suggest_frame.grid_remove()

        # ligne d'état sous la barre (chargement / astuce)
        self.search_status = ctk.CTkLabel(self.suggest_frame, text="", font=FONT, text_color=COLORS["text_secondary"])
        self.search_status.grid(row=0, column=0, sticky="w", padx=2, pady=(2, 4))

        # conteneur des boutons (pour rester en grid)
        self.suggest_list = ctk.CTkFrame(self.suggest_frame, fg_color="transparent")
        self.suggest_list.grid(row=1, column=0, sticky="ew")
        self.suggest_list.grid_columnconfigure(0, weight=1)

        # (3) liste
        self.list_frame = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.list_frame.grid(row=3, column=0, sticky="nsew", pady=(6, 0))

    def _date_fr(self, d):
        wd = _JOURS[d.weekday()].capitalize()
        return f"{wd} {d.day} {_MOIS[d.month-1]}"

    def _go_prev(self):
        self._current_date -= timedelta(days=1)
        self.date_lbl.configure(text=self._date_fr(self._current_date))
        self.reload()

    def _go_next(self):
        self._current_date += timedelta(days=1)
        self.date_lbl.configure(text=self._date_fr(self._current_date))
        self.reload()

    def _go_today(self):
        self._current_date = datetime.now().date()
        self.date_lbl.configure(text=self._date_fr(self._current_date))
        self.reload()

    # ---------------- Recherche asynchrone ----------------

    def _on_search_change(self, _evt=None):
        query = (self.search_entry.get() or "").strip()
        try:
            if self._search_after_id:
                self.after_cancel(self._search_after_id)
        except Exception:
            pass
        if len(query) < 2:
            self._hide_suggestions()
            return
        self._search_after_id = self.after(250, lambda q=query: self._kick_search(q))

    # --- nouveau: utilitaire
    def _scroll_list_to_top(self):
        try:
            canvas = getattr(self.list_frame, "_parent_canvas", None) \
                     or getattr(self.list_frame, "_canvas", None)
            if canvas:
                canvas.yview_moveto(0)
        except Exception:
            pass

    def _kick_search(self, query: str):
        """Lance la recherche en thread, avec cache et séquence."""
        qnorm = query.lower().strip()
        self._search_after_id = None
        self._search_seq += 1
        my_seq = self._search_seq

        # état visuel
        self._show_suggestions([])  # affiche le conteneur vide
        self.search_status.configure(text="Recherche…")

        # Cache immédiat
        if qnorm in self._search_cache:
            results = self._search_cache[qnorm]
            self._apply_suggestions_async(my_seq, qnorm, results)
            return

        if self._searching:
            # on ne bloque pas : la dernière frappe gagnera
            pass
        self._searching = True

        def worker():
            try:
                try:
                    results = self.notion.search_courses(query, limit=8) or []
                except Exception:
                    traceback.print_exc()
                    results = []
                self._search_cache[qnorm] = results
            finally:
                self._apply_suggestions_async(my_seq, qnorm, results)
                self._searching = False

        threading.Thread(target=worker, daemon=True).start()

    def _apply_suggestions_async(self, seq: int, key: str, results: list):
        # Ignore si une frappe plus récente a été faite
        if seq != self._search_seq:
            return
        def apply():
            self.search_status.configure(text=("Aucun résultat" if not results else ""))
            self._show_suggestions(results)
        self._ui(apply)

    def _show_suggestions(self, results: list):
        for w in self.suggest_list.winfo_children():
            w.destroy()

        if not results:
            # zone totalement retirée pour éviter l’espace
            self.suggest_frame.grid_remove()
            return

        # rendre visible uniquement quand on a des résultats
        self.suggest_frame.grid()
        for idx, r in enumerate(results):
            btn = ctk.CTkButton(
                self.suggest_list, text=r["title"], anchor="w", height=28,
                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_primary"],
                command=lambda it=r: self._pick_search_result(it)
            )
            btn.grid(row=idx, column=0, sticky="ew", pady=2)

    def _hide_suggestions(self):
        for w in self.suggest_list.winfo_children():
            w.destroy()
        self.suggest_frame.grid_remove()

    def _pick_search_result(self, course: Dict):
        course = dict(course)
        if self.item_switch.get() and course.get("is_college") and course.get("item_num") is not None:
            try:
                pref = f"ITEM {int(course['item_num'])}"
            except Exception:
                pref = f"ITEM {course['item_num']}"
            course["title"] = f"{pref} - {course['title']}"
        course["done"] = False
        self.planner.add(self._current_date, course)
        self.search_entry.delete(0, "end")
        self._hide_suggestions()
        self.reload()
        # --- LIVE UPDATE ---
        emit("revisions.changed"); emit("stats.changed")
        try: self.event_generate("<<PlannerChanged>>", when="tail")
        except Exception: pass

    # ---------------- fusion due/planned + rendu ----------------
    def _fetch_due_for(self, d: date) -> List[Dict]:
        try:
            due = self.notion.get_courses_due_on(d)
        except Exception:
            traceback.print_exc()
            due = []

        planned = self.planner.list_for(d)
        planned_by_id = {x["id"]: x for x in planned}

        merged: List[Dict] = []
        seen = set()

        for it in due:
            cid = it.get("id")
            loc = planned_by_id.get(cid)
            merged.append({
                **it, "source": "notion", "done": bool(loc.get("done")) if loc else False,
            })
            seen.add(cid)

        for it in planned:
            cid = it.get("id")
            if cid not in seen:
                merged.append({
                    "id": cid, "title": it.get("title"),
                    "is_college": bool(it.get("is_college", False)),
                    "item_num": it.get("item_num"), "source": "local",
                    "done": bool(it.get("done", False)),
                })

        merged.sort(key=lambda x: bool(x.get("done", False)))
        return merged

    def reload(self):
        self._hide_suggestions()
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._items_vars.clear()

        items = self._fetch_due_for(self._current_date)
        if not items:
            ctk.CTkLabel(
                self.list_frame,
                text="Aucune révision prévue.",
                text_color=COLORS["text_secondary"], font=FONT
            ).pack(anchor="w", padx=6, pady=8)
            self._scroll_list_to_top()
            return

        for item in items:
            var = ctk.BooleanVar(value=bool(item.get("done", False)))
            ctk.CTkCheckBox(
                self.list_frame,
                text=item["title"],
                variable=var,
                text_color=COLORS["text_primary"],
                fg_color=COLORS["bg_card_hover"],
                hover_color=COLORS["accent"],
                command=lambda it=item, v=var: self._on_checked(it, v),
            ).pack(fill="x", padx=6, pady=6)
            self._items_vars[item["id"]] = var

        self._scroll_list_to_top()

    def _on_checked(self, item: Dict, var: ctk.BooleanVar):
        is_on = bool(var.get())
        cid = item["id"]
        self.planner.set_done(self._current_date, cid, is_on)
        if is_on:
            try:
                self.notion.increment_review_counter(cid, item.get("is_college", False))
                self.notion.append_review_to_daily_bilan(item["title"])
            except Exception:
                traceback.print_exc()
        # --- LIVE UPDATE ---
        emit("revisions.changed"); emit("stats.changed")
        try: self.event_generate("<<PlannerChanged>>", when="tail")
        except Exception: pass

    def _add_placeholder_item(self):
        title = self.search_entry.get().strip()
        if not title:
            return
        if self.item_switch.get():
            title = f"ITEM ? - {title}"
        fake = {
            "id": f"local-{datetime.now().timestamp()}",
            "title": title,
            "is_college": bool(self.item_switch.get()),
            "item_num": None,
            "done": False,
        }
        self.planner.add(self._current_date, fake)
        self.search_entry.delete(0, "end")
        self.reload()
        # --- LIVE UPDATE ---
        emit("revisions.changed"); emit("stats.changed")
        try: self.event_generate("<<PlannerChanged>>", when="tail")
        except Exception: pass


# ---------- Widget : To-Do par date (J / J+1 / J+2) ----------
class TodoByDateWidget(ctk.CTkFrame):
    """
    Affiche la To-Do d'une date (J/J+1/J+2) :
      - Section Notion (checkbox propriétés de page), synchronisée
      - Section Ajouts locaux (LocalTodoStore), NON synchronisée
    """
    def __init__(self, parent, notion=None, on_date_change=None):
        # Fond identique à la carte
        super().__init__(parent, fg_color=COLORS["bg_card"])
        self.notion = notion or get_notion_client()
        self.local = LocalTodoStore()
        self.on_date_change = on_date_change or (lambda d: None)

        self.base_date = datetime.now().date()
        self.delta = 0  # 0=J, 1=J+1, 2=J+2
        self._page_id = None
        self._notion_vars: Dict[str, ctk.BooleanVar] = {}
        self._local_vars: Dict[str, ctk.BooleanVar] = {}

        # rendu différé + anti re-entrance
        self._rendering = False
        self._pending_render = None

        self._build()
        self._schedule_render()

    # ---- UI ----
    def _build(self):
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self.grid_columnconfigure(0, weight=1)

        # Barre date
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(1, weight=1)

        self.prev_btn = ctk.CTkButton(bar, text="◀", width=42, command=self._go_prev)
        self.prev_btn.grid(row=0, column=0, padx=(0, 8))

        self.date_lbl = ctk.CTkLabel(bar, text="", font=("Helvetica", 16, "bold"),
                                     text_color=COLORS["text_primary"])
        self.date_lbl.grid(row=0, column=1, sticky="w")

        self.today_btn = ctk.CTkButton(bar, text="Aujourd'hui", width=120, command=self._go_today)
        self.today_btn.grid(row=0, column=2, padx=8)

        self.next_btn = ctk.CTkButton(bar, text="▶", width=42, command=self._go_next)
        self.next_btn.grid(row=0, column=3)

        # Conteneur listes → même fond que la carte
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_card"])
        self.scroll.grid(row=1, column=0, sticky="nsew")

        self.notion_title = ctk.CTkLabel(self.scroll, text="Depuis Notion",
                                         font=("Helvetica", 14, "bold"),
                                         text_color=COLORS["text_secondary"])
        self.notion_title.pack(anchor="w", pady=(2, 6))

        # IMPORTANT : conteneurs transparents pour hériter du fond de la carte
        self.notion_box = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.notion_box.pack(fill="x", pady=(0, 10))

        self.local_title = ctk.CTkLabel(self.scroll, text="Ajouts locaux (non synchronisés)",
                                        font=("Helvetica", 14, "bold"),
                                        text_color=COLORS["text_secondary"])
        self.local_title.pack(anchor="w", pady=(6, 6))

        # Ajout local
        add_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        add_row.pack(fill="x", pady=(0, 6))
        add_row.grid_columnconfigure(0, weight=1)

        self.entry_local = ctk.CTkEntry(add_row, placeholder_text="Ajouter une tâche locale…",
                                        height=32, fg_color=COLORS["bg_light"])
        self.entry_local.grid(row=0, column=0, sticky="ew")
        self.entry_local.bind("<Return>", lambda e: self._add_local())

        self.btn_add = ctk.CTkButton(add_row, text="+", width=36, command=self._add_local)
        self.btn_add.grid(row=0, column=1, padx=(8, 0))

        self.local_box = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.local_box.pack(fill="x")

        # Progression
        self.progress_lbl = ctk.CTkLabel(self, text="Progression : –", text_color=COLORS["text_secondary"])
        self.progress_lbl.grid(row=2, column=0, sticky="w", pady=(8, 0))

    # ---- Navigation ----
    def _current_date(self) -> date:
        return self.base_date + timedelta(days=self.delta)

    # (non utilisé en dehors, donc remplace par iso de _current_date())
    def _current_iso(self) -> str:
        return self._current_date().isoformat()

    def _sync_buttons(self):
        self.prev_btn.configure(state=("disabled" if self.delta <= 0 else "normal"))
        self.next_btn.configure(state=("disabled" if self.delta >= 2 else "normal"))

    def _go_prev(self):
        if self.delta > 0:
            self.delta -= 1
            self._schedule_render()

    def _go_next(self):
        if self.delta < 2:
            self.delta += 1
            self._schedule_render()

    def _go_today(self):
        self.delta = 0
        self._schedule_render()

    # ---- Data ----
    def _load_notion_checks(self) -> Dict[str, bool]:
        try:
            page = self.notion.get_todo_page_by_date(TO_DO_DATABASE_ID, self._current_iso())
        except Exception:
            traceback.print_exc()
            page = None
        self._page_id = page["id"] if page else None
        if not page:
            return {}
        try:
            props = page.get("properties", {}) or {}
            return {k: v["checkbox"] for k, v in props.items() if v.get("type") == "checkbox"}
        except Exception:
            traceback.print_exc()
            return {}

    # ---- Render ----
    def _schedule_render(self):
        try:
            if hasattr(self, "_pending_render") and self._pending_render is not None:
                self.after_cancel(self._pending_render)
        except Exception:
            pass
        self._pending_render = self.after(0, self._render)

    def _render(self):
        if getattr(self, "_rendering", False):
            return
        self._rendering = True
        try:
            d = self._current_date()
            self.date_lbl.configure(text=fmt_date_fr(d))
            self._sync_buttons()
            try:
                self.on_date_change(d)
            except Exception:
                traceback.print_exc()

            # Reset contenus
            for w in self.notion_box.winfo_children():
                w.destroy()
            for w in self.local_box.winfo_children():
                w.destroy()
            self._notion_vars.clear()
            self._local_vars.clear()

            # Notion
            checks = self._load_notion_checks()
            if not checks:
                ctk.CTkLabel(self.notion_box, text="(aucune tâche Notion)",
                             text_color=COLORS["text_secondary"]).pack(anchor="w", padx=10, pady=8)
                self._notion_done = (0, 0)
            else:
                done = 0
                for name in sorted(checks.keys(), key=str.lower):
                    var = ctk.BooleanVar(value=bool(checks[name]))
                    if var.get():
                        done += 1
                    ctk.CTkCheckBox(
                        self.notion_box,
                        text=name,
                        variable=var,
                        text_color=COLORS["text_primary"],
                        fg_color=COLORS["bg_card_hover"],   # harmonisé
                        hover_color=COLORS["accent"],
                        command=lambda n=name, v=var: self._on_toggle_notion(n, v)
                    ).pack(fill="x", padx=10, pady=6)
                    self._notion_vars[name] = var
                self._notion_done = (done, len(checks))

            # Locaux
            try:
                local_items = self.local.list(self._current_iso())
            except Exception:
                traceback.print_exc()
            else:
                pass
            local_items = locals().get("local_items", [])

            if not local_items:
                ctk.CTkLabel(self.local_box, text="(aucun ajout local)",
                             text_color=COLORS["text_secondary"]).pack(anchor="w", padx=10, pady=8)
                self._local_done = (0, 0)
            else:
                l_done = 0
                for it in local_items:
                    var = ctk.BooleanVar(value=bool(it.get("checked", False)))
                    if var.get():
                        l_done += 1
                    row = ctk.CTkFrame(self.local_box, fg_color="transparent")
                    row.pack(fill="x", padx=8, pady=4)
                    ctk.CTkCheckBox(
                        row, text=str(it.get("text", "")), variable=var,
                        text_color=COLORS["text_primary"],
                        fg_color=COLORS["bg_card_hover"],   # harmonisé
                        hover_color=COLORS["accent"],
                        command=lambda iid=it.get("id",""), v=var: self._on_toggle_local(iid, v)
                    ).pack(side="left", fill="x", expand=True)
                    ctk.CTkButton(
                        row, text="X", width=32,
                        command=lambda iid=it.get("id",""): self._on_remove_local(iid)
                    ).pack(side="right", padx=(8, 0))
                    if "id" in it:
                        self._local_vars[it["id"]] = var
                self._local_done = (l_done, len(local_items))

            self._update_progress()
        except Exception:
            traceback.print_exc()
        finally:
            self._rendering = False
            self._pending_render = None

    def _update_progress(self):
        dn, tn = getattr(self, "_notion_done", (0, 0))
        dl, tl = getattr(self, "_local_done", (0, 0))
        self.progress_lbl.configure(text=f"Progression — Notion: {dn}/{tn} • Locaux: {dl}/{tl}")

    # ---- Actions ---
    def _on_toggle_notion(self, prop_name: str, var: ctk.BooleanVar):
        if not self._page_id:
            return
        try:
            self.notion.update_checkbox_property(self._page_id, prop_name, bool(var.get()))
        except Exception:
            traceback.print_exc()
        self._schedule_render()
        # --- LIVE UPDATE ---
        emit("todo.changed"); emit("stats.changed")
        try: self.event_generate("<<TodoChanged>>", when="tail")
        except Exception: pass

    def _add_local(self):
        txt = (self.entry_local.get() or "").strip()
        if not txt:
            return
        try:
            self.local.add(self._current_iso(), txt)
        except Exception:
            traceback.print_exc()
            return
        try:
            self.entry_local.delete("0", "end")
        except Exception:
            pass
        self._schedule_render()
        # --- LIVE UPDATE ---
        emit("todo.changed"); emit("stats.changed")
        try: self.event_generate("<<TodoChanged>>", when="tail")
        except Exception: pass

    def _on_toggle_local(self, item_id: str, var: ctk.BooleanVar):
        try:
            self.local.set_checked(self._current_iso(), item_id, bool(var.get()))
        except Exception:
            traceback.print_exc()
        self._schedule_render()
        # --- LIVE UPDATE ---
        emit("todo.changed"); emit("stats.changed")
        try: self.event_generate("<<TodoChanged>>", when="tail")
        except Exception: pass

    def _on_remove_local(self, item_id: str):
        try:
            self.local.remove(self._current_iso(), item_id)
        except Exception:
            traceback.print_exc()
        self._schedule_render()
        # --- LIVE UPDATE ---
        emit("todo.changed"); emit("stats.changed")
        try: self.event_generate("<<TodoChanged>>", when="tail")
        except Exception: pass


# ---------- Dashboard ----------
class Dashboard(ctk.CTkFrame):
    """
    Colonne gauche: To-Do par date + Bilan du jour.
    Centre/droite: widgets (Prochaines révisions / Focus / Stats / Backlog).
    """
    def __init__(self, parent, data_manager=None):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.notion = get_notion_client()
        self.data_manager = data_manager

        # Prépare J/J+1/J+2 (et J-1) côté Notion
        DailyToDoGenerator().generate(origin="dashboard")

        self._evt_hooks: list[tuple[str, callable]] = []  # pour off() à la destruction

        self._build_layout()
        self._install_event_hooks()
        self.refresh_widgets()  # premier rendu Stats/Backlog

    # ----- Layout maître -----
    def _build_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0, minsize=60)
        for c in range(3):
            self.grid_columnconfigure(c, weight=1, uniform="cols")

        # Colonne gauche
        self.left_col = ctk.CTkFrame(self, fg_color="transparent")
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(14, 7), pady=(14, 0))
        self.left_col.grid_rowconfigure(0, weight=1)
        self.left_col.grid_columnconfigure(0, weight=1)

        self.left_card = Card(self.left_col, "To-Do")
        self.left_card.grid(row=0, column=0, sticky="nsew")

        # Colonnes centre/droite
        self._build_center_right()

        # Contenu carte gauche
        self._build_left_card(self.left_card.body)

        # Barre IA en bas
        self._build_ai_bar()

    def _build_center_right(self):
        # ===== Colonne centre =====
        self.mid_col = ctk.CTkFrame(self, fg_color="transparent")
        self.mid_col.grid(row=0, column=1, sticky="nsew", padx=7, pady=(14, 0))
        self.mid_col.grid_rowconfigure(0, weight=1)  # haut
        self.mid_col.grid_rowconfigure(1, weight=1)  # bas
        self.mid_col.grid_columnconfigure(0, weight=1)

        # Haut : Prochaines révisions
        self.upcoming = UpcomingReviewsWidget(self.mid_col, self.notion)
        self.upcoming.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        # >>> rafraîchir stats quand le planner change (avec mini-sync)
        self.upcoming.bind("<<PlannerChanged>>", lambda e: self._after_data_change())

        # Bas : Statistiques rapides — IMPORTANT : on partage le même Notion et le même Planner
        self.quick_stats = QuickStatsWidget(
            self.mid_col,
            notion=self.notion,
            planner=self.upcoming.planner,   # ← même instance que “Prochaines révisions”
        )
        self.quick_stats.grid(row=1, column=0, sticky="nsew", pady=(7, 0))

        # ===== Colonne droite =====
        self.right_col = ctk.CTkFrame(self, fg_color="transparent")
        self.right_col.grid(row=0, column=2, sticky="nsew", padx=(7, 14), pady=(14, 0))
        self.right_col.grid_rowconfigure(0, weight=1)  # haut
        self.right_col.grid_rowconfigure(1, weight=1)  # bas
        self.right_col.grid_columnconfigure(0, weight=1)

        # Haut : Focus (Pomodoro)
        rtop = Card(self.right_col, "Focus")
        rtop.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        self.focus_mode = FocusMode(rtop.body)
        self.focus_mode.pack(fill="both", expand=True)
        # >>> si le Pomodoro émet <<FocusLogged>>, on MAJ les stats (avec mini-sync)
        try:
            self.focus_mode.bind("<<FocusLogged>>", lambda e: (emit("revisions.changed"), emit("stats.changed"), self._after_data_change()))
        except Exception:
            pass

        # Bas : Backlog (À rattraper)
        self.backlog = BacklogWidget(self.right_col)
        self.backlog.grid(row=1, column=0, sticky="nsew", pady=(7, 0))

    # ----- Carte gauche: To-Do + Bilan du jour -----
    def _build_left_card(self, parent):
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_columnconfigure(0, weight=1)

        # To-Do (J/J+1/J+2 + ajouts locaux)
        def _update_card_title(d: date):
            self.left_card.title_label.configure(text=f"To-Do — {fmt_date_fr(d)}")

        self.todo_by_date = TodoByDateWidget(parent, notion=self.notion, on_date_change=_update_card_title)
        self.todo_by_date.grid(row=0, column=0, sticky="nsew")
        # >>> notifier stats quand la To-Do change (avec mini-sync)
        self.todo_by_date.bind("<<TodoChanged>>", lambda e: (emit("todo.changed"), emit("stats.changed"), self._after_data_change()))

        # Bilan du jour
        bilan = ctk.CTkFrame(parent, fg_color="transparent")
        bilan.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bilan.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bilan, text="Bilan du jour", font=("Helvetica", 16, "bold"),
                     text_color=COLORS["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 6))

        ctk.CTkLabel(bilan, text="Commentaires :", font=FONT,
                     text_color=COLORS["text_primary"]).grid(row=1, column=0, sticky="w")
        self.comment_box = ctk.CTkTextbox(bilan, height=80, corner_radius=8, fg_color=COLORS["bg_light"])
        self.comment_box.grid(row=2, column=0, sticky="ew", pady=(4, 8))

        btn_row = ctk.CTkFrame(bilan, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="e")
        ctk.CTkButton(btn_row, text="Envoyer", height=30, command=self._send_comment).pack(side="right")

    # ----- Barre IA -----
    def _build_ai_bar(self):
        ai_container = ctk.CTkFrame(self, fg_color=COLORS["bg_light"], height=60)
        ai_container.grid(row=2, column=0, columnspan=3, sticky="sew", pady=0)
        ai_container.grid_propagate(False)

        self.ai_entry = ctk.CTkEntry(
            ai_container, placeholder_text=AI_PLACEHOLDER,
            height=44, corner_radius=22, border_width=0,
            fg_color=COLORS["bg_card"], text_color=COLORS["text_primary"]
        )
        self.ai_entry.pack(fill="x", padx=18, pady=8)
        self.ai_entry.bind("<Return>", self._on_ai_submit_event)

    # ----- Commentaire -----
    def _send_comment(self):
        txt = self.comment_box.get("1.0", "end").strip()
        if not txt:
            return
        self.notion.append_daily_bilan([], txt)
        self.comment_box.delete("1.0", "end")
        # Un commentaire peut impacter certaines stats d’activité → on émet large
        emit("stats.changed")

    # ====== Rafraîchissement Stats/Backlog ======
    def refresh_widgets(self):
        try:
            # si QuickStats expose reload_async (version non bloquante), on l’utilise
            if hasattr(self.quick_stats, "reload_async"):
                self.quick_stats.reload_async()
            else:
                self.quick_stats.refresh()
        except Exception:
            traceback.print_exc()
        try:
            if hasattr(self.backlog, "reload_async"):
                self.backlog.reload_async()
            else:
                self.backlog.refresh()
        except Exception:
            traceback.print_exc()

    # === Mini-sync + rafraîchissement (utilisé après chaque coche) ===
    def _after_data_change(self):
        """
        Appelé après une action utilisateur (cocher To-Do, marquer une révision, etc.).
        - Force une sync courte (force=True) pour contrecarrer un éventuel TTL cache.
        - Puis rafraîchit les widgets de statistiques et backlog.
        """
        # Récupère DataManager depuis l'App si non fourni au constructeur
        dm = self.data_manager
        try:
            if dm is None and hasattr(self.master, "data_manager"):
                dm = self.master.data_manager
        except Exception:
            dm = self.data_manager

        if dm and hasattr(dm, "sync_async"):
            try:
                dm.sync_async(on_done=lambda: self.after(0, self._refresh_stats_soft), force=True)
                return
            except Exception:
                traceback.print_exc()
        elif dm and hasattr(dm, "sync_background"):
            try:
                dm.sync_background()
            except Exception:
                traceback.print_exc()

        # Fallback si pas de sync_async
        self._refresh_stats_soft()

    def _refresh_stats_soft(self):
        """
        Rafraîchit les tuiles de stats/backlog sans bloquer l’UI.
        """
        try:
            if hasattr(self.quick_stats, "reload_async"):
                self.quick_stats.reload_async()
            else:
                self.quick_stats.refresh()
        except Exception:
            traceback.print_exc()
        try:
            if hasattr(self.backlog, "reload_async"):
                self.backlog.reload_async()
            else:
                self.backlog.refresh()
        except Exception:
            traceback.print_exc()

    # ====== Hooks EventBus ======
    def _install_event_hooks(self):
        """
        S'abonne aux événements de données pour rafraîchir UI de façon ciblée.
        """
        def hook(event: str, cb):
            on(event, cb)
            self._evt_hooks.append((event, cb))

        hook("stats.changed", lambda *a, **k: self.after(0, self._refresh_stats_soft))
        hook("revisions.changed", lambda *a, **k: self.after(0, self._refresh_stats_soft))
        hook("todo.changed", lambda *a, **k: self.after(0, self._refresh_stats_soft))
        hook("notion:page_updated", lambda *a, **k: self.after(0, self._refresh_stats_soft))

    def destroy(self):
        # Désabonnement propre
        try:
            for ev, cb in getattr(self, "_evt_hooks", []):
                try:
                    off(ev, cb)
                except Exception:
                    pass
        finally:
            return super().destroy()

    # ====== IA ======
    def _on_ai_submit_event(self, _evt):
        self._on_ai_submit()

    def _on_ai_submit(self):
        query = self.ai_entry.get().strip()
        if not query:
            return
        self._open_ai_dialog_and_query(query)
        self.ai_entry.delete("0", "end")

    def _open_ai_dialog_and_query(self, query: str):
        if not query:
            return

        dlg = AIAnswerDialog.open(parent=self, title="Réponse IA", typing=False, sources=[])
        dlg.start_loader("Je réfléchis")

        def worker():
            try:
                got_first_chunk = False
                for chunk in rag.stream(query):
                    if not chunk:
                        continue
                    if not got_first_chunk:
                        got_first_chunk = True
                        dlg.after(0, dlg.stop_loader)
                    dlg.after(0, dlg.append, chunk)
                dlg.after(0, getattr(dlg.text, "reparse_from_buffer", lambda: None))
                try:
                    res = rag.ask_with_sources(query)
                    dlg.after(0, dlg.set_sources, res.get("sources", []))
                except Exception:
                    traceback.print_exc()
            except Exception as e:
                dlg.after(0, dlg.append, f"\n\n[Erreur: {e!r}]")

        threading.Thread(target=worker, daemon=True).start()
