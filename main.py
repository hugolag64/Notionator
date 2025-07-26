import customtkinter as ctk
from ui.sidebar import Sidebar
from ui.styles import COLORS


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Fenêtre principale
        self.title("Notionator")
        self.geometry("1000x600")
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        # Layout : 2 colonnes (sidebar + contenu)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = Sidebar(self, self.switch_frame)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        # Zone contenu
        self.content_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
        self.content_frame.grid(row=0, column=1, sticky="nsew")

        # Écran par défaut
        self.show_accueil()

    # ------------------- Navigation -------------------
    def switch_frame(self, screen):
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if screen == "accueil":
            self.show_accueil()
        elif screen.startswith("semestre_"):
            num = screen.split("_")[1]
            self.show_semestre(num)
        elif screen == "colleges":
            self.show_colleges()

    # ------------------- ACCUEIL -------------------
    def show_accueil(self):
        # Nettoyer l'écran
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        # Titre
        title = ctk.CTkLabel(
            self.content_frame,
            text="Accueil",
            font=("Helvetica", 32, "bold"),
            text_color=COLORS["accent"]
        )
        title.pack(pady=30)

        # Conteneur cartes
        cards_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        cards_frame.pack(pady=20)

        # Fonction création carte
        def create_card(parent, title, value):
            normal_width = 280
            normal_height = 160

            card = ctk.CTkFrame(
                parent,
                width=normal_width,
                height=normal_height,
                corner_radius=20,
                fg_color=COLORS["bg_card"],
                border_width=1,
                border_color="#D0D0D0"  # contour gris discret
            )
            card.pack_propagate(False)

            # Titre
            ctk.CTkLabel(
                card,
                text=title,
                font=("Helvetica", 18, "bold"),
                text_color=COLORS["text_primary"]
            ).pack(pady=(15, 5))

            # Sous-texte
            ctk.CTkLabel(
                card,
                text=value,
                font=("Helvetica", 14),
                text_color=COLORS["text_secondary"]
            ).pack()

            # Hover : contour bleu + fond hover
            def on_enter(event):
                card.configure(fg_color=COLORS["bg_card_hover"], border_color=COLORS["accent"])

            def on_leave(event):
                card.configure(fg_color=COLORS["bg_card"], border_color="#D0D0D0")

            card.bind("<Enter>", on_enter)
            card.bind("<Leave>", on_leave)

            return card

        # Trois cartes
        card1 = create_card(cards_frame, "Tâches Notion", "5 tâches à faire")
        card1.grid(row=0, column=0, padx=20)

        card2 = create_card(cards_frame, "Google Drive", "3 fichiers liés")
        card2.grid(row=0, column=1, padx=20)

        card3 = create_card(cards_frame, "Google Calendar", "Réviser : Anatomie")
        card3.grid(row=0, column=2, padx=20)

        # --- BARRE DE RECHERCHE ---
        search_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        search_frame.pack(pady=40)

        self.search_var = ctk.StringVar()

        search_entry = ctk.CTkEntry(
            search_frame,
            width=500,
            height=45,
            corner_radius=25,
            border_width=2,
            border_color="#DDDDDD",  # gris par défaut
            textvariable=self.search_var,
            placeholder_text="Rechercher dans Notion ou poser une question...",
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_secondary"]
        )
        search_entry.grid(row=0, column=0, padx=(0, 10))

        # ------------------- Gestion focus -------------------
        def set_focus_blue(event=None):
            search_entry.configure(border_color=COLORS["accent"])

        def set_focus_gray(event=None):
            search_entry.configure(border_color="#DDDDDD")

        # Bind classique FocusIn/Out
        search_entry.bind("<FocusIn>", set_focus_blue)
        search_entry.bind("<FocusOut>", set_focus_gray)

        # Vérification après chaque clic global
        def check_focus_after_click(event):
            if event.widget is search_entry:
                set_focus_blue()
            else:
                set_focus_gray()
                self.focus_set()

        self.bind_all("<Button-1>", check_focus_after_click)
        # ------------------------------------------------------

        # Bouton recherche
        search_button = ctk.CTkButton(
            search_frame,
            text="🔍",
            width=45,
            height=45,
            corner_radius=25,
            fg_color=COLORS["accent"],
            text_color="white",
            command=self.execute_search
        )
        search_button.grid(row=0, column=1)

        # Résultat recherche
        self.search_result_label = ctk.CTkLabel(
            self.content_frame,
            text="",
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"]
        )
        self.search_result_label.pack(pady=10)

    # ------------------- SEMESTRES -------------------
    def show_semestre(self, num):
        ctk.CTkLabel(self.content_frame, text=f"Semestre {num}",
                     font=("Helvetica", 24, "bold")).pack(pady=50)

    # ------------------- COLLÈGES -------------------
    def show_colleges(self):
        ctk.CTkLabel(self.content_frame, text="Collèges",
                     font=("Helvetica", 24, "bold")).pack(pady=50)

    # ------------------- Recherche -------------------
    def execute_search(self):
        query = self.search_var.get()
        if not query.strip():
            self.search_result_label.configure(text="Veuillez entrer une recherche.")
            return

        self.search_result_label.configure(text=f"Recherche en cours pour : {query}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
