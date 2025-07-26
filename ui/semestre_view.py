import customtkinter as ctk

class SemestreView(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        ctk.CTkLabel(self, text="Semestre", font=("Helvetica", 18, "bold")).pack(pady=10)
