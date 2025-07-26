import customtkinter as ctk

class Sidebar(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, width=200)
        self.pack_propagate(False)

        ctk.CTkButton(self, text="Accueil").pack(pady=10, fill="x")
        ctk.CTkButton(self, text="Semestres").pack(pady=10, fill="x")
        ctk.CTkButton(self, text="Collège").pack(pady=10, fill="x")
        ctk.CTkButton(self, text="Rafraîchir Notion").pack(pady=10, fill="x")
