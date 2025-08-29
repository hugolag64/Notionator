# services/local_search.py
from __future__ import annotations

import os
import json
import fitz  # PyMuPDF
import faiss
import numpy as np
from typing import List, Dict, Any, Tuple

# OCR
import pytesseract
from PIL import Image
from io import BytesIO

# --- Chemins absolus vers /data (robuste) ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR     = os.path.join(PROJECT_ROOT, "data")

# --- .env ---
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
from services import pdf_sync  # scan incrémental + mapping/registry

# --- Réglages (chemins absolus) ---
INDEX_PATH    = os.path.join(DATA_DIR, "pdf_index.faiss")
META_PATH     = os.path.join(DATA_DIR, "pdf_metadata.json")
PDF_FOLDER    = os.path.join(DATA_DIR, "pdf")
MAPPING_FILE  = os.path.join(DATA_DIR, "pdf_mapping.json")
REGISTRY_FILE = os.path.join(DATA_DIR, "pdf_registry.json")  # utilisé par pdf_sync

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL  = "gpt-4o"

MAX_SNIPPETS = 6
MAX_CONTEXT_CHARS = 12000

# --- OCR config ---
TESSERACT_EXE = os.getenv("TESSERACT_EXE")  # ex: C:\Program Files\Tesseract-OCR\tesseract.exe
if TESSERACT_EXE and os.path.exists(TESSERACT_EXE):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
OCR_LANG = os.getenv("OCR_LANG", "fra+eng")

# --- Client OpenAI ---
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError(
        "❌ Clé OpenAI absente. Ajoute OPENAI_API_KEY=sk-... dans ton fichier .env à la racine."
    )
client = OpenAI(api_key=api_key)

def _sanity_check_openai() -> None:
    """Ping minimal pour vérifier embeddings + chat (coût négligeable)."""
    try:
        _ = client.embeddings.create(model=EMBED_MODEL, input="ping").data[0].embedding
    except Exception as e:
        raise RuntimeError(f"❌ Test embeddings échoué ({EMBED_MODEL}): {e}")
    try:
        r = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "OK ?"}],
            max_tokens=2,
            temperature=0.0,
        )
        _ = (r.choices[0].message.content or "").strip()
    except Exception as e:
        raise RuntimeError(f"❌ Test chat échoué ({CHAT_MODEL}): {e}")

# ------------------------------
# Utils JSON / mapping
# ------------------------------
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

def _load_mapping() -> Dict[str, Dict[str, str]]:
    return _load_json(MAPPING_FILE, {})

MAPPING = _load_mapping()

def _resolve_pdf_source(basename: str) -> Dict[str, str | None]:
    info = MAPPING.get(basename, {})
    path = info.get("path")
    url  = info.get("url")
    if not path:
        candidate = os.path.abspath(os.path.join(PDF_FOLDER, basename))
        if os.path.exists(candidate):
            path = candidate
    return {"path": path, "url": url}

# ------------------------------
# Normalisation des racines
# ------------------------------
def _as_str_roots(roots_like: Any) -> List[str]:
    """
    Normalise drive_roots en liste[str] pour pdf_sync.scan_and_update_mapping.
    Accepte:
      - str
      - dict {"path": "..."}
      - list/tuple de str|dict
      - None -> []
    """
    out: List[str] = []
    if isinstance(roots_like, str):
        out.append(roots_like)
    elif isinstance(roots_like, dict):
        p = roots_like.get("path")
        if isinstance(p, str) and p:
            out.append(p)
    elif isinstance(roots_like, (list, tuple)):
        for it in roots_like:
            if isinstance(it, str) and it:
                out.append(it)
            elif isinstance(it, dict):
                p = it.get("path")
                if isinstance(p, str) and p:
                    out.append(p)
    return [p for p in out if isinstance(p, str) and p]

# ------------------------------
# Chunking & extraction
# ------------------------------
def _chunk_words(text: str, words_per_chunk: int = 250, overlap: int = 30) -> List[str]:
    words = text.split()
    chunks = []
    i = 0
    n = len(words)
    step = max(1, words_per_chunk - overlap)
    while i < n:
        chunk = " ".join(words[i:i + words_per_chunk]).strip()
        if chunk:
            chunks.append(chunk)
        i += step
    return chunks

def _normalize_whitespace(s: str) -> str:
    return " ".join(s.replace("\xa0", " ").split())

def _ocr_page_to_text(page: fitz.Page) -> str:
    """OCR de secours via rendu raster de la page."""
    zoom = 2.0  # ~288 DPI visuels, bon compromis
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(BytesIO(pix.tobytes("png")))
    txt = pytesseract.image_to_string(img, lang=OCR_LANG or "fra")
    return _normalize_whitespace(txt.strip())

def extract_chunks_from_pdf(pdf_path: str, basename: str) -> List[Dict[str, Any]]:
    """
    Extraction hybride :
      1) texte natif
      2) si vide/mini → OCR
    """
    src = _resolve_pdf_source(basename)
    out: List[Dict[str, Any]] = []

    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc, start=1):
        try:
            text = _normalize_whitespace(page.get_text().strip())
        except Exception:
            text = ""

        if len(text) < 30:
            try:
                ocr_text = _ocr_page_to_text(page)
                if len(ocr_text) >= 30:
                    text = ocr_text
            except Exception:
                pass

        if not text:
            continue

        for piece in _chunk_words(text, 250, 30):
            out.append({
                "title": basename,
                "page": page_num,
                "text": piece,
                "path": src["path"],
                "url":  src["url"],
            })
    return out

# ---- AJOUT: compter les pages ----
def _get_page_count(pdf_path: str) -> int:
    try:
        with fitz.open(pdf_path) as d:
            return d.page_count
    except Exception:
        return 0
# ----------------------------------

# ------------------------------
# Index build / load
# ------------------------------
def _create_index_from_embeddings(all_embs: List[List[float]]) -> faiss.Index:
    dim = len(all_embs[0])
    index = faiss.IndexFlatL2(dim)
    index.add(np.array(all_embs, dtype="float32"))
    return index

def build_index_full(
    drive_roots: Any = None,
    max_size_kb: int = 80_000,
    verbose: bool = True
) -> None:
    """Build complet en s'appuyant sur pdf_sync (donc filtré par taille)."""
    _sanity_check_openai()
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    os.makedirs(PDF_FOLDER, exist_ok=True)

    # 1) Scanner avec filtre de taille pour remplir/mettre à jour le mapping
    roots = _as_str_roots(drive_roots) or [r"G:\Mon Drive\Médecine"]
    pdf_sync.scan_and_update_mapping(
        roots=roots,
        also_include_folder=PDF_FOLDER,
        max_size_kb=max_size_kb
    )
    mapping = _load_mapping()

    # 2) Constituer la liste finale des PDFs à indexer depuis le mapping (filtré)
    pdf_files: List[Tuple[str, str]] = []
    for base, info in mapping.items():
        p = info.get("path")
        if not p or not os.path.exists(p):
            continue
        try:
            size_kb = os.path.getsize(p) // 1024
        except OSError:
            continue
        if size_kb > max_size_kb:
            if verbose:
                print(f"[Index] IGNORE (>{max_size_kb} Ko): {base} ({size_kb} Ko)")
            continue
        pdf_files.append((p, os.path.basename(p)))

    if verbose:
        print(f"[Index] PDFs détectés (filtrés) : {len(pdf_files)}")

    all_meta: List[Dict[str, Any]] = []
    all_embs: List[List[float]] = []

    for pdf_path, basename in pdf_files:
        try:
            chunks = extract_chunks_from_pdf(pdf_path, basename)
            if verbose:
                print(f"  - {basename}: {len(chunks)} chunks")

            # ---- AJOUT: écrire les métadonnées PDF ----
            try:
                pages = _get_page_count(pdf_path)
                save_pdf_meta(pdf_path, pages, len(chunks))
            except Exception as e:
                if verbose:
                    print(f"[meta] skip {basename}: {e}")
            # -------------------------------------------

            for rec in chunks:
                emb = client.embeddings.create(model=EMBED_MODEL, input=rec["text"]).data[0].embedding
                all_embs.append(emb)
                all_meta.append(rec)
        except Exception as e:
            print(f"[WARN] Extraction échouée pour {basename} : {e}")

    if not all_embs:
        raise RuntimeError("Aucun embedding généré après filtrage. Vérifie tes PDFs / mapping / tailles / OCR.")

    index = _create_index_from_embeddings(all_embs)
    faiss.write_index(index, INDEX_PATH)
    _save_json(META_PATH, all_meta)
    if verbose:
        print(f"[OK] Index sauvegardé ({len(all_meta)} chunks).")

def _append_to_index(new_embs: List[List[float]]) -> faiss.Index:
    """Charge l'index existant et ajoute de nouveaux vecteurs."""
    index = faiss.read_index(INDEX_PATH)
    index.add(np.array(new_embs, dtype="float32"))
    faiss.write_index(index, INDEX_PATH)
    return index

# ---------- ONE-SHOT: backfill 40–80 Mo ----------
def _indexed_basenames_from_meta() -> set[str]:
    """Basenames déjà présents dans META_PATH (considérés comme indexés)."""
    meta = _load_json(META_PATH, [])
    return {os.path.basename(m.get("path") or m.get("title") or "") for m in meta if (m.get("path") or m.get("title"))}

def _collect_pdf_candidates_by_size(
    drive_roots: List[str],
    min_size_kb: int,
    max_size_kb: int,
    include_pdf_folder: bool = True,
) -> List[Tuple[str, str, int]]:
    """
    Retourne une liste [(abs_path, basename, size_kb)] des PDFs dont la taille est dans [min, max].
    On scanne les roots + (optionnel) data/pdf.
    """
    seen: dict[str, Tuple[str, int]] = {}
    roots = list(drive_roots or [])
    if include_pdf_folder:
        roots.append(PDF_FOLDER)

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
                if size_kb < min_size_kb or size_kb > max_size_kb:
                    continue
                if name not in seen:
                    seen[name] = (abspath, size_kb)

    out: List[Tuple[str, str, int]] = []
    for base, (p, sz) in seen.items():
        out.append((p, base, sz))
    return out

def backfill_large_pdfs_between(
    min_size_kb: int = 40_000,
    max_size_kb: int = 80_000,
    drive_roots: Any = None,
    verbose: bool = True
) -> None:
    """
    ONE-SHOT: n'ajoute à l'index QUE les PDFs entre min_size_kb et max_size_kb
    qui ne sont pas déjà présents dans META_PATH. Ne touche pas au reste.
    """
    _sanity_check_openai()
    roots = _as_str_roots(drive_roots) or [r"G:\Mon Drive\Médecine"]

    # 1) Met à jour le mapping jusqu'à max_size_kb (pour avoir les liens/paths frais)
    pdf_sync.scan_and_update_mapping(
        roots=roots,
        also_include_folder=PDF_FOLDER,
        max_size_kb=max_size_kb
    )

    # 2) Liste des fichiers candidats par taille
    candidates = _collect_pdf_candidates_by_size(
        drive_roots=roots,
        min_size_kb=min_size_kb,
        max_size_kb=max_size_kb,
        include_pdf_folder=True,
    )

    # 3) Filtre ceux déjà indexés (via META_PATH)
    already = _indexed_basenames_from_meta()
    todo = [(p, b, s) for (p, b, s) in candidates if b not in already]

    if verbose:
        print(f"[Backfill] candidats {min_size_kb//1024}-{max_size_kb//1024} Mo : {len(candidates)}")
        print(f"[Backfill] déjà indexés (par basename): {len(candidates) - len(todo)}")
        print(f"[Backfill] à ajouter: {len(todo)}")

    if not todo:
        if verbose:
            print("[Backfill] Rien à ajouter.")
        return

    # 4) Assure l'existence d'un index (sinon build filtré max_size_kb)
    if not (os.path.exists(INDEX_PATH) and os.path.exists(META_PATH)):
        if verbose:
            print("[Backfill] Aucun index trouvé -> construction complète filtrée…")
        build_index_full(drive_roots=roots, max_size_kb=max_size_kb, verbose=verbose)
        return  # le build complet a déjà inclus la tranche

    # 5) Append incrémental pour la tranche ciblée
    meta_existing: List[Dict[str, Any]] = _load_json(META_PATH, [])
    new_meta: List[Dict[str, Any]] = []
    new_embs: List[List[float]] = []

    mapping = _load_mapping()

    for path, basename, size_kb in todo:
        try:
            if not os.path.exists(path):
                info = mapping.get(basename, {})
                alt = info.get("path")
                if alt and os.path.exists(alt):
                    path = alt
                else:
                    if verbose:
                        print(f"[Backfill] SKIP (introuvable): {basename}")
                    continue

            chunks = extract_chunks_from_pdf(path, basename)
            if verbose:
                print(f"  - {basename} ({size_kb} Ko): {len(chunks)} chunks")

            # ---- AJOUT: écrire les métadonnées PDF ----
            try:
                pages = _get_page_count(path)
                save_pdf_meta(path, pages, len(chunks))
            except Exception as e:
                if verbose:
                    print(f"[meta] skip {basename}: {e}")
            # -------------------------------------------

            for rec in chunks:
                emb = client.embeddings.create(model=EMBED_MODEL, input=rec["text"]).data[0].embedding
                new_embs.append(emb)
                new_meta.append(rec)
        except Exception as e:
            print(f"[Backfill][WARN] {basename}: {e}")

    if not new_embs:
        if verbose:
            print("[Backfill] Aucun chunk généré (aucun ajout).")
        return

    _append_to_index(new_embs)
    meta_existing.extend(new_meta)
    _save_json(META_PATH, meta_existing)

    if verbose:
        print(f"[Backfill] Terminé (+{len(new_meta)} chunks).")
# ---------- /ONE-SHOT ----------

def ensure_index_up_to_date(
    drive_roots: Any = None,
    verbose: bool = True,
    max_size_kb: int = 80_000  # taille max PDF
) -> None:
    """
    1) Scan incrémental filtré (pdf_sync) -> mapping/registry
    2) Si index inexistant -> build complet (filtré)
    3) Sinon -> n'indexe QUE les fichiers nouveaux/modifiés (filtrés) et append
    """
    _sanity_check_openai()
    roots = _as_str_roots(drive_roots) or [r"G:\Mon Drive\Médecine"]

    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    os.makedirs(PDF_FOLDER, exist_ok=True)

    # 1) Scan incrémental
    scan = pdf_sync.scan_and_update_mapping(
        roots=roots,
        also_include_folder=PDF_FOLDER,
        max_size_kb=max_size_kb
    )
    changed = scan.get("new_or_modified", []) or []

    # 2) Si pas d'index -> build complet (filtré)
    if not (os.path.exists(INDEX_PATH) and os.path.exists(META_PATH)):
        if verbose:
            print("[Index] Absent -> construction complète…")
        build_index_full(drive_roots=roots, max_size_kb=max_size_kb, verbose=verbose)
        return

    if not changed:
        if verbose:
            print("[Index] À jour (aucun nouveau PDF/aucune modif détectée).")
        return

    # 3) Indexation incrémentale
    if verbose:
        print(f"[Index] Nouv./modifiés: {len(changed)} → indexation incrémentale…")

    meta_existing: List[Dict[str, Any]] = _load_json(META_PATH, [])
    new_meta: List[Dict[str, Any]] = []
    new_embs: List[List[float]] = []

    for path in changed:
        try:
            size_kb = os.path.getsize(path) // 1024
        except OSError:
            continue
        if size_kb > max_size_kb:
            if verbose:
                base = os.path.basename(path)
                print(f"[Index] IGNORE (>{max_size_kb} Ko): {base} ({size_kb} Ko)")
            continue

        basename = os.path.basename(path)
        try:
            chunks = extract_chunks_from_pdf(path, basename)
            if verbose:
                print(f"  - {basename}: {len(chunks)} chunks (MAJ)")

            # ---- AJOUT: écrire les métadonnées PDF ----
            try:
                pages = _get_page_count(path)
                save_pdf_meta(path, pages, len(chunks))
            except Exception as e:
                if verbose:
                    print(f"[meta] skip {basename}: {e}")
            # -------------------------------------------

            for rec in chunks:
                emb = client.embeddings.create(model=EMBED_MODEL, input=rec["text"]).data[0].embedding
                new_embs.append(emb)
                new_meta.append(rec)
        except Exception as e:
            print(f"[WARN] Extraction échouée pour {basename} : {e}")

    if not new_embs:
        if verbose:
            print("[Index] Rien à ajouter (aucun chunk extrait après filtrage).")
        return

    _append_to_index(new_embs)
    meta_existing.extend(new_meta)
    _save_json(META_PATH, meta_existing)

    if verbose:
        print(f"[OK] Index incrémental terminé (+{len(new_meta)} chunks).")

# ------------------------------
# Recherche & réponse
# ------------------------------
def _trim_context_blocks(blocks: List[str], max_chars: int) -> List[str]:
    total = 0
    kept = []
    for b in blocks:
        if total + len(b) + 2 > max_chars:
            break
        kept.append(b)
        total += len(b) + 2
    return kept

def load_index() -> tuple[faiss.Index | None, List[Dict[str, Any]]]:
    """Charge l'index + meta si disponibles. Ne lève pas d'exception."""
    try:
        if not (os.path.exists(INDEX_PATH) and os.path.exists(META_PATH)):
            return None, []
        index = faiss.read_index(INDEX_PATH)
        meta = _load_json(META_PATH, [])
        return index, meta
    except Exception:
        return None, []

def semantic_search(query: str, k: int = 4) -> List[Dict[str, Any]]:
    # ⚠️ pas de ensure_index_up_to_date ici (évite les blocages pendant la question)
    index, meta = load_index()
    if index is None or index.ntotal == 0:
        return []

    try:
        emb_resp = client.embeddings.create(model=EMBED_MODEL, input=query)
        if not emb_resp.data:
            return []
        q_emb = emb_resp.data[0].embedding
    except Exception:
        return []

    k = max(1, min(int(k), int(index.ntotal)))
    D, I = index.search(np.array([q_emb], dtype="float32"), k)

    out = []
    for i in I[0]:
        if 0 <= i < len(meta):
            out.append(meta[i])
    return out

def answer_with_sources(query: str, k: int = MAX_SNIPPETS) -> Dict[str, Any]:
    results = semantic_search(query, k=k)

    if not results:
        return {
            "answer": (
                "Je n'ai trouvé aucun extrait pertinent dans tes sources indexées.\n"
                "• Vérifie que l'index FAISS contient bien tes cours (menu: Scanner / build).\n"
                "• Ou reformule ta question."
            ),
            "sources": []
        }

    unique_sources = []
    seen = set()
    ctx_blocks = []

    for r in results:
        key = (r.get("title"), r.get("page"), r.get("path"), r.get("url"))
        if key not in seen:
            seen.add(key)
            unique_sources.append({
                "title": r.get("title"),
                "page": r.get("page"),
                "path": r.get("path"),
                "url":  r.get("url"),
            })
        ctx_blocks.append(f"[{r.get('title')} – p.{r.get('page')}]\n{r.get('text')}")

    ctx_blocks = _trim_context_blocks(ctx_blocks, MAX_CONTEXT_CHARS)
    context_text = "\n\n".join(ctx_blocks)

    prompt = f"""Tu es un assistant de révision médicale.
Tu dois répondre UNIQUEMENT à partir des extraits fournis et rester concis.
Si les extraits ne suffisent pas, dis-le.

Extraits :
{context_text}

Question : {query}

Consignes :
- Réponse claire (8–12 lignes max), listes si utile.
- Termine par "Sources" : Titre – p.X, Titre – p.Y
"""

    try:
        chat = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = ""
        if chat.choices and chat.choices[0].message and chat.choices[0].message.content:
            content = chat.choices[0].message.content.strip()
        if not content:
            content = "Aucune réponse générée à partir des extraits."
    except Exception as e:
        content = f"Erreur LLM: {e!r}"

    return {"answer": content, "sources": unique_sources}

def stream_answer_chunks(query: str, k: int = MAX_SNIPPETS):
    """Génère des morceaux de texte (stream) pour l’UI)."""
    results = semantic_search(query, k=k)
    if not results:
        yield ("Je n'ai trouvé aucun extrait pertinent dans tes sources indexées.\n"
               "• Lance le scan/index dans Notionator.\n"
               "• Ou reformule ta question.")
        return

    unique_sources, seen, ctx_blocks = [], set(), []
    for r in results:
        key = (r.get("title"), r.get("page"), r.get("path"), r.get("url"))
        if key not in seen:
            seen.add(key)
            unique_sources.append({
                "title": r.get("title"),
                "page": r.get("page"),
                "path": r.get("path"),
                "url":  r.get("url"),
            })
        ctx_blocks.append(f"[{r.get('title')} – p.{r.get('page')}]\n{r.get('text')}")

    ctx_blocks = _trim_context_blocks(ctx_blocks, MAX_CONTEXT_CHARS)
    context_text = "\n\n".join(ctx_blocks)

    prompt = f"""Tu es un assistant de révision médicale.
Tu dois répondre UNIQUEMENT à partir des extraits fournis et rester concis.
Si les extraits ne suffisent pas, dis-le.

Extraits :
{context_text}

Question : {query}

Consignes :
- Réponse claire (8–12 lignes max), listes si utile.
- Termine par "Sources" : Titre – p.X, Titre – p.Y
"""

    try:
        stream = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            stream=True,
        )
        for ev in stream:
            try:
                delta = ev.choices[0].delta.content
                if delta:
                    yield delta
            except Exception:
                continue
    except Exception as e:
        yield f"\n\n[Erreur LLM: {e!r}]"

# ------------------------------
# Ouverture d’une source
# ------------------------------
import webbrowser
import pathlib

def open_source(src: Dict[str, Any]) -> None:
    page = int(src.get("page", 1))
    path = src.get("path")
    url  = src.get("url")

    if path and os.path.exists(path):
        p = pathlib.Path(path).absolute().as_uri()
        webbrowser.open_new(f"{p}#page={page}")
        return
    if url:
        glue = "#" if "#" not in url else "&"
        webbrowser.open_new(f"{url}{glue}page={page}")
        return

    candidate = os.path.abspath(os.path.join(PDF_FOLDER, src["title"]))
    if os.path.exists(candidate):
        webbrowser.open_new(pathlib.Path(candidate).absolute().as_uri() + f"#page={page}")
    else:
        print(f"[WARN] Impossible d’ouvrir la source : {src}")

# --------- API attendue par l'UI (ai_search) ---------
def ask(query: str) -> str:
    try:
        res = answer_with_sources(query, k=MAX_SNIPPETS)
        return res.get("answer", "Aucune réponse.")
    except Exception as e:
        return f"Erreur local_search.ask: {e!r}"

def stream(query: str):
    """Proxy simple pour l’UI."""
    return stream_answer_chunks(query, k=MAX_SNIPPETS)

def ask_with_sources(query: str) -> dict:
    """Réponse + sources (pour affichage dédié)."""
    return answer_with_sources(query, k=MAX_SNIPPETS)

# ------------------------------
# Main: test rapide
# ------------------------------
if __name__ == "__main__":
    print("[Test] Vérification OpenAI…")
    _sanity_check_openai()
    print("[OK] API fonctionnelle ✔")

    # Scan + incrémental (par défaut sur G:\Mon Drive\Médecine + data/pdf), limite 80 Mo
    ensure_index_up_to_up_to_date = ensure_index_up_to_date  # alias pour clarté CLI
    ensure_index_up_to_date(verbose=True, max_size_kb=80_000)

    # Démo si index dispo:
    q = "Principes du traitement de l'insuffisance cardiaque"
    try:
        resp = answer_with_sources(q, k=MAX_SNIPPETS)
        print("\n=== Réponse ===")
        print(resp["answer"])
        print("\n=== Sources ===")
        for s in resp["sources"]:
            print(f"- {s['title']} – p.{s['page']}")
    except Exception as e:
        print(f"[ERR] Demo: {e}")
