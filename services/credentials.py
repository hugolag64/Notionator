# services/credentials.py
from __future__ import annotations
import os, json

# <repo_root>/data
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DEFAULT_PATH = os.path.join(DATA_DIR, "credentials.json")

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def resolve_google_credentials_path() -> str:
    """
    Renvoie un chemin vers un credentials.json Google valide.
    Ordre de priorité :
      1) GOOGLE_CREDENTIALS_PATH (fichier existant)
      2) GOOGLE_CREDENTIALS_JSON (contenu JSON → écrit en data/credentials.json)
      3) data/credentials.json (s'il existe)
    Lève FileNotFoundError sinon (avec message clair).
    """
    p = os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if p and os.path.exists(p):
        return p

    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if raw:
        _ensure_data_dir()
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise ValueError("GOOGLE_CREDENTIALS_JSON n'est pas un JSON valide") from e
        with open(DEFAULT_PATH, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return DEFAULT_PATH

    if os.path.exists(DEFAULT_PATH):
        return DEFAULT_PATH

    raise FileNotFoundError(
        f"credentials.json introuvable. "
        f"Place le fichier dans {DEFAULT_PATH} OU définis "
        f"GOOGLE_CREDENTIALS_PATH (chemin) ou GOOGLE_CREDENTIALS_JSON (contenu)."
    )
