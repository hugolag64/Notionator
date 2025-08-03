import customtkinter as ctk
from customtkinter import CTkImage
from PIL import Image
from ui.styles import COLORS, LOGO_SIZE, SIDEBAR_WIDTH
import os

class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, switch_frame, reload_callback):  # <-- Ajoute reload_callback
        super().__init__(parent, width=SIDEBAR_WIDTH, fg_color=COLORS["bg_sidebar"])

        self.pack_propagate(False)
        self.grid_propagate(False)

        self.switch_frame = switch_frame
        self.reload_callback = reload_callback  # <-- Stocke le callback

        # Charger logo
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "..", "assets", "logo.png")
        logo_image = Image.open(logo_path)
        self.logo_img = CTkImage(light_image=logo_image, dark_image=logo_image, size=LOGO_SIZE)

        # Afficher logo
        self.logo_label = ctk.CTkLabel(self, image=self.logo_img, text="")
        self.logo_label.pack(pady=(20, 10))

        # Titre
        title = ctk.CTkLabel(
            self,
            text="Notionator",
            font=("Helvetica", 18, "bold"),
            text_color=COLORS["text_sidebar"]
        )
        title.pack(pady=(0, 20))

        # -------- Barre de recherche moderne --------
        search_frame = ctk.CTkFrame(
            self,
            fg_color="transparent"
        )
        search_frame.pack(pady=(0, 20), padx=10, fill="x")

        # Icône loupe
        search_icon = ctk.CTkLabel(
            search_frame,
            text="🔍",
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"],
            width=20
        )
        search_icon.grid(row=0, column=0, padx=(5, 0))

        # Champ recherche
        search_entry = ctk.CTkEntry(
            search_frame,
            width=SIDEBAR_WIDTH - 60,
            height=35,
            corner_radius=12,
            border_width=1,
            border_color="#CCCCCC",
            placeholder_text="Rechercher cours...",
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_sidebar"],
            placeholder_text_color=COLORS["text_secondary"]
        )
        search_entry.grid(row=0, column=1, padx=(5, 5))

        # Changement couleur au focus
        def focus_in(_=None):
            search_entry.configure(border_color=COLORS["accent"])

        def focus_out(_=None):
            search_entry.configure(border_color="#CCCCCC")

        search_entry.bind("<FocusIn>", focus_in)
        search_entry.bind("<FocusOut>", focus_out)

        # Lancer la recherche en live
        def search_courses(event=None):
            query = search_entry.get().lower()
            courses = ["Anatomie", "Physiologie", "Infectiologie", "Pharmacologie", "Immunologie"]
            results = [c for c in courses if query in c.lower()]
            print(f"[Recherche Sidebar] '{query}' → {results}")

        search_entry.bind("<KeyRelease>", search_courses)

        # ---- Police pour boutons principaux et sous-boutons ----
        font_main = ("Helvetica", 16)
        font_sub = ("Helvetica", 14)

        # Bouton Accueil
        self.btn_accueil = ctk.CTkButton(
            self,
            text="Accueil",
            font=font_main,
            fg_color="transparent",
            text_color=COLORS["text_sidebar"],
            hover_color=COLORS["bg_card_hover"],
            command=lambda: self.switch_frame("accueil")
        )
        self.btn_accueil.pack(pady=10, fill="x", padx=10)

        # Bouton Semestres (toggle)
        self.semestres_expanded = False
        self.btn_semestres = ctk.CTkButton(
            self,
            text="Semestres ▼",
            font=font_main,
            fg_color="transparent",
            text_color=COLORS["text_sidebar"],
            hover_color=COLORS["bg_card_hover"],
            command=self.toggle_semestres
        )
        self.btn_semestres.pack(pady=10, fill="x", padx=10)

        # Frame contenant les boutons Semestre 1 à 12 (masqué par défaut)
        self.semestres_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_sidebar"])
        self.semestre_buttons = []
        for i in range(1, 13):
            btn = ctk.CTkButton(
                self.semestres_frame,
                text=f"Semestre {i}",
                font=font_sub,
                fg_color="transparent",
                text_color=COLORS["text_sidebar"],
                hover_color=COLORS["bg_card_hover"],
                command=lambda i=i: self.select_semestre(i)
            )
            btn.pack(pady=2, fill="x", padx=20)
            self.semestre_buttons.append(btn)

        # Bouton "Tous les semestres" ajouté dans le dépliage
        btn_all_semestres = ctk.CTkButton(
            self.semestres_frame,
            text="Tous les semestres",
            font=font_sub,
            fg_color="transparent",
            text_color=COLORS["text_sidebar"],
            hover_color=COLORS["bg_card_hover"],
            command=self.select_tous_les_semestres
        )
        btn_all_semestres.pack(pady=(5, 2), fill="x", padx=20)

        # Bouton Collèges
        self.btn_colleges = ctk.CTkButton(
            self,
            text="Collèges",
            font=font_main,
            fg_color="transparent",
            text_color=COLORS["text_sidebar"],
            hover_color=COLORS["bg_card_hover"],
            command=lambda: self.switch_frame("colleges")
        )
        self.btn_colleges.pack(pady=10, fill="x", padx=10)

        # Bouton Recharger Notion
        self.btn_reload = ctk.CTkButton(
            self,
            text="Recharger Notion",
            fg_color=COLORS["accent"],
            text_color=COLORS["text_light"],
            command=self.reload_callback   # <-- Utilise le callback passé
        )
        self.btn_reload.pack(pady=30, fill="x", padx=10)

        # Bouton filtre Actions
        self.filter_btn = ctk.CTkButton(
            self,
            text="Actions à faire",
            width=160,
            height=36,
            fg_color="#BFBFBF",
            text_color="white",
            corner_radius=10,
            command=self.toggle_action_filter
        )
        self.filter_btn.pack(pady=(10, 20))

    # ----------------- Fonctions -----------------
    def toggle_semestres(self):
        """Affiche ou cache la liste des semestres sans modifier la largeur."""
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
        else:
            self.semestres_frame.pack(fill="x", padx=10, after=self.btn_semestres)
            self.btn_semestres.configure(text="Semestres ▲")
        self.semestres_expanded = not self.semestres_expanded

    #Bouton filtrer
    def toggle_action_filter(self):
        # Inverse l'état global
        self.master.show_only_actions = not self.master.show_only_actions

        # Met à jour la couleur et le texte
        if self.master.show_only_actions:
            self.filter_btn.configure(text="Voir tout", fg_color=COLORS["accent"])
        else:
            self.filter_btn.configure(text="Actions à faire", fg_color="#BFBFBF")

        # Recharge la vue en cours
        current = getattr(self.master, "current_screen", None)
        if current and (current.startswith("semestre_") or current == "colleges" or current == "tous_les_semestres"):
            self.master.switch_frame(current)

    #Replie semestre
    def select_semestre(self, i):
        """Sélectionne un semestre et replie la liste."""
        self.switch_frame(f"semestre_{i}")
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
            self.semestres_expanded = False

    def select_tous_les_semestres(self):
        """Affiche tous les cours de tous les semestres."""
        self.switch_frame("tous_les_semestres")
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
            self.semestres_expanded = False
