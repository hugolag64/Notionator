# ui/sidebar.py
import os
import customtkinter as ctk
from customtkinter import CTkImage
from PIL import Image
from ui.styles import COLORS, LOGO_SIZE, SIDEBAR_WIDTH


class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, switch_frame, reload_callback, rescan_pdfs_callback=None):
        super().__init__(parent, width=SIDEBAR_WIDTH, fg_color=COLORS["bg_sidebar"])

        self.pack_propagate(False)
        self.grid_propagate(False)

        self.switch_frame = switch_frame
        self.reload_callback = reload_callback
        self.rescan_pdfs_callback = rescan_pdfs_callback

        # ---------- Logo ----------
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "..", "assets", "logo.png")
        self.logo_img = None
        if os.path.exists(logo_path):
            try:
                logo_image = Image.open(logo_path)
                self.logo_img = CTkImage(light_image=logo_image, dark_image=logo_image, size=LOGO_SIZE)
            except Exception:
                self.logo_img = None

        self.logo_label = ctk.CTkLabel(self, image=self.logo_img, text="")
        self.logo_label.pack(pady=(20, 10))

        # ---------- Titre ----------
        title = ctk.CTkLabel(
            self, text="Notionator", font=("Helvetica", 18, "bold"),
            text_color=COLORS["text_sidebar"]
        )
        title.pack(pady=(0, 18))

        # ---------- Barre de recherche ----------
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(pady=(0, 16), padx=10, fill="x")

        search_icon = ctk.CTkLabel(
            search_frame, text="🔍", font=("Helvetica", 14),
            text_color=COLORS["text_secondary"], width=20
        )
        search_icon.grid(row=0, column=0, padx=(5, 0))

        self.search_entry = ctk.CTkEntry(
            search_frame, width=SIDEBAR_WIDTH - 60, height=35,
            corner_radius=12, border_width=1,
            border_color=COLORS.get("bg_card_hover", "#CBD5E1"),
            placeholder_text="Rechercher cours...",
            fg_color=COLORS["bg_card"], text_color=COLORS["text_sidebar"],
            placeholder_text_color=COLORS["text_secondary"]
        )
        self.search_entry.grid(row=0, column=1, padx=(5, 5))

        def focus_in(_=None): self.search_entry.configure(border_color=COLORS["accent"])
        def focus_out(_=None): self.search_entry.configure(border_color=COLORS.get("bg_card_hover", "#CBD5E1"))
        self.search_entry.bind("<FocusIn>", focus_in)
        self.search_entry.bind("<FocusOut>", focus_out)

        # --- Débounce & routing vers SearchResultsView ---
        self._search_after_id = None
        self._last_sent_query = ""

        def _do_search():
            q = self.search_entry.get().strip()
            if q and q != self._last_sent_query:
                self.switch_frame(f"search:{q}")
                self._last_sent_query = q
            elif not q:
                if hasattr(self.master, "reset_to_previous"):
                    self.master.reset_to_previous()

        def on_key_release(event=None):
            if event and event.keysym == "Escape":
                self.search_entry.delete(0, "end")
                self._last_sent_query = ""
                if hasattr(self.master, "reset_to_previous"):
                    self.master.reset_to_previous()
                return

            if self._search_after_id:
                try:
                    self.after_cancel(self._search_after_id)
                except Exception:
                    pass
            self._search_after_id = self.after(180, _do_search)

        self.search_entry.bind("<KeyRelease>", on_key_release)

        # ---------- Helpers boutons (uniformisation style clair + contour bleu) ----------
        def primary_btn(parent, text, command):
            return ctk.CTkButton(
                parent,
                text=text,
                height=36,
                corner_radius=10,
                fg_color=COLORS["bg_card"],  # fond clair
                hover_color=COLORS["bg_card_hover"],  # hover léger
                border_width=1,
                border_color="#D1D5DB",  # ← contour gris clair
                text_color=COLORS["text_sidebar"],  # ← texte gris foncé
                command=command
            )

        def nav_btn(parent, text, command):
            return ctk.CTkButton(
                parent, text=text, font=("Helvetica", 16),
                fg_color="transparent", text_color=COLORS["text_sidebar"],
                hover_color=COLORS["bg_card_hover"],
                command=command
            )

        def sub_nav_btn(parent, text, command):
            return ctk.CTkButton(
                parent, text=text, font=("Helvetica", 14),
                fg_color="transparent", text_color=COLORS["text_sidebar"],
                hover_color=COLORS["bg_card_hover"],
                command=command
            )

        # ---------- Boutons navigation ----------
        self.btn_accueil = nav_btn(self, "Accueil", lambda: self.switch_frame("accueil"))
        self.btn_accueil.pack(pady=6, fill="x", padx=10)

        self.semestres_expanded = False
        self.btn_semestres = nav_btn(self, "Semestres ▼", self.toggle_semestres)
        self.btn_semestres.pack(pady=6, fill="x", padx=10)

        self.semestres_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_sidebar"])
        self.semestre_buttons = []
        for i in range(1, 12 + 1):
            btn = sub_nav_btn(self.semestres_frame, f"Semestre {i}", lambda i=i: self.select_semestre(i))
            btn.pack(pady=2, fill="x", padx=20)
            self.semestre_buttons.append(btn)

        btn_all_semestres = sub_nav_btn(self.semestres_frame, "Tous les semestres", self.select_tous_les_semestres)
        btn_all_semestres.pack(pady=(5, 2), fill="x", padx=20)

        self.btn_colleges = nav_btn(self, "Collèges", lambda: self.switch_frame("colleges"))
        self.btn_colleges.pack(pady=6, fill="x", padx=10)

        # ---------- Actions (style uniforme clair + contour bleu) ----------
        self.btn_reload = primary_btn(self, "Recharger Notion", self.reload_callback)
        self.btn_reload.pack(pady=(22, 10), fill="x", padx=10)

        if self.rescan_pdfs_callback:
            self.btn_rescan = primary_btn(self, "Scanner les PDF", self.rescan_pdfs_callback)
            self.btn_rescan.pack(pady=(0, 14), fill="x", padx=10)

        self.filter_btn = primary_btn(self, "Actions à faire", self.toggle_action_filter)
        self.filter_btn.pack(pady=(2, 20), padx=10, fill="x")
        self._sync_filter_btn()  # Sync initial

        # ---------- Loader discret ----------
        self.loader_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.loader_label = ctk.CTkLabel(
            self.loader_frame, text="Synchronisation…",
            text_color=COLORS["text_secondary"]
        )
        self.loader_bar = ctk.CTkProgressBar(self.loader_frame, mode="indeterminate")
        self.loader_label.pack(padx=10, pady=(0, 4))
        self.loader_bar.pack(fill="x", padx=10, pady=(0, 10))
        self.loader_visible = False  # caché au départ

        # ---------- Spacer pour pousser le bouton Paramètres en bas ----------
        self._bottom_spacer = ctk.CTkFrame(self, fg_color="transparent")
        self._bottom_spacer.pack(expand=True, fill="both")

        # ---------- Bouton Paramètres (secondaire, en bas) ----------
        self.btn_settings = ctk.CTkButton(
            self, text="Paramètres  ⚙️",
            fg_color=COLORS.get("bg_card", "#EFEFEF"),
            hover_color=COLORS.get("bg_card_hover", "#E5E5E5"),
            text_color=COLORS.get("text_sidebar", "#111111"),
            command=lambda: self.switch_frame("settings"),
            corner_radius=10,
            height=36
        )
        self.btn_settings.pack(side="bottom", pady=12, fill="x", padx=10)

    # ---------- Helpers ----------
    def _sync_filter_btn(self):
        """Synchronise le texte du bouton filtre selon l'état global."""
        if getattr(self.master, "show_only_actions", False):
            self.filter_btn.configure(text="Voir tout")
        else:
            self.filter_btn.configure(text="Actions à faire")

    # ---------- Loader API ----------
    def show_loader(self):
        if self.loader_visible:
            return
        self.btn_reload.configure(state="disabled")
        if hasattr(self, "btn_rescan"):
            self.btn_rescan.configure(state="disabled")
        self.loader_frame.pack(fill="x", padx=10, after=self.btn_reload)
        self.loader_bar.start()
        self.loader_visible = True

    def hide_loader(self):
        if not self.loader_visible:
            return
        self.loader_bar.stop()
        self.loader_frame.pack_forget()
        self.btn_reload.configure(state="normal")
        if hasattr(self, "btn_rescan"):
            self.btn_rescan.configure(state="normal")
        self.loader_visible = False

    # ---------- Logique ----------
    def toggle_semestres(self):
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
        else:
            self.semestres_frame.pack(fill="x", padx=10, after=self.btn_semestres)
            self.btn_semestres.configure(text="Semestres ▲")
        self.semestres_expanded = not self.semestres_expanded

    def toggle_action_filter(self):
        self.master.show_only_actions = not self.master.show_only_actions
        self._sync_filter_btn()
        current = getattr(self.master, "current_screen", None)
        if current and (current.startswith("semestre_") or current in ("colleges", "tous_les_semestres")):
            self.master.switch_frame(current)

    def select_semestre(self, i):
        self.switch_frame(f"semestre_{i}")
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
            self.semestres_expanded = False

    def select_tous_les_semestres(self):
        self.switch_frame("tous_les_semestres")
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
            self.semestres_expanded = False
