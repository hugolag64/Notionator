# utils/dnd.py
from __future__ import annotations
import sys
from typing import Callable
from tkinter import filedialog

try:
    import windnd  # Windows-only
except Exception:
    windnd = None

# Petite marge sous la limite brutale de windnd (200) pour éviter l’exception
_HOOK_LIMIT_SAFE = 180
_HOOK_COUNT = 0


def attach_drop(widget, on_files: Callable[[list[str]], None], enable_fallback_click: bool = True):
    """
    Active le drop natif Windows sur 'widget' si windnd dispo.
    Si hook impossible (limite atteinte / bug windnd / non-Windows), fallback: clic -> open file dialog.

    NB: windnd lève parfois une *string* ("over hook limit...") au lieu d'une Exception.
    On catch TOUT et on bascule en fallback proprement.
    """
    global _HOOK_COUNT

    def _use_fallback():
        if enable_fallback_click:
            # Bind sur le label + son parent pour être tolérant
            try:
                widget.bind("<Button-1>", lambda _e: _pick_files(on_files))
            except Exception:
                pass
            # Optionnel: petit tooltip textuel si la classe le supporte
            try:
                widget.tooltip_text = "DnD indisponible: clic pour choisir un PDF"
            except Exception:
                pass

    if not (sys.platform.startswith("win") and windnd is not None):
        _use_fallback()
        return

    # Si on a déjà accroché beaucoup d’éléments, rester en fallback pour éviter la casse
    if _HOOK_COUNT >= _HOOK_LIMIT_SAFE:
        _use_fallback()
        return

    try:
        # IMPORTANT: hook une seule fois par widget
        # windnd ne propose pas d’idempotence, donc on protège côté app
        if getattr(widget, "_windnd_hooked", False):
            return

        windnd.hook_dropfiles(
            widget,
            func=lambda paths: _on_drop_async(paths, on_files),
            force_unicode=True,
        )
        setattr(widget, "_windnd_hooked", True)
        _HOOK_COUNT += 1
    except BaseException:
        # windnd peut lever une string → on catch large et on fallback
        _use_fallback()


def _on_drop_async(paths, on_files):
    if not paths:
        return
    files = [p for p in paths if isinstance(p, str)]
    if files:
        try:
            on_files(files)
        except Exception:
            # Ne jamais planter le thread Tk à cause d’un callback utilisateur
            pass


def _pick_files(on_files):
    try:
        paths = filedialog.askopenfilenames(
            title="Sélectionner un PDF",
            filetypes=[("PDF", "*.pdf"), ("Tous les fichiers", "*.*")],
        )
        if paths:
            on_files(list(paths))
    except Exception:
        pass
