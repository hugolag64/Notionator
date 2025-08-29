# services/shortcuts.py
from __future__ import annotations
import tkinter as tk
from typing import Callable, Dict

# ---------- Définition des raccourcis ----------
SHORTCUTS: Dict[str, Dict[str, str]] = {
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


# ---------- Gestionnaire central ----------
class ShortcutManager:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.bindings: Dict[str, Callable] = {}

    def register(self, key_combo: str, callback: Callable):
        """
        Enregistre un raccourci global (ex: "Ctrl+F") lié à une fonction callback.
        Génére un pattern robuste pour Tk: "<Control-KeyPress-f>".
        """
        sequence = self._to_sequence(key_combo)
        if sequence:
            # Important: add="+" pour ne PAS écraser des bindings existants
            self.root.bind_all(sequence, lambda e: callback(), add="+")
            self.bindings[key_combo] = callback

    # ---------- Helpers ----------
    def _to_sequence(self, key_combo: str) -> str:
        """
        Convertit "Ctrl+F" en "<Control-KeyPress-f>" usable par Tk.
        - Modificateurs en CaseExacte: Control / Shift / Alt
        - Touche finale en keysym (letters en minuscule)
        """
        if not key_combo or "+" not in key_combo:
            return ""

        parts = [p.strip() for p in key_combo.split("+") if p.strip()]
        if len(parts) < 2:
            return ""

        *mods, key = parts
        mod_map = {
            "ctrl": "Control",
            "control": "Control",
            "shift": "Shift",
            "alt": "Alt",
        }
        mods_norm = []
        for m in mods:
            mm = mod_map.get(m.lower())
            if not mm:
                # ignore mod inconnu plutôt que planter
                continue
            mods_norm.append(mm)

        keysym = self._to_keysym(key)
        if not keysym:
            return ""

        # Pattern robuste : <Control-KeyPress-f> (et co)
        # Si plusieurs modifs: "<Control-Shift-KeyPress-f>"
        prefix = "-".join(mods_norm)
        if prefix:
            return f"<{prefix}-KeyPress-{keysym}>"
        return f"<KeyPress-{keysym}>"

    def _to_keysym(self, key: str) -> str:
        """
        Normalise la touche finale en keysym Tk acceptable.
        - Lettres → minuscule (f, g, t…)
        - Chiffres → 0..9
        - Ponctuation courante mappée (comma, period, slash, minus, equal, bracketleft, bracketright, semicolon, apostrophe)
        """
        k = key.strip()

        # Lettres
        if len(k) == 1 and k.isalpha():
            return k.lower()

        # Chiffres
        if len(k) == 1 and k.isdigit():
            return k

        # Ponctuation mappée
        punct_map = {
            ",": "comma",
            ".": "period",
            "/": "slash",
            "-": "minus",
            "=": "equal",
            ";": "semicolon",
            "'": "apostrophe",
            "[": "bracketleft",
            "]": "bracketright",
            "\\": "backslash",
            "`": "grave",
            " ": "space",
        }
        if k in punct_map:
            return punct_map[k]

        # Quelques alias déjà en clair
        alias = {
            "comma": "comma",
            "period": "period",
            "slash": "slash",
            "minus": "minus",
            "equal": "equal",
            "semicolon": "semicolon",
            "apostrophe": "apostrophe",
            "bracketleft": "bracketleft",
            "bracketright": "bracketright",
            "backslash": "backslash",
            "space": "space",
        }
        return alias.get(k.lower(), "")


# ---------- Helper pour affichage dans Paramètres ----------
def get_shortcuts_list() -> Dict[str, Dict[str, str]]:
    """
    Retourne les raccourcis (ex: pour afficher dans la section Paramètres).
    """
    return SHORTCUTS
