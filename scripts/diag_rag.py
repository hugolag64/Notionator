# scripts/diag_rag.py
from __future__ import annotations
import json, re, sys
from collections import defaultdict
from pathlib import Path

# --- ancrage racine projet (indépendant du working dir)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
def prj(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)

# --- config par défaut + lecture optionnelle depuis config.py
RAG_METADATA_PATH = "data/pdf_metadata.json"
RAG_INDEX_PATH    = "data/pdf_index.faiss"
RAG_TOP_K         = 5

# si un config.py existe à la racine, on l'utilise
cfg = prj("config.py")
if cfg.exists():
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import config as _cfg  # type: ignore
        RAG_METADATA_PATH = getattr(_cfg, "RAG_METADATA_PATH", RAG_METADATA_PATH)
        RAG_INDEX_PATH    = getattr(_cfg, "RAG_INDEX_PATH", RAG_INDEX_PATH)
        RAG_TOP_K         = int(getattr(_cfg, "RAG_TOP_K", RAG_TOP_K))
    except Exception:
        pass

WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")

def norm(text: str):
    return [w.lower() for w in WORD_RE.findall(text or "")]

def load_chunks():
    """
    Supporte deux formats:
    A) Liste d'entrées 'par page' : {title, page, text, path, url}
    B) Ancien format 'par PDF'    : {name/title/file, chunks/segments/pages/...}
    Retourne une liste: [(display_name, idx_or_page, text), ...]
    """
    p = prj(*Path(RAG_METADATA_PATH).parts)
    if not p.exists():
        print(f"[ERR] pdf_metadata introuvable : {p}")
        sys.exit(2)

    data = json.loads(p.read_text(encoding="utf-8"))

    # --- Format A: plat par page (ton cas actuel)
    # Heuristique: entrée = dict avec 'text' et 'page' au niveau racine
    if data and isinstance(data[0], dict) and "text" in data[0] and "page" in data[0]:
        chunks = []
        for e in data:
            txt = e.get("text") or ""
            if not txt.strip():
                continue
            title = e.get("title") or e.get("name") or "UNK"
            page = e.get("page")
            # nom lisible pour le diag
            display = f"{title} (p.{page})" if page is not None else str(title)
            chunks.append((display, int(page) if isinstance(page, int) else 0, txt))
        print("[info] Format détecté: 'par page' (title/page/text).")
        return chunks

    # --- Format B: ancien (par PDF + sous-clés)
    def extract_texts(entry):
        out = []
        for key in ("chunks", "segments", "pages", "texts", "content", "excerpts"):
            items = entry.get(key) or []
            for i, ch in enumerate(items):
                if isinstance(ch, dict):
                    txt = ch.get("text") or ch.get("content") or ch.get("body")
                else:
                    txt = str(ch)
                if txt and txt.strip():
                    out.append((i, txt))
            if out:
                return key, out
        return None, out

    chunks = []
    used = set()
    for entry in data:
        name = entry.get("name") or entry.get("title") or entry.get("file") or "UNK"
        key, pairs = extract_texts(entry)
        if key:
            used.add(key)
        for i, txt in pairs:
            chunks.append((name, i, txt))

    if used:
        print(f"[info] Format détecté: par PDF ({', '.join(sorted(used))}).")
    else:
        print("[warn] Aucune clé texte trouvée.")
    return chunks


def build_inverted(chunks):
    inv = defaultdict(list)
    texts = []
    for idx, (_n, _i, t) in enumerate(chunks):
        texts.append(t)
        for w in set(norm(t)):
            inv[w].append(idx)
    return inv, texts

def keyword_search(query, inv, top_k):
    q = set(norm(query))
    scores = defaultdict(int)
    for w in q:
        for idx in inv.get(w, []):
            scores[idx] += 1
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/diag_rag.py \"question\"")
        sys.exit(1)

    meta_path = prj(*Path(RAG_METADATA_PATH).parts)
    index_path = prj(*Path(RAG_INDEX_PATH).parts)

    query = " ".join(sys.argv[1:])
    chunks = load_chunks()
    print(f"[info] metadata: {meta_path}")
    print(f"[info] index    : {index_path} (peut être absent si non créé)")
    print(f"[info] chunks   : {len(chunks)}")

    inv, _ = build_inverted(chunks)
    hits = keyword_search(query, inv, RAG_TOP_K)
    if not hits:
        print("[-] Aucun match en recherche mots-clés -> soit query trop spécifique, soit chunks vides.")
        sys.exit(4)

    print("[+] Extraits qui matchent (fallback mots-clés) :")
    for rank, (idx, score) in enumerate(hits, 1):
        name, i, txt = chunks[idx]
        snippet = re.sub(r"\s+", " ", txt).strip()
        print(f"{rank}. {name} [chunk {i}] score={score} :: {snippet[:220]}{'…' if len(snippet)>220 else ''}")

if __name__ == "__main__":
    main()
