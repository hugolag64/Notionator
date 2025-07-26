import customtkinter as ctk
from core.notion_sync import fetch_courses_due_today, get_course_title
from core.chatgpt_api import ask_question


class Dashboard(ctk.CTkFrame):
    """Vue principale de l'application avec la recherche et les rappels du jour."""

    def __init__(self, master):
        super().__init__(master)

        # Barre de recherche alimentée par l'IA
        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            self,
            textvariable=self.search_var,
            placeholder_text="Rechercher ou poser une question...",
        )
        search_entry.pack(fill="x", pady=(0, 15))
        search_entry.bind("<Return>", self.on_search)

        # Zone d'affichage du résultat IA
        self.result_box = ctk.CTkTextbox(self, height=100)
        self.result_box.pack(fill="x")

        # Titre principal
        ctk.CTkLabel(
            self, text="Bienvenue \U0001F44B", font=("Helvetica", 24, "bold")
        ).pack(anchor="w", pady=(20, 10))

        # Section des cours à faire aujourd'hui
        ctk.CTkLabel(
            self,
            text="Cours à faire aujourd'hui",
            font=("Helvetica", 16, "bold"),
        ).pack(anchor="w")
        self.pastille_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.pastille_frame.pack(fill="x", pady=(5, 0))

        self.update_pastilles()

    def on_search(self, _event=None):
        """Interroger l'API OpenAI et afficher la réponse."""
        question = self.search_var.get().strip()
        if not question:
            return
        response = ask_question(question)
        self.result_box.delete("1.0", "end")
        self.result_box.insert("end", response)

    def update_pastilles(self):
        """Mettre à jour l'affichage des cours à faire aujourd'hui."""
        for widget in self.pastille_frame.winfo_children():
            widget.destroy()

        courses = fetch_courses_due_today()
        if not courses:
            ctk.CTkLabel(
                self.pastille_frame, text="Aucun cours prévu aujourd'hui."
            ).pack(anchor="w")
            return

        for course in courses:
            title = get_course_title(course)
            lbl = ctk.CTkLabel(
                self.pastille_frame,
                text=title,
                fg_color="#E5E5E5",
                text_color="black",
                corner_radius=15,
                padx=10,
                pady=5,
            )
            lbl.pack(side="left", padx=5, pady=5)
