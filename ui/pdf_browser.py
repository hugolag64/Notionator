# ui/pdf_browser.py
from __future__ import annotations
import customtkinter as ctk
from tkinter import messagebox
from typing import Callable, List, Dict
from .styles import COLORS

class PDFBrowser(ctk.CTkToplevel):
    """
    Ouvre un listing simple des PDFs renvoyés par fetch_files().
    Chaque entrée = {'name': str, 'webViewLink': str, ...}
    Retour: dict du fichier choisi ou None (via PDFBrowser.open).
    """
    def __init__(self, parent, fetch_files: Callable[[], List[Dict]]):
        super().__init__(parent)
        self.title("Parcourir les PDF")
        self.geometry("560x520")
        self.configure(fg_color=COLORS["bg_light"])
        self.transient(parent)
        self.grab_set()
        self.result: Dict | None = None
        self._fetch_files = fetch_files

        ctk.CTkLabel(self, text="Sélectionnez un PDF", font=("Helvetica", 20, "bold"),
                     text_color=COLORS["accent"]).pack(pady=(16, 8))

        # Conteneur scrollable
        wrapper = ctk.CTkFrame(self, fg_color="transparent")
        wrapper.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self.scroll = ctk.CTkScrollableFrame(wrapper, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True)

        # Boutons bas
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(btns, text="Rafraîchir", command=self._reload).pack(side="left")
        ctk.CTkButton(btns, text="Fermer", fg_color="#BFBFBF", text_color="black",
                      hover_color="#AFAFAF", command=self._close).pack(side="right")

        self._reload()

    def _reload(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        try:
            files = self._fetch_files() or []
        except Exception as e:
            messagebox.showerror("Erreur", f"Échec du chargement des PDF:\n{e}")
            files = []

        if not files:
            ctk.CTkLabel(self.scroll, text="Aucun PDF trouvé.",
                         text_color=COLORS["text_secondary"], font=("Helvetica", 14)).pack(pady=20)
            return

        for f in files:
            row = ctk.CTkFrame(self.scroll, fg_color="#F5F5F5", corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)
            name = f.get("name", "Sans nom")

            lbl = ctk.CTkLabel(row, text=name, anchor="w", text_color="#000",
                               font=("Helvetica", 14))
            lbl.pack(side="left", padx=10, pady=10, fill="x", expand=True)

            def _select(file=f):
                self.result = file
                self.destroy()

            # Double-clic ou bouton
            lbl.bind("<Double-Button-1>", lambda _e, file=f: _select(file))
            ctk.CTkButton(row, text="Choisir", width=90, command=_select).pack(side="right", padx=8, pady=8)

    def _close(self):
        self.result = None
        self.destroy()

    @staticmethod
    def open(parent, fetch_files: Callable[[], List[Dict]]):
        dlg = PDFBrowser(parent, fetch_files)
        parent.wait_window(dlg)
        return dlg.result
