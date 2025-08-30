from __future__ import annotations
from typing import Dict, Any, List
from .notion_api import NotionAPI
from .notion_batcher import NotionBatcher

class NotionService:
    def __init__(self, token: str, db_courses_id: str):
        self.api = NotionAPI(token)
        self.db = db_courses_id
        self.batcher = NotionBatcher(self._apply_batch)

    def list_courses(self, filters: Dict[str, Any] | None = None) -> List[dict]:
        kwargs = {"filter": filters} if filters else {}
        return self.api.query_db(self.db, **kwargs).get("results", [])

    def update_course_pdf(self, page_id: str, url: str):
        # UI optimiste: on met à jour la vue tout de suite (côté UI), puis on flush
        self.batcher.update_later(page_id, {"URL PDF COLLEGE": {"url": url}})

    def _apply_batch(self, batch):
        for pid, props in batch:
            self.api.update_page(pid, props)

    def flush(self):  # au shutdown
        self.batcher.flush()
