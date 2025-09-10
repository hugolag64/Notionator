# main.py
from __future__ import annotations

import sys
import time
import threading
import logging
from datetime import datetime

import faulthandler
faulthandler.enable()

import customtkinter as ctk

from ui.sidebar import Sidebar
from ui.dashboard import Dashboard
from ui.styles import COLORS, set_theme
from ui.semestre_view import SemestreView
from ui.college_view import CollegeView
from ui.loading_screen import LoadingScreen
from ui.search_view import SearchResultsView  # ← vue résultats de recherche
from ui.settings_view import SettingsView     # ← vue Paramètres
from ui.ai_dialog import AIAnswerDialog       # ← pour la recherche ChatGPT locale (fallback)

from services.boot import kickoff_background_tasks          # tâches lourdes (Notion/RAG) en arrière-plan
from services.local_search import ensure_index_up_to_date   # utilisé SEULEMENT par le bouton "Scanner les PDF"
from services.actions_manager import ActionsManager, BASE_FOLDER
from services.data_manager import DataManager
from services.notion_client import get_notion_client
from services.daily_todo_generator import DailyToDoGenerator
from services.logger import get_logger
from services.profiler import span, enable, render_report
from services.quick_summary import QuickSummaryUpdater      # Bilan rapide auto
from services.settings_store import settings                # ← préférences persistées
from config import DATABASE_COURS_ID as COURSES_DATABASE_ID, MAX_PDF_SIZE_KB

# --- Auto-scan PDF (léger) & exclusivité ---
from services.pdf_autoscan import AutoScanManager, _as_str_roots
from services.exclusive import run_exclusive

# --- Worker moderne (UI pump, IO/CPU pools, série par clé, callbacks UI) ---
from services.worker import (
    run_io, run_serial, then, then_finally, install_ui_pump, shutdown
)

# --- Raccourcis clavier ---
from services.shortcuts import ShortcutManager

logger = get_logger(__name__)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- Apparence sobre : lit la préférence et applique avec set_theme ---
        try:
            raw_mode = settings.get("appearance.theme", "system")
        except Exception:
            raw_mode = "system"
        norm_mode = str(raw_mode).lower()
        if norm_mode not in {"system", "light", "dark"}:
            norm_mode = "system"
        set_theme(norm_mode)

        # État global
        self.show_only_actions = True
        self.current_screen = "accueil"
        self.previous_screen = None
        self.current_search_query = ""

        # Services
        self.data_manager: DataManager | None = None
        self.actions_manager: ActionsManager | None = None
        self.quick_summary: QuickSummaryUpdater | None = None

        # Vues courantes / cache
        self.dashboard_view: Dashboard | None = None
        self._view_cache: dict[str, ctk.CTkFrame] = {}   # écran -> frame
        self._loading_flags: dict[str, bool] = {}       # écran -> charge en cours ?

        # Splash court
        self.withdraw()
        self._start_time = time.perf_counter()
        logger.info("=== Lancement de Notionator (fast start) ===")

        self.loading_screen = LoadingScreen(self, ["Préparation de l'interface..."])
        self.loading_screen.geometry("460x220")
        self.loading_screen.update()

        enable()  # profiler

        # Boot minimal
        self._minimal_bootstrap_sync()

        # UI immédiate
        self.after(0, self._finish_ui_init_fast)

        # Tâches lourdes après affichage
        self.after(300, lambda: kickoff_background_tasks(self))

    # Tk callback guard
    def report_callback_exception(self, exc, val, tb):
        logger.exception("Tk callback error", exc_info=(exc, val, tb))
        try:
            import tkinter.messagebox as mb
            mb.showerror("Erreur", f"{exc.__name__}: {val}")
        except Exception:
            pass

    # ------------------ Boot minimal ------------------
    def _minimal_bootstrap_sync(self):
        with span("todo.generate"):
            try:
                DailyToDoGenerator().generate(origin="main")
            except Exception:
                logger.exception("Erreur pendant la génération de la To-Do quotidienne")

        with span("bootstrap.cache_load"):
            try:
                self.data_manager = DataManager()
            except Exception:
                logger.exception("DataManager init a échoué")

        with span("bootstrap.pdf_scan"):
            try:
                self.actions_manager = ActionsManager(
                    data_manager=self.data_manager,
                    notion_api=get_notion_client(),
                    root=None,
                    refresh_callback=None
                )
            except Exception:
                logger.exception("ActionsManager init a échoué")

    # ------------------ UI ------------------
    def _finish_ui_init_fast(self):
        with span("ui.init"):
            self.title("Notionator")
            self.geometry("1000x600")
            try:
                self.state("zoomed")
            except Exception:
                pass

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(
            self,
            self.switch_frame,
            self.on_reload_click,
            rescan_pdfs_callback=self._rescan_pdfs
        )
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content_frame = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        self.content_frame.grid(row=0, column=1, sticky="nsew")

        # Installe la pompe UI du worker (callbacks sûrs sur le main thread)
        install_ui_pump(self)

        if self.actions_manager and getattr(self.actions_manager, "root", None) is None:
            self.actions_manager.root = self

        # Affiche immédiatement l'accueil via le cache de vues
        self.switch_frame("accueil")

        if self.loading_screen:
            self.loading_screen.destroy()
            self.loading_screen = None

        total_time = time.perf_counter() - self._start_time
        logger.info(f"UI affichée en {total_time:.2f} sec ✔")

        self.deiconify()
        logger.info("Application démarrée")

        self.bind_all("<Control-Shift-KeyPress-P>", self._dump_profiler)

        if self.data_manager and hasattr(self.data_manager, "sync_background"):
            with span("sync.background.kickoff"):
                self.data_manager.sync_background()

        self.quick_summary = QuickSummaryUpdater()
        # Décalé pour laisser l’UI se stabiliser
        self.after(4000, lambda: self._update_bilan_async(chunked=False))

        # --- Auto-scan PDF léger au démarrage (non bloquant) ---
        def _start_pdf_autoscan():
            try:
                # IMPORTANT : pas de run_exclusive ici, l'autoscan gère lui-même
                # l'exclusivité quand il déclenche l'indexation.
                AutoScanManager().check_and_maybe_scan()
            except Exception:
                logger.exception("AutoScan PDF au boot a échoué")

        # Laisse l'UI respirer, puis lance l'autoscan (listing léger)
        self.after(4500, _start_pdf_autoscan)

        # --- Raccourcis clavier (Ctrl+F / G / C / A / T) ---
        self._init_shortcuts()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------ Raccourcis : enregistrement ------------------
    def _init_shortcuts(self):
        """
        Enregistre les 5 raccourcis cœur :
          Ctrl+F → Recherche (sidebar)
          Ctrl+G → Recherche (ChatGPT local)
          Ctrl+C → Vue Collège
          Ctrl+A → Ajouter un cours (vue en cours)
          Ctrl+T → Ouvrir la To-Do du jour
        """
        try:
            self.shortcut_mgr = ShortcutManager(self)
            self.shortcut_mgr.register("Ctrl+F", self._shortcut_search_sidebar)
            self.shortcut_mgr.register("Ctrl+G", self._shortcut_search_local_ai)
            self.shortcut_mgr.register("Ctrl+C", lambda: self.switch_frame("colleges"))
            self.shortcut_mgr.register("Ctrl+A", self._shortcut_add_course_in_current_view)
            self.shortcut_mgr.register("Ctrl+T", self._shortcut_open_todo_today)
            logger.info("[Shortcuts] Raccourcis Ctrl enregistrés.")
        except Exception:
            logger.exception("[Shortcuts] Échec de l'initialisation des raccourcis.")

    # ------------------ Handlers des raccourcis ------------------
    def _is_text_input_focused(self) -> bool:
        try:
            w = self.focus_get()
        except Exception:
            return False
        from tkinter import Entry, Text
        return isinstance(w, (ctk.CTkEntry, ctk.CTkTextbox, Entry, Text))

    def _shortcut_search_sidebar(self):
        """Donne le focus à la barre de recherche de la sidebar si disponible."""
        if self._is_text_input_focused():
            return
        try:
            if hasattr(self.sidebar, "focus_search"):
                self.sidebar.focus_search()
                return
        except Exception:
            logger.exception("focus_search() a échoué sur Sidebar.")
        # Fallback doux
        self.switch_frame("accueil")

    def _shortcut_search_local_ai(self):
        """
        Ouvre la recherche ChatGPT locale.
        Priorité : si le Dashboard expose un trigger, on l'utilise.
        Sinon, ouvre un AIAnswerDialog minimaliste.
        """
        if self._is_text_input_focused():
            return
        try:
            if self.dashboard_view and hasattr(self.dashboard_view, "open_ai_dialog"):
                self.dashboard_view.open_ai_dialog()
                return
        except Exception:
            logger.exception("open_ai_dialog() du Dashboard a échoué.")

        # Fallback : ouvre un modal AIAnswerDialog avec paramètres minimaux
        try:
            dlg = AIAnswerDialog(
                self,
                title="Recherche locale",
                content="",
                width=700,
                height=500,
                typing_speed_ms=0,
                sources=[]
            )
            if hasattr(dlg, "open"):
                dlg.open(title="Recherche locale", initial_text="")
            elif hasattr(dlg, "show"):
                dlg.show(title="Recherche locale", initial_text="")
            else:
                dlg.lift()
        except Exception:
            logger.exception("AIAnswerDialog n'a pas pu être ouvert (fallback).")

    def _shortcut_add_course_in_current_view(self):
        """Détecte la vue active et tente d'ouvrir le flux d'ajout de cours/ITEM."""
        try:
            targets = list(self.content_frame.winfo_children())
            for w in targets:
                if str(w.winfo_manager()) == "place":  # on place/lift nos vues
                    for method_name in (
                        "add_course_quick",
                        "open_add_course_dialog",
                        "add_course",
                        "open_add_item_dialog",
                    ):
                        if hasattr(w, method_name):
                            try:
                                getattr(w, method_name)()
                                return
                            except Exception:
                                logger.exception("Appel %s() a échoué.", method_name)
            logger.info("Aucun handler d'ajout de cours trouvé dans la vue actuelle.")
        except Exception:
            logger.exception("Ajout de cours (raccourci) : erreur inattendue.")

    def _shortcut_open_todo_today(self):
        """Basculer sur le Dashboard et ouvrir/charger la To-Do du jour si possible."""
        if self._is_text_input_focused():
            return
        try:
            self.switch_frame("accueil")
            if self.dashboard_view and hasattr(self.dashboard_view, "load_today_todo"):
                try:
                    self.dashboard_view.load_today_todo()
                except Exception:
                    logger.exception("load_today_todo() a échoué.")
        except Exception:
            logger.exception("Ouverture To-Do du jour : erreur inattendue.")

    # ------------------ Bilan rapide ------------------
    def _update_bilan_async(self, chunked: bool = False):
        if not self.quick_summary:
            return

        def _job():
            with span("quick_summary.update_all"):
                self.quick_summary.update_all()

        # Sérialise sous la clé "quick_summary" pour éviter la concurrence avec d'autres jobs
        fut = run_serial("quick_summary", _job)
        then(
            fut,
            on_success=lambda _: logger.info("[Bilan rapide] Mise à jour terminée."),
            on_error=lambda e: logger.exception("[Bilan rapide] Échec de la mise à jour.", exc_info=e),
            use_ui=False,
        )

    def on_close(self):
        try:
            # Optionnel: dernière mise à jour rapide (non bloquante)
            if self.quick_summary:
                run_serial("quick_summary", self.quick_summary.update_all)
        except Exception:
            logger.exception("[Bilan rapide] Échec de la mise à jour à la fermeture.")
        finally:
            try:
                render_report()
            except Exception:
                pass
            try:
                # Arrêt propre des executors (n'interrompt pas l'UI)
                shutdown(wait=False)
            except Exception:
                logger.exception("worker.shutdown() a échoué")
            self.destroy()

    # ------------------ Scan PDF bouton ------------------
    def _rescan_pdfs(self):
        logger.info("[UI] Scan PDF demandé (exclusif)")
        if hasattr(self.sidebar, "show_loader"):
            self.sidebar.show_loader()

        def _worker():
            # Utilise la même normalisation que l'autoscan (BASE_FOLDER string/dict/list)
            roots = _as_str_roots(BASE_FOLDER)
            ensure_index_up_to_date(
                drive_roots=roots,
                verbose=True,
                max_size_kb=MAX_PDF_SIZE_KB
            )

        # Range le job manuel derrière la même clé d'exclusivité "pdf_index"
        fut = run_io(run_exclusive, "pdf_index", _worker)

        def _hide():
            if hasattr(self.sidebar, "hide_loader"):
                self.sidebar.hide_loader()

        then_finally(fut, _hide, use_ui=True)

    # ------------------ Profiler ------------------
    def _dump_profiler(self, *_):
        try:
            render_report()
            logger.info("[profilage] Rapport instantané généré (voir console et data/profiler_last.json).")
        except Exception:
            logger.exception("Échec du dump du profiler.")

    # =================== Navigation & Cache de vues ===================
    def switch_frame(self, screen: str):
        """
        Routes supportées:
          - 'accueil'
          - 'semestre_<n>'
          - 'colleges'
          - 'tous_les_semestres'
          - 'search:<query>'   (utilise le flux existant open_search)
          - 'settings'
        """
        logger.info(f"Changement d'écran vers : {screen}")
        if screen != self.current_screen:
            self.previous_screen = self.current_screen
        self.current_screen = screen

        # Route "search:" garde le flux existant (création à la volée)
        if screen.startswith("search:"):
            query = screen.split("search:", 1)[1]
            return self.open_search(query)

        # 1) Récupère/instancie la vue depuis le cache (sans I/O)
        frame = self._view_cache.get(screen)
        if frame is None:
            frame = self._create_view(screen)
            if frame is None:
                return
            self._view_cache[screen] = frame
            # Place la vue, occupe tout l'espace (évite .pack qui reconstruit)
            frame.place(in_=self.content_frame, relx=0, rely=0, relwidth=1, relheight=1)

        # 2) Affiche instantanément
        frame.lift()

        # 3) Lazy load: si la vue expose load_async(), on le lance une fois (non bloquant)
        if not self._loading_flags.get(screen, False) and hasattr(frame, "load_async"):
            self._loading_flags[screen] = True
            self._safe_load_async(screen, frame)

    def _create_view(self, screen: str):
        """
        Crée la vue SANS I/O bloquante.
        Les requêtes réseau doivent aller dans load_async() de la vue si disponible.
        """
        if screen == "accueil":
            dash = Dashboard(self.content_frame)
            self.dashboard_view = dash
            return dash

        if screen.startswith("semestre_"):
            num = screen.split("_")[1]
            view = SemestreView(self.content_frame, num, self.data_manager, self.show_only_actions)
            self.dashboard_view = None
            return view

        if screen == "colleges":
            view = CollegeView(self.content_frame, self.data_manager, get_notion_client(), self.show_only_actions)
            self.dashboard_view = None
            return view

        if screen == "tous_les_semestres":
            view = SemestreView(self.content_frame, "all", self.data_manager, self.show_only_actions)
            self.dashboard_view = None
            return view

        if screen == "settings":
            self.dashboard_view = None
            return SettingsView(self.content_frame)

        logger.warning("Écran inconnu: %s", screen)
        return None

    def _safe_load_async(self, screen: str, frame: ctk.CTkFrame):
        """Appelle load_async() en worker puis refresh() sur le thread UI."""
        # Exécute la charge en background
        fut = run_io(lambda: frame.load_async())

        def _post_refresh():
            try:
                getattr(frame, "refresh", lambda: None)()
            except Exception:
                logger.exception("refresh() a échoué pour %s", screen)
            self._loading_flags[screen] = False

        then_finally(fut, _post_refresh, use_ui=True)

    # =================== Flux recherche existant ===================
    def open_course_from_search(self, course: dict):
        sem = course.get("semestre")
        if sem:
            self.switch_frame(f"semestre_{sem}")

    def open_search(self, query: str):
        self.current_search_query = query
        if not self.data_manager:
            results = []
        else:
            try:
                results = self.data_manager.search_courses(query)
            except Exception:
                logger.exception("search_courses a échoué; résultats vides.")
                results = []

        # Détruit seulement l'ancien contenu ad hoc de la recherche
        for widget in self.content_frame.winfo_children():
            if str(widget) not in [str(f) for f in self._view_cache.values()]:
                widget.destroy()

        view = SearchResultsView(
            self.content_frame,
            query,
            results,
            self.data_manager,
            on_open_course=self.open_course_from_search
        )
        # Place au-dessus (sans casser le cache des autres vues)
        view.place(in_=self.content_frame, relx=0, rely=0, relwidth=1, relheight=1)
        view.lift()
        self.dashboard_view = None

    # ------------------ Refresh utilitaire ------------------
    def _refresh_from_cache(self):
        # Ré-affiche la vue courante (déjà en cache) et relance un load_async si dispo
        self.switch_frame(self.current_screen)

    def on_reload_click(self):
        self._refresh_from_cache()
        if hasattr(self.sidebar, "show_loader"):
            self.sidebar.show_loader()

        if self.data_manager and hasattr(self.data_manager, "sync_async"):
            # ⚠️ DataManager a migré sur force_full
            self.data_manager.sync_async(on_done=self._on_sync_done, force_full=True)
        elif self.data_manager and hasattr(self.data_manager, "sync_background"):
            self.data_manager.sync_background()

    def _on_sync_done(self):
        self.after(0, self._apply_post_sync)

    def _apply_post_sync(self):
        self._refresh_from_cache()
        if hasattr(self.sidebar, "hide_loader"):
            self.sidebar.hide_loader()

    # --- Toggle filtre actions ---
    def toggle_global_filter(self):
        self.show_only_actions = not self.show_only_actions
        self.switch_frame(self.current_screen)


# ------------------ Entrée ------------------
if __name__ == "__main__":
    def _global_excepthook(exc_type, exc_value, exc_tb):
        try:
            logger.exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        finally:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _global_excepthook

    def _thread_excepthook(args: threading.ExceptHookArgs):
        try:
            logger.exception("Thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        finally:
            if hasattr(threading, "__excepthook__"):
                threading.__excepthook__(args)

    threading.excepthook = _thread_excepthook

    banner = (
        "\n\n--- Nouvelle session Notionator --- "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        "---\n\n"
    )
    logging.getLogger().info(banner)

    try:
        app = App()
        app.mainloop()
    except Exception:
        logging.getLogger(__name__).exception("Erreur critique dans la boucle principale")
    finally:
        try:
            render_report()
        except Exception:
            pass
