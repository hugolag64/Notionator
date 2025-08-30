# services/paths.py
from __future__ import annotations
from pathlib import Path

# Racine du projet = dossier parent de ce fichier -> ../..
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def prj(*parts: str) -> Path:
    """Chemin absolu ancré à la racine du projet."""
    return PROJECT_ROOT.joinpath(*parts)
