import time
import customtkinter as ctk
from ui.sidebar import Sidebar
from ui.styles import COLORS
from ui.semestre_view import SemestreView
from ui.college_view import CollegeView
from ui.loading_screen import LoadingScreen
from services.data_manager import DataManager
from services.logger import get_logger
import logging
from datetime import datetime
from tkinter import messagebox
import threading

logger = get_logger(__name__)

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.withdraw()  # Masque la fenêtre principale au début

        # --- Etat global filtre actions ---
        self.show_only_actions = False

        # Début du chrono global
        self._start_time = time.perf_counter()
        logger.info("=== Lancement de Notionator ===")

        loading_messages = [
            "Chargement du cache local...",
            "Préparation de l'interface...",
            "Synchronisation en arrière-plan..."
        ]

        loading_screen = LoadingScreen(self, loading_messages)
        loading_screen.geometry("500x300")  # Fenêtre plus grande
        loading_screen.update()

        def load():
            # --- Initialisation DataManager ---
            data_start = time.perf_counter()
            self.data_manager = DataManager()  # Charge cache JSON existant
            data_end = time.perf_counter()
            logger.info(f"Chargement cache local : {data_end - data_start:.2f} sec")

            loading_screen.next_message()

            # --- Initialisation UI ---
            ui_start = time.perf_counter()
            self.current_screen = "accueil"
            self.title("Notionator")
            self.geometry("1000x600")  # Taille par défaut
            self.state("zoomed")  # Plein écran dès le début
            ctk.set_appearance_mode("light")
            ctk.set_default_color_theme("blue")

            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(0, weight=1)

            self.sidebar = Sidebar(self, self.switch_frame, self.reload_notion_data)
            self.sidebar.grid(row=0, column=0, sticky="ns")

            self.content_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
            self.content_frame.grid(row=0, column=1, sticky="nsew")

            self.show_accueil()
            ui_end = time.perf_counter()
            logger.info(f"Initialisation UI : {ui_end - ui_start:.2f} sec")

            loading_screen.next_message()
            loading_screen.destroy()

            # Fin du chrono global
            total_time = time.perf_counter() - self._start_time
            logger.info(f"Temps total d'ouverture : {total_time:.2f} sec")

            self.deiconify()  # Affiche la fenêtre principale

            # Lancer sync partielle en arrière-plan
            self.data_manager.sync_background()

        threading.Thread(target=load, daemon=True).start()

    # -------------------- Navigation --------------------
    def switch_frame(self, screen):
        logger.info(f"Changement d'écran vers : {screen}")
        self.current_screen = screen
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.unbind_all("<Button-1>")

        if screen == "accueil":
            self.show_accueil()
        elif screen.startswith("semestre_"):
            num = screen.split("_")[1]
            self.show_semestre(num)
        elif screen == "colleges":
            self.show_colleges()

        elif screen == "tous_les_semestres":
            self.show_semestre("all")

    def show_accueil(self):
        logger.debug("Affichage de l'écran d'accueil")
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        title = ctk.CTkLabel(
            self.content_frame,
            text="Accueil",
            font=("Helvetica", 32, "bold"),
            text_color=COLORS["accent"]
        )
        title.pack(pady=30)

        cards_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        cards_frame.pack(pady=20)

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
                border_color="#D0D0D0"
            )
            card.pack_propagate(False)
            ctk.CTkLabel(
                card,
                text=title,
                font=("Helvetica", 18, "bold"),
                text_color=COLORS["text_primary"]
            ).pack(pady=(15, 5))
            ctk.CTkLabel(
                card,
                text=value,
                font=("Helvetica", 14),
                text_color=COLORS["text_secondary"]
            ).pack()

            def on_enter(event):
                card.configure(fg_color=COLORS["bg_card_hover"], border_color=COLORS["accent"])
            def on_leave(event):
                card.configure(fg_color=COLORS["bg_card"], border_color="#D0D0D0")

            card.bind("<Enter>", on_enter)
            card.bind("<Leave>", on_leave)
            return card

        nb_cours = len(self.data_manager.get_all_courses())
        card1 = create_card(cards_frame, "Tâches Notion", f"{nb_cours} cours")
        card1.grid(row=0, column=0, padx=20)
        card2 = create_card(cards_frame, "Google Drive", "3 fichiers liés")
        card2.grid(row=0, column=1, padx=20)
        card3 = create_card(cards_frame, "Google Calendar", "Réviser : Anatomie")
        card3.grid(row=0, column=2, padx=20)

        # Champ recherche
        search_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        search_frame.pack(pady=40)

        self.search_var = ctk.StringVar()
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
            placeholder_text_color=COLORS["text_secondary"]
        )
        search_entry.grid(row=0, column=0, padx=(0, 10))

        def set_focus_blue(event=None):
            if search_entry.winfo_exists():
                search_entry.configure(border_color=COLORS["accent"])

        def set_focus_gray(event=None):
            if search_entry.winfo_exists():
                search_entry.configure(border_color="#DDDDDD")

        search_entry.bind("<FocusIn>", set_focus_blue)
        search_entry.bind("<FocusOut>", set_focus_gray)

        def check_focus_after_click(event):
            if not search_entry.winfo_exists():
                return
            if event.widget == search_entry:
                return
            set_focus_gray()
            if search_entry.focus_get() == search_entry:
                self.focus_set()

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
            command=self.execute_search
        )
        search_button.grid(row=0, column=1)

        self.search_result_label = ctk.CTkLabel(
            self.content_frame,
            text="",
            font=("Helvetica", 14),
            text_color=COLORS["text_secondary"]
        )
        self.search_result_label.pack(pady=10)

    def show_semestre(self, num):
        logger.info(f"Affichage du semestre {num}")
        semestre_view = SemestreView(self.content_frame, num, self.data_manager, self.show_only_actions)
        semestre_view.pack(expand=True, fill="both")

    def show_colleges(self):
        college_view = CollegeView(self.content_frame, self.data_manager, self.show_only_actions)
        college_view.pack(expand=True, fill="both")

    def execute_search(self):
        query = self.search_var.get()
        if not query.strip():
            self.search_result_label.configure(text="Veuillez entrer une recherche.")
            logger.warning("Recherche vide effectuée")
            return
        logger.info(f"Recherche lancée pour : {query}")
        self.search_result_label.configure(text=f"Recherche en cours pour : {query}")

    def reload_notion_data(self):
        """
        Recharge manuellement le cache depuis Notion :
        - Effectue une sync partielle (arrière-plan)
        - Recharge l’écran courant
        """
        logger.info("Rechargement manuel du cache Notion")
        self.data_manager.sync_background()
        self.switch_frame(self.current_screen)
        messagebox.showinfo("Synchronisation lancée", "La synchronisation partielle est en cours en arrière-plan.")

    # --- Toggle filtre actions ---
    def toggle_global_filter(self):
        self.show_only_actions = not self.show_only_actions
        self.switch_frame(self.current_screen)


if __name__ == "__main__":
    banner = (
        "\n\n--- Nouvelle session Notionator --- "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        "---\n\n"
    )
    logging.getLogger().info(banner)

    try:
        app = App()
        logger.info("Application démarrée")
        app.mainloop()
    except Exception:
        logger.exception("Erreur critique dans la boucle principale")
