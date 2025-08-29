# ui/styles.py
from __future__ import annotations
import customtkinter as ctk

# ---------- Palettes ----------
LIGHT_COLORS = {
    # Fonds
    "bg": "#F5F6FA",
    "bg_card": "#FFFFFF",
    "bg_sidebar": "#E5E7EB",
    "bg_card_hover": "#F3F4F6",

    # Texte
    "text": "#1F2937",
    "text_primary": "#111827",
    "text_secondary": "#6B7280",
    "text_light": "#FFFFFF",
    "text_sidebar": "#1F2937",

    # Accent (uniforme partout)
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",

    # Divers
    "card": "#FFFFFF",
    "chip_bg": "#F3F4F6",
    "bg_dark": "#121212",  # juste indicatif
}

DARK_COLORS = {
    # Fonds
    "bg": "#121212",        # fond global
    "bg_card": "#1E1E1E",   # cartes
    "bg_sidebar": "#1A1A1A",
    "bg_card_hover": "#2C2C2C",

    # Texte
    "text": "#EAEAEA",
    "text_primary": "#F3F4F6",
    "text_secondary": "#9CA3AF",
    "text_light": "#FFFFFF",
    "text_sidebar": "#EAEAEA",

    # Accent (uniforme partout)
    "accent": "#3B82F6",
    "accent_hover": "#2563EB",

    # Divers
    "card": "#1E1E1E",
    "chip_bg": "#2C2C2C",
    "bg_dark": "#121212",
}

# Couleurs actives (par défaut → clair)
COLORS: dict[str, str] = {}


def _apply_palette(palette: dict[str, str]) -> None:
    """Applique la palette et crée des alias rétrocompatibles."""
    COLORS.clear()
    COLORS.update(palette)

    # --- Aliases rétrocompatibles ---
    COLORS.setdefault("bg_light", COLORS.get("bg", "#FFFFFF"))
    COLORS.setdefault("card", COLORS.get("bg_card", COLORS.get("bg")))
    COLORS.setdefault("chip_bg", COLORS.get("chip_bg", "#F3F4F6"))
    COLORS.setdefault("bg_card_hover", COLORS.get("bg_card_hover", COLORS.get("bg_card", COLORS.get("bg"))))
    COLORS.setdefault("text", COLORS.get("text", "#111111"))
    COLORS.setdefault("text_secondary", COLORS.get("text_secondary", "#666666"))


# ---------- Police et tailles ----------
FONT = ("Helvetica", 14)
LOGO_SIZE = (80, 80)
SIDEBAR_WIDTH = 200


# ---------- Gestion du thème global ----------
def set_theme(mode: str = "system"):
    """
    Applique le thème global CustomTkinter + notre palette.
    mode ∈ {"light", "dark", "system"}
    """
    if mode not in {"light", "dark", "system"}:
        raise ValueError(f"Mode inconnu : {mode}")

    if mode == "dark":
        ctk.set_appearance_mode("dark")
        _apply_palette(DARK_COLORS)
    elif mode == "light":
        ctk.set_appearance_mode("light")
        _apply_palette(LIGHT_COLORS)
    else:  # "system"
        ctk.set_appearance_mode("system")
        # Fallback clair si CustomTkinter n'adapte pas tout
        _apply_palette(LIGHT_COLORS)
