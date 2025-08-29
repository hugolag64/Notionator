# ui/center_notice.py
from __future__ import annotations
import customtkinter as ctk
from services.settings_store import settings

class CenterNotice(ctk.CTkToplevel):
    """
    Petit modal centré, style Apple-like.
    Utilisé pour signaler la fin de session/pause, etc.
    """
    def __init__(self, parent, title: str, message: str,
                 button_text: str = "OK", on_close=None):
        super().__init__(parent)
        self.withdraw()  # éviter flicker le temps du layout
        self.title(title)
        self.configure(fg_color="#FFFFFF")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Dimensions et centrage
        w, h = 420, 220
        self.geometry(f"{w}x{h}")
        try:
            self.update_idletasks()
            if parent is not None:
                px = parent.winfo_rootx()
                py = parent.winfo_rooty()
                pw = parent.winfo_width()
                ph = parent.winfo_height()
                x = px + (pw - w) // 2
                y = py + (ph - h) // 2
                self.geometry(f"+{x}+{y}")
        except Exception:
            pass

        # Container
        wrap = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=16)
        wrap.pack(fill="both", expand=True, padx=16, pady=16)

        # Titre + message
        lbl_title = ctk.CTkLabel(wrap, text=title, font=("SF Pro Display", 18, "bold"), text_color="#0B1320")
        lbl_msg = ctk.CTkLabel(wrap, text=message, font=("SF Pro Text", 13), text_color="#374151", justify="center")
        lbl_title.place(relx=0.5, rely=0.22, anchor="center")
        lbl_msg.place(relx=0.5, rely=0.48, anchor="center")

        # Bouton (width/height dans le CONSTRUCTEUR, pas dans place)
        self._on_close = on_close
        btn_ok = ctk.CTkButton(
            wrap,
            text=button_text or "OK",
            width=140, height=36, corner_radius=12,
            command=self._close
        )
        btn_ok.place(relx=0.5, rely=0.78, anchor="center")

        self.deiconify()
        self.focus_force()
        self.bind("<Escape>", lambda _e: self._close())

    def _close(self):
        try:
            if callable(self._on_close):
                self._on_close()
        finally:
            self.grab_release()
            self.destroy()

    # Helper statique
    @staticmethod
    def show(parent, title: str, message: str, button_text: str = "OK", on_close=None):
        CenterNotice(parent, title, message, button_text, on_close)
