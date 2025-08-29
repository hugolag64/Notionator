# services/pdf_sync.py
from __future__ import annotations
import os
import json
from typing import List, Dict, Any

# --- Chemins absolus vers /data ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

MAPPING_FILE  = os.path.join(DATA_DIR, "pdf_mapping.json")
REGISTRY_FILE = os.path.join(DATA_DIR, "pdf_registry.json")


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_roots(roots_any: List[Any]) -> List[str]:
    """
    Accepte:
      - ["C:/path", "/other"]
      - [{"path": "C:/path"}, {"path": "/other"}]
      - mélange des deux
    Retourne toujours une liste de chemins (str) existants (ou plausibles).
    """
    norm: List[str] = []
    if not isinstance(roots_any, list):
        return norm
    for entry in roots_any:
        path = None
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, dict):
            # .get sans planter si dict incomplet
            p = entry.get("path") if hasattr(entry, "get") else None
            if isinstance(p, str):
                path = p
        # On garde même si le chemin n'existe pas encore : le scan filtrera
        if isinstance(path, str) and path.strip():
            norm.append(path)
    # Déduplique en conservant l'ordre
    seen = set()
    out: List[str] = []
    for p in norm:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _collect_pdfs(roots: List[str], max_size_kb: int = 40_000) -> Dict[str, str]:
    """
    Retourne {basename: absolute_path} pour tous les PDFs <= max_size_kb.
    En cas de doublon de basename, on garde le 1er trouvé (priorité = ordre de roots).
    """
    found: Dict[str, str] = {}
    for root in roots:
        if not root or not os.path.exists(root):
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.lower().endswith(".pdf"):
                    continue
                abspath = os.path.abspath(os.path.join(dirpath, name))
                try:
                    size_kb = os.path.getsize(abspath) // 1024
                except OSError:
                    continue
                if size_kb > max_size_kb:
                    # Ignore les gros PDF pour économiser l'index
                    continue
                if name not in found:
                    found[name] = abspath
    return found


def scan_and_update_mapping(
    roots: List[Any],
    also_include_folder: str | None = None,
    max_size_kb: int = 40_000
) -> Dict[str, List[str]]:
    """
    Met à jour pdf_mapping.json et pdf_registry.json.

    - roots: dossiers Drive/locaux à scanner récursivement
             (liste de str ou liste de dicts {"path": ...})
    - also_include_folder: en plus, inclure tous les PDFs de ce dossier (ex: data/pdf)
    - max_size_kb: taille max autorisée par PDF (par défaut 40 000 Ko ~ 40 Mo)

    Retour:
      {
        "new_or_modified": [paths...],  # chemins absolus à réindexer
        "unchanged": [paths...]
      }
    """
    # 1) Normaliser les racines vers des str
    roots_str = _normalize_roots(roots)

    mapping = _load_json(MAPPING_FILE, {})
    registry = _load_json(REGISTRY_FILE, {})

    # 2) Collecte des PDFs dans les racines, filtrés par taille
    candidates = _collect_pdfs(roots_str, max_size_kb=max_size_kb)

    # 3) Inclure optionnellement un dossier "local" (data/pdf), avec le même filtre
    if also_include_folder and os.path.exists(also_include_folder):
        try:
            for f in os.listdir(also_include_folder):
                if f.lower().endswith(".pdf"):
                    abspath = os.path.abspath(os.path.join(also_include_folder, f))
                    try:
                        size_kb = os.path.getsize(abspath) // 1024
                    except OSError:
                        continue
                    if size_kb <= max_size_kb:
                        candidates.setdefault(f, abspath)
        except Exception:
            # On garde silencieux: l'inclusion additionnelle n'est pas critique
            pass

    new_or_modified: List[str] = []
    unchanged: List[str] = []

    # 4) Mettre à jour mapping + détecter changements
    for basename, path in candidates.items():
        try:
            st = os.stat(path)
        except OSError:
            continue
        size = st.st_size
        mtime = int(st.st_mtime)

        reg_key = os.path.abspath(path)
        prev = registry.get(reg_key)
        current = {"size": size, "mtime": mtime}

        # mapping: préserver les URLs si déjà présentes
        entry = mapping.get(basename)
        if not isinstance(entry, dict):
            entry = {}
        entry_path = entry.get("path")
        if entry_path != path:
            entry["path"] = path
        mapping[basename] = entry

        if prev == current:
            unchanged.append(path)
        else:
            new_or_modified.append(path)
            registry[reg_key] = current

    # 5) Sauvegardes
    _save_json(MAPPING_FILE, mapping)
    _save_json(REGISTRY_FILE, registry)

    return {"new_or_modified": new_or_modified, "unchanged": unchanged}
