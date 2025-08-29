# ui/notifications_panel.py
from __future__ import annotations
import customtkinter as ctk
from typing import Optional
from services.notification_center import NotificationCenter, Notification

LEVEL_LABEL = {"info": "Info", "success": "Succès", "warning": "Alerte", "error": "Erreur"}

class NotificationsPanel(ctk.CTkFrame):
    """
    Panneau scrollable avec filtre (niveau/catégorie), marquer lu, tout effacer, etc.
    À placer dans le Dashboard (colonne droite haut, par ex.).
    """
    def __init__(self, parent):
        super().__init__(parent, fg_color="#FFFFFF", corner_radius=18)
        self._nc = NotificationCenter.instance()

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 0))
        title = ctk.CTkLabel(header, text="Notifications", font=("SF Pro Display", 18, "bold"), text_color="#0B1320")
        title.pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right")
        self.btn_clear = ctk.CTkButton(actions, text="Tout effacer", height=30, corner_radius=12, command=self._clear)
        self.btn_clear.pack(side="right", padx=4)
        self.btn_clear_non = ctk.CTkButton(actions, text="Effacer non-sticky", height=30, corner_radius=12, command=self._clear_non_sticky)
        self.btn_clear_non.pack(side="right", padx=4)

        # Filtres
        filters = ctk.CTkFrame(self, fg_color="transparent")
        filters.pack(fill="x", padx=16, pady=(8, 6))

        self.level_var = ctk.StringVar(value="all")
        self.category_var = ctk.StringVar(value="all")

        lvl = ctk.CTkOptionMenu(filters, values=["all", "info", "success", "warning", "error"], variable=self.level_var, width=120, corner_radius=12, command=lambda _: self.refresh())
        cat = ctk.CTkEntry(filters, placeholder_text="Filtrer par catégorie (ex. pomodoro)", width=220)
        cat.bind("<KeyRelease>", lambda e: self._on_category_change(cat.get()))
        lvl.pack(side="left", padx=(0, 8))
        cat.pack(side="left")

        # Liste scrollable
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        self.body = body

        # Sub to stream
        self._unsub = self._nc.subscribe(lambda n: self.after(0, self.refresh))
        self.refresh()

    def destroy(self):
        try:
            self._unsub()
        except Exception:
            pass
        return super().destroy()

    def _on_category_change(self, text: str):
        self.category_var.set(text.strip() or "all")
        self.refresh()

    def _clear(self):
        self._nc.clear_all()
        self.refresh()

    def _clear_non_sticky(self):
        self._nc.clear_non_sticky()
        self.refresh()

    def refresh(self):
        for w in self.body.winfo_children():
            w.destroy()

        level = self.level_var.get()
        category = self.category_var.get()

        items = self._nc.all()
        for n in items:
            if level != "all" and n.level != level:
                continue
            if category != "all" and n.category.lower() != category.lower():
                continue
            self._render_item(n)

    def _render_item(self, n: Notification):
        card = ctk.CTkFrame(self.body, corner_radius=14, fg_color="#F9FAFB")
        card.pack(fill="x", pady=6, padx=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))

        title = f"{n.title} · {LEVEL_LABEL.get(n.level, n.level)} · {n.category}"
        lbl = ctk.CTkLabel(top, text=title, font=("SF Pro Display", 14, "bold"), text_color="#0B1320")
        lbl.pack(side="left")

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right")
        mark = ctk.CTkButton(right, text=("Marquer non lu" if n.read else "Marquer lu"), height=28, corner_radius=10, command=lambda nid=n.id, r=not n.read: self._mark(nid, r))
        mark.pack(side="left", padx=4)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(6, 10))

        msg = ctk.CTkLabel(body, text=n.message, font=("SF Pro Text", 12), text_color="#1F2937", justify="left", wraplength=520)
        msg.pack(anchor="w")

        if n.actions:
            btns = ctk.CTkFrame(body, fg_color="transparent")
            btns.pack(anchor="w", pady=(8, 0))
            for a in n.actions:
                b = ctk.CTkButton(btns, text=a.label, height=28, corner_radius=10, command=a.callback)
                b.pack(side="left", padx=4)

    def _mark(self, nid: int, read: bool):
        self._nc.mark_read(nid, read)
        self.refresh()
