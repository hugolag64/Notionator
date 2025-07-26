"""Entrée principale de l'application Notionator."""

import customtkinter as ctk

from config import THEME
from ui.main_window import MainWindow


def main() -> None:
    """Initialiser et lancer l'interface graphique principale."""

    # Appliquer les paramètres de thème
    ctk.set_appearance_mode(THEME)
    ctk.set_default_color_theme("blue")

    # Créer la fenêtre principale et démarrer la boucle d'événements
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
