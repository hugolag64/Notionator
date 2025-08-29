# ui/pdf_selector.py
from __future__ import annotations
import customtkinter as ctk
from threading import Thread
from pathlib import Path
from urllib.parse import urlparse
from tkinter import messagebox
from ui.dropzone import DropZone  # nécessite ui/dropzone.py (fourni précédemment)

LIGHT_BG = "#F5F6F7"


class PDFSelector(ctk.CTkToplevel):
    @staticmethod
    def open(parent, **kwargs):
        dlg = PDFSelector(parent, **kwargs)
        parent.wait_window(dlg)
        return dlg.result_url

    def __init__(
        self,
        parent,
        search_callback,
        initial_query: str | None = None,
        best_matches: list | None = None,
        show_search: bool = True,
        folder_hint: str | None = None,
    ):
        super().__init__(parent)
        self.result_url: str | None = None
        self._search_cb = search_callback
        self._items: list[dict] = []
        self._selected_index: int | None = None
        self._hover_index: int | None = None

        # Modale
        self.transient(parent)
        self.grab_set()
        self.title("Lier un PDF")
        self.configure(fg_color=(LIGHT_BG, "#0f0f10"))
        self.geometry("1040x720")
        self.minsize(980, 620)
        self.resizable(True, True)
        self._center_on_parent(parent)

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # Titre
        ctk.CTkLabel(
            self,
            text="Lier un PDF",
            anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("gray15", "gray85"),
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        # Hint dossier
        if folder_hint:
            ctk.CTkLabel(
                self,
                text=f"Dossier ciblé : {folder_hint}",
                anchor="w",
                font=ctk.CTkFont(size=12),
                text_color=("gray30", "gray70"),
            ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        # Barre recherche
        self._search_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="transparent")
        self._search_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._search_frame.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(self._search_frame, placeholder_text="Rechercher", height=40)
        self._entry.grid(row=0, column=0, sticky="ew", padx=(6, 6), pady=6)
        self._entry.bind("<Return>", lambda _e: self._do_search())

        self._search_btn = ctk.CTkButton(self._search_frame, text="Rechercher", width=140, command=self._do_search)
        self._search_btn.grid(row=0, column=1, padx=(0, 6), pady=6)

        self._spinner = ctk.CTkLabel(self._search_frame, text="", width=60, anchor="w")
        self._spinner.grid(row=0, column=2, padx=(0, 6), pady=6)

        if not show_search:
            self._search_frame.grid_remove()

        # Dropzone (DnD Windows via windnd, sinon clic fichier)
        self._drop = DropZone(
            self,
            on_files=self._on_drop_files,
            text="Glissez votre PDF ici\nou cliquez pour parcourir",
            height=120,
        )
        self._drop.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))

        # Liste résultats
        self._list = ctk.CTkScrollableFrame(self, corner_radius=12, fg_color=(LIGHT_BG, "#101113"))
        self._list.grid(row=5, column=0, sticky="nsew", padx=12, pady=(6, 8))
        self._list.grid_columnconfigure(0, weight=1)

        # Barre boutons
        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))
        btn_bar.grid_columnconfigure((0, 1, 2), weight=1)

        self._ok = ctk.CTkButton(btn_bar, text="OK", command=self._confirm, state="disabled")
        self._ok.grid(row=0, column=1, padx=8)

        ctk.CTkButton(
            btn_bar,
            text="Annuler",
            fg_color=("gray85", "gray20"),
            hover_color=("gray78", "gray25"),
            text_color=("gray20", "white"),
            command=self._cancel,
        ).grid(row=0, column=2, padx=8, sticky="e")

        # Bindings
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._confirm() if self.focus_get() is not self._entry else None)
        self.bind("<Control-v>", self._paste_path)
        self.bind("<Command-v>", self._paste_path)  # mac mapping si besoin

        # Données initiales
        if initial_query:
            self._entry.insert(0, initial_query)
        if best_matches:
            self._set_items(self._normalize(best_matches))

        self.lift()
        self.focus_force()

    # ---------- Helpers
    def _center_on_parent(self, parent):
        try:
            self.update_idletasks()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 4}")

    def _set_busy(self, busy: bool):
        if busy:
            self._entry.configure(state="disabled")
            self._search_btn.configure(state="disabled", text="Recherche…")
            self.configure(cursor="watch")
            self._spinner.configure(text="⏳")
        else:
            self._entry.configure(state="normal")
            self._search_btn.configure(state="normal", text="Rechercher")
            self.configure(cursor="")
            self._spinner.configure(text="")

    def _restyle_rows(self):
        for i, child in enumerate(self._list.winfo_children()):
            if i == self._selected_index:
                color = ("#DCE7FF", "#212229")
            elif i == self._hover_index:
                color = ("#EEF0F2", "#1b1c20")
            else:
                color = ("white", "#16171a")
            child.configure(fg_color=color)

    def _handle_leave(self, idx: int):
        rows = self._list.winfo_children()
        if idx >= len(rows):
            return
        row = rows[idx]
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        widget = row.winfo_containing(x, y)
        inside = widget is not None and (widget == row or str(widget).startswith(str(row)))
        if not inside and self._hover_index == idx:
            self._hover_index = None
            self._restyle_rows()

    def _clear_list(self):
        for child in self._list.winfo_children():
            child.destroy()

    def _row(self, idx: int, name: str, path: str, url: str):
        row = ctk.CTkFrame(self._list, corner_radius=10, fg_color=("white", "#16171a"))
        row.grid(row=idx, column=0, sticky="ew", padx=6, pady=4)
        row.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            row,
            text=name,
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("black", "white"),
        )
        subtitle = ctk.CTkLabel(
            row,
            text=(path or "(chemin indisponible)"),
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=("gray35", "gray70"),
        )

        title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        subtitle.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        def select(_e=None):
            self._select_index(idx)

        for w in (row, title, subtitle):
            w.bind("<Button-1>", select)

        def on_enter(_e=None):
            self._hover_index = idx
            self._restyle_rows()

        def on_leave(_e=None):
            self.after(15, self._handle_leave, idx)

        for w in (row, title, subtitle):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        row.bind("<Double-Button-1>", lambda _e: (select(), self._confirm()))

    def _select_index(self, idx: int):
        self._selected_index = idx
        self._ok.configure(state="normal")
        self._restyle_rows()

    # ---------- Folder helpers
    def _display_folder_from_path(self, p: Path) -> str:
        parts = p.parts
        if len(parts) >= 3:
            return " / ".join(parts[-3:-1])
        return str(p.parent)

    def _folder_from_local_string(self, s: str) -> str:
        if not s:
            return ""
        try:
            return self._display_folder_from_path(Path(s))
        except Exception:
            return ""

    def _canon_folder_from_any(self, v) -> str:
        if not v:
            return ""
        if isinstance(v, (list, tuple)):
            v = "/".join(map(str, v))
        s = str(v).replace("\\", "/")
        parts = [p for p in s.split("/") if p]
        return " / ".join(parts[-2:]) if len(parts) >= 2 else s

    # ---------- Normalisation
    def _normalize(self, items: list):
        norm = []
        for it in items:
            if isinstance(it, str):
                url = it
                name = (
                    Path(it).name
                    if not it.startswith(("http://", "https://", "file://"))
                    else (urlparse(it).path.split("/")[-1] or "PDF")
                )
                path = self._folder_from_local_string(it)

            elif isinstance(it, (list, tuple)) and len(it) >= 3:
                name, path, url = it[0], it[1], it[2]
                path = path or self._folder_from_local_string(url)

            elif isinstance(it, dict):
                # PATCH: accepter aussi les clés typiques de Google Drive
                url = (
                    it.get("url")
                    or it.get("href")
                    or it.get("path")
                    or it.get("webViewLink")     # lien de visualisation Drive
                    or it.get("webContentLink")  # lien de téléchargement Drive
                    or it.get("alternateLink")   # anciens liens Drive
                    or it.get("link")            # clé générique
                )
                name = it.get("name") or it.get("title")
                if not name and url:
                    # si URL HTTP(S)/file, récupérer le dernier segment pour un nom plausible
                    if isinstance(url, str):
                        name = (
                            Path(url).name
                            if not url.startswith(("http://", "https://", "file://"))
                            else (urlparse(url).path.split("/")[-1] or "PDF")
                        )
                if not name:
                    name = "PDF"

                path = (
                    it.get("folder")
                    or it.get("folder_display")
                    or it.get("path_display")
                    or it.get("parent")
                    or it.get("directory")
                    or ""
                )
                if not path:
                    parents = it.get("parents")
                    if isinstance(parents, (list, tuple)) and parents:
                        path = " / ".join(map(str, parents[-2:])) if len(parents) >= 2 else str(parents[-1])
                if not path:
                    local_hint = it.get("path") or (
                        url if url and isinstance(url, str) and not url.startswith(("http://", "https://", "file://")) else ""
                    )
                    path = self._folder_from_local_string(str(local_hint) if local_hint else "")
                path = self._canon_folder_from_any(path)

            else:
                continue

            # Éviter 'None' comme chaîne si pas d'URL
            url_str = str(url) if url is not None else ""
            norm.append({"name": str(name), "path": str(path), "url": url_str})
        return norm

    def _set_items(self, items: list[dict]):
        self._items = items
        self._selected_index = None
        self._hover_index = None
        self._ok.configure(state="disabled")
        self._clear_list()
        for i, it in enumerate(items):
            self._row(i, it["name"], it["path"], it["url"])
        self._restyle_rows()

    # ---------- Actions
    def _do_search(self):
        query = self._entry.get().strip()
        self._set_busy(True)

        def worker():
            try:
                results = self._search_cb(query) if self._search_cb else []
            except Exception:
                results = []
            norm = self._normalize(results)
            self.after(0, lambda: (self._set_items(norm), self._set_busy(False)))

        Thread(target=worker, daemon=True).start()

    def _on_drop_files(self, files: list[str]) -> None:
        pdfs = [p for p in files if isinstance(p, str) and p.lower().endswith(".pdf")]
        if not pdfs:
            messagebox.showinfo("Format non supporté", "Sélectionne un fichier PDF.")
            return
        # Choix direct: on renvoie le premier PDF déposé
        self.result_url = pdfs[0]
        self.destroy()

    def _paste_path(self, _e=None):
        try:
            import pyperclip
            raw = pyperclip.paste().strip()
        except Exception:
            raw = ""
        if not raw:
            return
        if raw.lower().endswith(".pdf") or raw.startswith(("http://", "https://", "file://")):
            self.result_url = raw
            self.destroy()

    def _confirm(self):
        if self._selected_index is None:
            return
        self.result_url = self._items[self._selected_index]["url"]
        self.destroy()

    def _cancel(self):
        self.result_url = None
        self.destroy()
