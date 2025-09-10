# services/pdf_autoscan.py
from __future__ import annotations
import os, json, time
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any

from services.logger import get_logger
from services.worker import run_io
from services.exclusive import run_exclusive
from services.actions_manager import BASE_FOLDER
from services.local_search import ensure_index_up_to_date
from utils.ui_queue import post
from config import MAX_PDF_SIZE_KB

# ---------- Hash rapide (option xxhash, fallback blake2b) ----------
try:
    import xxhash
    def fast_hash(p: str) -> str:
        h = xxhash.xxh3_64()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
except Exception:
    import hashlib
    def fast_hash(p: str) -> str:
        h = hashlib.blake2b(digest_size=16)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

logger = get_logger(__name__)

STATE_FILE = os.path.join("data", "pdf_autoscan_state.json")
SCAN_LIMIT = 1000        # max PDFs listés lors du quick-scan
QUICK_SCAN_BUDGET = 2.0  # secondes max pour le listing léger
MIN_SIZE = 4 * 1024      # ignore < 4 Ko (souvent vides)

@dataclass(frozen=True)
class Fingerprint:
    size: int
    mtime: float

# ---------- I/O sûrs (silencieux) ----------
def _safe_load(path: str) -> Dict[str, Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fp": {}}  # fp = {pdf_path: {"size": int, "mtime": float, "h": str?}}

def _safe_dump(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _toast(title: str, message: str) -> None:
    try:
        post({"type": "toast", "title": title, "message": message})
    except Exception:
        try:
            post({"title": title, "message": message, "level": "info"})
        except Exception:
            pass

# ---------- scan & diff (silencieux) ----------
def _iter_pdfs(base: str, limit: int, budget_s: float) -> List[str]:
    start = time.time()
    out: List[str] = []
    for root, _, files in os.walk(base):
        for name in files:
            if name.lower().endswith(".pdf"):
                full = os.path.join(root, name)
                out.append(full)
                if len(out) >= limit or (time.time() - start) > budget_s:
                    return out
    return out

def _fingerprint(path: str) -> Fingerprint | None:
    try:
        st = os.stat(path)
        if st.st_size < MIN_SIZE:
            return None
        return Fingerprint(size=int(st.st_size), mtime=float(st.st_mtime))
    except Exception:
        return None

def _detect_changes(current: Dict[str, Fingerprint],
                    previous: Dict[str, Dict]) -> Tuple[List[str], Dict[str, Dict]]:
    """
    Compare l'état courant à l'état précédent.
    - Retourne (changed_paths, new_state)
    - Calcule un hash uniquement pour les fichiers détectés comme nouveaux/modifiés.
    - Conserve le hash précédent pour les fichiers inchangés (si déjà présent).
    """
    changed: List[str] = []
    new_state: Dict[str, Dict] = {}
    for p, fp in current.items():
        if fp is None:
            continue
        prev = previous.get(p)
        changed_flag = (not prev) or prev.get("size") != fp.size or abs(prev.get("mtime", 0.0) - fp.mtime) > 1e-3
        if changed_flag:
            try:
                h = fast_hash(p)
            except Exception:
                h = None
            print(f"[autoscan] Nouveau/modifié: {os.path.basename(p)}" + (f" (hash={h[:8]})" if h else ""))
            changed.append(p)
            new_state[p] = {"size": fp.size, "mtime": fp.mtime, "h": h}
        else:
            # Fichier identique → on recopie l'ancien état (y compris 'h' si présent)
            keep = {"size": fp.size, "mtime": fp.mtime}
            if isinstance(prev, dict) and "h" in prev:
                keep["h"] = prev["h"]
            new_state[p] = keep
    return changed, new_state

def _as_str_roots(base_like: Any) -> List[str]:
    """
    Normalise BASE_FOLDER vers une liste[str] exploitable par pdf_sync.
    """
    out: List[str] = []
    if isinstance(base_like, str):
        out = [base_like]
    elif isinstance(base_like, dict):
        p = base_like.get("path")
        if isinstance(p, str) and p:
            out = [p]
    elif isinstance(base_like, (list, tuple)):
        for it in base_like:
            if isinstance(it, str) and it:
                out.append(it)
            elif isinstance(it, dict):
                p = it.get("path")
                if isinstance(p, str) and p:
                    out.append(p)
    return [p for p in out if isinstance(p, str) and p]

# ---------- manager ----------
class AutoScanManager:
    """
    Au boot:
    - Quick listing des PDFs (budget/limite)
    - Diff avec l'état précédent (size, mtime [+ hash sur changements])
    - Si changements → indexation complète (même flux que le bouton)
    - Sauvegarde de l'état final (en conservant les hash connus)
    """
    def __init__(self, base_folder: str | None = None):
        self.base = base_folder or BASE_FOLDER

    def check_and_maybe_scan(self) -> None:
        # BASE_FOLDER doit exister
        roots = _as_str_roots(self.base)
        use_root = roots[0] if roots else None
        if not use_root or not os.path.isdir(use_root):
            return

        prev = _safe_load(STATE_FILE).get("fp", {})
        pdfs = _iter_pdfs(use_root, limit=SCAN_LIMIT, budget_s=QUICK_SCAN_BUDGET)

        current: Dict[str, Fingerprint] = {}
        for p in pdfs:
            fp = _fingerprint(p)
            if fp:
                current[p] = fp

        changed, new_state = _detect_changes(current, prev)
        if not changed:
            _safe_dump(STATE_FILE, {"fp": new_state})
            return

        _toast("Indexation PDF", "Nouveaux PDF détectés → indexation en tâche de fond.")

        def _run_full_index():
            try:
                # IMPORTANT: on passe bien une LISTE DE STR à l'indexeur
                ensure_index_up_to_date(
                    drive_roots=roots,
                    verbose=True,
                    max_size_kb=MAX_PDF_SIZE_KB
                )
            finally:
                # État final rafraîchi (listing plus généreux pour fiabiliser)
                # On essaie de conserver les hash existants et d'ajouter
                # le hash pour les fichiers qui ont changé.
                all_prev = _safe_load(STATE_FILE).get("fp", {}) or {}
                all_pdfs = _iter_pdfs(use_root, limit=SCAN_LIMIT, budget_s=10.0)
                final_state: Dict[str, Dict] = {}
                changed_set = set(changed)

                for p in all_pdfs:
                    fp = _fingerprint(p)
                    if not fp:
                        continue
                    rec = {"size": fp.size, "mtime": fp.mtime}
                    prev_meta = all_prev.get(p)
                    if prev_meta and prev_meta.get("size") == fp.size and abs(prev_meta.get("mtime", 0.0) - fp.mtime) <= 1e-3:
                        # inchangé depuis le dernier dump → on recopie le hash si présent
                        if "h" in prev_meta:
                            rec["h"] = prev_meta["h"]
                    elif p in changed_set:
                        # fichier marqué "changé" → on (re)calcule le hash maintenant
                        try:
                            rec["h"] = fast_hash(p)
                        except Exception:
                            pass
                    final_state[p] = rec

                _safe_dump(STATE_FILE, {"fp": final_state})

        run_io(run_exclusive, "pdf_index", _run_full_index)
