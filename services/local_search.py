# services/local_search.py
from __future__ import annotations

import os
import json
import math
import re
import itertools
import webbrowser
import pathlib
from io import BytesIO
from typing import List, Dict, Any, Tuple, Iterable
from threading import RLock
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(override=True)


# ──────────────────────────────────────────────────────────────────────────────
# Chemins & config
# ──────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR     = PROJECT_ROOT / "data"

INDEX_PATH    = DATA_DIR / "pdf_index.faiss"
META_PATH     = DATA_DIR / "pdf_metadata.json"
PDF_FOLDER    = DATA_DIR / "pdf"
MAPPING_FILE  = DATA_DIR / "pdf_mapping.json"
REGISTRY_FILE = DATA_DIR / "pdf_registry.json"

# .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── OpenAI (lecture robuste + options)
raw_key = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY = (raw_key or "").strip().strip('"\'')

USE_LLM = os.getenv("USE_LLM", "1") == "1"         # mettre USE_LLM=0 dans .env pour forcer 100% local
OPENAI_PROJECT = os.getenv("OPENAI_PROJECT") or None

# Réglages modèles
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
CHAT_MODEL  = os.getenv("CHAT_MODEL",  "gpt-4o-mini")
MAX_SNIPPETS = 6
MAX_CONTEXT_CHARS = 12000

# OCR (facultatif)
TESSERACT_EXE = os.getenv("TESSERACT_EXE")  # ex: C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_LANG = os.getenv("OCR_LANG", "fra+eng")

# ──────────────────────────────────────────────────────────────────────────────
# Imports optionnels (FAISS / OpenAI / OCR)
# ──────────────────────────────────────────────────────────────────────────────
FAISS_AVAILABLE = True
try:
    import faiss
except Exception:
    FAISS_AVAILABLE = False

FITZ_AVAILABLE = True
try:
    import fitz  # PyMuPDF
except Exception:
    FITZ_AVAILABLE = False

OCR_AVAILABLE = False
try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
    if TESSERACT_EXE and os.path.exists(TESSERACT_EXE):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
except Exception:
    OCR_AVAILABLE = False

OPENAI_AVAILABLE = False
client = None
try:
    if USE_LLM and OPENAI_API_KEY:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY, project=OPENAI_PROJECT)  # project optionnel
        OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False
    client = None

# Dépendances calcul
try:
    import numpy as np
except Exception:
    np = None
    FAISS_AVAILABLE = False

# Services internes
try:
    from services import pdf_sync  # scan incrémental + mapping/registry
except Exception:
    pdf_sync = None  # on garde la recherche même sans scan auto

# ──────────────────────────────────────────────────────────────────────────────
# Utilitaires JSON & mapping
# ──────────────────────────────────────────────────────────────────────────────
def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_mapping() -> Dict[str, Dict[str, str]]:
    return _load_json(MAPPING_FILE, {})

MAPPING = _load_mapping()

def _resolve_pdf_source(basename: str) -> Dict[str, str | None]:
    info = MAPPING.get(basename, {}) or {}
    path = info.get("path")
    url  = info.get("url")
    if not path:
        candidate = (PDF_FOLDER / basename).resolve()
        if candidate.exists():
            path = str(candidate)
    return {"path": path, "url": url}

# ──────────────────────────────────────────────────────────────────────────────
# Extraction PDF (texte natif + OCR)
# ──────────────────────────────────────────────────────────────────────────────
_WORDS = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")

def _normalize_whitespace(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())

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

def _ocr_page_to_text(page) -> str:
    if not (OCR_AVAILABLE and FITZ_AVAILABLE):
        return ""
    zoom = 2.0  # ~288 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(BytesIO(pix.tobytes("png")))
    return _normalize_whitespace(pytesseract.image_to_string(img, lang=OCR_LANG or "fra").strip())

def _get_page_count(pdf_path: str) -> int:
    if not FITZ_AVAILABLE:
        return 0
    try:
        with fitz.open(pdf_path) as d:
            return d.page_count
    except Exception:
        return 0

def extract_chunks_from_pdf(pdf_path: str, basename: str) -> List[Dict[str, Any]]:
    """
    Enregistrements “par page” :
    {title, page, text, path, url}
    → compatible avec ton pdf_metadata.json actuel.
    """
    out: List[Dict[str, Any]] = []
    src = _resolve_pdf_source(basename)

    if not FITZ_AVAILABLE:
        return out

    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc, start=1):
        try:
            text = _normalize_whitespace(page.get_text().strip())
        except Exception:
            text = ""

        if len(text) < 30 and OCR_AVAILABLE:
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

# Petit journal “meta PDF” (non bloquant)
def save_pdf_meta(pdf_path: str, pages: int, chunks: int) -> None:
    try:
        summary_path = DATA_DIR / "pdf_meta_summary.json"
        summary = _load_json(summary_path, {})
        base = os.path.basename(pdf_path)
        summary[base] = {"pages": int(pages), "chunks": int(chunks), "path": pdf_path}
        _save_json(summary_path, summary)
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
# Construction / mise à jour FAISS (optionnelle)
# ──────────────────────────────────────────────────────────────────────────────
def _create_index_from_embeddings(all_embs: List[List[float]]):
    if not (FAISS_AVAILABLE and np is not None):
        return None
    dim = len(all_embs[0])
    index = faiss.IndexFlatL2(dim)
    index.add(np.array(all_embs, dtype="float32"))
    return index

def _embed(text: str) -> List[float] | None:
    if not OPENAI_AVAILABLE:
        return None
    try:
        return client.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
    except Exception:
        return None

def build_index_full(drive_roots: Any = None, max_size_kb: int = 80_000, verbose: bool = True) -> None:
    """
    Build complet FAISS + pdf_metadata.json (format page).
    Si OPENAI/FAISS indisponible → on remplit quand même pdf_metadata.json.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PDF_FOLDER.mkdir(parents=True, exist_ok=True)

    # 1) Scan mapping (si service dispo)
    roots = []
    if isinstance(drive_roots, (list, tuple)):
        roots = list(drive_roots)
    elif isinstance(drive_roots, str):
        roots = [drive_roots]
    if not roots:
        roots = [r"G:\Mon Drive\Médecine"]

    if pdf_sync:
        try:
            pdf_sync.scan_and_update_mapping(roots=roots, also_include_folder=str(PDF_FOLDER), max_size_kb=max_size_kb)
        except Exception as e:
            if verbose:
                print(f"[scan] warning: {e}")

    mapping = _load_mapping()

    # 2) Liste des PDFs filtrés par taille
    pdf_files: List[Tuple[str, str]] = []
    seen = set()
    for base, info in (mapping or {}).items():
        path = (info or {}).get("path")
        if not path or not os.path.exists(path):
            continue
        try:
            size_kb = os.path.getsize(path) // 1024
        except OSError:
            continue
        if size_kb > max_size_kb:
            if verbose:
                print(f"[Index] IGNORE (>{max_size_kb} Ko): {base} ({size_kb} Ko)")
            continue
        if base not in seen:
            seen.add(base)
            pdf_files.append((path, base))

    if verbose:
        print(f"[Index] PDFs détectés (filtrés) : {len(pdf_files)}")

    all_meta: List[Dict[str, Any]] = []
    all_embs: List[List[float]] = []

    for pdf_path, basename in pdf_files:
        try:
            chunks = extract_chunks_from_pdf(pdf_path, basename)
            if verbose:
                print(f"  - {basename}: {len(chunks)} chunks")
            try:
                pages = _get_page_count(pdf_path)
                save_pdf_meta(pdf_path, pages, len(chunks))
            except Exception:
                pass
            all_meta.extend(chunks)
            if OPENAI_AVAILABLE:
                for rec in chunks:
                    emb = _embed(rec["text"])
                    if emb:
                        all_embs.append(emb)
        except Exception as e:
            if verbose:
                print(f"[WARN] Extraction échouée pour {basename}: {e}")

    _save_json(META_PATH, all_meta)

    if OPENAI_AVAILABLE and FAISS_AVAILABLE and all_embs:
        index = _create_index_from_embeddings(all_embs)
        if index is not None:
            faiss.write_index(index, str(INDEX_PATH))
            if verbose:
                print(f"[OK] Index FAISS sauvegardé ({len(all_meta)} chunks).")
            return

    if verbose:
        print("[OK] Metadata rempli. FAISS/embeddings indisponibles → fallback local actif.")

def _append_to_index(new_embs: List[List[float]]):
    if not (FAISS_AVAILABLE and np is not None and INDEX_PATH.exists()):
        return None
    index = faiss.read_index(str(INDEX_PATH))
    index.add(np.array(new_embs, dtype="float32"))
    faiss.write_index(index, str(INDEX_PATH))
    return index

def ensure_index_up_to_date(drive_roots: Any = None, verbose: bool = True, max_size_kb: int = 80_000) -> None:
    """
    1) Scan incrémental (si service dispo)
    2) Build complet si index/metadata absents
    3) Append incrémental sinon
    """
    if not META_PATH.exists() or (FAISS_AVAILABLE and not INDEX_PATH.exists()):
        if verbose:
            print("[Index] Absent → construction complète…")
        build_index_full(drive_roots=drive_roots, max_size_kb=max_size_kb, verbose=verbose)
        return

    if not pdf_sync:
        if verbose:
            print("[Index] pdf_sync indisponible → pas d’incrémental.")
        return

    try:
        scan = pdf_sync.scan_and_update_mapping(
            roots=drive_roots if isinstance(drive_roots, (list, tuple)) else [drive_roots] if isinstance(drive_roots, str) else [r"G:\Mon Drive\Médecine"],
            also_include_folder=str(PDF_FOLDER),
            max_size_kb=max_size_kb
        )
    except Exception as e:
        if verbose:
            print(f"[scan] warning: {e}")
        return

    changed = (scan or {}).get("new_or_modified", []) or []
    if not changed:
        if verbose:
            print("[Index] À jour (aucune modification).")
        return

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
            try:
                pages = _get_page_count(path)
                save_pdf_meta(path, pages, len(chunks))
            except Exception:
                pass
            new_meta.extend(chunks)
            if OPENAI_AVAILABLE:
                for rec in chunks:
                    emb = _embed(rec["text"])
                    if emb:
                        new_embs.append(emb)
        except Exception as e:
            if verbose:
                print(f"[WARN] Extraction échouée pour {basename}: {e}")

    if new_meta:
        meta_existing.extend(new_meta)
        _save_json(META_PATH, meta_existing)

    if new_embs and FAISS_AVAILABLE:
        _append_to_index(new_embs)

    if verbose:
        print(f"[OK] Index incrémental terminé (+{len(new_meta)} chunks).")

# ──────────────────────────────────────────────────────────────────────────────
# Recherche locale — Fallback bm25-lite sur pdf_metadata.json (format par page)
# ──────────────────────────────────────────────────────────────────────────────
def _norm(text: str) -> List[str]:
    return [w.lower() for w in _WORDS.findall(text or "")]

class _LocalBM25:
    def __init__(self, meta_path: Path):
        self.meta_path = meta_path
        self._lock = RLock()
        self._mtime = 0.0
        self._docs: List[Dict[str, Any]] = []   # {"text", "source{name,path,url,page}"}
        self._inv: Dict[str, List[int]] = {}
        self._idf: Dict[str, float] = {}

    def _load_docs_from_meta(self) -> List[Dict[str, Any]]:
        data = _load_json(self.meta_path, [])
        docs: List[Dict[str, Any]] = []

        # Format actuel: liste plate {title, page, text, path, url}
        if data and isinstance(data[0], dict) and "text" in data[0] and "page" in data[0]:
            for e in data:
                txt = (e.get("text") or "").strip()
                if not txt:
                    continue
                docs.append({
                    "text": txt,
                    "source": {
                        "name": e.get("title") or e.get("name") or "Sans titre",
                        "path": e.get("path"),
                        "url":  e.get("url"),
                        "page": e.get("page"),
                    }
                })
            return docs

        # Anciens formats: {name/title} + {chunks/segments/pages/...}
        def extract_texts(entry) -> List[str]:
            for key in ("chunks", "segments", "pages", "texts", "content", "excerpts"):
                items = entry.get(key) or []
                out = []
                for ch in items:
                    if isinstance(ch, dict):
                        txt = ch.get("text") or ch.get("content") or ch.get("body")
                    else:
                        txt = str(ch)
                    if txt and txt.strip():
                        out.append(txt)
                if out:
                    return out
            return []

        for entry in data:
            src = {
                "name": entry.get("name") or entry.get("title") or entry.get("file") or "Sans titre",
                "path": entry.get("path") or entry.get("file_path") or entry.get("abs_path"),
            }
            texts = extract_texts(entry)
            for i, txt in enumerate(texts):
                docs.append({"text": txt, "source": {**src, "page": i}})
        return docs

    def _rebuild_if_needed(self):
        with self._lock:
            if not self.meta_path.exists():
                self._docs, self._inv, self._idf = [], {}, {}
                self._mtime = 0.0
                return
            mtime = self.meta_path.stat().st_mtime
            if mtime == self._mtime and self._docs:
                return
            self._mtime = mtime

            self._docs = self._load_docs_from_meta()
            self._inv.clear()
            df: Dict[str, int] = {}
            for idx, d in enumerate(self._docs):
                for w in set(_norm(d["text"])):
                    self._inv.setdefault(w, []).append(idx)
                    df[w] = df.get(w, 0) + 1
            N = max(1, len(self._docs))
            self._idf = {w: math.log(1.0 + N / (dfw + 1.0)) for w, dfw in df.items()}

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> List[Dict[str, Any]]:
        self._rebuild_if_needed()
        if not self._docs:
            return []
        q_terms = set(_norm(query))
        scores: Dict[int, float] = {}
        for w in q_terms:
            idf = self._idf.get(w, 0.0)
            for idx in self._inv.get(w, []):
                scores[idx] = scores.get(idx, 0.0) + idf
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:max(1, top_k)]
        out: List[Dict[str, Any]] = []
        for idx, sc in ranked:
            if sc < min_score:
                continue
            d = self._docs[idx]
            out.append({
                "text": d["text"],
                "score": float(sc),
                "source": d["source"],
                "engine": "bm25-lite",
            })
        return out

_BM25 = _LocalBM25(META_PATH)

# ──────────────────────────────────────────────────────────────────────────────
# Recherche sémantique (FAISS + embeddings) — si disponible
# ──────────────────────────────────────────────────────────────────────────────
def _load_index_and_meta():
    if not META_PATH.exists():
        return None, []
    meta = _load_json(META_PATH, [])
    if not (FAISS_AVAILABLE and INDEX_PATH.exists() and np is not None):
        return None, meta
    try:
        index = faiss.read_index(str(INDEX_PATH))
        return index, meta
    except Exception:
        return None, meta

def semantic_search(query: str, k: int = 4) -> List[Dict[str, Any]]:
    index, meta = _load_index_and_meta()
    if index is None or not OPENAI_AVAILABLE or np is None:
        return []
    emb = _embed(query)
    if not emb:
        return []
    k = max(1, min(int(k), int(getattr(index, "ntotal", 0) or 0)))
    if k == 0:
        return []
    D, I = index.search(np.array([emb], dtype="float32"), k)
    out = []
    for i in I[0]:
        if 0 <= i < len(meta):
            out.append(meta[i])
    return out

# ──────────────────────────────────────────────────────────────────────────────
# IA : réponse à partir d’extraits (LLM si dispo, sinon formatage local)
# ──────────────────────────────────────────────────────────────────────────────
def _snippet(text: str, max_len: int = 360) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= max_len else s[:max_len].rstrip() + "…"

def _format_list(results: List[Dict[str, Any]]) -> str:
    if not results:
        return ("Je n'ai trouvé aucun extrait pertinent dans tes sources indexées.\n"
                "• Lance le scan/index dans Notionator.\n"
                "• Ou reformule ta question.")
    lines: List[str] = []
    for r in results:
        src = r.get("source", {}) or {}
        title = src.get("name", "Sans titre")
        page = src.get("page", "?")
        score = r.get("score", 0.0)
        lines.append(f"• {title} (p.{page}) — {score:.2f}\n  {_snippet(r.get('text',''))}\n")
    return "\n".join(lines).strip()

def _gpt_answer_from_context(query: str, ctx_blocks: List[str]) -> str:
    """
    Si LLM indisponible ou erreur (401, invalid api…),
    on renvoie simplement les extraits – pas d'erreur utilisateur.
    """
    if not (OPENAI_AVAILABLE and client):
        return "\n\n".join(ctx_blocks)

    prompt = f"""Tu es un assistant de révision médicale.
Tu dois répondre UNIQUEMENT à partir des extraits fournis et rester concis.
Si les extraits ne suffisent pas, dis-le.

Extraits :
{'\n\n'.join(ctx_blocks)}

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
        return content or "\n\n".join(ctx_blocks)
    except Exception as e:
        # 401 / clé invalide → on coupe le LLM pour la session, pas d'erreur visible
        if "invalid_api_key" in str(e).lower() or "401" in str(e):
            globals()["OPENAI_AVAILABLE"] = False
            return "\n\n".join(ctx_blocks)
        # autres erreurs : on reste silencieux
        return "\n\n".join(ctx_blocks)

def answer_with_sources(query: str, k: int = MAX_SNIPPETS) -> Dict[str, Any]:
    # 1) Essai sémantique si possible
    sem = semantic_search(query, k=k)
    results: List[Dict[str, Any]] = []
    if sem:
        seen = set()
        for r in sem:
            key = (r.get("title"), r.get("page"), r.get("path"), r.get("url"))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "text": r.get("text", ""),
                "score": 1.0,  # score inconnu
                "source": {
                    "name": r.get("title", "Sans titre"),
                    "page": r.get("page"),
                    "path": r.get("path"),
                    "url":  r.get("url"),
                }
            })

    # 2) Fallback bm25-lite
    if not results:
        results = _BM25.search(query, top_k=k, min_score=0.0)

    if not results:
        return {
            "answer": (
                "Je n'ai trouvé aucun extrait pertinent dans tes sources indexées.\n"
                "• Lance le scan/index dans Notionator.\n"
                "• Ou reformule ta question."
            ),
            "sources": []
        }

    # Contexte (limite taille)
    ctx_blocks: List[str] = []
    unique_sources = []
    seen_src = set()
    total = 0
    for r in results:
        src = r.get("source", {}) or {}
        title = src.get("name", "Sans titre"); page = src.get("page", "?")
        block = f"[{title} – p.{page}]\n{r.get('text','')}"
        if total + len(block) + 2 > MAX_CONTEXT_CHARS:
            break
        ctx_blocks.append(block)
        total += len(block) + 2
        key = (title, page, src.get("path"), src.get("url"))
        if key not in seen_src:
            seen_src.add(key)
            unique_sources.append({
                "title": title, "page": page,
                "path": src.get("path"), "url": src.get("url"),
                "score": round(float(r.get("score", 0.0)), 3),
                "snippet": _snippet(r.get("text",""), 220),
            })

    answer_text = _gpt_answer_from_context(query, ctx_blocks)
    return {"answer": answer_text, "sources": unique_sources}

# ──────────────────────────────────────────────────────────────────────────────
# API attendue par services/ai_search.py
# ──────────────────────────────────────────────────────────────────────────────
def search(query: str) -> List[str]:
    query = (query or "").strip()
    if not query:
        return ["⚠️ Entrez une question."]
    res = _BM25.search(query, top_k=MAX_SNIPPETS, min_score=0.0)
    if not res:
        return ["Aucune réponse trouvée."]
    lines = []
    for r in res:
        src = r.get("source", {}) or {}
        lines.append(f"• {src.get('name','Sans titre')} (p.{src.get('page','?')}) — {_snippet(r.get('text',''))}")
    return lines

def ask(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "⚠️ Entrez une question."
    res = answer_with_sources(query, k=MAX_SNIPPETS)
    return res.get("answer", "Aucune réponse.")

# alias usuels
qa = ask
answer = ask

def stream(query: str) -> Iterable[str]:
    text = ask(query)
    words = text.split()
    if not words:
        yield ""
        return
    burst = 6 if len(words) > 900 else 4
    it = iter(words)
    while True:
        chunk = list(itertools.islice(it, burst))
        if not chunk:
            break
        yield " ".join(chunk) + " "

def ask_with_sources(query: str) -> dict:
    return answer_with_sources(query, k=MAX_SNIPPETS)

# ──────────────────────────────────────────────────────────────────────────────
# Ouverture d’une source
# ──────────────────────────────────────────────────────────────────────────────
def open_source(src: Dict[str, Any]) -> None:
    page = int(src.get("page", 1) or 1)
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

    title = src.get("title") or src.get("name") or ""
    candidate = (PDF_FOLDER / title).resolve()
    if candidate.exists():
        webbrowser.open_new(candidate.as_uri() + f"#page={page}")
    else:
        print(f"[WARN] Impossible d’ouvrir la source : {src}")

# ──────────────────────────────────────────────────────────────────────────────
# CLI de test (optionnel)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        ensure_index_up_to_date(verbose=True, max_size_kb=80_000)
    except Exception as e:
        print(f"[Index] note: {e}")

    q = "Les 4 causes de PAC ?"
    resp = answer_with_sources(q, k=MAX_SNIPPETS)
    print("\n=== Réponse ===")
    print(resp["answer"])
    print("\n=== Sources ===")
    for s in resp["sources"]:
        print(f"- {s['title']} – p.{s['page']}")
