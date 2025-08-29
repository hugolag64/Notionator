# ui/notification_toast.py
from __future__ import annotations
import customtkinter as ctk
import tkinter as tk
from typing import Optional
from services.notification_center import Notification, NotificationCenter

COLORS = {
    "bg": "#111827",
    "text": "#F9FAFB",
    "info": "#2563EB",
    "success": "#16A34A",
    "warning": "#D97706",
    "error": "#DC2626",
}

class NotificationToast(ctk.CTkFrame):
    def __init__(self, parent):
        # pas de fond, pas de bord
        super().__init__(parent, fg_color="transparent", width=1, height=1)
        # placer en bas-droite mais sans occuper d'espace visible
        self.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        self.configure(width=1, height=1)  # <- empêche un carré visible
        self._stack: list[ctk.CTkFrame] = []
        self._unsub = NotificationCenter.instance().subscribe(self._on_notification)


    def destroy(self):
        try:
            self._unsub()
        except Exception:
            pass
        return super().destroy()

    def _on_notification(self, n: Notification):
        self.after(0, lambda: self._push_toast(n))

    def _push_toast(self, n: Notification):
        card = ctk.CTkFrame(self, corner_radius=14, fg_color=COLORS["bg"])
        # accent à gauche selon niveau
        left = ctk.CTkFrame(card, width=6, fg_color=COLORS.get(n.level, COLORS["info"]), corner_radius=6)
        left.pack(side="left", fill="y", padx=(8, 10), pady=10)

        title = ctk.CTkLabel(card, text=n.title, font=("SF Pro Display", 14, "bold"), text_color=COLORS["text"])
        msg = ctk.CTkLabel(card, text=n.message, font=("SF Pro Text", 12), text_color="#D1D5DB", justify="left", wraplength=280)

        btns_wrap = ctk.CTkFrame(card, fg_color="transparent")
        for act in (n.actions or []):
            b = ctk.CTkButton(btns_wrap, text=act.label, height=28, corner_radius=10, command=act.callback)
            b.pack(side="left", padx=4)

        title.pack(anchor="w", pady=(10, 0), padx=(0, 12))
        msg.pack(anchor="w", padx=(0, 12), pady=(2, 8))
        if n.actions:
            btns_wrap.pack(anchor="w", padx=(0, 12), pady=(0, 10))

        # empilement visuel
        if self._stack:
            y_offset = sum(w.winfo_reqheight() + 8 for w in self._stack)
            card.place(relx=1.0, rely=1.0, x=-0, y=-(y_offset), anchor="se")
        else:
            card.place(relx=1.0, rely=1.0, anchor="se")

        self._stack.append(card)

        # auto-close si non sticky
        if not n.sticky:
            self.after(4500, lambda: self._dismiss(card))

    def _dismiss(self, card: ctk.CTkFrame):
        if card not in self._stack:
            return
        idx = self._stack.index(card)
        card.destroy()
        del self._stack[idx]
        # Réarrange le stack restant
        self.after(0, self._reflow)

    def _reflow(self):
        y = 0
        for card in self._stack:
            h = card.winfo_reqheight()
            card.place_configure(relx=1.0, rely=1.0, x=-0, y=-y, anchor="se")
            y += h + 8
