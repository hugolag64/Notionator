# services/pdf_metadata.py
from __future__ import annotations
import json, os, hashlib, tempfile, threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Optional, List

DEFAULT_PATH = os.path.join("data", "pdf_metadata.json")
_LOCK = threading.Lock()

@dataclass
class PdfMeta:
    path: str
    name: str
    size: int
    mtime_iso: str
    sha1: str
    pages: int
    chunk_count: int
    detected_items: List[str]      # ex: ["ITEM 208"]
    college: Optional[str]         # ex: "Pneumologie"
    last_indexed_iso: str

class PdfMetadataStore:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._db: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._db = json.load(f)
            except Exception:
                self._db = {}
        else:
            self._db = {}

    def _atomic_write(self):
        fd, tmp = tempfile.mkstemp(prefix="pdfmeta_", suffix=".json",
                                   dir=os.path.dirname(self.path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._db, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try: os.remove(tmp)
            except Exception: pass
            raise

    def upsert(self, meta: PdfMeta):
        with _LOCK:
            self._db[meta.path] = asdict(meta)
            self._atomic_write()

    def get(self, path: str) -> Optional[Dict]:
        return self._db.get(path)

def compute_sha1(path: str, block: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b: break
            h.update(b)
    return h.hexdigest()

def iso_now() -> str:
    return datetime.now().astimezone().isoformat()
