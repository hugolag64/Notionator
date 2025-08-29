# services/quick_summary.py
from __future__ import annotations

import re
from typing import Dict, List, Tuple, Optional

from notion_client.errors import APIResponseError

from services.notion_client import NotionAPI
from services.logger import get_logger
from services.profiler import profiled, span
from config import DATABASE_COURS_ID as COURSES_DB_ID

logger = get_logger(__name__)

BILAN_QUERY = "bilan rapide"  # substring after normalization
LABELS = ("non commenc", "en cours", "termin")  # motifs détectant des lignes de bilan


class QuickSummaryUpdater:
    """
    Met à jour 'Bilan rapide' IN-PLACE (sans créer de nouveaux blocs, sans déplacer).
    - Pages Semestre : filtre select 'Semestre' == valeur.
    - Page Collèges  : AGRÉGAT de TOUS les cours où 'Collège' (multi_select) est NON VIDE.
    - Supprime les sections 'Bilan rapide' dupliquées sur une même page (on garde la 1re).
    """

    def __init__(self, notion: Optional[NotionAPI] = None):
        self.notion = notion or NotionAPI()
        self.client = self.notion.client
        # Cache pour limiter les recherches de pages par titre
        self._page_title_cache: Dict[str, Optional[str]] = {}

    # ---------------- Public API ----------------

    @profiled("quick_summary.update_all")
    def update_all(self) -> None:
        logger.info("[Bilan rapide] update_all() – start")

        # 1) Semestres
        semesters = self._distinct_values("Semestre")
        logger.info(f"[Bilan rapide] Semestres: {semesters}")
        for s in semesters:
            page_id = self._find_semester_page(str(s))
            logger.info(f"[Bilan rapide] Page Semestre '{s}' → {page_id}")
            if not page_id:
                continue
            try:
                filt = {"property": "Semestre", "select": {"equals": str(s)}}
                counts = self._compute_counts(filt)
                logger.info(f"[Bilan rapide][Semestre {s}] {counts}")
                self._update_section_in_place_recursive(page_id, counts)
            except Exception:
                logger.exception(f"[Bilan rapide] update Semestre {s} failed")

        # 2) Collèges (AGRÉGAT)
        colleges_page_id = self._find_page_by_title("Collèges") or self._find_page_by_title("Colleges")
        logger.info(f"[Bilan rapide] Page 'Collèges' → {colleges_page_id}")
        if colleges_page_id:
            try:
                filt = {"property": "Collège", "multi_select": {"is_not_empty": True}}
                counts = self._compute_counts(filt)
                logger.info(f"[Bilan rapide][Collèges (agrégat)] {counts}")
                self._update_section_in_place_recursive(colleges_page_id, counts)
            except Exception:
                logger.exception("[Bilan rapide] update Collèges (agrégat) failed")
        else:
            logger.warning("[Bilan rapide] Page 'Collèges' introuvable (titre différent ?)")

        logger.info("[Bilan rapide] update_all() – done")

    # ---------------- Core ----------------

    @profiled("quick_summary.compute_counts")
    def _compute_counts(self, filter_dict: Dict) -> Dict[str, int]:
        has_more, cursor = True, None
        total = non_commence = en_cours = termine = 0
        while has_more:
            payload = {"database_id": COURSES_DB_ID, "filter": filter_dict, "page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            with span("notion.databases.query:counters"):
                resp = self.client.databases.query(**payload)
            for row in resp.get("results", []):
                total += 1
                k = self._norm_status_key(self._get_status_value(row))
                if k == "non_commence":
                    non_commence += 1
                elif k == "termine":
                    termine += 1
                else:
                    en_cours += 1
            has_more = resp.get("has_more", False)
            cursor = resp.get("next_cursor")
        return {
            "total": total,
            "non_commence": non_commence,
            "en_cours": en_cours,
            "termine": termine,
        }

    # --- Parcours récursif page et mise à jour in-place ---

    @profiled("quick_summary.update_in_place")
    def _update_section_in_place_recursive(self, page_id: str, counts: Dict[str, int]) -> None:
        """
        DFS sur la page. Pour chaque conteneur (page/bloc avec enfants) :
        - repère les headings 'Bilan rapide'
        - garde le premier trouvé comme principal → met à jour ses 3 puces
        - archive toutes les autres sections en double
        """
        logger.info(f"[Bilan rapide] in-place recursive on page {page_id} with {counts}")

        sections: List[Tuple[str, List[Dict], int, str]] = []
        self._dfs_collect_sections(container_id=page_id, sections=sections)

        logger.info(f"[Bilan rapide] sections found: {len(sections)}")
        if not sections:
            logger.warning("[Bilan rapide] Aucun heading 'Bilan rapide' trouvé → skip")
            return

        primary_container, siblings, h_idx, h_id = sections[0]
        logger.info(f"[Bilan rapide] primary container={primary_container} heading_idx={h_idx} id={h_id}")

        # Archiver les doublons
        for (cont, sibs, idx, _hid) in sections[1:]:
            try:
                self._archive_section(container_id=cont, siblings=sibs, start_index=idx)
            except Exception:
                logger.exception("[Bilan rapide] archiving duplicate section failed")

        # Mettre à jour les 3 bullets sous le heading principal
        self._update_bullets_under(
            container_id=primary_container,
            siblings=siblings,
            heading_index=h_idx,
            counts=counts,
        )

    @profiled("quick_summary.collect_sections")
    def _dfs_collect_sections(self, container_id: str, sections: List[Tuple[str, List[Dict], int, str]]) -> None:
        """DFS dans l'arbre de blocs – collecte toutes les sections 'Bilan rapide' (container_id, siblings, index, heading_id)."""
        siblings = self._list_all_children(container_id)
        for i, b in enumerate(siblings):
            tp = b.get("type")
            if tp in ("heading_1", "heading_2", "heading_3"):
                title_norm = self._norm_heading(self._plain(b[tp].get("rich_text", [])))
                if BILAN_QUERY in title_norm:
                    sections.append((container_id, siblings, i, b["id"]))
        for b in siblings:
            if b.get("has_children"):
                try:
                    self._dfs_collect_sections(container_id=b["id"], sections=sections)
                except Exception:
                    logger.exception(f"[Bilan rapide] DFS failed on child {b.get('id')}")

    def _archive_section(self, container_id: str, siblings: List[Dict], start_index: int) -> None:
        """Archive le heading à start_index et tous ses suivants jusqu’au prochain heading dans le même conteneur."""
        if start_index >= len(siblings):
            return
        ids = [siblings[start_index]["id"]]
        for i in range(start_index + 1, len(siblings)):
            tp = siblings[i].get("type")
            if tp in ("heading_1", "heading_2", "heading_3"):
                break
            bid = siblings[i].get("id")
            if bid:
                ids.append(bid)
        logger.info(f"[Bilan rapide] archiving {len(ids)} blocks (duplicate section)")
        for bid in ids:
            self._archive_block(bid)

    @profiled("quick_summary.update_bullets")
    def _update_bullets_under(self, container_id: str, siblings: List[Dict], heading_index: int, counts: Dict[str, int]) -> None:
        """Met à jour le texte des 3 bullets sous le heading (même conteneur). Pas de création/déplacement."""
        under: List[Dict] = []
        for i in range(heading_index + 1, len(siblings)):
            tp = siblings[i].get("type")
            if tp in ("heading_1", "heading_2", "heading_3"):
                break
            under.append(siblings[i])

        bullets = [b for b in under if b.get("type") == "bulleted_list_item"]
        logger.info(f"[Bilan rapide] bullets under primary: {len(bullets)}")

        y = counts["total"]; a = counts["non_commence"]; b = counts["en_cours"]; c = counts["termine"]
        lines = [f"Non commencés : {a}/{y}", f"En cours : {b}/{y}", f"Terminés : {c}/{y}"]

        # helper pour extraire le texte actuel d'un bullet
        def _bullet_text(bblk: Dict) -> str:
            return self._plain(bblk["bulleted_list_item"].get("rich_text", [])).strip()

        if len(bullets) >= 3:
            # PATCH only-if-needed pour limiter le bruit sur l'API
            for idx, txt in enumerate(lines):
                try:
                    current = _bullet_text(bullets[idx]).strip()
                    if current != txt.strip():
                        self._update_bullet_text(bullets[idx]["id"], txt)
                except Exception:
                    logger.exception("[Bilan rapide] bullet patch failed (>=3)")
            # archiver des bullets excédentaires ressemblant à des lignes de bilan
            extras = 0
            for extra in bullets[3:]:
                try:
                    if self._looks_like_bilan_line(extra):
                        self._archive_block(extra["id"]); extras += 1
                except Exception:
                    logger.exception("[Bilan rapide] archive extra bullet failed")
            if extras:
                logger.info(f"[Bilan rapide] archived extra bullets: {extras}")
            return

        # Moins de 3 bullets : mettre à jour ce qui existe et nettoyer les paragraphes bilan-like
        for idx, bblk in enumerate(bullets):
            if idx < len(lines):
                try:
                    current = _bullet_text(bblk)
                    if current != lines[idx]:
                        self._update_bullet_text(bblk["id"], lines[idx])
                except Exception:
                    logger.exception("[Bilan rapide] bullet patch failed (<3)")
        paras_archived = 0
        for b in under:
            if b.get("type") == "paragraph":
                try:
                    txt = self._plain(b["paragraph"].get("rich_text", [])).lower()
                    if any(k in txt for k in LABELS):
                        self._archive_block(b["id"]); paras_archived += 1
                except Exception:
                    logger.exception("[Bilan rapide] archive paragraph failed")
        if paras_archived:
            logger.info(f"[Bilan rapide] archived bilan-like paragraphs: {paras_archived}")

    # ---------------- Notion helpers ----------------

    @profiled("quick_summary.list_children")
    def _list_all_children(self, block_id: str) -> List[Dict]:
        """Liste complète des enfants (pagination) pour n'importe quel bloc (page incluse)."""
        all_blocks: List[Dict] = []
        cursor = None
        while True:
            kwargs = {"block_id": block_id}
            if cursor:
                kwargs["start_cursor"] = cursor
            with span("notion.blocks.children.list"):
                resp = self.client.blocks.children.list(**kwargs)
            all_blocks.extend(resp.get("results", []))
            if not resp.get("has_more", False):
                break
            cursor = resp.get("next_cursor")
        return all_blocks

    @profiled("quick_summary.distinct_values")
    def _distinct_values(self, prop_name: str) -> List[str]:
        """Valeurs distinctes best‑effort pour select/multi-select."""
        seen, values, cursor, has_more = set(), [], None, True
        while has_more:
            q = {"database_id": COURSES_DB_ID, "page_size": 100}
            if cursor:
                q["start_cursor"] = cursor
            with span("notion.databases.query:distinct"):
                resp = self.client.databases.query(**q)
            for row in resp.get("results", []):
                p = row.get("properties", {}).get(prop_name, {})
                if p.get("type") == "select" and p.get("select"):
                    name = p["select"]["name"]
                    if name not in seen:
                        seen.add(name); values.append(name)
                elif p.get("type") == "multi_select" and p.get("multi_select"):
                    for it in p["multi_select"]:
                        name = it["name"]
                        if name not in seen:
                            seen.add(name); values.append(name)
            has_more = resp.get("has_more", False)
            cursor = resp.get("next_cursor")
        return values

    def _find_semester_page(self, s: str) -> Optional[str]:
        """Essaie 'Semestre {s}', puis '{s}', avec cache pour éviter 2x search par run."""
        cand1 = f"Semestre {s}"
        if cand1 not in self._page_title_cache:
            self._page_title_cache[cand1] = self._find_page_by_title(cand1)
        if self._page_title_cache[cand1]:
            return self._page_title_cache[cand1]
        if s not in self._page_title_cache:
            self._page_title_cache[s] = self._find_page_by_title(s)
        return self._page_title_cache[s]

    @profiled("quick_summary.find_page_by_title")
    def _find_page_by_title(self, title: str) -> Optional[str]:
        try:
            with span("notion.search:page"):
                resp = self.client.search(query=title, filter={"value": "page", "property": "object"})
            for r in resp.get("results", []):
                if r.get("object") != "page":
                    continue
                props = r.get("properties", {}) or {}
                page_title = None
                for p in props.values():
                    if p.get("type") == "title" and p["title"]:
                        page_title = self._plain(p["title"])
                        break
                if page_title and self._norm_heading(page_title) == self._norm_heading(title):
                    return r["id"]
        except APIResponseError:
            logger.exception(f"[Bilan rapide] find_page_by_title('{title}') failed")
        return None

    # ---------------- Text & status utils ----------------

    def _get_status_value(self, row: Dict) -> str:
        props = row.get("properties", {})
        p = props.get("Statut") or props.get("Status") or {}
        t = p.get("type")
        if t == "status" and p.get("status"):
            return p["status"]["name"]
        if t == "select" and p.get("select"):
            return p["select"]["name"]
        return "En cours"

    def _norm_status_key(self, status: str) -> str:
        s = (status or "").strip().lower()
        if s.startswith("non"):
            return "non_commence"
        if "termin" in s or "fini" in s:
            return "termine"
        return "en_cours"

    def _plain(self, rich_list: List[Dict]) -> str:
        return "".join([r.get("plain_text") or r.get("text", {}).get("content", "") for r in rich_list])

    def _norm_heading(self, text: str) -> str:
        # Retire emojis/ponctuation, compacte les espaces, minuscule
        s = re.sub(r"[^\w\sÀ-ÖØ-öø-ÿ-]", " ", text, flags=re.UNICODE)
        s = re.sub(r"\s+", " ", s).strip().lower()
        return s

    def _looks_like_bilan_line(self, block: Dict) -> bool:
        if block.get("type") == "bulleted_list_item":
            txt = self._plain(block["bulleted_list_item"].get("rich_text", [])).lower()
            return any(k in txt for k in LABELS)
        if block.get("type") == "paragraph":
            txt = self._plain(block["paragraph"].get("rich_text", [])).lower()
            return any(k in txt for k in LABELS)
        return False

    @profiled("quick_summary.update_bullet_text")
    def _update_bullet_text(self, block_id: str, text: str) -> None:
        logger.info(f"[Bilan rapide] update bullet {block_id} -> {text}")
        try:
            with span("notion.blocks.update:bullet_text"):
                self.client.blocks.update(
                    block_id=block_id,
                    bulleted_list_item={"rich_text": [{"type": "text", "text": {"content": text}}]}
                )
        except APIResponseError:
            logger.exception(f"[Bilan rapide] bullet update failed for {block_id}")

    @profiled("quick_summary.archive_block")
    def _archive_block(self, block_id: str) -> None:
        try:
            with span("notion.blocks.update:archive"):
                self.client.blocks.update(block_id=block_id, archived=True)
        except APIResponseError:
            logger.exception(f"[Bilan rapide] archive failed for {block_id}")
