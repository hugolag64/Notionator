# ui/semestre_view.py
from __future__ import annotations
import os, re, platform, subprocess, webbrowser, time, threading
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image

from .styles import COLORS
from services.notion_client import NotionAPI
from services.drive_sync import DriveSync
from services.actions_manager import ActionsManager, BASE_FOLDER
from ui.components import UEDialogSingleSelect
from ui.pdf_selector import PDFSelector
from utils.dnd import attach_drop  # DnD sur le titre de cours
from services.worker import run_io
from services.exclusive import run_exclusive
from utils.ui_queue import post
from utils.event_bus import emit  # ← NEW: diffusion d'événements

BATCH_SIZE = 15  # ← 15 items par page


class SemestreView(ctk.CTkFrame):
    def __init__(self, parent, semestre_num, data_manager, show_only_actions: bool = False):
        super().__init__(parent, fg_color=COLORS["bg_light"])
        self.semestre_num = semestre_num
        self.data_manager = data_manager
        self.show_only_actions = show_only_actions

        self.notion_api = NotionAPI()
        self.actions_manager = ActionsManager(
            data_manager=self.data_manager,
            notion_api=self.notion_api,
            root=self,
            refresh_callback=self._refresh_and_rebuild_ui,
        )

        self.action_icon = self._load_action_icon()
        self.offset = 0
        self.loaded_courses: list[dict] = []
        self._ue_dialog_open = False

        self._refresh_courses()
        self._build_ui()

    # --------------------------- Data
    def _has_actions(self, course: dict) -> bool:
        return not (course["pdf_ok"] and course["anki_ok"] and course["resume_ok"] and course["rappel_ok"])

    def _refresh_courses(self):
        all_cours = self.data_manager.get_parsed_courses(mode="semestre", semestre_num=self.semestre_num) or []
        if self.show_only_actions:
            all_cours = [c for c in all_cours if self._has_actions(c)]
        self.filtered_courses = all_cours
        self.loaded_courses = self.filtered_courses[:BATCH_SIZE]
        self.offset = len(self.loaded_courses)

    def _load_more(self):
        next_offset = self.offset + BATCH_SIZE
        self.loaded_courses = self.filtered_courses[:next_offset]
        self.offset = len(self.loaded_courses)
        self._build_ui()

    # --------------------------- UI
    def _build_ui(self):
        for w in self.winfo_children():
            w.destroy()

        # Titre + bouton [+]
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(fill="x", pady=(16, 6))
        title_text = "Tous les cours" if self.semestre_num == "all" else f"Semestre {self.semestre_num}"
        ctk.CTkLabel(
            title_frame, text=title_text, font=("Helvetica", 28, "bold"), text_color=COLORS["accent"]
        ).pack(side="left", padx=(10, 0))

        ctk.CTkButton(
            title_frame, text="+", width=40, height=40, font=("Helvetica", 22, "bold"),
            fg_color=COLORS["accent"], text_color="white", corner_radius=20,
            command=self._open_add_course_modal,
        ).pack(side="right", padx=(0, 10))

        # Conteneur principal
        self.container = ctk.CTkFrame(self, fg_color=COLORS["bg_light"])
        self.container.pack(padx=30, pady=10, fill="both", expand=True)

        weights = [4, 3, 3, 2]
        for col, w in enumerate(weights):
            self.container.grid_columnconfigure(col, weight=w, uniform="col")
        self.container.grid_rowconfigure(1, weight=1)  # la zone scrollable prend l’espace

        # En-tête
        header = ctk.CTkFrame(self.container, fg_color="#BFBFBF", corner_radius=8)
        header.grid(row=0, column=0, columnspan=4, sticky="nsew", pady=(0, 4))
        for col, text in enumerate(["Cours", "UE", "Statut", "Actions"]):
            ctk.CTkLabel(
                header, text=text, font=("Helvetica", 16, "bold"),
                text_color=COLORS["text_primary"], anchor="center"
            ).grid(row=0, column=col, padx=4, pady=8, sticky="nsew")
            header.grid_columnconfigure(col, weight=weights[col], uniform="col")

        # Corps scrollable
        self.content = ctk.CTkScrollableFrame(self.container, fg_color=COLORS["bg_light"], corner_radius=0)
        self.content.grid(row=1, column=0, columnspan=4, sticky="nsew")
        for col, w in enumerate(weights):
            self.content.grid_columnconfigure(col, weight=w, uniform="col")

        def level_for(label: str) -> str:
            m = re.search(r"(\d+)", label or "")
            n = int(m.group(1)) if m else None
            if not n:
                return "DFGSM2"
            return ("DFGSM2" if 3 <= n <= 4 else
                    "DFGSM3" if 5 <= n <= 6 else
                    "DFASM1" if 7 <= n <= 8 else
                    "DFASM2" if 9 <= n <= 10 else
                    "DFASM3")

        status_labels = ["PDF", "Anki", "Résumé", "Rappel"]

        for i, course in enumerate(self.loaded_courses):
            # Cours (label cliquable si URL)
            text_color = "#0078D7" if course["pdf_ok"] else COLORS["text_primary"]
            lbl = ctk.CTkLabel(
                self.content,
                text=course["nom"],
                font=("Helvetica", 14),
                text_color=text_color,
                anchor="center",
                wraplength=250,
                fg_color="transparent",
            )
            lbl.grid(row=i, column=0, padx=4, pady=6, sticky="nsew")

            # Lien si URL présente
            if course["pdf_ok"] and course.get("url_pdf"):
                url = course["url_pdf"]
                lbl.bind("<Enter>", lambda e, w=lbl: w.configure(fg_color="#E9EEF5", cursor="hand2"))
                lbl.bind("<Leave>", lambda e, w=lbl: w.configure(fg_color="transparent", cursor=""))
                lbl.bind("<Button-1>", lambda e, link=url: webbrowser.open(link))

            # DnD PDF sur le titre du cours (callback thread-safe)
            attach_drop(lbl, on_files=lambda files, cid=course["id"]: self._on_drop_course_async(files, cid))

            # UE (cliquable pour ouvrir le dossier local)
            ue_text = ", ".join(course.get("ue") or []) if course.get("ue") else "Aucune UE"
            ue_lbl = ctk.CTkLabel(self.content, text=ue_text, font=("Helvetica", 14),
                                  text_color=COLORS["text_secondary"], anchor="center")
            ue_lbl.grid(row=i, column=1, padx=4, pady=6, sticky="nsew")
            if course.get("ue"):
                semestre_label = course.get("semestre", "").strip()
                niveau = level_for(semestre_label)
                ue_name = course["ue"][0].strip()
                ue_path = os.path.join(BASE_FOLDER, niveau, semestre_label, ue_name)

                def _open_folder(path=ue_path):
                    if not os.path.isdir(path):
                        messagebox.showinfo("Dossier introuvable", f"Dossier inexistant:\n{path}")
                        return
                    if platform.system() == "Windows":
                        os.startfile(path)  # type: ignore[attr-defined]
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["open", path])
                    else:
                        subprocess.Popen(["xdg-open", path])

                ue_lbl.bind("<Enter>", lambda e, w=ue_lbl: w.configure(cursor="hand2", text_color=COLORS["accent"]))
                ue_lbl.bind("<Leave>", lambda e, w=ue_lbl: w.configure(cursor="", text_color=COLORS["text_secondary"]))
                ue_lbl.bind("<Button-1>", lambda e, p=ue_path: _open_folder(p))

            # Statut
            stf = ctk.CTkFrame(self.content, fg_color="transparent")
            stf.grid(row=i, column=2, padx=40, pady=6, sticky="nsew")
            for j, ok in enumerate([course["pdf_ok"], course["anki_ok"], course["resume_ok"], course["rappel_ok"]]):
                icon, color = ("✔", "green") if ok else ("✘", "red")
                ctk.CTkLabel(stf, text=f"{icon} {status_labels[j]}", font=("Helvetica", 12),
                             text_color=color).pack(side="left", padx=3)

            # Actions
            af = ctk.CTkFrame(self.content, fg_color="transparent")
            af.grid(row=i, column=3, padx=4, pady=6, sticky="nsew")
            btnc = ctk.CTkFrame(af, fg_color="transparent")
            btnc.pack(anchor="center")

            actions_all = self.actions_manager.get_available_actions(course, is_college=False)
            menu_actions = [a for a in actions_all if a in ("resume", "anki", "rappel")]

            if menu_actions:
                cmd = lambda c=course, acts=menu_actions: self.actions_manager.open_actions_menu(c, acts, is_college=False)
            else:
                cmd = lambda c=course: self.on_next_action(c)

            ctk.CTkButton(
                btnc, text="", image=self.action_icon, width=36, height=30,
                corner_radius=6, fg_color="transparent", hover_color="#E6E6E6",
                command=cmd
            ).pack(side="left", padx=5)

        # Bouton "Charger plus"
        if hasattr(self, "load_more_btn") and self.load_more_btn.winfo_exists():
            self.load_more_btn.destroy()
        if self.offset < len(self.filtered_courses):
            self.load_more_btn = ctk.CTkButton(
                self.container, text="Charger plus", width=150, height=40,
                fg_color=COLORS["accent"], text_color="white", corner_radius=20,
                command=self._load_more
            )
            self.load_more_btn.grid(row=2, column=0, columnspan=4, pady=(10, 6))

    # ---------- DnD : traitement en arrière-plan + UI via post()
    def _on_drop_course_async(self, files: list[str], course_id: str):
        """Callback DnD (thread Tk) -> délègue au worker + exclusif."""
        run_io(run_exclusive, "drop.pdf", self._link_pdf_to_course_bg, files, course_id)

    def _link_pdf_to_course_bg(self, files: list[str], course_id: str):
        """THREAD BG: I/O lourde + Notion. Aucune action Tk ici."""
        pdfs = [p for p in files if isinstance(p, str) and p.lower().endswith(".pdf")]
        if not pdfs:
            post(lambda: messagebox.showinfo("Format non supporté", "Dépose un fichier PDF."))
            return

        path = pdfs[0]
        ok, msg = self.actions_manager.link_pdf_to_course(course_id, path)
        if ok:
            # feedback UI + sync + event immédiat
            post(self._after_drop_success)
            post(lambda cid=course_id: emit("notion:page_updated", cid))
            # poll le cache local et rafraîchir quand l'URL apparaît
            run_io(run_exclusive, "poll.semestre.pdf", self._poll_pdf_and_refresh_bg, course_id)
        else:
            post(lambda m=msg: messagebox.showerror("Erreur", f"Échec: {m}"))

    def _poll_pdf_and_refresh_bg(self, course_id: str, tries: int = 10, delay_s: float = 0.35):
        """
        THREAD BG: re-interroge le cache local (DataManager) jusqu'à voir pdf_ok + url_pdf.
        Dès que c'est visible, on refresh l'UI côté thread Tk.
        """
        for _ in range(tries):
            try:
                pages = self.data_manager.get_parsed_courses(mode="semestre", semestre_num=self.semestre_num) or []
                row = next((c for c in pages if c.get("id") == course_id), None)
                if row and row.get("pdf_ok") and row.get("url_pdf"):
                    post(self._refresh_and_rebuild_ui)
                    post(lambda cid=course_id: emit("notion:page_updated", cid))
                    return
            except Exception:
                pass
            time.sleep(delay_s)
        # Fallback
        post(self._refresh_and_rebuild_ui)
        post(lambda cid=course_id: emit("notion:page_updated", cid))

    def _after_drop_success(self):
        """THREAD UI: feedback + refresh."""
        try:
            if hasattr(self, "refresh") and callable(getattr(self, "refresh")):
                self.refresh()
            if hasattr(self, "notify") and callable(getattr(self, "notify")):
                self.notify("PDF ajouté", subtitle="Lien Notion mis à jour")
        finally:
            self._after_notion_update()

    # --------------------------- Actions
    def on_next_action(self, course: dict):
        if not course:
            return
        action = self.actions_manager.get_next_action(course, is_college=False)

        if action in ("ue", "ue_college"):
            if self._ue_dialog_open:
                return
            self._ue_dialog_open = True
            try:
                self._link_ue_flow(course)
            finally:
                self._ue_dialog_open = False
            return

        if action == "pdf":
            self._link_pdf_flow(course)
        else:
            self.actions_manager.do_action(course, action, is_college=False)

    def _link_ue_flow(self, course: dict):
        semestre_label = course.get("semestre") or (
            f"Semestre {self.semestre_num}" if self.semestre_num != "all" else None
        )
        ue_ids = self._pick_ue_ids(semestre_label)
        if not ue_ids:
            return

        if hasattr(self.notion_api, "set_course_ues"):
            self.notion_api.set_course_ues(course["id"], ue_ids)
        else:
            self.notion_api.set_course_ue_relation(course["id"], ue_ids[0])

        # Notifie & mini-sync
        emit("notion:page_updated", course["id"])
        self.data_manager.refresh_course(course["id"])
        self._link_pdf_flow(course)

    def _pick_ue_ids(self, semestre_label: str | None):
        if self._ue_dialog_open:
            return None
        self._ue_dialog_open = True
        try:
            try:
                ue_pages = self.notion_api.get_ue()
            except Exception:
                ue_pages = []

            def _ue_name(p):
                t = p.get("properties", {}).get("UE", {}).get("title", [])
                return t[0]["text"]["content"] if t and t[0].get("text") else "Sans titre"

            def _sem_name(p):
                return (p.get("properties", {}).get("Semestre", {}).get("select") or {}).get("name")

            items = [(p["id"], _ue_name(p)) for p in ue_pages if not semestre_label or _sem_name(p) == semestre_label]

            selected: list[str] = []

            def on_validate(ids: list[str]):
                nonlocal selected
                selected = ids
                dlg.destroy()

            dlg = UEDialogSingleSelect(self, ue_items=items, on_validate=on_validate)
            self.wait_window(dlg)
            return selected or None
        finally:
            self._ue_dialog_open = False

    def _link_pdf_flow(self, course: dict):
        # Prépare recherche Drive ciblée
        semestre_name = course.get("semestre") or (
            f"Semestre {self.semestre_num}" if self.semestre_num != "all" else None
        )
        ue_list = course.get("ue") or []
        ue_name = ue_list[0] if ue_list else None
        scope_label = f"{semestre_name} / {ue_name}" if (semestre_name and ue_name) else (semestre_name or "")

        drive = DriveSync()

        # Suggestions ciblées UE
        best_matches = []
        try:
            if semestre_name and ue_name:
                files = drive.list_pdfs_by_semestre_ue(
                    semestre_name, ue_name, course_name=(course.get("nom") or "").strip()
                ) or []
                for f in files[:5]:
                    url = f.get("webViewLink") or f.get("url") or f.get("path")
                    name = f.get("name")
                    if url and name:
                        best_matches.append({"name": name, "url": url, "folder": scope_label})
        except Exception:
            pass

        def _canon_folder(v):
            if not v:
                return ""
            if isinstance(v, (list, tuple)):
                v = "/".join(map(str, v))
            s = str(v).replace("\\", "/")
            parts = [p for p in s.split("/") if p]
            return " / ".join(parts[-2:]) if len(parts) >= 2 else s

        base_cb = getattr(drive, "search_pdf_medecine", None)

        if callable(base_cb):
            def cb(q: str):
                try:
                    results = base_cb(q) or []
                    out = []
                    for r in results:
                        if not isinstance(r, dict):
                            continue
                        folder = (r.get("folder") or r.get("folder_display") or r.get("path_display")
                                  or r.get("path") or r.get("parent") or r.get("directory") or r.get("parents"))
                        r["folder"] = _canon_folder(folder)
                        out.append(r)
                    return out
                except Exception:
                    return []
        else:
            def cb(q: str):
                try:
                    results = drive.search_pdf_in_semestre(semestre_name, q) or []
                    for r in results:
                        if isinstance(r, dict):
                            folder = (r.get("folder") or r.get("folder_display") or r.get("path_display")
                                      or r.get("path") or r.get("parent") or r.get("directory") or r.get("parents"))
                            r["folder"] = _canon_folder(folder)
                    return results
                except Exception:
                    return []

        url = PDFSelector.open(
            self.winfo_toplevel(),
            search_callback=cb,
            initial_query="",
            best_matches=best_matches,
            folder_hint=scope_label,
            show_search=True,
        )
        if not url:
            return

        # --- Normalisation: si chemin local → file:// ---
        try:
            if os.path.isabs(url) and url.lower().endswith(".pdf"):
                from pathlib import Path
                url = Path(url).resolve().as_uri()
        except Exception:
            pass

        # --- Patch immédiat du cache local pour affichage instantané ---
        try:
            self.data_manager.update_url_local(course["id"], "URL PDF", url)
        except Exception:
            pass

        # Rendu direct + event
        course["url_pdf"] = url
        course["pdf_ok"] = True
        self._refresh_and_rebuild_ui()
        emit("notion:page_updated", course["id"])

        # --- Push Notion en ARRIÈRE-PLAN (client neuf, pas d'appel Tk ici) ---
        def _push_notion():
            try:
                na = NotionAPI()
                if hasattr(na, "attach_pdf_to_course"):
                    na.attach_pdf_to_course(course["id"], url, is_college=False)
                else:
                    na.update_course_pdf(course["id"], url, is_college=False)
            except Exception:
                # éviter de bloquer l'UI en cas d'erreur réseau
                pass
            finally:
                emit("notion:page_updated", course["id"])
                self._after_notion_update()

        threading.Thread(target=_push_notion, daemon=True).start()

    def _open_add_course_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Ajouter un cours")
        modal.geometry("350x230")
        modal.transient(self)
        modal.grab_set()
        modal.resizable(False, False)

        self.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        modal.geometry(f"350x230+{px + (pw // 2) - 175}+{py + (ph // 2) - 115}")

        ctk.CTkLabel(modal, text="Nom du cours :", font=("Helvetica", 14)).pack(pady=(18, 5))
        entry_nom = ctk.CTkEntry(modal, width=230); entry_nom.pack()

        ctk.CTkLabel(modal, text="UE :", font=("Helvetica", 14)).pack(pady=(15, 5))
        all_ue = self.data_manager.get_all_ue() or []
        ue_for_semestre = []
        for ue in all_ue:
            props = ue.get("properties", {})
            sem = props.get("Semestre", {}).get("select", {}).get("name", "")
            if sem == f"Semestre {self.semestre_num}":
                title = props.get("UE", {}).get("title", [{}])
                name = title[0]["text"]["content"] if title and title[0].get("text") else "Sans titre"
                ue_for_semestre.append((ue["id"], name))

        ue_choices = [name for _, name in ue_for_semestre]
        selected_ue = ctk.StringVar(value=ue_choices[0] if ue_choices else "")
        ctk.CTkOptionMenu(modal, variable=selected_ue, values=ue_choices).pack(pady=(0, 10))

        def ajouter():
            nom = entry_nom.get().strip()
            ue_nom = selected_ue.get()
            if not nom or not ue_nom:
                messagebox.showerror("Erreur", "Merci de remplir tous les champs."); return
            ue_id = next((uid for uid, uname in ue_for_semestre if uname == ue_nom), None)
            if not ue_id:
                messagebox.showerror("Erreur", "UE sélectionnée invalide."); return

            new_course = {"UE": {"relation": [{"id": ue_id}]},
                          "Semestre": {"select": {"name": f"Semestre {self.semestre_num}"}}}
            NotionAPI().add_cours(title=nom, properties=new_course)

            def _done():
                self.after(0, self._refresh_and_rebuild_ui)
                emit("notion:page_updated", "<new>")

            try:
                self.data_manager.sync_background(on_done=_done)
            except TypeError:
                self.data_manager.sync_with_notion()
                self._refresh_and_rebuild_ui()
                emit("notion:page_updated", "<new>")

            modal.destroy(); messagebox.showinfo("Ajout réussi", "Le cours a bien été ajouté.")

        ctk.CTkButton(modal, text="Ajouter", command=ajouter, fg_color=COLORS["accent"]).pack(pady=(10, 0))

    # --------------------------- Utils
    def _load_action_icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "action.png")
        try:
            img = Image.open(icon_path).convert("RGBA").resize((16, 16))
            return ctk.CTkImage(light_image=img, size=(16, 16))
        except Exception as e:
            print("Erreur chargement action.png :", e); return None

    def _refresh_and_rebuild_ui(self):
        self._refresh_courses(); self._build_ui()

    def _after_notion_update(self):
        def _done(): self.after(0, self._refresh_and_rebuild_ui)
        try:
            self.data_manager.sync_background(on_done=_done)
        except TypeError:
            self.data_manager.sync_with_notion(); self._refresh_and_rebuild_ui()
