import customtkinter as ctk
from .styles import COLORS


class Dashboard(ctk.CTkFrame):
    """Main dashboard with fixed cards (no resize)."""

    def __init__(self, parent):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.cards = []
        self.cards_frame = None
        self._build_ui()

    def _build_ui(self) -> None:
        # Titre principal
        title = ctk.CTkLabel(
            self,
            text="Accueil",
            font=("Helvetica", 32, "bold"),
            text_color=COLORS["accent"],
        )
        title.pack(pady=30)

        # Conteneur cartes
        self.cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.cards_frame.pack(expand=True, fill="both", padx=20, pady=10)

        # Données cartes
        self.cards_data = [
            ("Tâches Notion", "5 tâches à faire"),
            ("Google Drive", "3 fichiers liés"),
            ("Google Calendar", "Réviser : Anatomie"),
        ]

        # Création cartes
        for title, value in self.cards_data:
            card = self._create_card(self.cards_frame, title, value)
            self.cards.append(card)

        # Placement fixe
        self._arrange_cards()

        # Barre recherche
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(pady=40)

        search_entry = ctk.CTkEntry(
            search_frame,
            width=500,
            height=45,
            corner_radius=25,
            border_width=2,
            border_color="#DDDDDD",
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

        search_button = ctk.CTkButton(
            search_frame,
            text="🔍",
            width=45,
            height=45,
            corner_radius=25,
            fg_color=COLORS["accent"],
            text_color="white",
            command=lambda: self.execute_search(search_entry.get()),
        )
        search_button.grid(row=0, column=1)

        self.search_result_label = ctk.CTkLabel(
            self,
            text="",
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"],
        )
        self.search_result_label.pack(pady=10)

    def _create_card(self, parent, title: str, value: str) -> ctk.CTkFrame:
        normal_bg = COLORS["bg_card"]
        hover_bg = COLORS["bg_card_hover"]
        normal_border = "#D0D0D0"
        hover_border = COLORS["accent"]

        card = ctk.CTkFrame(
            parent,
            width=280,
            height=160,
            corner_radius=20,
            fg_color=normal_bg,
            border_width=1,
            border_color=normal_border
        )
        card.grid_propagate(False)

        ctk.CTkLabel(
            card,
            text=title,
            font=("Helvetica", 18, "bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(15, 5))

        ctk.CTkLabel(
            card,
            text=value,
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"],
        ).pack()

        def animate_color(widget, start_color, end_color, steps=8, delay=15):
            def hex_to_rgb(hex_color):
                hex_color = hex_color.lstrip('#')
                return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

            def rgb_to_hex(rgb):
                return f'#{int(rgb[0]):02x}{int(rgb[1]):02x}{int(rgb[2]):02x}'

            start_rgb = hex_to_rgb(start_color)
            end_rgb = hex_to_rgb(end_color)

            def step(i=0):
                r = start_rgb[0] + (end_rgb[0] - start_rgb[0]) * i / steps
                g = start_rgb[1] + (end_rgb[1] - start_rgb[1]) * i / steps
                b = start_rgb[2] + (end_rgb[2] - start_rgb[2]) * i / steps
                widget.configure(fg_color=rgb_to_hex((r, g, b)))
                if i < steps:
                    widget.after(delay, step, i + 1)

            step()

        def on_enter(event):
            animate_color(card, normal_bg, hover_bg)
            card.configure(border_color=hover_border, cursor="hand2")

        def on_leave(event):
            animate_color(card, hover_bg, normal_bg)
            card.configure(border_color=normal_border, cursor="")

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)

        return card

    def _arrange_cards(self):
        """Place les cartes en ligne fixe (3 colonnes)."""
        for i, card in enumerate(self.cards):
            card.grid(row=0, column=i, padx=20, pady=10, sticky="n")

        for i in range(len(self.cards)):
            self.cards_frame.grid_columnconfigure(i, weight=1)
