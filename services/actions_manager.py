# services/actions_manager.py
import os
import difflib
import json
from tkinter import messagebox, filedialog
import customtkinter as ctk
from ui.pdf_selector import PDFSelector
from ui.components import UEDialogSingleSelect, CollegeDialogMultiSelect
from services.logger import get_logger
import webbrowser
import pyperclip
import subprocess
from ui.styles import COLORS
import sys
import re
import unicodedata
from urllib.parse import urlparse, unquote
from services.profiler import profiled, span

from datetime import datetime, timedelta
from pathlib import Path

from services.google_calendar import GoogleCalendarClient
from config import GOOGLE_CALENDAR_ID, GOOGLE_TIMEZONE

# Drag & drop (optionnel)
try:
    from tkinterdnd2 import DND_FILES
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

logger = get_logger(__name__)

# Fichiers
DRIVE_MAP_FILE = os.path.join("data", "pdf_mapping.json")        # nom PDF -> URL Drive OU dict {"path": ..., "url": ...}
ID_PATH_MAP_FILE = os.path.join("data", "local_pdf_by_id.json")  # id Notion -> {semestre|college: chemin}
LOCAL_PDF_MAP = os.path.join("data", "local_pdf_map.json")       # filename -> chemin local
BASE_FOLDER = r"G:\Mon Drive\Médecine"


def _path_to_file_url(p: str) -> str:
    """
    Convertit un chemin local en URL file:/// portable (Windows → file:///G:/...).
    Laisse passer les http(s) intacts.
    """
    if not p:
        return ""
    s = str(p).strip()
    if s.startswith(("http://", "https://", "file://")):
        return s
    try:
        return Path(s).resolve().as_uri()
    except Exception:
        return s


class ActionsManager:
    # --- Garde‑fous URL ---
    _URL_FORBIDDEN = {None, "", "none", "null", "-"}

    def __init__(self, data_manager, notion_api, root, refresh_callback=None):
        self.data_manager = data_manager
        self.notion_api = notion_api
        self.root = root
        self.refresh_callback = refresh_callback
        # Maps
        self.drive_map = self._load_json_file(DRIVE_MAP_FILE) or {}
        self.id_path_map = self._load_json_file(ID_PATH_MAP_FILE) or {}
        self._migrate_id_path_map_schema()  # migration vers {semestre, college}
        self._ensure_local_pdf_map_loaded()
        # Scan incrémental
        logger.info(f"[pdf] BASE_FOLDER={os.path.abspath(BASE_FOLDER)} exists={os.path.isdir(BASE_FOLDER)}")
        before = len(getattr(self, "_local_pdf_map", {}))
        self._incremental_scan_all()
        after = len(getattr(self, "_local_pdf_map", {}))
        logger.info(f"[pdf] local_pdf_map: {before} -> {after} entrées")
        # Bootstrap id -> path non destructif
        self.bootstrap_local_pdf_by_id(add_new_only=True)

    # ------------------------ Helpers URL ------------------------
    def _is_valid_url(self, url: str) -> bool:
        if url is None:
            return False
        s = str(url).strip()
        if s.lower() in self._URL_FORBIDDEN:
            return False
        if s.startswith(("http://", "https://", "file://")):
            return True
        try:
            return os.path.isabs(s)  # accepte aussi les chemins locaux
        except Exception:
            return False


    # ---------- Normalisation mapping PDF ----------
    def _load_pdf_items(self) -> list[dict]:
        """
        Retourne une liste d'items normalisés pour PDFSelector :
        [{ "name": "<fichier.pdf>", "path": "<dossier lisible>", "url": "<file://... OU https://...>" }, ...]
        Accepte:
          - ancien format: { "Nom.pdf": "https://..." }
          - nouveau format: { "Nom.pdf": { "path": "G:\\...\\Nom.pdf" } }
        """
        items = []
        try:
            raw = self._load_json_file(DRIVE_MAP_FILE) or {}
        except Exception:
            raw = {}

        for name, val in raw.items():
            if not isinstance(name, str):
                continue

            # 1) cas ancien: valeur = URL
            if isinstance(val, str):
                url = val.strip()
                # petit dossier lisible pour l’UI
                path_display = os.path.dirname(url) if url.startswith(("http://", "https://")) else os.path.dirname(val)
                items.append({"name": name, "path": path_display, "url": url})
                continue

            # 2) cas nouveau: {"path": "G:\\...\\Nom.pdf"}
            if isinstance(val, dict):
                loc = val.get("path") or val.get("local_path") or ""
                if not loc:
                    continue
                # fabrique une URL file:// à partir du chemin Windows
                try:
                    from pathlib import Path
                    file_url = Path(loc).resolve().as_uri()  # -> file:///G:/...
                except Exception:
                    # fallback très tolérant
                    file_url = "file:///" + loc.replace("\\", "/").lstrip("/")

                # Dossier compact pour l’UI
                parent = os.path.dirname(loc)
                path_display = " / ".join([p for p in parent.replace("\\", "/").split("/") if p][-2:])

                items.append({"name": name, "path": path_display, "url": file_url})
                continue

        return items


    # ------------------------ Helpers cache ------------------------
    @staticmethod
    def _is_missing_college(value) -> bool:
        if isinstance(value, str):
            return value.strip() in ("", "-", "Aucun", "None")
        return not value

    def _refresh_after_sync(self, delay_ms: int = 500):
        try:
            self.data_manager.sync_background()
        except Exception:
            pass
        if self.refresh_callback:
            self.root.after(delay_ms, self.refresh_callback)

    def _cache_patch_url(self, course_id: str, field: str, url: str):
        if not self._is_valid_url(url):
            logger.warning(f"[cache] URL ignorée (vide/None) pour {field} sur {course_id}")
            return
        dm = self.data_manager
        if hasattr(dm, "update_url_local"):
            dm.update_url_local(course_id, field, url); return
        if hasattr(dm, "patch_properties"):
            dm.patch_properties(course_id, {field: {"url": url}}); return
        if hasattr(dm, "update_course_local"):
            dm.update_course_local(course_id, {field: url})

    def _cache_patch_relation(self, course_id: str, field: str, rel_ids: list[str]):
        dm = self.data_manager
        if hasattr(dm, "update_relation_local"):
            dm.update_relation_local(course_id, field, rel_ids); return
        if hasattr(dm, "patch_properties"):
            dm.patch_properties(course_id, {field: {"relation": [{"id": x} for x in rel_ids]}}); return
        if hasattr(dm, "update_course_local"):
            dm.update_course_local(course_id, {field: {"relation": [{"id": x} for x in rel_ids]}})

    def _cache_patch_multi_select(self, course_id: str, field: str, values: list[str]):
        dm = self.data_manager
        if hasattr(dm, "update_multi_select_local"):
            dm.update_multi_select_local(course_id, field, values); return
        payload = {field: {"multi_select": [{"name": v} for v in values]}}
        if hasattr(dm, "patch_properties"):
            dm.patch_properties(course_id, payload); return
        if hasattr(dm, "update_course_local"):
            dm.update_course_local(course_id, payload)

    def _cache_patch_checkbox(self, course_id: str, field: str, value: bool = True):
        dm = self.data_manager
        payload = {field: {"checkbox": value}}
        if hasattr(dm, "update_checkbox_local"):
            dm.update_checkbox_local(course_id, field, value); return
        if hasattr(dm, "patch_properties"):
            dm.patch_properties(course_id, payload); return
        if hasattr(dm, "update_course_local"):
            dm.update_course_local(course_id, payload)

    def _cache_patch_date(self, course_id: str, field: str, date_iso: str):
        dm = self.data_manager
        payload = {field: {"date": {"start": date_iso}}}
        if hasattr(dm, "update_date_local"):
            dm.update_date_local(course_id, field, date_iso); return
        if hasattr(dm, "patch_properties"):
            dm.patch_properties(course_id, payload); return
        if hasattr(dm, "update_course_local"):
            dm.update_course_local(course_id, payload)

    # ------------------------ Workflow général ------------------------
    def get_next_action(self, course, is_college: bool = False):
        if is_college:
            if self._is_missing_college(course.get("college")):
                return "ue_college"
        else:
            if not course.get("ue_ids"):
                return "ue"
        if not course.get("pdf_ok"):
            return "pdf"
        if is_college:
            if not course.get("resume_college_ok"): return "resume"
            if not course.get("anki_college_ok"):   return "anki"
            if not course.get("rappel_college_ok"): return "rappel"
        else:
            if not course.get("resume_ok"): return "resume"
            if not course.get("anki_ok"):   return "anki"
            if not course.get("rappel_ok"): return "rappel"
        return None

    def _has_pdf(self, course: dict, is_college: bool) -> bool:
        return bool(course.get("url_pdf") or course.get("pdf_ok"))

    def get_available_actions(self, course, is_college=False):
        if not self._has_pdf(course, is_college):
            return ["pdf"]
        if is_college:
            if self._is_missing_college(course.get("college")):
                return ["ue_college"]
        else:
            if not course.get("ue_ids"):
                return ["ue_college"]

        actions = []
        if is_college:
            if not course.get("resume_college_ok"): actions.append("resume")
            if not course.get("anki_college_ok"):   actions.append("anki")
            if not course.get("rappel_college_ok"): actions.append("rappel")
        else:
            if not course.get("resume_ok"): actions.append("resume")
            if not course.get("anki_ok"):   actions.append("anki")
            if not course.get("rappel_ok"): actions.append("rappel")
        return actions

    def open_actions_menu(self, course, actions, is_college=False):
        win = ctk.CTkToplevel(self.root)
        win.title("Choisir une action")
        win.geometry("300x260")
        win.configure(fg_color=COLORS["bg_light"])
        win.resizable(False, False)
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 130
        win.geometry(f"300x260+{x}+{y}")
        win.grab_set()

        ctk.CTkLabel(win, text="Actions disponibles", font=("Helvetica", 16, "bold"),
                     text_color=COLORS["text_primary"]).pack(pady=(20, 10))

        for action in actions:
            ctk.CTkButton(
                win,
                text=action.capitalize(),
                fg_color=COLORS["accent"],
                text_color="white",
                hover_color=COLORS["accent_hover"],
                command=lambda a=action: (self.do_action(course, a, is_college=is_college), win.destroy())
            ).pack(pady=6, padx=20, fill="x")

    def do_action(self, course, action_type, is_college=False, extra_data=None):
        import threading
        def _push(props: dict):
            def _run():
                try:
                    from services.notion_client import NotionAPI
                    NotionAPI().update_course_pdf(course["id"], url, is_college=is_college)
                except Exception as e:
                    logger.warning(f"Push Notion échoué pour {course['id']} : {e}")

            threading.Thread(target=_run, daemon=True).start()

        # -------- PDF --------
        if action_type == "pdf":
            if extra_data and "url_pdf" in extra_data:
                selected = extra_data["url_pdf"]
                field = "URL PDF COLLEGE" if is_college else "URL PDF"
                if not self._is_valid_url(selected):
                    messagebox.showwarning("PDF", "Aucune URL/chemin valide sélectionné — aucune modification n'a été faite.")
                    return
                # Cache local: on garde exactement ce qui a été choisi (peut être un chemin)
                try:
                    self._cache_patch_url(course["id"], field, selected)
                except Exception:
                    pass
                # Notion: on pousse uniquement une URL (http/https/file)
                push_url = selected
                if os.path.isabs(selected):
                    push_url = _path_to_file_url(selected)
                if str(push_url).startswith(("http://", "https://", "file://")):
                    _push({field: {"url": push_url}})
                else:
                    logger.info("[pdf] Valeur non-URL gardée en cache seulement (pas de push Notion)")
                course["url_pdf"] = push_url
                course["pdf_ok"] = True
                if self.refresh_callback: self.refresh_callback()
                return

            field = "URL PDF COLLEGE" if is_college else "URL PDF"
            self._link_pdf(course, field, is_college)
            return

        # -------- Liaison UE / Collège --------
        if action_type == "ue_college":
            if not is_college:
                if hasattr(self.root, "_ue_dialog_open") and self.root._ue_dialog_open:
                    return
                if hasattr(self.root, "_link_ue_flow"):
                    self.root._ue_dialog_open = True
                    try: self.root._link_ue_flow(course)
                    finally: self.root._ue_dialog_open = False
                return
            colleges = self.data_manager.get_all_colleges()
            if not colleges:
                messagebox.showinfo("Collège", "Aucune option de Collège trouvée."); return
            def _on_validate(selected: list[str]):
                if not selected:
                    messagebox.showwarning("Sélection vide", "Aucun collège sélectionné."); return
                self.data_manager.update_multi_select_local(course["id"], "Collège", selected)
                course["college"] = selected[0] if isinstance(selected, list) else selected
                if self.refresh_callback: self.refresh_callback()
                _push({"Collège": {"multi_select": [{"name": n} for n in selected]}})
            CollegeDialogMultiSelect(self.root, colleges, _on_validate); return

        # -------- Toggles collège --------
        if is_college and action_type in ("resume_college", "rappel_college", "anki_college"):
            prop_map = {
                "resume_college": ("Résumé collège", "resume_college_ok"),
                "rappel_college": ("Rappel collège", "rappel_college_ok"),
                "anki_college": ("Anki collège", "anki_college_ok"),
            }
            prop_name, local_key = prop_map[action_type]
            new_val = not course.get(local_key, False)
            self.data_manager.update_checkbox_local(course["id"], prop_name, new_val)
            course[local_key] = new_val
            if self.refresh_callback: self.refresh_callback()
            _push({prop_name: {"checkbox": new_val}}); return

        # -------- Générateurs --------
        if action_type == "resume":
            self.run_resume_via_chatgpt(course, is_college=is_college); return
        if action_type == "anki":
            self.run_anki_via_chatgpt(course, is_college=is_college); return
        if action_type == "rappel":
            self.action_rappel(course, is_college=is_college); return

        messagebox.showinfo("Action inconnue", f"Action non gérée: {action_type}")

    # ------------------------ Action: Rappel ------------------------
    def _parse_fr_date(self, s: str) -> datetime:
        s = re.sub(r"\D", "", (s or "").strip())  # garde seulement les chiffres
        if not re.fullmatch(r"\d{8}", s):
            raise ValueError("bad format")
        j, m, a = int(s[:2]), int(s[2:4]), int(s[4:])
        return datetime(a, m, j)

    def action_rappel(self, course: dict, is_college: bool = False) -> None:
        page_id = course.get("id") or course.get("notion_id")
        name = (course.get("nom") or course.get("title") or "Cours").strip()

        if is_college:
            done_prop = "Rappel fait collège"
            d1_prop = "Date 1ère lecture collège"
            j_props = [("Lecture J3 collège", 3), ("Lecture J7 collège", 7), ("Lecture J14 collège", 14),
                       ("Lecture J30 collège", 30)]
            title_prefix = f"ITEM {name} Rappel"
            local_flag = "rappel_college_ok"
            gcal_color = "10"  # Basilic
        else:
            done_prop = "Rappel fait"
            d1_prop = "Date 1ère lecture"
            j_props = [("Lecture J3", 3), ("Lecture J7", 7), ("Lecture J14", 14), ("Lecture J30", 30)]
            title_prefix = f"Cours {name} Rappel"
            local_flag = "rappel_ok"
            gcal_color = "2"  # Sauge

        if course.get(local_flag) or course.get(done_prop):
            messagebox.showinfo("Rappel", "Rappel déjà effectué.")
            return

        dlg = ctk.CTkInputDialog(title="Date 1re lecture", text="Entre la date au format JJMMAAAA (ex: 14082025) :")
        s = dlg.get_input()
        if not s:
            return
        try:
            first_dt = self._parse_fr_date(s)  # datetime
        except Exception:
            messagebox.showerror("Format invalide", "Format attendu : JJMMAAAA (ex: 14082025).")
            return

        props = {
            done_prop: {"checkbox": True},
            d1_prop: {"date": {"start": first_dt.date().isoformat()}},
        }
        reminders = []
        for prop, delta in j_props:
            day_iso = (first_dt + timedelta(days=delta)).date().isoformat()
            props[prop] = {"date": {"start": day_iso}}
            reminders.append((delta, day_iso))

        try:
            self.notion_api.client.pages.update(page_id=page_id, properties=props)
        except Exception as e:
            logger.error(f"Notion update failed (rappel): {e}")
            messagebox.showerror("Notion", f"Échec mise à jour Notion : {e}")
            return

        try:
            self._cache_patch_checkbox(page_id, done_prop, True)
            self._cache_patch_date(page_id, d1_prop, first_dt.date().isoformat())
            for prop, delta in j_props:
                day_iso = (first_dt + timedelta(days=delta)).date().isoformat()
                self._cache_patch_date(page_id, prop, day_iso)
        except Exception:
            pass

        course[local_flag] = True

        if messagebox.askyesno("Google Calendar", "Créer des événements de rappel à 07:00 ?"):
            try:
                gcal = GoogleCalendarClient()
                for delta, iso_day in reminders:
                    start_dt = datetime.strptime(iso_day + " 07:00", "%Y-%m-%d %H:%M")
                    try:
                        gcal.create_event(
                            calendar_id=GOOGLE_CALENDAR_ID,
                            title=f"{title_prefix} J{delta}",
                            start_dt=start_dt,
                            duration_minutes=30,
                            timezone=GOOGLE_TIMEZONE,
                            color_id=gcal_color,
                        )
                    except TypeError:
                        gcal.create_event(
                            calendar_id=GOOGLE_CALENDAR_ID,
                            title=f"{title_prefix} J{delta}",
                            start_dt=start_dt,
                            duration_minutes=30,
                            timezone=GOOGLE_TIMEZONE,
                        )
                messagebox.showinfo("Google Calendar", "Rappels créés.")
            except Exception as e:
                logger.error(f"Google Calendar error: {e}")
                messagebox.showerror("Google Calendar", f"Erreur création événements : {e}")

        if self.refresh_callback:
            self.refresh_callback()

    # ------------------------ Liaison PDF ------------------------
    def _link_pdf(self, course, target_field, is_college):
        """
        Ouvre le PDFSelector (nouvelle API) en s’appuyant sur pdf_mapping.json,
        puis écrit l’URL (file:// ou http(s)) dans le cache local et dans Notion.
        """
        # Prépare la liste normalisée pour le sélecteur
        pdf_items = self._load_pdf_items()
        if not pdf_items:
            messagebox.showwarning(
                "Mapping vide",
                "Aucun PDF indexé. Lance 'Scanner les PDF' ou vérifie data/pdf_mapping.json."
            )
            return

        # callback simple: filtrage par sous-chaîne sur le nom (coté UI)
        def _search_cb(query: str) -> list[dict]:
            q = (query or "").strip().lower()
            if not q:
                return pdf_items
            return [it for it in pdf_items if q in it["name"].lower()]

        # requête initiale = nom du cours (améliore la sélection)
        course_title = (course.get("nom") or course.get("title") or "").strip()

        # Ouvre le sélecteur (nouvelle API)
        try:
            selected_url = PDFSelector.open(
                self.root,
                search_callback=_search_cb,
                initial_query=course_title or None,
                best_matches=[it["name"] for it in pdf_items],  # juste pour un affichage initial pertinent
                show_search=True,
                folder_hint=None,
            )
        except TypeError:
            # Compat si l'ancienne signature était encore importée
            dlg = PDFSelector(
                self.root,
                search_callback=_search_cb,
                initial_query=course_title or None,
                best_matches=[it["name"] for it in pdf_items],
                show_search=True,
                folder_hint=None,
            )
            self.root.wait_window(dlg)
            selected_url = getattr(dlg, "result_url", None)

        if not selected_url:
            return

        # Le sélecteur peut renvoyer un chemin local OU déjà une URL.
        # On normalise: si c’est un chemin -> on le convertit en file://
        url = str(selected_url).strip()
        if os.path.isabs(url) and url.lower().endswith(".pdf"):
            try:
                from pathlib import Path
                url = Path(url).resolve().as_uri()
            except Exception:
                url = "file:///" + url.replace("\\", "/").lstrip("/")

        # Sécurité
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
            messagebox.showwarning("PDF", "La sélection ne ressemble pas à une URL/chemin valide.")
            return

        # Patch LOCAL immédiat (affichage instantané côté UI)
        try:
            self._cache_patch_url(course["id"], target_field, url)
        except Exception:
            pass

        course["url_pdf"] = url
        course["pdf_ok"] = True

        # Push Notion (champ URL direct)
        try:
            self.notion_api.update_course_pdf(course["id"], url, is_college=is_college)
        except Exception as e:
            logger.warning(f"Push Notion échoué pour {course['id']} : {e}")

        messagebox.showinfo("PDF ajouté", f"PDF lié.")
        if self.refresh_callback:
            self.refresh_callback()
        self._refresh_after_sync()

    # ------------------------ Utilitaires ------------------------
    def _copy_to_clipboard(self, text: str) -> None:
        try:
            pyperclip.copy(text)
        except Exception:
            try:
                self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
            except Exception as e:
                logger.error(f"Clipboard error: {e}")

    def _open_in_explorer(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                if os.path.exists(path):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                else:
                    subprocess.Popen(["explorer", os.path.normpath(os.path.dirname(path))])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as e:
            logger.warning(f"Explorer open failed: {e}")

    def _display_folder_from_path(self, p: Path) -> str:
        parts = p.parts
        if len(parts) >= 3:
            return " / ".join(parts[-3:-1])
        return str(p.parent)

    # -------- Résolution chemin local pour Anki/Résumé --------
    def _get_pdf_path(self, course: dict, is_college: bool = False) -> str | None:
        cid = course.get("id") or course.get("notion_id")

        # 0) lecture directe par vue si déjà mappé
        if cid and cid in self.id_path_map and isinstance(self.id_path_map[cid], dict):
            p0 = self.id_path_map[cid].get(self._view_key(is_college))
            if p0 and os.path.exists(p0):
                return p0

        # 1) URL -> filename -> index local (avec re-scan si besoin)
        url = course.get("url_pdf") or course.get("URL PDF") or course.get("URL PDF COLLEGE")
        if url:
            filename = None
            if getattr(self, "drive_map", None):
                for name, u in self.drive_map.items():
                    u0 = u.get("url") if isinstance(u, dict) else u
                    if u0 == url:
                        filename = name if name.lower().endswith(".pdf") else f"{name}.pdf"
                        break
            if not filename:
                base = unquote(os.path.basename(urlparse(url).path))
                if base.lower().endswith(".pdf") and base not in ("", "/"):
                    filename = base
            if filename:
                self._ensure_local_pdf_map_loaded()
                p = self._local_pdf_map.get(filename)
                if not p or not os.path.exists(p):
                    self._incremental_scan_all()
                    self._ensure_local_pdf_map_loaded()
                    p = self._local_pdf_map.get(filename)
                if p and os.path.exists(p):
                    if cid:
                        self.set_local_pdf_for(cid, p, is_college=is_college)
                    return p

        # 2) ancien format: id -> chemin str
        if cid and cid in self.id_path_map and isinstance(self.id_path_map[cid], str):
            p = self.id_path_map[cid]
            if isinstance(p, str) and os.path.exists(p):
                self.set_local_pdf_for(cid, p, is_college=self._is_in_college_tree(p))
                p2 = self.id_path_map[cid].get(self._view_key(is_college))
                if p2 and os.path.exists(p2):
                    return p2

        # 3) bootstrap ciblé pour ce cours
        self.bootstrap_local_pdf_by_id(add_new_only=True)
        if cid and cid in self.id_path_map and isinstance(self.id_path_map[cid], dict):
            p = self.id_path_map[cid].get(self._view_key(is_college))
            if p and os.path.exists(p):
                return p

        # 4) vue semestre: arbo dédiée
        if not is_college:
            sp = self._find_pdf_in_semester_tree(course)
            if sp and os.path.exists(sp):
                if cid:
                    self.set_local_pdf_for(cid, sp, is_college=False)
                return sp

        # 5) recherche contextuelle large
        ctx_path = self._find_pdf_by_context(course, is_college=is_college)
        if ctx_path and os.path.exists(ctx_path):
            if cid:
                self.set_local_pdf_for(cid, ctx_path, is_college=is_college)
            return ctx_path

        # 6) index local par filename, avec filtres
        self._ensure_local_pdf_map_loaded()
        title = (course.get("nom") or course.get("title") or "").strip()
        year = self._norm(course.get("annee") or course.get("niveau") or "")
        sem = self._norm(course.get("semestre") or "")
        ues = course.get("ue") or []
        ue = self._norm((ues[0] if isinstance(ues, list) and ues else ues) or "")

        def _candidates(filtered: bool) -> list[str]:
            if not self._local_pdf_map:
                return []
            res = []
            for fname, path in self._local_pdf_map.items():
                if filtered:
                    rp = self._norm(path)
                    if year and year not in rp:   continue
                    if sem and sem not in rp:     continue
                    if ue and ue not in rp:       continue
                res.append(fname)
            return res

        def _match(names: list[str]) -> str | None:
            if not title or not names:
                return None
            best = difflib.get_close_matches(title, names, n=1, cutoff=0.45)
            if not best:
                return None
            path = self._local_pdf_map.get(best[0])
            return path if path and os.path.exists(path) else None

        path = _match(_candidates(filtered=True))
        if not path:
            self._refresh_local_pdf_map()
            path = _match(_candidates(filtered=True))
        if not path:
            path = _match(_candidates(filtered=False))

        if path:
            if cid:
                self.set_local_pdf_for(cid, path, is_college=is_college)
            return path

        logger.info("[pdf] Fallback file dialog pour sélection manuelle")
        selected = filedialog.askopenfilename(
            initialdir=BASE_FOLDER,
            title="Choisir le PDF du cours",
            filetypes=[("PDF files", "*.pdf")]
        )
        if selected:
            if cid:
                self.set_local_pdf_for(cid, selected, is_college=is_college)
            else:
                fname = os.path.basename(selected)
                self._ensure_local_pdf_map_loaded()
                self._local_pdf_map[fname] = selected
                self._save_json_file(LOCAL_PDF_MAP, self._local_pdf_map)
        return selected

    def _mark_done(self, page_id: str, checkbox_prop: str | None, status_prop: str | None) -> None:
        payload = {"properties": {}}
        if checkbox_prop:
            payload["properties"][checkbox_prop] = {"checkbox": True}
        if status_prop:
            payload["properties"][status_prop] = {"status": {"name": "Fait"}}
        try:
            self.notion_api.client.pages.update(page_id=page_id, **payload)
        except Exception as e:
            logger.error(f"Notion update failed: {e}")

    def _build_resume_prompt(self, course: dict, pdf_path: str) -> str:
        return (
            "Je suis étudiant en DFGSM3. J'utilise des fiches pour réviser. "
            "Réalise cette fiche complète et synthétique en te basant uniquement sur ce document.\n"
            "Voici quelques consignes :\n"
            "- Ta fiche doit suivre la trame du document et être structurée similairement\n"
            "- Elle doit se concentrer sur les informations importantes, les données importantes et ce qu'il faut savoir par cœur (souvent en gras ou rouge dans le document par exemple)\n"
            "- Les phrases doivent être précises\n"
            "- Tu dois résumer l'ensemble du document et faire apparaître les termes clefs, valeurs, concepts"
        )

    def _build_anki_prompt(self, course: dict, pdf_path: str) -> str:
        return (
            "Je suis étudiant en DFGSM3. J'utilise Anki comme support pour révision. "
            "Réalise un deck complet niveau de difficulté expert (40 questions) de flashcards en te basant uniquement sur ce document. Voici quelques instructions :\n"
            "- Les flashcards doivent être simples, claires et se concentrer sur les informations importantes (elles sont souvent en gras) ou sur des données chiffrées\n"
            "- Les questions doivent être spécifiques et sans ambiguïté\n"
            "- Utilise un langage simple et direct pour que les cartes soient faciles à comprendre et à lire\n"
            "- Les réponses ne doivent contenir qu'un seul fait/nom/concept/terme clef ou valeur\n"
            "- Les questions doivent finir par un \"?\""
        )

    def run_resume_via_chatgpt(self, course: dict, is_college: bool = False) -> None:
        pdf_path = self._get_pdf_path(course, is_college=is_college)
        if not pdf_path:
            messagebox.showwarning("PDF manquant", "Aucun PDF sélectionné."); return
        prompt = self._build_resume_prompt(course, pdf_path)
        self._copy_to_clipboard(prompt)
        webbrowser.open("https://chatgpt.com/g/g-MrgKnTZbc-resume")
        self._open_in_explorer(pdf_path)
        if messagebox.askyesno("Résumé", "Prompt copié. GPT ouvert.\nGlisse le PDF puis Entrée.\n\nMarquer Résumé comme FAIT ?"):
            page_id = course.get("id") or course.get("notion_id")
            checkbox = "Résumé collège" if is_college else "Résumé"
            self._mark_done(page_id, checkbox_prop=checkbox, status_prop=None)
            try: self._cache_patch_checkbox(page_id, checkbox, True)
            except Exception: pass
            if is_college: course["resume_college_ok"] = True
            else:          course["resume_ok"] = True
            if self.refresh_callback: self.refresh_callback()

    def run_anki_via_chatgpt(self, course: dict, is_college: bool = False) -> None:
        pdf_path = self._get_pdf_path(course, is_college=is_college)
        if not pdf_path:
            messagebox.showwarning("PDF manquant", "Aucun PDF sélectionné."); return
        prompt = self._build_anki_prompt(course, pdf_path)
        self._copy_to_clipboard(prompt)
        webbrowser.open("https://chatgpt.com/g/g-pS0Pd0eoP-flashcards-generator-for-quizlet-anki-and-noji")
        self._open_in_explorer(pdf_path)
        if messagebox.askyesno("Anki", "Prompt copié. GPT ouvert.\nGlisse le PDF puis Entrée.\n\nMarquer Anki comme FAIT ?"):
            page_id = course.get("id") or course.get("notion_id")
            checkbox = "Anki collège" if is_college else "Anki"
            self._mark_done(page_id, checkbox_prop=checkbox, status_prop=None)
            try: self._cache_patch_checkbox(page_id, checkbox, True)
            except Exception: pass
            if is_college: course["anki_college_ok"] = True
            else:          course["anki_ok"] = True
            if self.refresh_callback: self.refresh_callback()

    # ------------------------ JSON utils ------------------------
    def _load_json_file(self, path: str) -> dict:
        import re, json as _json, os as _os
        if not _os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except _json.JSONDecodeError as e:
            logger.warning(f"Load JSON failed for {path}: {e}; tentative de réparation")
            try:
                raw = open(path, "r", encoding="utf-8").read()
                raw = raw.lstrip("\ufeff")
                fixed = raw
                while True:
                    new = re.sub(r',\s*([}\]])', r'\1', fixed)
                    if new == fixed: break
                    fixed = new
                data = _json.loads(fixed)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"{os.path.basename(path)} réparé et normalisé")
                return data
            except Exception as e2:
                logger.warning(f"Echec réparation {path}: {e2}")
                return {}
        except Exception as e:
            logger.warning(f"Load JSON failed for {path}: {e}")
            return {}

    def _save_json_file(self, path: str, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Save JSON failed for {path}: {e}")

    # --- Sources de cours ---
    def _list_all_courses(self) -> list[dict]:
        if hasattr(self.data_manager, "list_courses"):
            return self.data_manager.list_courses()
        if hasattr(self.data_manager, "get_all_courses"):
            return self.data_manager.get_all_courses()
        cache = getattr(self.data_manager, "cache", {})
        courses = cache.get("courses") or []
        if isinstance(courses, dict):
            return list(courses.values())
        return courses if isinstance(courses, list) else []

    # --- URL -> filename ---
    def _guess_filename_from_url(self, url: str) -> str | None:
        if not url:
            return None
        for name, u in (self.drive_map or {}).items():
            if isinstance(u, dict):
                u = u.get("url") or u.get("path") or ""
            if u == url:
                return name if name.lower().endswith(".pdf") else f"{name}.pdf"
        base = os.path.basename(urlparse(url).path)
        base = unquote(base)
        return base if base.lower().endswith(".pdf") and base not in ("", "/") else None

    def _path_from_local_index_by_filename(self, filename: str) -> str | None:
        self._ensure_local_pdf_map_loaded()
        path = self._local_pdf_map.get(filename)
        return path if path and os.path.exists(path) else None

    # ------------------------ Index disque ------------------------
    def _incremental_scan_all(self) -> None:
        self._ensure_local_pdf_map_loaded()
        if not os.path.isdir(BASE_FOLDER):
            logger.warning(f"[pdf] BASE_FOLDER introuvable: {BASE_FOLDER}"); return
        changed = False; count = 0
        for root, _, files in os.walk(BASE_FOLDER):
            for fn in files:
                if fn.lower().endswith(".pdf"):
                    full = os.path.join(root, fn); count += 1
                    if fn not in self._local_pdf_map:
                        self._local_pdf_map[fn] = full; changed = True
        logger.info(f"[pdf] PDFs vus sur disque: {count}")
        if changed:
            self._save_json_file(LOCAL_PDF_MAP, self._local_pdf_map)
            logger.info(f"[pdf] index mis à jour: {len(self._local_pdf_map)}")
        else:
            logger.info("[pdf] aucun nouveau PDF")

    def _ensure_local_pdf_map_loaded(self) -> None:
        if getattr(self, "_local_pdf_map", None) is None:
            self._local_pdf_map = self._load_json_file(LOCAL_PDF_MAP) or {}

    def _refresh_local_pdf_map(self) -> None:
        mp = {}
        if not os.path.isdir(BASE_FOLDER):
            logger.warning(f"BASE_FOLDER introuvable: {BASE_FOLDER}")
        else:
            for root, _, files in os.walk(BASE_FOLDER):
                for fn in files:
                    if fn.lower().endswith(".pdf"):
                        mp[fn] = os.path.join(root, fn)
        self._local_pdf_map = mp
        self._save_json_file(LOCAL_PDF_MAP, mp)

    # ------------------------ Recherche contextuelle ------------------------
    def _find_pdf_by_context(self, course: dict, is_college: bool) -> str | None:
        def _norm(s: str) -> str:
            return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
        def _first_text(v):
            if isinstance(v, list) and v: v = v[0]
            if isinstance(v, dict): return (v.get("name") or v.get("title") or v.get("plain_text") or "").strip()
            if isinstance(v, str): return v.strip()
            return ""
        def _in_college_tree(path: str) -> bool:
            p = os.path.normcase(os.path.abspath(path))
            roots = [os.path.join(BASE_FOLDER, n) for n in ("Collèges", "Colleges", "College")]
            for r in roots:
                r_abs = os.path.normcase(os.path.abspath(r))
                if p == r_abs or p.startswith(r_abs + os.sep): return True
            return False

        year = _norm(course.get("annee") or course.get("niveau") or "")
        sem = _norm(course.get("semestre") or "")
        ueval = course.get("ue") or []
        ue = _norm(_first_text(ueval))
        title = _norm(course.get("nom") or course.get("title") or "")
        if not title or not os.path.isdir(BASE_FOLDER): return None

        best_path, best_score = None, 0.0
        for root, _, files in os.walk(BASE_FOLDER):
            rl = _norm(root)
            if is_college:
                if not _in_college_tree(root): continue
            else:
                pass
            if year and not is_college and year not in rl:   continue
            if sem and not is_college and sem not in rl:     continue
            if ue and  not is_college and ue not in rl:      continue
            for fn in files:
                if not fn.lower().endswith(".pdf"): continue
                score = difflib.SequenceMatcher(None, os.path.splitext(_norm(fn))[0], title).ratio()
                if score > best_score:
                    best_score, best_path = score, os.path.join(root, fn)
        return best_path if best_score >= 0.55 else None

    def _norm(self, s: str) -> str:
        return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()

    def _is_in_college_tree(self, path: str) -> bool:
        try:
            p = os.path.normcase(os.path.abspath(path))
            bases = [os.path.join(BASE_FOLDER, n) for n in ("Collèges", "Colleges", "College")]
            for b in bases:
                b_abs = os.path.normcase(os.path.abspath(b))
                if p == b_abs or p.startswith(b_abs + os.sep): return True
        except Exception:
            pass
        return False

    def _best_match_dir(self, parent: str, token: str) -> str | None:
        cand, best, bt = None, 0.0, self._norm(token)
        try:
            for name in os.listdir(parent):
                p = os.path.join(parent, name)
                if not os.path.isdir(p): continue
                score = difflib.SequenceMatcher(None, self._norm(name), bt).ratio()
                if bt in self._norm(name): score += 0.2
                if score > best: best, cand = score, p
        except Exception:
            return None
        return cand

    def _find_pdf_in_semester_tree(self, course: dict) -> str | None:
        year = (course.get("annee") or course.get("niveau") or "").strip()
        sem = (course.get("semestre") or "").strip()
        ues = course.get("ue") or []
        ue = (ues[0] if isinstance(ues, list) and ues else ues or "").strip()
        title = (course.get("nom") or course.get("title") or "").strip()
        if not (year and sem and ue and title): return None
        if not os.path.isdir(BASE_FOLDER): return None

        ydir = self._best_match_dir(BASE_FOLDER, year)
        if not ydir: return None
        sdir = self._best_match_dir(ydir, sem)
        if not sdir: return None
        udir = self._best_match_dir(sdir, ue)
        if not udir: return None

        best_path, best = None, 0.0
        base = self._norm(os.path.splitext(title)[0])
        for root, _, files in os.walk(udir):
            for fn in files:
                if not fn.lower().endswith(".pdf"): continue
                score = difflib.SequenceMatcher(None, self._norm(os.path.splitext(fn)[0]), base).ratio()
                if score > best:
                    best, best_path = score, os.path.join(root, fn)
        return best_path if best >= 0.55 else None

    # ------------------------ Bootstrap id -> path ------------------------
    def bootstrap_local_pdf_by_id(self, add_new_only: bool = True) -> None:
        from services.profiler import span  # au cas où pas déjà importé en haut du fichier
        with span("pdf.scan"):
            self._ensure_local_pdf_map_loaded()
            courses = self._list_all_courses()
            if not courses:
                logger.info("[bootstrap] Aucun cours trouvé")
                return

            added, updated, stale = 0, 0, 0

            for c in courses:
                cid = c.get("id") or c.get("notion_id")
                if not cid:
                    continue

                existing = self.id_path_map.get(cid)
                if isinstance(existing, dict):
                    if (existing.get("semestre") and os.path.exists(existing.get("semestre"))) or \
                            (existing.get("college") and os.path.exists(existing.get("college"))):
                        continue
                    else:
                        stale += 1
                elif isinstance(existing, str):
                    if os.path.exists(existing):
                        pass
                    else:
                        stale += 1

                path = None

                # A) URL -> filename -> index local
                url = c.get("url_pdf") or c.get("URL PDF") or c.get("URL PDF COLLEGE")
                fname = self._guess_filename_from_url(url) if url else None
                if fname:
                    path = self._local_pdf_map.get(fname)
                    if path and not os.path.exists(path):
                        path = None

                # B) Vue semestre
                is_college = bool(c.get("college") or c.get("Collège"))
                if not path and not is_college:
                    path = self._find_pdf_in_semester_tree(c)
                    if path and not os.path.exists(path):
                        path = None

                # C) Contexte global
                if not path:
                    path = self._find_pdf_by_context(c, is_college=is_college)
                    if path and not os.path.exists(path):
                        path = None

                # D) Fallback: nom proche
                if not path and self._local_pdf_map:
                    title = (c.get("nom") or c.get("title") or "").strip()
                    if title:
                        names = list(self._local_pdf_map.keys())
                        best = difflib.get_close_matches(title, names, n=1, cutoff=0.55)
                        if best:
                            cand = self._local_pdf_map.get(best[0])
                            if cand and os.path.exists(cand):
                                path = cand

                if path:
                    entry = self.id_path_map.get(cid)
                    if not isinstance(entry, dict):
                        entry = {"semestre": None, "college": None}
                    key = "college" if self._is_in_college_tree(path) or is_college else "semestre"
                    if not entry.get(key):
                        entry[key] = path
                        if cid not in self.id_path_map:
                            added += 1
                        else:
                            updated += 1
                        self.id_path_map[cid] = entry

            if added or updated:
                self._save_json_file(ID_PATH_MAP_FILE, self.id_path_map)

            logger.info(f"[bootstrap] id→path ajoutés={added}, mis_à_jour={updated}, périmés_detectés={stale}")

    # ------------------------ Drag & Drop ------------------------
    def register_drop_target(self, widget, course: dict, is_college: bool = False):
        if not DND_AVAILABLE:
            return
        try:
            ok = widget.tk.call("package", "provide", "tkdnd")
            if not ok:
                return
        except Exception:
            return

        def _on_drop(event):
            raw = event.data or ""
            items, buf, in_brace = [], "", False
            for ch in raw:
                if ch == "{":
                    in_brace = True; buf = ""; continue
                if ch == "}":
                    in_brace = False; items.append(buf); buf = ""; continue
                if in_brace:
                    buf += ch
            if not items:
                items = raw.split()
            pdfs = [p for p in items if p.lower().endswith(".pdf") and os.path.exists(p)]
            if not pdfs:
                messagebox.showwarning("Drop", "Aucun fichier PDF valide détecté."); return
            path = pdfs[0]
            cid = course.get("id") or course.get("notion_id")
            self.set_local_pdf_for(cid, path, is_college=is_college)
            messagebox.showinfo("PDF lié", f"Chemin enregistré pour la vue {'Collège' if is_college else 'Semestre'}:\n{path}")

        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', _on_drop)
        except Exception:
            return

    # ------------------------ Helpers schéma multi-vues ------------------------
    def _migrate_id_path_map_schema(self):
        changed = False
        newmap = {}
        for cid, val in (self.id_path_map or {}).items():
            if isinstance(val, dict):
                newmap[cid] = {"semestre": val.get("semestre"), "college": val.get("college")}
                continue
            entry = {"semestre": None, "college": None}
            path = val
            if isinstance(path, str) and os.path.isabs(path):
                if self._is_in_college_tree(path):
                    entry["college"] = path
                else:
                    entry["semestre"] = path
                changed = True
            newmap[cid] = entry
        if newmap and newmap != self.id_path_map:
            self.id_path_map = newmap
            self._save_json_file(ID_PATH_MAP_FILE, self.id_path_map)

    def _view_key(self, is_college: bool) -> str:
        return "college" if is_college else "semestre"

    def set_local_pdf_for(self, course_id: str, path: str, is_college: bool):
        if not course_id or not path:
            return
        fname = os.path.basename(path)
        self._ensure_local_pdf_map_loaded()
        self._local_pdf_map[fname] = path
        self._save_json_file(LOCAL_PDF_MAP, self._local_pdf_map)
        entry = self.id_path_map.get(course_id)
        if not isinstance(entry, dict):
            entry = {"semestre": None, "college": None}
        entry[self._view_key(is_college)] = path
        self.id_path_map[course_id] = entry
        self._save_json_file(ID_PATH_MAP_FILE, self.id_path_map)

    # --------- Drop PDF helpers publics ---------
    def link_pdf_to_course(self, course_id: str, local_path: str) -> tuple[bool, str]:
        try:
            url = local_path  # ou DriveSync().upload_and_get_url(local_path)
            return self._set_pdf_url(page_id=course_id, url=url, is_college=False)
        except Exception as e:
            logger.exception("link_pdf_to_course failed")
            return False, str(e)

    def link_pdf_to_item(self, item_id: str, local_path: str) -> tuple[bool, str]:
        try:
            url = local_path  # ou DriveSync().upload_and_get_url(local_path)
            return self._set_pdf_url(page_id=item_id, url=url, is_college=True)
        except Exception as e:
            logger.exception("link_pdf_to_item failed")
            return False, str(e)

    def _set_pdf_url(self, page_id: str, url: str, is_college: bool) -> tuple[bool, str]:
        na = self.notion_api
        if hasattr(na, "attach_pdf_to_course"):
            na.attach_pdf_to_course(page_id, url, is_college=is_college)
            return True, url
        if hasattr(na, "update_course_pdf"):
            push_url = url
            if os.path.isabs(url):
                push_url = _path_to_file_url(url)
            na.update_course_pdf(page_id, push_url, is_college=is_college)
            return True, push_url
        msg = ("NotionAPI ne fournit ni 'attach_pdf_to_course' ni 'update_course_pdf'. "
               "Remplace l’appel précédent à 'update_page_property' par l’une de ces deux méthodes "
               "ou implémente-en une.")
        logger.error(msg)
        return False, msg
