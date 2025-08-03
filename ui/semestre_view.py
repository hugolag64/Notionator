import customtkinter as ctk
from .styles import COLORS
import webbrowser
from services.notion_client import NotionAPI
from tkinter import messagebox  # Pour popup simple

BATCH_SIZE = 30  # Nombre de cours chargés par lot

class SemestreView(ctk.CTkFrame):
    def __init__(self, parent, semestre_num, data_manager, show_only_actions=False):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.semestre_num = semestre_num
        self.data_manager = data_manager
        self.show_only_actions = show_only_actions

        # Gestion lazy loading
        self.offset = 0
        self.loaded_courses = []

        self._refresh_courses()
        self._build_ui()

    # --- Vérifie si le cours a des actions à faire ---
    def _has_actions(self, course):
        return not (course["pdf_ok"] and course["anki_ok"] and course["resume_ok"] and course["rappel_ok"])

    # --- Charge + filtre les cours ---
    def _refresh_courses(self):
        """Charge et filtre les cours par semestre."""
        # Récupérer tous les cours parsés depuis DataManager
        all_cours = self.data_manager.get_parsed_courses(mode="semestre", semestre_num=self.semestre_num)

        # Filtre actions si activé
        if self.show_only_actions:
            all_cours = [c for c in all_cours if self._has_actions(c)]

        # Stocke les cours filtrés pour lazy loading
        self.filtered_courses = all_cours
        self.loaded_courses = self.filtered_courses[:BATCH_SIZE]
        self.offset = len(self.loaded_courses)

    # --- Charger plus de cours ---
    def _load_more(self):
        next_offset = self.offset + BATCH_SIZE
        self.loaded_courses = self.filtered_courses[:next_offset]
        self.offset = len(self.loaded_courses)
        self._build_ui()

    # --- Construction de l'UI ---
    def _build_ui(self):
        # Efface contenu
        for widget in self.winfo_children():
            widget.destroy()

        # --- Titre ---
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", pady=(16, 6))

        title_text = "Tous les cours" if self.semestre_num == "all" else f"Semestre {self.semestre_num}"
        title = ctk.CTkLabel(
            title_frame,
            text=title_text,
            font=("Helvetica", 28, "bold"),
            text_color=COLORS["accent"]
        )

        title.pack(side="top")

        # Bouton ajout
        add_btn = ctk.CTkButton(
            title_frame,
            text="+",
            width=40,
            height=40,
            font=("Helvetica", 22, "bold"),
            fg_color=COLORS["accent"],
            text_color="white",
            corner_radius=20,
            command=self._open_add_course_modal
        )
        add_btn.pack(side="right", padx=(0, 10))

        # --- Tableau ---
        container = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
        container.pack(padx=30, pady=10, fill="both", expand=True)

        weights = [4, 3, 3, 2]
        for col, w in enumerate(weights):
            container.grid_columnconfigure(col, weight=w, uniform="col")

        header_frame = ctk.CTkFrame(container, fg_color="#BFBFBF", corner_radius=8)
        header_frame.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 4))

        headers = ["Cours", "UE", "Statut", "Actions"]
        for col, text in enumerate(headers):
            ctk.CTkLabel(
                header_frame,
                text=text,
                font=("Helvetica", 16, "bold"),
                text_color=COLORS["text_primary"],
                anchor="center"
            ).grid(row=0, column=col, padx=4, pady=8, sticky="nsew")
            header_frame.grid_columnconfigure(col, weight=weights[col], uniform="col")

        content_frame = ctk.CTkFrame(container, fg_color=COLORS["bg_light"])
        content_frame.grid(row=1, column=0, columnspan=4, sticky="nsew")

        for col, w in enumerate(weights):
            content_frame.grid_columnconfigure(col, weight=w, uniform="col")

        # --- Affichage des cours chargés ---
        status_labels = ["PDF", "Anki", "Résumé", "Rappel"]

        for i, course in enumerate(self.loaded_courses):
            # Cours
            text_color = "#0078D7" if course["pdf_ok"] else COLORS["text_primary"]
            course_label = ctk.CTkLabel(
                content_frame,
                text=course["nom"],
                font=("Helvetica", 14),
                text_color=text_color,
                anchor="center",
                wraplength=250,
                fg_color="transparent"
            )
            course_label.grid(row=i, column=0, padx=4, pady=6, sticky="nsew")

            if course["pdf_ok"]:
                url = course["url_pdf"]

                def open_pdf(event, link=url):
                    webbrowser.open(link)

                def on_enter(event, widget=course_label):
                    widget.configure(fg_color="#E9EEF5", cursor="hand2")

                def on_leave(event, widget=course_label):
                    widget.configure(fg_color="transparent", cursor="")

                course_label.bind("<Enter>", on_enter)
                course_label.bind("<Leave>", on_leave)
                course_label.bind("<Button-1>", open_pdf)

            # UE
            ue_text = ", ".join(course["ue"]) if course["ue"] else "Aucune UE"
            ctk.CTkLabel(
                content_frame,
                text=ue_text,
                font=("Helvetica", 14),
                text_color=COLORS["text_secondary"],
                anchor="center"
            ).grid(row=i, column=1, padx=4, pady=6, sticky="nsew")

            # Statut
            status_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
            status_frame.grid(row=i, column=2, padx=40, pady=6, sticky="nsew")
            statuses = [
                course["pdf_ok"],
                course["anki_ok"],
                course["resume_ok"],
                course["rappel_ok"]
            ]
            for j, status in enumerate(statuses):
                icon = "✔" if status else "✘"
                color = "green" if status else "red"
                ctk.CTkLabel(
                    status_frame,
                    text=f"{icon} {status_labels[j]}",
                    font=("Helvetica", 12),
                    text_color=color
                ).pack(side="left", padx=3)

            # Actions
            actions_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
            actions_frame.grid(row=i, column=3, padx=4, pady=6, sticky="nsew")

            btn_container = ctk.CTkFrame(actions_frame, fg_color="transparent")
            btn_container.pack(anchor="center")

            self._action_btn(btn_container, "👁️", "#0078D7", lambda c=course: self.view_course(c)).pack(side="left", padx=5)
            self._action_btn(btn_container, "✏️", "#0078D7", lambda c=course: self.edit_course(c)).pack(side="left", padx=5)
            self._action_btn(btn_container, "🗑️", "red", lambda c=course: self.delete_course(c)).pack(side="left", padx=5)

        # --- Bouton Charger plus ---
        if self.offset < len(self.filtered_courses):
            ctk.CTkButton(
                self,
                text="Charger plus",
                width=150,
                height=40,
                fg_color=COLORS["accent"],
                text_color="white",
                corner_radius=20,
                command=self._load_more
            ).pack(pady=20)

    # --- Bouton action générique ---
    def _action_btn(self, parent, text, color, command):
        return ctk.CTkButton(
            parent,
            text=text,
            width=36,
            height=30,
            corner_radius=6,
            fg_color="transparent",
            hover_color="#E6E6E6",
            text_color=color,
            font=("Helvetica", 16),
            command=command
        )

    # --- Modal ajout cours ---
    def _open_add_course_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Ajouter un cours")
        modal.geometry("350x230")
        modal.transient(self)
        modal.grab_set()
        modal.resizable(False, False)

        # Centrer
        self.update_idletasks()
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_width = self.winfo_width()
        parent_height = self.winfo_height()
        pos_x = parent_x + (parent_width // 2) - 175
        pos_y = parent_y + (parent_height // 2) - 115
        modal.geometry(f"350x230+{pos_x}+{pos_y}")

        # Nom du cours
        ctk.CTkLabel(modal, text="Nom du cours :", font=("Helvetica", 14)).pack(pady=(18, 5))
        entry_nom = ctk.CTkEntry(modal, width=230)
        entry_nom.pack()

        # UE
        ctk.CTkLabel(modal, text="UE :", font=("Helvetica", 14)).pack(pady=(15, 5))
        all_ue = self.data_manager.get_all_ue()
        ue_for_semestre = []
        for ue in all_ue:
            props = ue.get("properties", {})
            semestre_prop = props.get("Semestre", {}).get("select", {}).get("name", "")
            if semestre_prop == f"Semestre {self.semestre_num}":
                ue_name = props.get("UE", {}).get("title", [{}])
                ue_name = ue_name[0]["text"]["content"] if ue_name and ue_name[0].get("text") else "Sans titre"
                ue_for_semestre.append((ue["id"], ue_name))

        ue_choices = [name for _, name in ue_for_semestre]
        selected_ue = ctk.StringVar(value=ue_choices[0] if ue_choices else "")

        ue_menu = ctk.CTkOptionMenu(modal, variable=selected_ue, values=ue_choices)
        ue_menu.pack(pady=(0, 10))

        def ajouter():
            nom = entry_nom.get().strip()
            ue_nom = selected_ue.get()
            if not nom or not ue_nom:
                messagebox.showerror("Erreur", "Merci de remplir tous les champs.")
                return
            # Chercher l'ID UE sélectionnée
            ue_id = next((uid for uid, uname in ue_for_semestre if uname == ue_nom), None)
            if not ue_id:
                messagebox.showerror("Erreur", "UE sélectionnée invalide.")
                return

            # Crée propriétés Notion
            new_course = {
                "UE": {"relation": [{"id": ue_id}]},
                "Semestre": {"select": {"name": f"Semestre {self.semestre_num}"}},
            }
            notion = NotionAPI()
            notion.add_cours(title=nom, properties=new_course)
            self.data_manager.sync_background()
            self._refresh_courses()
            self._build_ui()
            modal.destroy()
            messagebox.showinfo("Ajout réussi", "Le cours a bien été ajouté.")

        ctk.CTkButton(modal, text="Ajouter", command=ajouter, fg_color=COLORS["accent"]).pack(pady=(10, 0))

    # --- Actions placeholder ---
    def view_course(self, course):
        print(f"[VIEW] {course['nom']}")

    def edit_course(self, course):
        print(f"[EDIT] {course['nom']}")

    def delete_course(self, course):
        print(f"[DELETE] {course['nom']}")
