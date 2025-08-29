# ui/college_view.py
from __future__ import annotations
import os
import re
import time
import webbrowser
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage

from ui.pdf_selector import PDFSelector
from .styles import COLORS
from constants import COLLEGE_NOTION_URLS
from services.drive_sync import DriveSync
from services.actions_manager import ActionsManager
from ui.components import CollegeDialogMultiSelect
from utils.dnd import attach_drop  # DnD direct sur le titre / item
from services.worker import run_io
from services.exclusive import run_exclusive
from utils.ui_queue import post
from utils.event_bus import emit  # ← NEW: notifications inter-vues

BATCH_SIZE = 15  # Lazy loading: 15 par page


class CollegeView(ctk.CTkFrame):
    _current_instance = None

    def __init__(self, parent, data_manager, notion_api, show_only_actions: bool = False):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        CollegeView._current_instance = self

        self.data_manager = data_manager
        self.notion_api = notion_api
        self.show_only_actions = show_only_actions

        self.actions_manager = ActionsManager(
            data_manager=self.data_manager,
            notion_api=self.notion_api,
            root=self,
            refresh_callback=self._refresh_light,
        )

        self.fiche_icon = self._load_fiche_icon()
        self.action_icon = self._load_action_icon()

        # Pagination + filtre
        self.offset = 0
        self.selected_college = ctk.StringVar(value="Tous")

        self._refresh_courses()
        self._build_ui()

    # ------------------------------ Helpers ------------------------------
    def _normalize_college_name(self, name: str) -> str:
        import unicodedata
        if not name:
            return ""
        name = " ".join(name.strip().lower().split())
        name = unicodedata.normalize("NFKD", name)
        return "".join(c for c in name if not unicodedata.combining(c))

    def _clean_college_name(self, name: str) -> str:
        if not name:
            return "-"
        return re.sub(r"^[^\w\s]+", "", name).strip()

    def _load_fiche_icon(self):
        path = os.path.join(os.path.dirname(__file__), "..", "assets", "fiche.png")
        try:
            img = Image.open(path).convert("RGBA").resize((24, 24))
            return CTkImage(light_image=img, size=(24, 24))
        except Exception as e:
            print("Erreur chargement fiche.png :", e)
            return None

    def _load_action_icon(self):
        path = os.path.join(os.path.dirname(__file__), "..", "assets", "action.png")
        try:
            img = Image.open(path).convert("RGBA").resize((16, 16))
            return CTkImage(light_image=img, size=(16, 16))
        except Exception as e:
            print("Erreur chargement action.png :", e)
            return None

    # ------------------------------ Data ------------------------------
    def _refresh_light(self):
        self._refresh_courses()
        self._build_ui()

    def _refresh_courses(self):
        """
        ⚠️ Lit depuis le cache local (DataManager) pour refléter immédiatement les patches:
        - URL PDF COLLEGE renseigné via update_url_local()
        - pdf_ok/url_pdf pris en compte sans attendre Notion
        """
        all_cours = self.data_manager.get_parsed_courses(mode="college") or []
        if self.show_only_actions:
            all_cours = [c for c in all_cours if self._has_actions(c)]
        # Conserve la liste complète pour pouvoir re-filtrer sans reperdre l’état
        self._all_courses = all_cours
        self.offset = 0

        # (Re)calcule les valeurs possibles du filtre Collège
        self._college_choices = self._compute_college_choices()
        # Assure qu'on ne reste pas sur une valeur qui n'existe plus
        if self.selected_college.get() not in (["Tous"] + self._college_choices):
            self.selected_college.set("Tous")

    def _has_actions(self, course):
        return not (
            course["pdf_ok"]
            and course["anki_college_ok"]
            and course["resume_college_ok"]
            and course["rappel_college_ok"]
        )

    def _course_has_college(self, course: dict, college_name: str) -> bool:
        """Supporte propriété Collège en string ou en liste (multiselect)."""
        if not college_name or college_name == "Tous":
            return True
        target = self._normalize_college_name(self._clean_college_name(college_name))

        value = course.get("college")
        if value is None or value == "":
            return False

        # Si multiselect → liste
        if isinstance(value, (list, tuple, set)):
            for v in value:
                if self._normalize_college_name(self._clean_college_name(str(v))) == target:
                    return True
            return False

        # Sinon string
        return self._normalize_college_name(self._clean_college_name(str(value))) == target

    def _compute_college_choices(self) -> list[str]:
        """Renvoie la liste triée des collèges existants (prop multiselect gérée)."""
        found = set()
        for c in self._all_courses:
            v = c.get("college")
            if isinstance(v, (list, tuple, set)):
                for item in v:
                    cleaned = self._clean_college_name(str(item))
                    if cleaned:
                        found.add(cleaned)
            else:
                cleaned = self._clean_college_name(str(v)) if v else ""
                if cleaned:
                    found.add(cleaned)
        return sorted(found, key=lambda s: s.lower())

    def _get_filtered_courses(self) -> list[dict]:
        sel = self.selected_college.get()
        if sel == "Tous":
            return self._all_courses
        return [c for c in self._all_courses if self._course_has_college(c, sel)]

    # ------------------------------ UI ------------------------------
    def _build_ui(self):
        for w in self.winfo_children():
            w.destroy()

        # ----- Titre + bouton [+] -----
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", pady=(16, 6))

        ctk.CTkLabel(
            title_frame, text="Collèges", font=("Helvetica", 28, "bold"), text_color=COLORS["accent"]
        ).pack(side="left", padx=(10, 0))

        ctk.CTkButton(
            title_frame,
            text="+",
            width=40,
            height=40,
            font=("Helvetica", 22, "bold"),
            fg_color=COLORS["accent"],
            text_color="white",
            corner_radius=20,
            command=self._open_add_course_modal,
        ).pack(side="right", padx=(0, 10))

        # ----- Conteneur principal -----
        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
        self.container.pack(padx=30, pady=10, fill="both", expand=True)

        # grid responsive
        weights = [4, 1, 1, 2, 3, 1]
        headers = ["Cours", "Item", "Fiche", "Collège", "Statut", "Actions"]
        for col, w in enumerate(weights):
            self.container.grid_columnconfigure(col, weight=w, uniform="col")

        # Zones: 0 = toolbar filtres, 1 = header liste, 2 = contenu scroll, 3 = bouton 'charger plus'
        self.container.grid_rowconfigure(2, weight=1)

        # ----- Toolbar filtres -----
        toolbar = ctk.CTkFrame(self.container, fg_color="transparent")
        toolbar.grid(row=0, column=0, columnspan=6, sticky="nsew", pady=(0, 6))

        ctk.CTkLabel(toolbar, text="Filtrer par Collège :", font=("Helvetica", 14)).pack(side="left", padx=(0, 10))

        options = ["Tous"] + self._college_choices
        self.filter_menu = ctk.CTkOptionMenu(
            toolbar,
            values=options,
            variable=self.selected_college,
            command=self._on_college_change,
            width=220,
        )
        self.filter_menu.pack(side="left")

        # ----- Header (barre fixe) -----
        header_frame = ctk.CTkFrame(self.container, fg_color="#BFBFBF", corner_radius=8)
        header_frame.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=(0, 4))
        for col, text in enumerate(headers):
            ctk.CTkLabel(
                header_frame, text=text, font=("Helvetica", 16, "bold"),
                text_color=COLORS["text_primary"], anchor="center"
            ).grid(row=0, column=col, padx=4, pady=8, sticky="nsew")
            header_frame.grid_columnconfigure(col, weight=weights[col], uniform="col")

        # ----- Corps scrollable -----
        self.content_frame = ctk.CTkScrollableFrame(
            self.container,
            fg_color=COLORS["bg_light"],
            corner_radius=0,
        )
        self.content_frame.grid(row=2, column=0, columnspan=6, sticky="nsew")
        for col, w in enumerate(weights):
            self.content_frame.grid_columnconfigure(col, weight=w, uniform="col")

        # Liste filtrée
        self.courses = self._get_filtered_courses()

        # Message vide
        if not self.courses:
            ctk.CTkLabel(
                self.content_frame, text="Aucun cours trouvé.",
                font=("Helvetica", 16), text_color=COLORS["text_secondary"]
            ).grid(row=0, column=0, columnspan=6, pady=20)
            # Nettoie le bouton 'charger plus' éventuel
            self._build_load_more_button()
            return

        # Première page
        self.load_more_courses()

        # Bouton "Charger plus"
        self._build_load_more_button()

    def _on_college_change(self, _=None):
        """Quand l’utilisateur change le filtre Collège."""
        self.offset = 0
        # Rebuild complet (plus simple & sûr côté pagination + scroll state)
        self._build_ui()

    def _build_load_more_button(self):
        # place le bouton sous la zone scrollable
        if hasattr(self, "load_more_btn") and self.load_more_btn.winfo_exists():
            self.load_more_btn.destroy()

        total = len(self._get_filtered_courses())
        if self.offset < total:
            self.load_more_btn = ctk.CTkButton(
                self.container,
                text="Charger plus",
                width=200,
                height=36,
                fg_color=COLORS["accent"],
                text_color="white",
                command=self.load_more_courses,
            )
            self.load_more_btn.grid(row=3, column=0, columnspan=6, pady=(10, 6))

    def load_more_courses(self):
        # Récupère la liste filtrée actuelle
        current = self._get_filtered_courses()
        start, end = self.offset, self.offset + BATCH_SIZE
        batch = current[start:end]

        for i, course in enumerate(batch, start=start):
            # ----- Col 0 — Cours (titre) + DnD -----
            text_color = "#0078D7" if course["pdf_ok"] else COLORS["text_primary"]
            course_label = ctk.CTkLabel(
                self.content_frame,
                text=course["nom"],
                font=("Helvetica", 14),
                text_color=text_color,
                anchor="center",
                wraplength=250,
                fg_color="transparent",
            )
            course_label.grid(row=i, column=0, padx=4, pady=6, sticky="nsew")

            # DnD thread-safe: délègue au worker + exclusif
            attach_drop(course_label, on_files=lambda files, pid=course["id"]: self._on_drop_item_async(files, pid))

            # Lien si URL présente
            if course["pdf_ok"] and course.get("url_pdf"):
                url = course["url_pdf"]

                def open_pdf(event, link=url): webbrowser.open(link)
                def on_enter(event, widget=course_label): widget.configure(fg_color="#E9EEF5", cursor="hand2")
                def on_leave(event, widget=course_label): widget.configure(fg_color="transparent", cursor="")

                course_label.bind("<Enter>", on_enter)
                course_label.bind("<Leave>", on_leave)
                course_label.bind("<Button-1>", open_pdf)

            # ----- Col 1 — Item (DnD accepté aussi) -----
            item_lbl = ctk.CTkLabel(
                self.content_frame, text=course["item"], font=("Helvetica", 14),
                text_color=COLORS["text_secondary"], anchor="center"
            )
            item_lbl.grid(row=i, column=1, padx=4, pady=6, sticky="nsew")
            attach_drop(item_lbl, on_files=lambda files, pid=course["id"]: self._on_drop_item_async(files, pid))

            # ----- Col 2 — Fiche -----
            if course.get("fiche_url"):
                ctk.CTkButton(
                    self.content_frame,
                    text="",
                    image=self.fiche_icon,
                    width=36,
                    height=36,
                    fg_color="transparent",
                    hover_color="#e4eaff",
                    command=lambda url=course["fiche_url"]: webbrowser.open(url),
                    corner_radius=6,
                ).grid(row=i, column=2, padx=4, pady=6, sticky="nsew")
            else:
                ctk.CTkLabel(self.content_frame, text="", fg_color="transparent").grid(row=i, column=2, padx=4, pady=6, sticky="nsew")

            # ----- Col 3 — Collège (clic) -----
            # Supporte string ou liste → on affiche proprement la/les valeurs
            value = course.get("college")
            if isinstance(value, (list, tuple, set)):
                college_display = " · ".join(self._clean_college_name(str(v)) for v in value if v)
                primary_for_link = next((self._clean_college_name(str(v)) for v in value if v), "")
            else:
                college_display = self._clean_college_name(value)
                primary_for_link = college_display

            normalized = self._normalize_college_name(primary_for_link)
            url = next(
                (v for k, v in COLLEGE_NOTION_URLS.items()
                 if self._normalize_college_name(k) == normalized),
                None
            )

            if url:
                def on_enter(e, widget): widget.configure(text_color="#0078D7", cursor="hand2")
                def on_leave(e, widget): widget.configure(text_color=COLORS["text_primary"], cursor="")
                def on_click(e, link): webbrowser.open(link)

                college_label = ctk.CTkLabel(
                    self.content_frame, text=college_display or "-", font=("Helvetica", 14),
                    text_color=COLORS["text_primary"], fg_color="transparent", anchor="center", cursor="hand2",
                )
                college_label.bind("<Enter>", lambda e, w=college_label: on_enter(e, w))
                college_label.bind("<Leave>", lambda e, w=college_label: on_leave(e, w))
                college_label.bind("<Button-1>", lambda e, link=url: on_click(e, link))
            else:
                college_label = ctk.CTkLabel(
                    self.content_frame, text=college_display or "-", font=("Helvetica", 14),
                    text_color=COLORS["text_secondary"], anchor="center",
                )

            college_label.grid(row=i, column=3, padx=4, pady=6, sticky="nsew")

            # ----- Col 4 — Statuts -----
            status_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
            status_frame.grid(row=i, column=4, padx=40, pady=6, sticky="nsew")
            statuses = [course["pdf_ok"], course["anki_college_ok"], course["resume_college_ok"], course["rappel_college_ok"]]
            for j, status in enumerate(statuses):
                icon = "✔" if status else "✘"
                color = "green" if status else "red"
                ctk.CTkLabel(
                    status_frame, text=f"{icon} {['PDF','Anki','Résumé','Rappel'][j]}",
                    font=("Helvetica", 12), text_color=color
                ).pack(side="left", padx=3)

            # ----- Col 5 — Actions -----
            actions_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
            actions_frame.grid(row=i, column=5, padx=4, pady=6, sticky="nsew")
            btn_container = ctk.CTkFrame(actions_frame, fg_color="transparent")
            btn_container.pack(anchor="center")

            actions_all = self.actions_manager.get_available_actions(course, is_college=True)
            menu_actions = [a for a in actions_all if a in ("resume", "anki", "rappel")]
            if menu_actions:
                cmd = lambda c=course, acts=menu_actions: self.actions_manager.open_actions_menu(c, acts, is_college=True)
            else:
                cmd = lambda c=course: self.on_next_action(c)

            ctk.CTkButton(
                btn_container,
                text="",
                image=self.action_icon,
                width=36,
                height=30,
                corner_radius=6,
                fg_color="transparent",
                hover_color="#E6E6E6",
                command=cmd,
            ).pack(side="left", padx=0)

        self.offset += len(batch)
        self._build_load_more_button()  # met à jour la visibilité du bouton

    # ---------- DnD PDF (thread-safe) ----------
    def _on_drop_item_async(self, files: list[str], page_id: str):
        """Callback DnD (thread Tk) → délègue au worker + exclusif."""
        run_io(run_exclusive, "drop.item.pdf", self._link_pdf_to_item_bg, files, page_id)

    def _link_pdf_to_item_bg(self, files: list[str], page_id: str):
        """THREAD BG: I/O lourde + Notion. Aucune action Tk ici."""
        pdfs = [p for p in files if isinstance(p, str) and p.lower().endswith(".pdf")]
        if not pdfs:
            post(lambda: messagebox.showinfo("Format non supporté", "Dépose un fichier PDF."))
            return

        path = pdfs[0]
        ok, msg = self.actions_manager.link_pdf_to_item(page_id, path)
        if ok:
            # Feedback + synchro
            post(self._after_drop_success)
            # Emet un événement pour d'autres vues (QuickStats/Backlog) immédiatement
            post(lambda pid=page_id: emit("notion:page_updated", pid))
            # Poll le CACHE pour attendre pdf_ok/url_pdf, puis refresh
            run_io(run_exclusive, "poll.pdf.url", self._poll_pdf_and_refresh_bg, page_id)
        else:
            post(lambda m=msg: messagebox.showerror("Erreur", f"Échec: {m}"))

    def _poll_pdf_and_refresh_bg(self, page_id: str, tries: int = 10, delay_s: float = 0.35):
        """
        THREAD BG: re-interroge le CACHE via DataManager (et non Notion).
        Dès que pdf_ok + url_pdf sont visibles, on rafraîchit l'UI.
        """
        for _ in range(tries):
            try:
                parsed = self.data_manager.get_parsed_courses(mode="college") or []
                row = next((c for c in parsed if c.get("id") == page_id), None)
                if row and row.get("pdf_ok") and row.get("url_pdf"):
                    post(self._refresh_light)
                    post(lambda pid=page_id: emit("notion:page_updated", pid))
                    return
            except Exception:
                pass
            time.sleep(delay_s)
        post(self._refresh_light)
        post(lambda pid=page_id: emit("notion:page_updated", pid))

    def _after_drop_success(self):
        """THREAD UI: feedback + refresh."""
        try:
            if hasattr(self, "notify") and callable(getattr(self, "notify")):
                self.notify("PDF ajouté", subtitle="Lien Notion (Collège) mis à jour")
        finally:
            self._after_notion_update()

    # ------------------------------ Actions — Collège → PDF ------------------------------
    def on_next_action(self, course: dict):
        if not course:
            return
        action = self.actions_manager.get_next_action(course, is_college=True)
        if action in ("ue", "ue_college"):
            self._link_college_flow(course)
        elif action == "pdf":
            self._link_pdf_flow(course)
        else:
            self.actions_manager.do_action(course, action, is_college=True)

    def _link_college_flow(self, course: dict):
        colleges = self._pick_colleges()
        if colleges is None:
            return
        if hasattr(self.notion_api, "set_course_colleges"):
            self.notion_api.set_course_colleges(course["id"], colleges)
        # Notifie les autres vues (ex: stats)
        emit("notion:page_updated", course["id"])
        self._after_notion_update()
        self._link_pdf_flow(course)

    def _link_pdf_flow(self, course: dict):
        course_name = (course.get("nom") or "").strip()
        item = course.get("item")
        parts = []
        if item not in (None, ""):
            parts.append(str(item))
        if course_name:
            parts.append(course_name)
        initial_query = " ".join(parts) or None

        college_name = self._clean_college_name(course.get("college") or "")
        drive = DriveSync()

        specific_files = []
        if college_name:
            specific_files = drive.list_pdfs_by_college(
                college_name=college_name,
                item_number=item,
                course_name=course_name,
            ) or []
        specific_files = specific_files[:5]

        folder_hint = f"Collège / {college_name} / ITEMS" if college_name else None
        best_matches = (
            [{"name": f["name"], "webViewLink": f.get("webViewLink") or f.get("webContentLink") or f.get("link"),
              "folder": folder_hint}
             for f in specific_files]
            if specific_files else []
        )

        if hasattr(drive, "search_pdf_medecine"):
            search_cb = lambda q: drive.search_pdf_medecine(q)
        else:
            search_cb = (lambda q, col=college_name: drive.search_pdf_in_college(col, q)) if college_name else (
                lambda q: [])

        url = PDFSelector.open(
            self.winfo_toplevel(),
            search_callback=search_cb,
            initial_query=initial_query,
            best_matches=best_matches,
            folder_hint=folder_hint,
            show_search=True,
        )
        if not url:
            return

        # --- Normalisation: chemin local -> file:// ---
        try:
            import os
            from pathlib import Path
            if os.path.isabs(url) and url.lower().endswith(".pdf"):
                url = Path(url).resolve().as_uri()  # file:///G:/...
        except Exception:
            pass

        # --- Patch immédiat du cache + objet local (affichage instantané) ---
        try:
            self.data_manager.update_url_local(course["id"], "URL PDF COLLEGE", url)
        except Exception:
            pass

        course["url_pdf"] = url
        course["pdf_ok"] = True
        self._refresh_light()
        emit("notion:page_updated", course["id"])  # ← invalide les vues qui écoutent

        # --- Push Notion en arrière-plan avec un client neuf (évite le partage entre threads) ---
        import threading
        def _push_notion():
            try:
                from services.notion_client import NotionAPI
                na = NotionAPI()
                if hasattr(na, "attach_pdf_to_course"):
                    na.attach_pdf_to_course(course["id"], url, is_college=True)
                else:
                    na.update_course_pdf(course["id"], url, is_college=True)
            except Exception:
                # on évite de bloquer l'UI en cas d'erreur; le cache local est déjà à jour
                pass
            finally:
                # relance une sync légère + refresh une fois fini
                emit("notion:page_updated", course["id"])
                self._after_notion_update()

        threading.Thread(target=_push_notion, daemon=True).start()

    @staticmethod
    def refresh_static():
        if CollegeView._current_instance:
            CollegeView._current_instance._refresh_courses()
            CollegeView._current_instance._build_ui()

    def _refresh_courses_and_ui(self):
        def _done():
            self.after(0, lambda: (self._refresh_courses(), self._build_ui()))
        try:
            self.data_manager.sync_background(on_done=_done)
        except TypeError:
            self.data_manager.sync_with_notion()
            self._refresh_courses()
            self._build_ui()

    # ------------------------------ Popups (ajout) ------------------------------
    def _open_add_course_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Ajouter un cours collège")
        modal.geometry("700x600")
        modal.transient(self)
        modal.grab_set()
        modal.resizable(False, False)

        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - 350
        y = self.winfo_rooty() + (self.winfo_height() // 2) - 300
        modal.geometry(f"+{x}+{y}")

        ctk.CTkLabel(modal, text="Ajouter un cours collège", font=("Helvetica", 18, "bold")).pack(pady=(15, 10))

        ctk.CTkLabel(modal, text="Nom du cours :", font=("Helvetica", 14)).pack(pady=(10, 0))
        entry_nom = ctk.CTkEntry(modal, width=300); entry_nom.pack()

        ctk.CTkLabel(modal, text="Numéro de l'ITEM :", font=("Helvetica", 14)).pack(pady=(15, 0))
        entry_item = ctk.CTkEntry(modal, width=100); entry_item.pack()

        ctk.CTkLabel(modal, text="Collèges :", font=("Helvetica", 14)).pack(pady=(20, 5))

        def nettoyer_nom(nom): return re.sub(r"^[^\w\s]+", "", nom).strip()

        all_colleges_raw = self.data_manager.get_all_colleges()
        college_mapping = {nettoyer_nom(c): c for c in all_colleges_raw}
        cleaned_colleges = sorted(college_mapping.keys())

        scroll_wrapper = ctk.CTkScrollableFrame(modal, width=640, height=260)
        scroll_wrapper.pack(pady=(0, 10), padx=10)

        selected_colleges = {}
        cols = 3
        for idx, label in enumerate(cleaned_colleges):
            var = ctk.BooleanVar()
            cb = ctk.CTkCheckBox(scroll_wrapper, text=label, variable=var)
            row, col = divmod(idx, cols)
            cb.grid(row=row, column=col, sticky="w", padx=10, pady=4)
            selected_colleges[label] = var

        def valider():
            nom = entry_nom.get().strip()
            item = entry_item.get().strip()
            selected = [label for label, var in selected_colleges.items() if var.get()]

            if not nom or not item or not selected:
                messagebox.showerror("Erreur", "Merci de remplir tous les champs.")
                return

            try:
                item_int = int(item)
            except ValueError:
                messagebox.showerror("Erreur", "Le numéro de l'ITEM doit être un entier.")
                return

            real_colleges = [college_mapping[label] for label in selected]

            properties = {
                "Cours": {"title": [{"text": {"content": nom}}]},
                "ITEM": {"number": item_int},
                "Collège": {"multi_select": [{"name": c} for c in real_colleges]},
            }

            self.notion_api.add_cours(title=nom, properties=properties)

            def _done():
                self.after(0, self._refresh_courses_and_ui)

            try:
                self.data_manager.sync_background(on_done=_done)
            except TypeError:
                self.data_manager.sync_with_notion()
                self._refresh_courses_and_ui()

            modal.destroy()
            emit("notion:page_updated", "<new>")  # signal léger
            messagebox.showinfo("Ajout réussi", "Le cours a bien été ajouté.")

        ctk.CTkButton(
            modal,
            text="Valider",
            command=valider,
            fg_color=COLORS["accent"]
        ).pack(pady=(10, 20))

    # --- Sélecteur Collèges ---
    def _pick_colleges(self) -> list[str] | None:
        choices = []
        if hasattr(self.notion_api, "get_all_college_choices"):
            choices = self.notion_api.get_all_college_choices() or []
        if not choices and hasattr(self.data_manager, "get_all_colleges"):
            choices = self.data_manager.get_all_colleges() or []

        selected: list[str] = []

        def _on_validate(res: list[str]):
            nonlocal selected
            selected = res
            dlg.destroy()

        dlg = CollegeDialogMultiSelect(self, colleges=choices, on_validate=_on_validate)
        self.wait_window(dlg)
        return selected or None

    def _after_notion_update(self):
        def _done():
            self.after(0, self._refresh_light)
        try:
            self.data_manager.sync_background(on_done=_done)
        except TypeError:
            self.data_manager.sync_with_notion()
            self._refresh_light()
