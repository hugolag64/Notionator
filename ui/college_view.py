import customtkinter as ctk
import webbrowser
from .styles import COLORS
from tkinter import messagebox
from PIL import Image, ImageTk
import os
import re
from constants import COLLEGE_NOTION_URLS

BATCH_SIZE = 30  # Lazy loading


class CollegeView(ctk.CTkFrame):
    def __init__(self, parent, data_manager, show_only_actions=False):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.data_manager = data_manager
        self.show_only_actions = show_only_actions  # filtre global

        # --- Icônes ---
        self.fiche_icon = self._load_fiche_icon()
        self.action_icon = self._load_action_icon()

        # Données + offset
        self._refresh_courses()
        self.offset = 0
        self._build_ui()

    # ------------------------------
    # Helpers
    # ------------------------------
    def _normalize_college_name(self, name: str) -> str:
        import unicodedata
        if not name:
            return ""
        name = name.strip().lower()
        name = " ".join(name.split())
        name = unicodedata.normalize("NFKD", name)
        return "".join(c for c in name if not unicodedata.combining(c))

    def _clean_college_name(self, name: str) -> str:
        """
        Supprime les emojis ou symboles au début du nom du collège
        """
        if not name:
            return "-"
        # Supprime tout caractère non alphabétique au début (emoji, icône)
        return re.sub(r'^[^\w\s]+', '', name).strip()

    def _load_fiche_icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'fiche.png')
        try:
            img = Image.open(icon_path).convert("RGBA").resize((28, 28))
            return ImageTk.PhotoImage(img)
        except Exception as e:
            print("Erreur chargement fiche.png :", e)
            return None

    def _load_action_icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), '..', 'assets', 'action.png')
        try:
            img = Image.open(icon_path).convert("RGBA").resize((28, 28))
            return ImageTk.PhotoImage(img)
        except Exception as e:
            print("Erreur chargement action.png :", e)
            return None

    # ------------------------------
    # Data
    # ------------------------------
    def _refresh_courses(self):
        # Récupérer tous les cours depuis DataManager (déjà parsés)
        all_cours = self.data_manager.get_all_courses_college()

        # Appliquer filtre "Actions"
        if self.show_only_actions:
            all_cours = [c for c in all_cours if self._has_actions(c)]

        self.courses = all_cours

    def _has_actions(self, course):
        """Retourne True si des validations manquent pour ce cours collège"""
        return not (
            course["pdf_ok"]
            and course["anki_college_ok"]
            and course["resume_college_ok"]
            and course["rappel_college_ok"]
        )

    # ------------------------------
    # UI
    # ------------------------------
    def _build_ui(self):
        for widget in self.winfo_children():
            widget.destroy()

        # --- Titre ---
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", pady=(16, 6))
        title = ctk.CTkLabel(
            title_frame,
            text="Collèges",
            font=("Helvetica", 28, "bold"),
            text_color=COLORS["accent"]
        )
        title.pack(side="top", padx=(0, 0))

        # --- Tableau ---
        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
        self.container.pack(padx=30, pady=10, fill="both", expand=True)

        # Colonnes
        weights = [4, 1, 1, 2, 3, 1]
        headers = ["Cours", "Item", "Fiche", "Collège", "Statut", "Actions"]

        for col, w in enumerate(weights):
            self.container.grid_columnconfigure(col, weight=w, uniform="col")

        # En-têtes
        header_frame = ctk.CTkFrame(self.container, fg_color="#BFBFBF", corner_radius=8)
        header_frame.grid(row=0, column=0, columnspan=6, sticky="nsew", pady=(0, 4))
        for col, text in enumerate(headers):
            ctk.CTkLabel(
                header_frame,
                text=text,
                font=("Helvetica", 16, "bold"),
                text_color=COLORS["text_primary"],
                anchor="center"
            ).grid(row=0, column=col, padx=4, pady=8, sticky="nsew")
            header_frame.grid_columnconfigure(col, weight=weights[col], uniform="col")

        # Contenu
        self.content_frame = ctk.CTkFrame(self.container, fg_color=COLORS["bg_light"])
        self.content_frame.grid(row=1, column=0, columnspan=6, sticky="nsew")
        for col, w in enumerate(weights):
            self.content_frame.grid_columnconfigure(col, weight=w, uniform="col")

        # Si aucun cours
        if not self.courses:
            ctk.CTkLabel(
                self.content_frame,
                text="Aucun cours trouvé.",
                font=("Helvetica", 16),
                text_color=COLORS["text_secondary"]
            ).grid(row=0, column=0, columnspan=6, pady=20)
            return

        # Charger premiers cours
        self.load_more_courses()

    def load_more_courses(self):
        """Charge le prochain lot de cours"""
        # Supprimer ancien bouton "Charger plus" s'il existe
        if hasattr(self, "load_more_btn") and self.load_more_btn.winfo_exists():
            self.load_more_btn.destroy()

        start = self.offset
        end = start + BATCH_SIZE
        batch = self.courses[start:end]

        status_labels = ["PDF", "Anki", "Résumé", "Rappel"]

        for i, course in enumerate(batch, start=start):
            # -------- Colonne Cours --------
            text_color = "#0078D7" if course["pdf_ok"] else COLORS["text_primary"]

            course_label = ctk.CTkLabel(
                self.content_frame,
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

            # -------- Colonne Item --------
            ctk.CTkLabel(
                self.content_frame,
                text=course["item"],
                font=("Helvetica", 14),
                text_color=COLORS["text_secondary"],
                anchor="center"
            ).grid(row=i, column=1, padx=4, pady=6, sticky="nsew")

            # -------- Colonne Fiche --------
            if course.get("fiche_url"):
                fiche_btn = ctk.CTkButton(
                    self.content_frame,
                    text="",
                    image=self.fiche_icon,
                    width=36,
                    height=36,
                    fg_color="transparent",
                    hover_color="#e4eaff",
                    command=lambda url=course["fiche_url"]: webbrowser.open(url),
                    corner_radius=6,
                    cursor="hand2"
                )
                fiche_btn.grid(row=i, column=2, padx=4, pady=6, sticky="nsew")
            else:
                ctk.CTkLabel(
                    self.content_frame,
                    text="",
                    fg_color="transparent"
                ).grid(row=i, column=2, padx=4, pady=6, sticky="nsew")

            # -------- Colonne Collège --------
            college_name = self._clean_college_name(course["college"])  # Nettoyage ici
            normalized_college = self._normalize_college_name(college_name)
            url = None
            for k, v in COLLEGE_NOTION_URLS.items():
                if self._normalize_college_name(k) == normalized_college:
                    url = v
                    break

            if url:
                def on_enter(event, widget=None):
                    widget.configure(text_color="#0078D7", cursor="hand2")

                def on_leave(event, widget=None):
                    widget.configure(text_color=COLORS["text_primary"], cursor="hand2")

                def on_click(event, link=url):
                    webbrowser.open(link)

                college_label = ctk.CTkLabel(
                    self.content_frame,
                    text=college_name,
                    font=("Helvetica", 14),
                    text_color=COLORS["text_primary"],
                    fg_color="transparent",
                    anchor="center",
                    cursor="hand2"
                )
                college_label.bind("<Enter>", lambda e, w=college_label: on_enter(e, w))
                college_label.bind("<Leave>", lambda e, w=college_label: on_leave(e, w))
                college_label.bind("<Button-1>", on_click)
            else:
                college_label = ctk.CTkLabel(
                    self.content_frame,
                    text=college_name or "-",
                    font=("Helvetica", 14),
                    text_color=COLORS["text_secondary"],
                    anchor="center"
                )

            college_label.grid(row=i, column=3, padx=4, pady=6, sticky="nsew")

            # -------- Colonne Statut --------
            status_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
            status_frame.grid(row=i, column=4, padx=40, pady=6, sticky="nsew")
            statuses = [
                course["pdf_ok"],
                course["anki_college_ok"],
                course["resume_college_ok"],
                course["rappel_college_ok"]
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

            # -------- Colonne Actions --------
            actions_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
            actions_frame.grid(row=i, column=5, padx=4, pady=6, sticky="nsew")
            btn_container = ctk.CTkFrame(actions_frame, fg_color="transparent")
            btn_container.pack(anchor="center")
            ctk.CTkButton(
                btn_container,
                text="",
                image=self.action_icon,
                width=36,
                height=30,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#E6E6E6",
                command=lambda c=course: self.on_action_click(c)
            ).pack(side="left", padx=0)

        # Update offset
        self.offset += len(batch)

        # Bouton "Charger plus"
        if self.offset < len(self.courses):
            self.load_more_btn = ctk.CTkButton(
                self.container,
                text="Charger plus",
                width=200,
                height=36,
                fg_color=COLORS["accent"],
                text_color="white",
                command=self.load_more_courses
            )
            self.load_more_btn.grid(row=2, column=0, columnspan=6, pady=10)

    # ------------------------------
    # Placeholder actions
    # ------------------------------
    def on_action_click(self, course):
        print(f"Menu actions pour : {course['nom']}")

    def view_course(self, course):
        print(f"[VIEW] {course['nom']}")

    def edit_course(self, course):
        print(f"[EDIT] {course['nom']}")

    def delete_course(self, course):
        print(f"[DELETE] {course['nom']}")
