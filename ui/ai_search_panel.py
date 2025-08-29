# ui/ai_search_panel.py
from __future__ import annotations
import customtkinter as ctk
from tkinter import messagebox
from typing import Optional, Dict, Any, List
from services import local_search as rag
from ui.styles import COLORS  # suppose que tu as déjà COLORS

class AISearchPanel(ctk.CTkFrame):
    """
    Barre de recherche IA locale (RAG) + rendu réponse + boutons sources cliquables.
    Apple-like: sobre, coin arrondi, espace, texte lisible.
    """
    def __init__(self, parent, placeholder: str = "Recherche via ChatGPT Local", **kwargs):
        super().__init__(parent, fg_color=COLORS.get("card_bg", "#111214"), corner_radius=16, **kwargs)

        # Ligne recherche
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder,
                                  height=44, corner_radius=12,
                                  fg_color=COLORS.get("input_bg", "#0C0D0E"),
                                  text_color=COLORS.get("text", "#EDEDED"),
                                  border_color=COLORS.get("border", "#2A2B2E"),
                                  border_width=1)
        self.entry.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        self.search_btn = ctk.CTkButton(self, text="Rechercher",
                                        height=44, corner_radius=12,
                                        fg_color=COLORS.get("accent", "#3A86FF"),
                                        command=self._on_submit)
        self.search_btn.grid(row=0, column=1, padx=(8,14), pady=(14,8))

        # Zone réponse
        self.answer_box = ctk.CTkTextbox(self, height=180, corner_radius=12,
                                         fg_color=COLORS.get("bg_light", "#0B0C0D"),
                                         text_color=COLORS.get("text", "#EDEDED"),
                                         wrap="word")
        self.answer_box.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=8)

        # Bandeau sources
        self.sources_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.sources_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0,14))

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # Bind Enter
        self.entry.bind("<Return>", lambda e: self._on_submit())

    def _on_submit(self):
        from ui.ai_dialog import AIAnswerDialog
        import threading

        q = self.entry.get().strip()
        if not q:
            return

        # UI lock
        self._set_loading(True)

        # 1) Ouvre la popup (non bloquante) avec loader
        toplevel = self.winfo_toplevel()
        dlg = AIAnswerDialog.open(
            parent=toplevel,
            title="ChatGPT Local",
            typing=False,  # on gère le stream nous-mêmes
            sources=[]  # on injectera les sources après
        )
        dlg.start_loader("Je réfléchis à ta question")

        # 2) Thread worker pour ne pas bloquer l'UI
        def worker():
            try:
                first_chunk = True
                # a) Streaming de la réponse
                for chunk in rag.stream(q):
                    if chunk:
                        if first_chunk:
                            first_chunk = False
                            # stop le loader dès le 1er token réel
                            dlg.stop_loader()
                        # append côté UI (thread-safe via after)
                        dlg.after(0, dlg.append, chunk)

                # b) En fin de stream : appliquer markdown si dispo
                try:
                    dlg.after(0, getattr(dlg.text, "reparse_from_buffer", lambda: None))
                except Exception:
                    pass

                # c) Récupérer les sources (one-shot) et les afficher
                try:
                    res = rag.ask_with_sources(q)
                    sources = res.get("sources", [])
                    dlg.after(0, dlg.set_sources, sources)
                except Exception:
                    pass

            except Exception as e:
                # Affiche l'erreur dans la popup
                dlg.after(0, dlg.append, f"\n\n[Erreur: {e!r}]")
            finally:
                # réactive la barre de recherche
                self.after(0, self._set_loading, False)

        threading.Thread(target=worker, daemon=True).start()

    def _render_answer(self, result: Dict[str, Any]):
        self.answer_box.configure(state="normal")
        self.answer_box.delete("1.0", "end")
        self.answer_box.insert("end", result.get("answer", "").strip() or "Aucune réponse.")
        self.answer_box.configure(state="disabled")

        # Boutons sources
        for w in self.sources_frame.winfo_children():
            w.destroy()
        sources: List[Dict[str, Any]] = result.get("sources", [])
        if sources:
            title = ctk.CTkLabel(self.sources_frame, text="Sources",
                                 text_color=COLORS.get("muted", "#A9ABB0"))
            title.pack(anchor="w", pady=(6, 4))
            for i, s in enumerate(sources, start=1):
                label = f"{i}. {s['title']} — p.{s['page']}"
                btn = ctk.CTkButton(self.sources_frame, text=label,
                                    height=36, corner_radius=10,
                                    fg_color=COLORS.get("chip_bg", "#1A1B1E"),
                                    hover_color=COLORS.get("chip_hover", "#232528"),
                                    text_color=COLORS.get("text", "#EDEDED"),
                                    command=lambda src=s: rag.open_source(src))
                btn.pack(fill="x", pady=4)

    def _set_loading(self, state: bool):
        self.search_btn.configure(state=("disabled" if state else "normal"),
                                  text=("… " if state else "Rechercher"))
        self.entry.configure(state=("disabled" if state else "normal"))
