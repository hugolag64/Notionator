# ui/widgets/backlog.py
from __future__ import annotations
import customtkinter as ctk
from datetime import date, timedelta
from typing import List

TITLE_CLR  = "#0B1320"
SUB_CLR    = "#6B7280"
CARD_BG    = "#FFFFFF"

try:
    from services.local_planner import LocalPlanner
except Exception:
    LocalPlanner = None


class BacklogWidget(ctk.CTkFrame):
    """
    Liste des cours à rattraper (top 5 par retard décroissant).
    Chaque ligne est cliquable → callback fourni (optionnel) pour ouvrir la fiche / cocher.
    """
    def __init__(self, parent, on_open=None, on_catch_up=None):
        super().__init__(parent, fg_color=CARD_BG, corner_radius=16)
        self.configure(border_color="#EEF0F3", border_width=1)
        self.grid_columnconfigure(0, weight=1)
        self._title = ctk.CTkLabel(self, text="À rattraper", text_color=TITLE_CLR, font=("SF Pro Display", 16, "bold"))
        self._title.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        self._sub = ctk.CTkLabel(self, text="Les 5 plus en retard", text_color=SUB_CLR, font=("SF Pro Text", 12))
        self._sub.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))

        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 8))
        self.grid_rowconfigure(2, weight=1)

        self._planner = LocalPlanner() if LocalPlanner else None
        self._on_open = on_open
        self._on_catch = on_catch_up

    def refresh(self):
        for w in self._container.winfo_children():
            w.destroy()

        items = self._get_overdue_items(limit=5)
        if not items:
            empty = ctk.CTkLabel(self._container, text="Rien à rattraper 🎉", text_color=SUB_CLR)
            empty.pack(padx=12, pady=8, anchor="w")
            return

        for it in items:
            self._row(self._container, it)

    # ----- helpers -----
    def _row(self, parent, it):
        row = ctk.CTkFrame(parent, fg_color="#F8FAFC", corner_radius=12)
        row.pack(fill="x", padx=8, pady=6)

        title = getattr(it, "title", getattr(it, "name", "Cours"))
        delay = getattr(it, "_delay", 0)
        lbl = ctk.CTkLabel(row, text=f"{title}", text_color=TITLE_CLR, font=("SF Pro Text", 13, "bold"))
        lbl.pack(side="left", padx=10, pady=10)

        sub = ctk.CTkLabel(row, text=f"{delay} j de retard", text_color=SUB_CLR, font=("SF Pro Text", 12))
        sub.pack(side="left", padx=8)

        btn_open = ctk.CTkButton(row, text="Ouvrir", width=74, command=lambda it=it: self._on_open and self._on_open(it))
        btn_open.pack(side="right", padx=8, pady=8)

        btn_fix = ctk.CTkButton(row, text="Rattraper", width=94,
                                command=lambda it=it: self._on_catch and self._on_catch(it))
        btn_fix.pack(side="right", padx=6, pady=8)

    def _get_overdue_items(self, limit=5) -> List[object]:
        lst = []
        try:
            if not self._planner:
                return lst
            today = date.today()
            # Si la méthode existe, on l'utilise
            overdue = getattr(self._planner, "overdue", None)
            if callable(overdue):
                lst = overdue()
            else:
                # Fallback : balaye 30 jours
                tmp = []
                for i in range(1, 31):
                    d = today - timedelta(days=i)
                    for r in self._planner.planned_for(d):
                        if not getattr(r, "done", False):
                            r._delay = i
                            tmp.append(r)
                # tri par retard décroissant
                lst = sorted(tmp, key=lambda x: getattr(x, "_delay", 0), reverse=True)
            return lst[:limit]
        except Exception:
            return []
