import customtkinter as ctk
from ui.sidebar import Sidebar
from ui.dashboard import Dashboard
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
        """Affiche le tableau de bord principal."""
        dashboard = Dashboard(self.content_frame)
        dashboard.pack(expand=True, fill="both")

    # ------------------- SEMESTRES -------------------
    def show_semestre(self, num):
        ctk.CTkLabel(self.content_frame, text=f"Semestre {num}",
                     font=("Helvetica", 24, "bold")).pack(pady=50)

    # ------------------- COLLÈGES -------------------
    def show_colleges(self):
        ctk.CTkLabel(self.content_frame, text="Collèges",
                     font=("Helvetica", 24, "bold")).pack(pady=50)



if __name__ == "__main__":
    app = App()
    app.mainloop()