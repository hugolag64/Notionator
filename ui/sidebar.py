# ui/sidebar.py
import customtkinter as ctk
from PIL import Image, ImageTk
from ui.styles import COLORS, LOGO_SIZE, SIDEBAR_WIDTH
import os

class Sidebar(ctk.CTkFrame):
    def __init__(self, master, switch_frame_callback):
        super().__init__(master, width=SIDEBAR_WIDTH, fg_color=COLORS["bg_dark"])
        self.pack_propagate(False)

        # Charger logo
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "..", "assets", "logo.png")
        img = Image.open(logo_path).resize(LOGO_SIZE)
        self.logo_img = ImageTk.PhotoImage(img)

        # Afficher logo
        self.logo_label = ctk.CTkLabel(self, image=self.logo_img, text="")
        self.logo_label.pack(pady=(20, 10))

        # Titre
        title = ctk.CTkLabel(self, text="Notionator", font=("Helvetica", 18, "bold"), text_color=COLORS["text_light"])
        title.pack(pady=(0, 30))

        # Bouton Accueil
        self.btn_accueil = ctk.CTkButton(
            self, text="Accueil", fg_color="transparent",
            text_color=COLORS["text_light"], hover_color=COLORS["accent"],
            command=lambda: switch_frame_callback("accueil")
        )
        self.btn_accueil.pack(pady=10, fill="x", padx=10)

        # Bouton Semestres (toggle)
        self.semestres_expanded = False
        self.btn_semestres = ctk.CTkButton(
            self, text="Semestres ▼", fg_color="transparent",
            text_color=COLORS["text_light"], hover_color=COLORS["accent"],
            command=self.toggle_semestres
        )
        self.btn_semestres.pack(pady=10, fill="x", padx=10)

        # Frame contenant les boutons Semestre 1 à 12 (masqué par défaut)
        self.semestres_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"])
        self.semestre_buttons = []
        for i in range(1, 13):
            btn = ctk.CTkButton(
                self.semestres_frame,
                text=f"Semestre {i}",
                fg_color="transparent",
                text_color=COLORS["text_light"],
                hover_color=COLORS["accent"],
                command=lambda i=i: switch_frame_callback(f"semestre_{i}")
            )
            btn.pack(pady=2, fill="x", padx=20)
            self.semestre_buttons.append(btn)

        # Bouton Collèges
        self.btn_colleges = ctk.CTkButton(
            self, text="Collèges", fg_color="transparent",
            text_color=COLORS["text_light"], hover_color=COLORS["accent"],
            command=lambda: switch_frame_callback("colleges")
        )
        self.btn_colleges.pack(pady=10, fill="x", padx=10)

        # Bouton Recharger Notion
        self.btn_reload = ctk.CTkButton(
            self, text="Recharger Notion", fg_color=COLORS["accent"],
            text_color=COLORS["text_light"],
            command=self.reload_notion
        )
        self.btn_reload.pack(pady=30, fill="x", padx=10)

    def toggle_semestres(self):
        """Affiche ou cache la liste des semestres."""
        if self.semestres_expanded:
            self.semestres_frame.pack_forget()
            self.btn_semestres.configure(text="Semestres ▼")
        else:
            self.semestres_frame.pack(fill="x", padx=10, after=self.btn_semestres)
            self.btn_semestres.configure(text="Semestres ▲")
        self.semestres_expanded = not self.semestres_expanded

    def reload_notion(self):
        # Placeholder pour action future
        print("Rechargement des données Notion…")
