# ui/dropzone.py
from __future__ import annotations
import sys
from typing import Callable
import customtkinter as ctk
from tkinter import filedialog

try:
    import windnd
except Exception:
    windnd = None

from services.worker import run_io
from services.exclusive import run_exclusive

class DropZone(ctk.CTkFrame):
    def __init__(self, parent, on_files: Callable[[list[str]], None],
                 text: str = "Glissez vos PDF ici\nou cliquez pour parcourir",
                 height: int = 140, **kwargs):
        super().__init__(parent, height=height, corner_radius=12, **kwargs)
        self.on_files = on_files
        self._label = ctk.CTkLabel(self, text=text, justify="center")
        self._label.pack(expand=True, fill="both", padx=12, pady=12)
        self._setup_dnd()

    def _setup_dnd(self):
        if sys.platform.startswith("win") and windnd is not None:
            windnd.hook_dropfiles(self, func=self._on_drop_async, force_unicode=True)
        else:
            msg = self._label.cget("text")
            self._label.configure(text=msg + "\n(DnD indisponible, clic pour importer)")
            self.bind("<Button-1>", lambda _e: self._open_dialog_async())
            self._label.bind("<Button-1>", lambda _e: self._open_dialog_async())

    def _on_drop_async(self, paths):
        files = [p for p in paths if isinstance(p, str)]
        if files:
            run_io(run_exclusive, "dropzone.pdf", self.on_files, files)

    def _open_dialog_async(self):
        paths = filedialog.askopenfilenames(
            title="Sélectionner des fichiers",
            filetypes=[("PDF", "*.pdf"), ("Tous les fichiers", "*.*")],
        )
        if paths:
            run_io(run_exclusive, "dropzone.pick", self.on_files, list(paths))
