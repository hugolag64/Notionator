# ui/settings_view.py
from __future__ import annotations
import webbrowser
import customtkinter as ctk
from typing import Literal, Dict

from ui.styles import COLORS, set_theme
from services.settings_store import settings
from config import FOCUS_DEFAULTS

# --- Raccourcis : import tolérant (fallback si module absent) ---
try:
    from services.shortcuts import get_shortcuts_list
except Exception:
    def get_shortcuts_list() -> Dict[str, Dict[str, str]]:
        # Fallback par défaut (les 5 essentiels + 3 optionnels)
        return {
            "Essentiels": {
                "Ctrl+F": "Recherche (sidebar)",
                "Ctrl+G": "Recherche (ChatGPT local)",
                "Ctrl+C": "Vue Collège",
                "Ctrl+A": "Ajouter un cours (vue en cours)",
                "Ctrl+T": "Ouvrir la To-Do du jour",
            },
            "Optionnels": {
                "Ctrl+D": "Aller au Dashboard",
                "Ctrl+M": "Focus Mode",
                "Ctrl+O": "Ouvrir page Notion du cours courant",
            }
        }


class SettingsView(ctk.CTkFrame):
    """
    Panneau de paramètres.
    - Thème: Système / Clair / Sombre
    - Focus: durée travail & pauses, sessions avant longue pause
    - Spotify: URL playlist + toggle 'lancer au début'
    - Raccourcis: liste en lecture seule (source services.shortcuts)
    """
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._build()

    # ---------- UI ----------
    def _build(self):
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Titre
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(14, 6), padx=10)
        title = ctk.CTkLabel(
            header, text="Paramètres",
            font=("Helvetica", 18, "bold"),
            text_color=COLORS["accent"]
        )
        title.pack(anchor="w")

        # Corps scrollable
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        # Groupes
        self._section_theme(body)
        self._section_focus(body)
        self._section_spotify(body)
        self._section_shortcuts(body)   # ← Nouveau panneau “Raccourcis clavier”

        # Actions
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", pady=(6, 4))
        ctk.CTkButton(btns, text="Enregistrer", command=self._save).pack(side="right")

    # ---------- Sections ----------
    def _section_theme(self, parent):
        card = _Card(parent, "Apparence")
        card.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))

        # Mapping FR -> modes CustomTkinter
        labels: list[Literal["Système", "Clair", "Sombre"]] = ["Système", "Clair", "Sombre"]
        self._to_mode = {"Système": "system", "Clair": "light", "Sombre": "dark"}
        inv = {v: k for k, v in self._to_mode.items()}

        # lecture clé imbriquée
        current_mode = settings.get("appearance.theme", "system")
        current_mode = str(current_mode).lower()
        if current_mode not in {"system", "light", "dark"}:
            current_mode = "system"

        self.theme_var = ctk.StringVar(value=inv[current_mode])

        ctk.CTkLabel(row, text="Thème", anchor="w").grid(row=0, column=0, sticky="w")
        opt = ctk.CTkOptionMenu(row, values=labels, variable=self.theme_var, width=140)
        opt.grid(row=0, column=1, padx=(10, 0))

    def _section_focus(self, parent):
        card = _Card(parent, "Focus (Pomodoro)")
        card.pack(fill="x", pady=(0, 10))

        fvals = settings.get("focus", {}) or {}
        self.work_var   = ctk.StringVar(value=str(fvals.get("work_min", FOCUS_DEFAULTS["WORK_MIN"])))
        self.short_var  = ctk.StringVar(value=str(fvals.get("short_break_min", FOCUS_DEFAULTS["SHORT_BREAK_MIN"])))
        self.long_var   = ctk.StringVar(value=str(fvals.get("long_break_min", FOCUS_DEFAULTS["LONG_BREAK_MIN"])))
        self.before_var = ctk.StringVar(value=str(fvals.get("sessions_before_long", FOCUS_DEFAULTS["SESSIONS_BEFORE_LONG"])))

        grid = ctk.CTkFrame(card.body, fg_color="transparent")
        grid.pack(fill="x", pady=(2, 4))
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1, uniform="fg")

        _mini_field(grid, "Travail (min)", self.work_var).grid(row=0, column=0, padx=6, sticky="ew")
        _mini_field(grid, "Pause courte (min)", self.short_var).grid(row=0, column=1, padx=6, sticky="ew")
        _mini_field(grid, "Pause longue (min)", self.long_var).grid(row=0, column=2, padx=6, sticky="ew")
        _mini_field(grid, "Sessions avant longue", self.before_var).grid(row=0, column=3, padx=6, sticky="ew")

    def _section_spotify(self, parent):
        card = _Card(parent, "Spotify")
        card.pack(fill="x", pady=(0, 10))

        fvals = settings.get("focus", {}) or {}
        self.launch_var = ctk.BooleanVar(value=bool(fvals.get("launch_spotify", True)))
        self.url_var = ctk.StringVar(value=str(fvals.get("spotify_url", FOCUS_DEFAULTS["SPOTIFY_URL"])))

        row1 = ctk.CTkFrame(card.body, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkSwitch(
            row1,
            text="Lancer la playlist au début d’une session de travail",
            variable=self.launch_var
        ).pack(anchor="w")

        row2 = ctk.CTkFrame(card.body, fg_color="transparent")
        row2.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row2, text="URL playlist :", anchor="w").grid(row=0, column=0, sticky="w")
        ent = ctk.CTkEntry(row2, textvariable=self.url_var)
        ent.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        row2.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            row2, text="Tester", width=80,
            command=lambda: webbrowser.open(self.url_var.get())
        ).grid(row=0, column=2)

    def _section_shortcuts(self, parent):
        """
        Mini panneau de consultation des raccourcis.
        Lecture seule, source: services.shortcuts.get_shortcuts_list()
        """
        card = _Card(parent, "Raccourcis clavier")
        card.pack(fill="x", pady=(0, 10))

        shortcuts = get_shortcuts_list() or {}

        # En-têtes style “table”
        header = ctk.CTkFrame(card.body, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        _table_header(header, "Raccourci").grid(row=0, column=0, sticky="w", padx=(0, 8))
        _table_header(header, "Action").grid(row=0, column=1, sticky="w")
        header.grid_columnconfigure(0, weight=0)
        header.grid_columnconfigure(1, weight=1)

        # Corps
        for cat, mapping in shortcuts.items():
            # Titre de catégorie
            cat_label = ctk.CTkLabel(
                card.body, text=cat,
                font=("Helvetica", 13, "bold"),
                text_color=COLORS["accent"]
            )
            cat_label.pack(anchor="w", pady=(6, 4))

            # Contenu
            table = ctk.CTkFrame(card.body, fg_color="#F9FAFB", corner_radius=10)
            table.pack(fill="x", padx=0, pady=(0, 8))

            # lignes
            for i, (key, desc) in enumerate(mapping.items()):
                row = ctk.CTkFrame(table, fg_color="transparent")
                row.pack(fill="x", padx=10, pady=(6 if i == 0 else 2, 6))

                # key “capsule”
                key_chip = _KeyChip(row, key)
                key_chip.grid(row=0, column=0, sticky="w")

                # desc
                lbl = ctk.CTkLabel(row, text=desc, anchor="w")
                lbl.grid(row=0, column=1, sticky="w", padx=(10, 0))
                row.grid_columnconfigure(1, weight=1)

            # petite séparation visuelle
            ctk.CTkFrame(card.body, fg_color="transparent", height=2).pack(fill="x")

    # ---------- Save ----------
    def _save(self):
        # Thème
        chosen_label = self.theme_var.get()
        mode = self._to_mode.get(chosen_label, "system")
        settings.set("appearance.theme", mode)
        try:
            set_theme(mode)  # applique globalement
        except Exception:
            pass

        # Focus
        def to_int(s: str, default: int) -> int:
            try:
                v = int(s)
                return max(1, min(180, v))
            except Exception:
                return default

        focus_block = {
            "work_min": to_int(self.work_var.get(), FOCUS_DEFAULTS["WORK_MIN"]),
            "short_break_min": to_int(self.short_var.get(), FOCUS_DEFAULTS["SHORT_BREAK_MIN"]),
            "long_break_min": to_int(self.long_var.get(), FOCUS_DEFAULTS["LONG_BREAK_MIN"]),
            "sessions_before_long": to_int(self.before_var.get(), FOCUS_DEFAULTS["SESSIONS_BEFORE_LONG"]),
            "spotify_url": self.url_var.get().strip(),
            "launch_spotify": bool(self.launch_var.get()),
        }
        existing = settings.get("focus", {}) or {}
        existing.update(focus_block)
        settings.set("focus", existing)

        # Persistance
        try:
            settings.save()
        except Exception:
            pass

        # Feedback (optionnel)
        try:
            from services.notification_center import NotificationCenter
            NotificationCenter.instance().notify(
                title="Paramètres",
                message="Changements enregistrés.",
                level="success",
                category="settings",
            )
        except Exception:
            pass


# ---------- Petites aides UI ----------
class _Card(ctk.CTkFrame):
    def __init__(self, parent, title: str):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=12)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent", height=40)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.grid_propagate(False)
        ctk.CTkLabel(
            head, text=title, font=("Helvetica", 16, "bold"),
            text_color=COLORS["accent"]
        ).pack(anchor="w")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))


def _mini_field(parent, label: str, var: ctk.StringVar):
    wrap = ctk.CTkFrame(parent, fg_color="#F9FAFB", corner_radius=10)
    ctk.CTkLabel(wrap, text=label, text_color="#6B7280").pack(anchor="w", padx=10, pady=(8, 2))
    ctk.CTkEntry(wrap, textvariable=var).pack(fill="x", padx=10, pady=(0, 8))
    return wrap


def _table_header(parent, text: str):
    return ctk.CTkLabel(parent, text=text, text_color="#6B7280", font=("Helvetica", 12, "bold"))


class _KeyChip(ctk.CTkFrame):
    """Pill stylée pour afficher une combinaison de touches."""
    def __init__(self, parent, key_text: str):
        super().__init__(parent, fg_color="#FFFFFF", corner_radius=8)
        self._lbl = ctk.CTkLabel(self, text=key_text, font=("Helvetica", 12), text_color="#0B1320")
        self._lbl.pack(padx=10, pady=6)
