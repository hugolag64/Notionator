"""Dashboard view for the main window."""

import customtkinter as ctk

from .styles import COLORS


class Dashboard(ctk.CTkFrame):
    """Main dashboard displaying cards and search bar."""

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.search_var = ctk.StringVar()
        self._build_ui()

    # -----------------------------------------------------
    def _build_ui(self) -> None:
        """Create all widgets for the dashboard."""

        # Titre principal
        title = ctk.CTkLabel(
            self,
            text="Accueil",
            font=("Helvetica", 32, "bold"),
            text_color=COLORS["accent"],
        )
        title.pack(pady=30)

        # ------ Cartes d'information ------
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.pack(pady=20)

        def create_card(parent, title: str, value: str) -> ctk.CTkFrame:
            normal_width = 280
            normal_height = 160

            card = ctk.CTkFrame(
                parent,
                width=normal_width,
                height=normal_height,
                corner_radius=20,
                fg_color=COLORS["bg_card"],
                border_width=1,
                border_color="#D0D0D0",
            )
            card.pack_propagate(False)

            # Titre de la carte
            ctk.CTkLabel(
                card,
                text=title,
                font=("Helvetica", 18, "bold"),
                text_color=COLORS["text_primary"],
            ).pack(pady=(15, 5))

            # Valeur / sous-texte
            ctk.CTkLabel(
                card,
                text=value,
                font=("Helvetica", 14),
                text_color=COLORS["text_secondary"],
            ).pack()

            # Effets de survol
            def on_enter(event):
                card.configure(
                    fg_color=COLORS["bg_card_hover"],
                    border_color=COLORS["accent"],
                )

            def on_leave(event):
                card.configure(fg_color=COLORS["bg_card"], border_color="#D0D0D0")

            card.bind("<Enter>", on_enter)
            card.bind("<Leave>", on_leave)

            return card

        card1 = create_card(cards_frame, "Tâches Notion", "5 tâches à faire")
        card1.grid(row=0, column=0, padx=20)

        card2 = create_card(cards_frame, "Google Drive", "3 fichiers liés")
        card2.grid(row=0, column=1, padx=20)

        card3 = create_card(cards_frame, "Google Calendar", "Réviser : Anatomie")
        card3.grid(row=0, column=2, padx=20)

        # ------ Barre de recherche ------
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(pady=40)

        search_entry = ctk.CTkEntry(
            search_frame,
            width=500,
            height=45,
            corner_radius=25,
            border_width=2,
            border_color="#DDDDDD",
            textvariable=self.search_var,
            placeholder_text="Rechercher dans Notion ou poser une question...",
            fg_color=COLORS["bg_card"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_secondary"],
        )
        search_entry.grid(row=0, column=0, padx=(0, 10))

        def set_focus_blue(_=None):
            search_entry.configure(border_color=COLORS["accent"])

        def set_focus_gray(_=None):
            search_entry.configure(border_color="#DDDDDD")

        search_entry.bind("<FocusIn>", set_focus_blue)
        search_entry.bind("<FocusOut>", set_focus_gray)

        def check_focus_after_click(event):
            if event.widget == search_entry:
                return
            set_focus_gray()
            if search_entry.focus_get() == search_entry:
                self.master.focus_set()

        self.bind_all("<Button-1>", check_focus_after_click)
        self.after(100, lambda: set_focus_gray())

        search_button = ctk.CTkButton(
            search_frame,
            text="🔍",
            width=45,
            height=45,
            corner_radius=25,
            fg_color=COLORS["accent"],
            text_color="white",
            command=self.execute_search,
        )
        search_button.grid(row=0, column=1)

        self.search_result_label = ctk.CTkLabel(
            self,
            text="",
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"],
        )
        self.search_result_label.pack(pady=10)

    # -------------------------------------------------
    def execute_search(self) -> None:
        """Callback for the search button."""
        query = self.search_var.get()
        if not query.strip():
            self.search_result_label.configure(text="Veuillez entrer une recherche.")
            return
        self.search_result_label.configure(text=f"Recherche en cours pour : {query}")

