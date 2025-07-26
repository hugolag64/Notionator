import customtkinter as ctk

class Dashboard(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        ctk.CTkLabel(self, text="Bienvenue 👋", font=("Helvetica", 24, "bold")).pack(pady=20)
        ctk.CTkLabel(self, text="Voici un aperçu de ta progression :", font=("Helvetica", 14)).pack(pady=10)
