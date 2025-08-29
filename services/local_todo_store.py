from __future__ import annotations
import json, os, tempfile
from typing import List, Dict
from services.logger import get_logger

logger = get_logger(__name__)
LOCAL_FILE = os.path.join("data", "local_todo.json")

def _atomic_write(path: str, data: dict):
    # Ecriture atomique: on écrit dans un tmp puis os.replace (atomique sous Windows aussi)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="._localtodo_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

class LocalTodoStore:
    """Stocke les ajouts locaux par date (YYYY-MM-DD), sans sync Notion."""
    def __init__(self, path: str = LOCAL_FILE):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            logger.debug("[LocalTodo] init -> creating empty store at %s", self.path)
            _atomic_write(self.path, {})

    def _load(self) -> Dict[str, List[Dict]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning("[LocalTodo] invalid json root (not a dict), resetting")
                    return {}
                return data
        except json.JSONDecodeError as e:
            logger.exception("[LocalTodo] JSONDecodeError: file likely corrupted; resetting to {}")
            return {}
        except FileNotFoundError:
            logger.warning("[LocalTodo] file missing, recreating")
            _atomic_write(self.path, {})
            return {}
        except Exception:
            logger.exception("[LocalTodo] _load failed")
            return {}

    def _save(self, data: Dict[str, List[Dict]]):
        try:
            _atomic_write(self.path, data)
        except Exception:
            logger.exception("[LocalTodo] _save failed (atomic_write)")

    def list(self, date_str: str) -> List[Dict]:
        items = self._load().get(date_str, [])
        logger.debug("[LocalTodo] list(%s) -> %d items", date_str, len(items))
        return items

    def add(self, date_str: str, text: str) -> Dict:
        data = self._load()
        iid = f"local-{abs(hash((date_str, text)))%10**10}"
        item = {"id": iid, "text": text, "checked": False}
        data.setdefault(date_str, []).append(item)
        logger.debug("[LocalTodo] add(%s, %r) -> id=%s", date_str, text, iid)
        self._save(data)
        return item

    def set_checked(self, date_str: str, item_id: str, checked: bool):
        data = self._load()
        items = data.get(date_str, [])
        found = False
        for it in items:
            if it.get("id") == item_id:
                it["checked"] = bool(checked)
                found = True
                break
        logger.debug("[LocalTodo] set_checked(%s, %s, %s) found=%s", date_str, item_id, checked, found)
        self._save(data)

    def remove(self, date_str: str, item_id: str):
        data = self._load()
        before = len(data.get(date_str, []))
        data[date_str] = [it for it in data.get(date_str, []) if it.get("id") != item_id]
        after = len(data.get(date_str, []))
        logger.debug("[LocalTodo] remove(%s, %s) %d->%d", date_str, item_id, before, after)
        self._save(data)
