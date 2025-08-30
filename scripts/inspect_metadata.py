# scripts/inspect_metadata.py
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

# → ancrage racine projet, peu importe le dossier courant
PROJECT_ROOT = Path(__file__).resolve().parents[1]
def prj(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)

META_PATH = prj("data", "pdf_metadata.json")

def sample_texts(entry):
    samples = []
    # chunks: [{text: ...}] ou ["..."]
    for ch in (entry.get("chunks") or []):
        txt = ch.get("text") if isinstance(ch, dict) else str(ch)
        if txt:
            samples.append(("chunks", txt[:120])); break
    # autres clés possibles
    for key in ("segments", "pages", "texts", "content", "excerpts"):
        for ch in (entry.get(key) or []):
            txt = ch.get("text") if isinstance(ch, dict) else str(ch)
            if txt:
                samples.append((key, txt[:120])); break
    return samples

def main():
    if not META_PATH.exists():
        print(f"[ERR] Introuvable: {META_PATH}")
        return
    data = json.loads(META_PATH.read_text(encoding="utf-8"))
    print(f"Entrées PDF: {len(data)}")
    key_counter = Counter()
    chunk_count = 0
    for e in data:
        key_counter.update(e.keys())
        chunk_count += len(e.get("chunks") or [])
    print("Clés les plus fréquentes:", key_counter.most_common(10))
    print("Total chunks (clé 'chunks'):", chunk_count)
    if data:
        print("\nExemple 1 — clés:", list(data[0].keys()))
        print("Extraits détectés:")
        for k, s in sample_texts(data[0]):
            print(f"  - via '{k}': {s!r}")

if __name__ == "__main__":
    main()
