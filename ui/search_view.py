# ui/search_view.py
from __future__ import annotations

import customtkinter as ctk
from typing import List, Dict, Callable, Optional
from ui.styles import COLORS

# Fallbacks sûrs
FG_BG   = COLORS.get("bg", COLORS.get("bg_light", "#F5F6F7"))
FG_CARD = COLORS.get("bg_card", "#FFFFFF")
TXT     = COLORS.get("text", COLORS.get("text_sidebar", "#0B1320"))
SUB     = COLORS.get("text_secondary", "#6B7280")
ACCENT  = COLORS.get("accent", "#3B82F6")
CHIP_BG = COLORS.get("chip_bg", "#F3F4F6")
OK_CLR  = COLORS.get("ok_green", "#16A34A")
KO_CLR  = COLORS.get("bad_red",  "#EF4444")

class SearchResultsView(ctk.CTkFrame):
    """
    Résultats de recherche "à la Semestre/Collège":
      - Titre cliquable
      - Chips Sx / UE
      - Statuts PDF / Anki / Résumé / Rappel (✔/✘)
    """
    def __init__(
        self,
        parent,
        query: str,
        results: List[Dict],
        data_manager,                                # ← (NOUVEAU) pour parser comme dans les vues
        on_open_course: Optional[Callable[[Dict], None]] = None,
    ):
        super().__init__(parent, fg_color=FG_BG)
        self.query = query
        self.results = results
        self.dm = data_manager
        self.on_open_course = on_open_course

        # On aura besoin des noms d'UE comme en SemestreView
        try:
            self._ue_map = self.dm.get_ue_map()
        except Exception:
            self._ue_map = {}

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 8))

        title = ctk.CTkLabel(
            header,
            text=f"Résultats pour « {query} »",
            font=("SF Pro Display", 22, "bold"),
            text_color=TXT
        )
        title.pack(side="left")

        count = ctk.CTkLabel(
            header,
            text=f"{len(results)} cours",
            font=("SF Pro Text", 14),
            text_color=SUB
        )
        count.pack(side="left", padx=(12, 0))

        # Conteneur scrollable
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        if not results:
            empty = ctk.CTkLabel(
                self.scroll,
                text="Aucun résultat.",
                font=("SF Pro Text", 16),
                text_color=SUB
            )
            empty.pack(pady=40)
        else:
            for item in results:
                self._add_course_row(item)

    # ---------- Helpers d'affichage ----------
    def _badge(self, parent, text: str):
        b = ctk.CTkLabel(
            parent,
            text=text,
            font=("SF Pro Text", 12),
            text_color=TXT,
            fg_color=CHIP_BG,
            corner_radius=8,
            padx=10, pady=4
        )
        b.pack(side="left", padx=6)

    def _status_chip(self, parent, label: str, ok: Optional[bool]):
        if ok is True:
            text = f"✔ {label}"
            color = OK_CLR
        elif ok is False:
            text = f"✘ {label}"
            color = KO_CLR
        else:
            text = f"• {label}"
            color = SUB
        c = ctk.CTkLabel(
            parent,
            text=text,
            font=("SF Pro Text", 12),
            text_color=color,
            fg_color="transparent"
        )
        c.pack(side="left", padx=8)

    def _open_course(self, course_min: Dict):
        if self.on_open_course:
            self.on_open_course(course_min)

    # ---------- Construction d'une ligne "comme en Semestre/Collège" ----------
    def _add_course_row(self, course_min: Dict):
        """
        course_min = {id, title, semestre (int|str|None), ue, college}
        On récupère le cours brut depuis le cache, puis on parse comme les vues:
        - mode 'semestre' (statuts PDF/Anki/Résumé/Rappel)
        - si pas pertinent, mode 'college' (…_collège)
        """
        raw = self.dm.get_course_by_id(course_min["id"])
        if not raw:
            return

        parsed_sem = self.dm.parse_course(raw, mode="semestre", ue_map=self._ue_map)
        parsed_col = self.dm.parse_course(raw, mode="college")

        # Choix du rendu principal :
        #  - si un semestre est renseigné → version "semestre"
        #  - sinon, si ITEM/college existe → version "collège"
        use_college = False
        if parsed_sem and parsed_sem.get("semestre"):
            data = parsed_sem
        elif parsed_col and parsed_col.get("item") is not None:
            data = parsed_col
            use_college = True
        else:
            # dernier recours : sem
            data = parsed_sem or {}

        card = ctk.CTkFrame(self.scroll, corner_radius=16, fg_color=FG_CARD)
        card.pack(fill="x", pady=10)

        # ----- Ligne 1 : Titre cliquable + bouton Ouvrir -----
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 6))

        link = ctk.CTkLabel(
            top,
            text=data.get("nom") or course_min.get("title", "Sans titre"),
            font=("SF Pro Text", 16, "bold"),
            text_color=ACCENT
        )
        link.pack(side="left")
        link.bind("<Button-1>", lambda _=None: self._open_course(course_min))

        open_btn = ctk.CTkButton(
            top, text="Ouvrir", height=32, corner_radius=10,
            fg_color=ACCENT, text_color="white",
            command=lambda: self._open_course(course_min),
        )
        open_btn.pack(side="right")

        # ----- Ligne 2 : Chips Sx / UE / Collège -----
        meta = ctk.CTkFrame(card, fg_color="transparent")
        meta.pack(fill="x", padx=12, pady=(0, 8))

        # Semestre chip
        sem_label = None
        if parsed_sem and parsed_sem.get("semestre"):
            # "Semestre 4" → "S4"
            sem_label = str(parsed_sem["semestre"]).strip()
            if sem_label.lower().startswith("semestre "):
                sem_label = "S" + sem_label.split(" ", 1)[1]
        elif isinstance(course_min.get("semestre"), int):
            sem_label = f"S{course_min['semestre']}"
        elif isinstance(course_min.get("semestre"), str):
            sem_label = course_min["semestre"]

        if sem_label:
            self._badge(meta, sem_label)

        # UE chips (comme en SemestreView : noms déjà résolus dans parsed_sem['ue'])
        if parsed_sem and parsed_sem.get("ue"):
            for ue_name in parsed_sem["ue"]:
                if ue_name:
                    self._badge(meta, ue_name)

        # Collège (si rendu collège)
        if use_college and parsed_col and parsed_col.get("college"):
            self._badge(meta, parsed_col["college"])

        # ----- Ligne 3 : Statuts (✔/✘) -----
        status = ctk.CTkFrame(card, fg_color="transparent")
        status.pack(fill="x", padx=12, pady=(0, 12))

        if not use_college:
            # Vue "semestre" (aligné sur SemestreView)
            self._status_chip(status, "PDF",    parsed_sem.get("pdf_ok"))
            self._status_chip(status, "Anki",   parsed_sem.get("anki_ok"))
            self._status_chip(status, "Résumé", parsed_sem.get("resume_ok"))
            self._status_chip(status, "Rappel", parsed_sem.get("rappel_ok"))
        else:
            # Vue "collège" (aligné sur CollegeView)
            self._status_chip(status, "PDF",    parsed_col.get("pdf_ok"))               # URL PDF COLLEGE
            self._status_chip(status, "Anki",   parsed_col.get("anki_college_ok"))
            self._status_chip(status, "Résumé", parsed_col.get("resume_college_ok"))
            self._status_chip(status, "Rappel", parsed_col.get("rappel_college_ok"))
