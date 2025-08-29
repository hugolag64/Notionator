# services/notion_client.py
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple, Union, Any

from notion_client import Client
from notion_client.errors import APIResponseError

# On réutilise schema_cache mais on y stocke le **NOM** de la propriété choisie,
# même si les fonctions s'appellent get/set_prop_id (compat rétro).
from services.schema_cache import get_prop_id, set_prop_id

from services.logger import get_logger
from services.profiler import profiled, span
from config import (
    NOTION_TOKEN,
    DATABASE_COURS_ID,
    DATABASE_UE_ID,
    DATABASE_ITEMS_ID,
    ITEM_NUMBER_PROP_COURS,
    ITEM_RELATION_PROP,
    ITEM_NUMBER_PROP_LISTE,
    TO_DO_DATABASE_ID,
    # --- QuickStats (mapping propriétés DB Cours) ---
    COURSE_PROP_PDF,
    COURSE_PROP_SUMMARY,
    COURSE_PROP_ANKI,
)
from config import DATABASE_COURS_ID as COURSES_DATABASE_ID
from config import DATABASE_UE_ID as UE_DATABASE_ID

logger = get_logger(__name__)


# ------------------------- Mini cache TTL en mémoire (embarqué) -------------------------
class _InProcessTTLCache:
    """Cache ultra-léger en mémoire, utilisable pendant un run (boot/génération)."""
    def __init__(self, ttl_seconds: int = 90):
        self.ttl = ttl_seconds
        self._store: Dict[Any, Tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        hit = self._store.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: Any, value: Any) -> None:
        self._store[key] = (time.time(), value)


def _to_hashable(x: Any) -> Any:
    if isinstance(x, dict):
        return tuple(sorted((k, _to_hashable(v)) for k, v in x.items()))
    if isinstance(x, list):
        return tuple(_to_hashable(i) for i in x)
    return x


def _payload_key(namespace: str, payload: dict) -> Tuple[str, Any]:
    # Clé stable/triée pour le cache des queries
    return namespace, _to_hashable(payload)


# ------------------------- Helpers URL (anti-None / anti-chemin local) -------------------------
_URL_FORBIDDEN = {"", "none", "null", "-"}

def _extract_url_value(v: Any) -> Optional[str]:
    """
    Accepte soit une chaîne, soit un dict {"url": "..."} (format envoyé par ActionsManager._push).
    """
    if isinstance(v, dict) and "url" in v:
        v = v.get("url")
    if v is None:
        return None
    return str(v)

def _is_url_ok(u: Optional[str]) -> bool:
    if u is None:
        return False
    s = str(u).strip()
    if s.lower() in _URL_FORBIDDEN:
        return False
    # autorise : http(s):// ou file://
    if s.startswith(("http://", "https://", "file://")):
        return True
    # un chemin absolu local est "ok" pour l'app, mais on NE LE PUSH PAS dans Notion
    try:
        return os.path.isabs(s)
    except Exception:
        return False

def _is_remote_url(u: Optional[str]) -> bool:
    if not u:
        return False
    s = str(u).strip().lower()
    return s.startswith("http://") or s.startswith("https://") or s.startswith("file://")

def _sanitize_props_for_update(props: dict | None) -> dict:
    """
    Nettoie un dict Notion `properties` avant envoi:
    - Ne pousse jamais 'url': 'None' / vide / '-'
    - Ne pousse pas de chemins locaux non 'file://'
    - Laisse passer le reste (checkbox, date, number, relation...)
    """
    if not isinstance(props, dict):
        return {}
    clean: dict = {}
    for k, v in props.items():
        if isinstance(v, dict) and "url" in v:
            u = _extract_url_value(v)
            if _is_url_ok(u) and _is_remote_url(u):
                clean[k] = {"url": u}
            else:
                # on DROP l'URL invalide
                continue
        else:
            clean[k] = v
    return clean


# =====================================================================
#                              NotionAPI
# =====================================================================
class NotionAPI:
    def __init__(self):
        self.client = Client(auth=NOTION_TOKEN)
        self.cours_db_id = DATABASE_COURS_ID
        self.ue_db_id = DATABASE_UE_ID

        # Cache des métadonnées de DB (propriétés -> type, options, etc.)
        self._props_cache: Dict[str, dict] = {}

        # Mémo pour courses_due_today
        self._courses_today_cache: Dict[str, dict] = {"key": None, "ts": 0.0, "data": []}

        # Cache des pages To-Do par date: { db_id: { "YYYY-MM-DD": page_dict } }
        self._todo_page_cache: Dict[str, Dict[str, dict]] = {}

        # Cache TTL pour queries/retrieve les plus fréquents
        self._ttl_cache = _InProcessTTLCache(ttl_seconds=90)

        logger.info("Client Notion initialisé")

    # ------------------- Wrappers de cache (queries & retrieve) -------------------
    def _cached_databases_query(self, **payload) -> dict:
        """
        Cache les requêtes /databases/{id}/query strictement identiques (mêmes filtres/tri).
        TTL court pour éviter la staleness sur une même exécution (boot/génération).
        """
        key = _payload_key("db.query", payload)
        got = self._ttl_cache.get(key)
        if got is not None:
            return got
        with span("notion.databases.query:cached"):
            res = self.client.databases.query(**payload)
        self._ttl_cache.set(key, res)
        return res

    def _cached_pages_retrieve(self, page_id: str) -> dict:
        key = ("page.retrieve", page_id)
        got = self._ttl_cache.get(key)
        if got is not None:
            return got
        with span("notion.pages.retrieve:cached"):
            res = self.client.pages.retrieve(page_id=page_id)
        self._ttl_cache.set(key, res)
        return res

    # ------------------- COURS -------------------
    @profiled("notion.cours.query_all")
    def get_cours(self) -> List[dict]:
        try:
            return self.query_database(self.cours_db_id)
        except Exception:
            logger.exception("Impossible de récupérer les cours")
            return []

    @profiled("notion.cours.by_semestre")
    def get_cours_by_semestre(self, semestre_label: str) -> List[dict]:
        try:
            if not semestre_label:
                return []
            label = semestre_label.strip()
            if not label.lower().startswith("semestre "):
                label = f"Semestre {label}"
            resp = self._cached_databases_query(
                database_id=self.cours_db_id,
                filter={"property": "Semestre", "select": {"equals": label}},
            )
            return resp.get("results", [])
        except Exception:
            logger.exception("Impossible de récupérer les cours du semestre %s", semestre_label)
            return []

    @profiled("notion.cours.add")
    def add_cours(self, title: str, properties: Optional[dict] = None) -> Optional[dict]:
        try:
            title_prop = self._title_prop(self.cours_db_id) or "Name"
            props = {title_prop: {"title": [{"text": {"content": title}}]}}
            if properties:
                props.update(properties)
            with span("notion.pages.create"):
                return self.client.pages.create(parent={"database_id": self.cours_db_id}, properties=props)
        except Exception:
            logger.exception("Impossible d'ajouter le cours: %s", title)
            return None

    # ------------------- UE -------------------
    @profiled("notion.ue.query_all")
    def get_ue(self) -> List[dict]:
        try:
            return self.query_database(self.ue_db_id)
        except Exception:
            logger.exception("Impossible de récupérer les UE")
            return []

    @profiled("notion.ue.add")
    def add_ue(self, title: str, properties: Optional[dict] = None) -> Optional[dict]:
        try:
            title_prop = self._title_prop(self.ue_db_id) or "Name"
            props = {title_prop: {"title": [{"text": {"content": title}}]}}
            if properties:
                props.update(properties)
            with span("notion.pages.create"):
                return self.client.pages.create(parent={"database_id": self.ue_db_id}, properties=props)
        except Exception:
            logger.exception("Impossible d'ajouter l'UE: %s", title)
            return None

    # ------------------- Parsing SemestreView -------------------
    @profiled("parse.cours.semestreview")
    def parse_cours(self, cours_page: dict, ue_map: Dict[str, str]) -> dict:
        try:
            props = cours_page["properties"]
            nom = props.get("Cours", {}).get("title", []) or props.get("Name", {}).get("title", [])
            nom = (nom[0]["text"]["content"] if nom and nom[0].get("text") else "Sans titre")
            ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
            ue_names = [ue_map[ue_id] for ue_id in ue_map if ue_id in ue_map]  # safe
            pdf_url = (props.get("URL PDF", {}) or {}).get("url")
            return {
                "nom": nom,
                "ue": ue_names,
                "pdf_ok": bool(pdf_url),
                "url_pdf": pdf_url,
                "anki_ok": props.get("Anki", {}).get("checkbox", False),
                "resume_ok": props.get("Résumé", {}).get("checkbox", False),
                "rappel_ok": props.get("Rappel fait", {}).get("checkbox", False),
            }
        except Exception:
            logger.exception("Erreur lors du parsing d'un cours pour SemestreView")
            return {}

    @profiled("parse.ue.map")
    def build_ue_map(self) -> Dict[str, str]:
        try:
            mapping: Dict[str, str] = {}
            for u in self.get_ue():
                props = u["properties"]
                tp = next((k for k, v in props.items() if v.get("type") == "title"), None)
                if tp and props.get(tp, {}).get("title"):
                    nom = props[tp]["title"][0]["text"]["content"]
                else:
                    nom = "Sans titre"
                mapping[u["id"]] = nom
            return mapping
        except Exception:
            logger.exception("Erreur lors de la construction de la map UE")
            return {}

    # ------------------- Cours avec ITEM (CollegeView) -------------------
    @profiled("notion.cours.with_item")
    def get_cours_with_item(self) -> List[dict]:
        try:
            resp = self._cached_databases_query(
                database_id=self.cours_db_id,
                filter={"property": "ITEM", "number": {"is_not_empty": True}},
            )
            return resp.get("results", [])
        except Exception:
            logger.exception("Impossible de récupérer les cours avec ITEM")
            return []

    @profiled("parse.cours.college")
    def parse_cours_college(self, cours_page: dict) -> dict:
        try:
            props = cours_page["properties"]
            nom_prop = next((k for k, v in props.items() if v.get("type") == "title"), "Name")
            nom_arr = props.get(nom_prop, {}).get("title", [])
            nom = nom_arr[0]["text"]["content"] if nom_arr and nom_arr[0].get("text") else "Sans titre"
            item = props.get("ITEM", {}).get("number")

            def nettoyer_nom(s: str) -> str:
                return re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ\s-]", "", s).strip()

            college_names = [nettoyer_nom(c["name"]) for c in props.get("Collège", {}).get("multi_select", [])]
            url_pdf_college = (props.get("URL PDF COLLEGE", {}) or {}).get("url")

            # Fiche EDN
            fiche_url = None
            try:
                arr = props.get("Fiche EDN", {}).get("rollup", {}).get("array", [])
                if arr and arr[0]["type"] == "rich_text":
                    rich = arr[0]["rich_text"]
                    if rich and "text" in rich[0]:
                        fiche_url = rich[0]["text"]["link"]["url"]
            except Exception:
                fiche_url = None

            return {
                "id": cours_page.get("id"),
                "nom": nom,
                "item": str(int(item)) if item is not None else "",
                "item_number": str(int(item)) if item is not None else "",
                "college": ", ".join(college_names) if college_names else "-",
                "pdf_ok": bool(url_pdf_college),
                "url_pdf": url_pdf_college,
                "anki_college_ok": props.get("Anki collège", {}).get("checkbox", False),
                "resume_college_ok": props.get("Résumé collège", {}).get("checkbox", False),
                "rappel_college_ok": props.get("Rappel fait collège", {}).get("checkbox", False),
                "lecture_j3_college_ok": props.get("Lecture J3 collège", {}).get("checkbox", False),
                "lecture_j7_college_ok": props.get("Lecture J7 collège", {}).get("checkbox", False),
                "lecture_j14_college_ok": props.get("Lecture J14 collège", {}).get("checkbox", False),
                "lecture_j30_college_ok": props.get("Lecture J30 collège", {}).get("checkbox", False),
                "fiche_url": fiche_url,
            }
        except Exception:
            logger.exception("Erreur parsing cours collège")
            return {}

    # ------------------- Parsing SemestreView simplifié -------------------
    @profiled("parse.cours.semestresimple")
    def parse_cours_semestre(self, cours_page: dict) -> dict:
        props = cours_page.get("properties", {})
        title_key = next((k for k, v in props.items() if v.get("type") == "title"), "Name")
        nom_prop = props.get(title_key, {}).get("title", [{}])
        nom = nom_prop[0]["text"]["content"] if nom_prop and nom_prop[0].get("text") else "Sans titre"

        semestre = (props.get("Semestre", {}).get("select") or {}).get("name")
        if semestre and not semestre.startswith("Semestre "):
            semestre = f"Semestre {semestre}"

        pdf_url = (props.get("URL PDF", {}) or {}).get("url")
        ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
        return {
            "id": cours_page.get("id"),
            "nom": nom,
            "semestre": semestre,
            "ue_ids": ue_ids,
            "pdf_ok": bool(pdf_url),
            "url_pdf": pdf_url,
            "anki_ok": props.get("Anki", {}).get("checkbox", False),
            "resume_ok": props.get("Résumé", {}).get("checkbox", False),
            "rappel_ok": props.get("Rappel fait", {}).get("checkbox", False),
        }

    # ------------------- Mise à jour Notion -------------------
    @profiled("notion.cours.update")
    def update_cours(self, cours_id: str, fields: dict) -> None:
        """
        Update générique robuste.
        - Supporte pour les URLs: value str OU dict {"url": "..."} (venant d'ActionsManager._push)
        - Ne pousse jamais d'URL invalide ni 'None'
        """
        props: dict = {}

        # Relations UE
        if "ue_ids" in fields:
            props["UE"] = {"relation": [{"id": x} for x in (fields.get("ue_ids") or []) if x]}

        # Multi-select Collège
        if "college" in fields:
            props["Collège"] = {"multi_select": [{"name": n} for n in (fields.get("college") or []) if n]}

        # URL PDF (semestre)
        if "URL PDF" in fields:
            raw = fields.get("URL PDF")
            url = _extract_url_value(raw)
            if _is_url_ok(url) and _is_remote_url(url):
                props["URL PDF"] = {"url": url}  # on push uniquement si remote
            # sinon: on n'envoie rien (on n'écrase pas)

        # URL PDF COLLEGE
        if "URL PDF COLLEGE" in fields:
            raw = fields.get("URL PDF COLLEGE")
            url = _extract_url_value(raw)
            if _is_url_ok(url) and _is_remote_url(url):
                props["URL PDF COLLEGE"] = {"url": url}

        if not props:
            logger.info("Aucun champ mappable pour %s. Ignoré.", cours_id)
            return

        safe_props = _sanitize_props_for_update(props)
        if not safe_props:
            logger.info("Props URL invalides filtrées pour %s. Aucune MAJ envoyée.", cours_id)
            return

        try:
            with span("notion.pages.update"):
                self.client.pages.update(page_id=cours_id, properties=safe_props)
        except TypeError:
            self.client.pages.update(cours_id, {"properties": safe_props})
        except Exception:
            logger.exception("Erreur MAJ du cours %s", cours_id)
            raise

    @profiled("notion.pages.retrieve:course")
    def get_cours_by_id(self, cours_id: str) -> Optional[dict]:
        try:
            with span("notion.pages.retrieve"):
                return self._cached_pages_retrieve(cours_id)
        except Exception:
            logger.exception("Erreur récupération du cours %s", cours_id)
            return None

    @profiled("notion.pages.retrieve:ue")
    def get_ue_by_id(self, ue_id: str) -> Optional[dict]:
        try:
            with span("notion.pages.retrieve"):
                return self._cached_pages_retrieve(ue_id)
        except Exception:
            logger.exception("Erreur récupération de l'UE %s", ue_id)
            return None

    # ----------- Choix Collège -----------
    @profiled("notion.db.props:college_choices")
    def get_all_college_choices(self) -> List[str]:
        try:
            props = self._db_props(self.cours_db_id)
            college_prop = props.get("Collège")
            if college_prop and college_prop.get("type") == "multi_select":
                return [opt["name"] for opt in college_prop["multi_select"].get("options", [])]
            return []
        except Exception:
            logger.exception("Impossible de récupérer les choix du multi-select Collège")
            return []

    # ----------- Liaison ITEMS ↔ COURS -----------
    @profiled("logic.auto_link_items")
    def auto_link_items_by_number(self) -> None:
        try:
            with span("notion.databases.query:cours"):
                cours_pages = self._cached_databases_query(database_id=DATABASE_COURS_ID)["results"]
            for page in cours_pages:
                props = page.get("properties", {})
                numero_val = props.get(ITEM_NUMBER_PROP_COURS, {}).get("number")
                deja_lie = props.get(ITEM_RELATION_PROP, {}).get("relation", [])
                if numero_val is None or deja_lie:
                    continue
                numero_val_str = str(int(numero_val)).strip()
                with span("notion.databases.query:items_text"):
                    items = self._cached_databases_query(
                        database_id=DATABASE_ITEMS_ID,
                        filter={"property": ITEM_NUMBER_PROP_LISTE, "rich_text": {"equals": numero_val_str}},
                    )["results"]
                if not items:
                    with span("notion.databases.query:items_number"):
                        items = self._cached_databases_query(
                            database_id=DATABASE_ITEMS_ID,
                            filter={"property": ITEM_NUMBER_PROP_LISTE, "number": {"equals": int(numero_val_str)}},
                        )["results"]
                if items:
                    item_id = items[0]["id"]
                    with span("notion.pages.update:link_item"):
                        self.client.pages.update(
                            page_id=page["id"], properties={ITEM_RELATION_PROP: {"relation": [{"id": item_id}]}}
                        )
                else:
                    logger.warning("Aucun ITEM trouvé pour %s", numero_val_str)
        except Exception:
            logger.exception("Erreur lors de la liaison automatique des ITEMS.")

    # ----------- Sync partielle -----------
    @profiled("notion.cours.updated_since")
    def get_updated_cours(self, since_datetime: datetime) -> List[dict]:
        try:
            iso_time = since_datetime.isoformat()
            with span("notion.databases.query:cours_updated"):
                resp = self._cached_databases_query(
                    database_id=self.cours_db_id,
                    filter={"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": iso_time}},
                )
            return resp.get("results", [])
        except Exception:
            logger.exception("Impossible de récupérer les cours mis à jour partiellement")
            return []

    # ----------- Mise à jour PDF -----------
    @profiled("notion.cours.update_pdf")
    def update_course_pdf(self, cours_id: str, pdf_url: Optional[str], is_college: bool = False) -> None:
        """
        Met à jour l'URL PDF (vue semestre/college) sans jamais pousser 'None' ni un chemin local.
        Accepte aussi un dict {"url": "..."} par sécurité.
        """
        field_name = "URL PDF COLLEGE" if is_college else "URL PDF"
        url = _extract_url_value(pdf_url)
        if not (_is_url_ok(url) and _is_remote_url(url)):
            logger.info("URL PDF ignorée (vide/invalide/locale) pour %s", cours_id)
            return
        props = _sanitize_props_for_update({field_name: {"url": url}})
        if not props:
            return
        try:
            with span("notion.pages.update"):
                self.client.pages.update(page_id=cours_id, properties=props)
        except Exception:
            logger.exception("Erreur MAJ PDF (%s) pour %s", field_name, cours_id)

    # ----------- Compteurs "Cours en attente d’actions" -----------
    @profiled("notion.cours.pending_actions_counters")
    def get_pending_actions_counters(self) -> Dict[str, int]:
        """
        Retourne des compteurs best‑effort pour la DB Cours :
          - pdf_missing     : prop PDF vide/non liée
          - summary_missing : prop Résumé non faite / vide
          - anki_missing    : prop Anki non faite / vide

        Les noms exacts des propriétés sont définis dans config.py :
        COURSE_PROP_PDF / COURSE_PROP_SUMMARY / COURSE_PROP_ANKI
        """
        def _filled(prop: Dict) -> bool:
            if not prop:
                return False
            t = prop.get("type")

            if t == "checkbox":
                return bool(prop.get("checkbox"))
            if t in ("files", "relation", "multi_select", "rich_text", "people"):
                return bool(prop.get(t))
            if t in ("select", "status"):
                return bool(prop.get(t))
            if t in ("url", "email", "phone_number"):
                return bool(prop.get(t))
            if t == "title":
                return bool(prop.get("title"))
            if t == "formula":
                f = prop.get("formula", {})
                ft = f.get("type")
                if ft == "boolean":
                    return bool(f.get("boolean"))
                if ft == "number":
                    n = f.get("number")
                    return n is not None and n != 0
                if ft in ("string", "date"):
                    return bool(f.get(ft))
            if t == "date":
                return bool(prop.get("date"))
            if t == "number":
                v = prop.get("number")
                return v is not None and v != 0
            return False  # par défaut

        def _find_prop(props: Dict, primary: str) -> Optional[Dict]:
            if primary in props:
                return props[primary]
            wanted = primary.strip().lower()
            for k, v in props.items():
                if k.strip().lower() == wanted:
                    return v
            alias_bag = {
                COURSE_PROP_PDF: {"pdf", "fichier", "fichiers", "document", "documents"},
                COURSE_PROP_SUMMARY: {"resume", "synthèse", "synthese", "notes", "summary"},
                COURSE_PROP_ANKI: {"flashcards", "cartes anki", "cartes", "deck"},
            }
            for k, v in props.items():
                if k.strip().lower() in alias_bag.get(primary, set()):
                    return v
            return None

        pdf_missing = summary_missing = anki_missing = 0

        cursor = None
        while True:
            payload = {"database_id": self.cours_db_id, "page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            resp = self.client.databases.query(**payload)

            for row in resp.get("results", []):
                props = row.get("properties", {}) or {}

                p_pdf = _find_prop(props, COURSE_PROP_PDF)
                p_sum = _find_prop(props, COURSE_PROP_SUMMARY)
                p_ank = _find_prop(props, COURSE_PROP_ANKI)

                if p_pdf is not None and not _filled(p_pdf):
                    pdf_missing += 1
                if p_sum is not None and not _filled(p_sum):
                    summary_missing += 1
                if p_ank is not None and not _filled(p_ank):
                    anki_missing += 1

            if not resp.get("has_more", False):
                break
            cursor = resp.get("next_cursor")

        return {
            "pdf_missing": int(pdf_missing),
            "summary_missing": int(summary_missing),
            "anki_missing": int(anki_missing),
        }

    # ------------------- Utilitaires DB génériques -------------------
    @profiled("notion.databases.query:generic")
    def query_database(self, database_id: str, options: dict | None = None) -> dict:
        """
        Wrapper robuste autour /databases/{id}/query.
        - Supprime 'filter' si vide ({}), car Notion le refuse.
        - Laisse passer 'sorts' vide ([]) qui est accepté.
        - Utilise un cache TTL sur les payloads identiques.
        """
        opts = dict(options or {})
        filt = opts.get("filter", None)
        if isinstance(filt, dict) and len(filt) == 0:
            opts.pop("filter", None)  # ← évite 400 "body.filter.* should be defined"

        try:
            return self._cached_databases_query(database_id=database_id, **opts)
        except APIResponseError as e:
            logger.error("Erreur requête vers la base %s: %s", database_id, e)
            raise

    @profiled("notion.blocks.update:text")
    def update_block_text(self, block_id: str, new_text: str) -> None:
        try:
            with span("notion.blocks.update"):
                self.client.blocks.update(
                    block_id=block_id, paragraph={"rich_text": [{"type": "text", "text": {"content": new_text}}]}
                )
        except Exception:
            logger.exception("Erreur lors de la mise à jour du texte du bloc")

    @profiled("notion.databases.query:all_pages")
    def get_all_pages(self, database_id: str) -> List[dict]:
        return get_all_notion_pages(self.client, database_id)

    # ------------------- Cache des métadonnées -------------------
    @profiled("notion.db.props:get")
    def _db_props(self, database_id: str) -> dict:
        if database_id in self._props_cache:
            return self._props_cache[database_id]
        with span("notion.databases.retrieve"):
            info = self.client.databases.retrieve(database_id=database_id)
        props = info.get("properties", {}) or {}
        self._props_cache[database_id] = props
        return props

    def _title_prop(self, database_id: str) -> Optional[str]:
        props = self._db_props(database_id)
        for name, meta in props.items():
            if meta.get("type") == "title":
                return name
        return None

    # ------------------- Résumés / titres -------------------
    def _course_title(self, page: dict) -> str:
        title_key = next((k for k, v in page["properties"].items() if v.get("type") == "title"), None)
        rich = page["properties"].get(title_key or "Name", {}).get("title", [])
        parts = []
        for t in rich:
            if "plain_text" in t:
                parts.append(t["plain_text"])
            else:
                parts.append(t.get("text", {}).get("content", ""))
        return "".join(parts).strip() or "Sans titre"

    # ---- Mémo interne pour courses_due_today ---------------------------------
    def _cache_get_courses_today(self, key: str, ttl_sec: int = 60) -> Optional[list]:
        if self._courses_today_cache["key"] == key:
            if time.time() - self._courses_today_cache["ts"] <= ttl_sec:
                return self._courses_today_cache["data"]
        return None

    def _cache_set_courses_today(self, key: str, data: list) -> None:
        self._courses_today_cache = {"key": key, "ts": time.time(), "data": data}

    @profiled("logic.courses_due_today")
    def get_courses_due_today(self, force_refresh: bool = False) -> List[dict]:
        props = self._db_props(COURSES_DATABASE_ID)
        date_props = [pname for pname, meta in props.items() if meta.get("type") == "date"]
        if not date_props:
            return []
        today_iso = date.today().isoformat()
        cache_key = f"{COURSES_DATABASE_ID}|{today_iso}"
        if not force_refresh:
            cached = self._cache_get_courses_today(cache_key, ttl_sec=60)
            if cached is not None:
                return cached
        or_filters = [{"property": pname, "date": {"equals": today_iso}} for pname in date_props]
        with span("notion.databases.query:courses_due_today"):
            resp = self._cached_databases_query(
                database_id=COURSES_DATABASE_ID, filter={"or": or_filters}, page_size=100
            )
        results: List[dict] = []
        for page in resp.get("results", []):
            matched, is_college = [], False
            for pname in date_props:
                val = page["properties"].get(pname, {}).get("date")
                if val and (val.get("start") or "")[:10] == today_iso:
                    matched.append(pname)
                    if "collège" in pname.lower() or "college" in pname.lower():
                        is_college = True
            if matched:
                item_num = None
                if is_college:
                    item_num = page.get("properties", {}).get(ITEM_NUMBER_PROP_COURS, {}).get("number")
                results.append(
                    {
                        "id": page["id"],
                        "title": self._course_title(page),
                        "matched_props": matched,
                        "is_college": is_college,
                        "item_num": item_num,
                    }
                )
        self._cache_set_courses_today(cache_key, results)
        return results

    @profiled("logic.courses_due_on")
    def get_courses_due_on(self, d: Union[str, date, datetime]) -> List[dict]:
        if isinstance(d, datetime):
            day = d.date().isoformat()
        elif isinstance(d, date):
            day = d.isoformat()
        else:
            day = str(d)
        props = self._db_props(COURSES_DATABASE_ID)
        date_props = [pname for pname, meta in props.items() if meta.get("type") == "date"]
        if not date_props:
            return []
        or_filters = [{"property": pname, "date": {"equals": day}} for pname in date_props]
        with span("notion.databases.query:courses_due_on"):
            resp = self._cached_databases_query(
                database_id=COURSES_DATABASE_ID, filter={"or": or_filters}, page_size=50
            )
        results: List[dict] = []
        for page in resp.get("results", []):
            matched, is_college = [], False
            for pname in date_props:
                val = page["properties"].get(pname, {}).get("date")
                if val and (val.get("start") or "")[:10] == day:
                    matched.append(pname)
                    if "collège" in pname.lower() or "college" in pname.lower():
                        is_college = True
            if matched:
                item_num = None
                if is_college:
                    item_num = page.get("properties", {}).get(ITEM_NUMBER_PROP_COURS, {}).get("number")
                results.append(
                    {
                        "id": page["id"],
                        "title": self._course_title(page),
                        "matched_props": matched,
                        "is_college": is_college,
                        "item_num": item_num,
                    }
                )
        return results

    @profiled("notion.cours.search")
    def search_courses(self, query: str, limit: int = 8) -> List[dict]:
        q = (query or "").strip()
        if not q:
            return []
        props = self._db_props(COURSES_DATABASE_ID)
        title_prop = next((k for k, v in props.items() if v.get("type") == "title"), "Cours")
        flt = {"property": title_prop, "title": {"contains": q}}
        with span("notion.databases.query:search_courses"):
            resp = self._cached_databases_query(
                database_id=COURSES_DATABASE_ID, filter=flt, page_size=max(1, min(limit, 25))
            )
        out: List[dict] = []
        for page in resp.get("results", []):
            pprops = page.get("properties", {})
            is_college = False
            if pprops.get(ITEM_NUMBER_PROP_COURS, {}).get("number") is not None:
                is_college = True
            elif pprops.get("Collège", {}).get("multi_select"):
                is_college = True
            out.append(
                {
                    "id": page["id"],
                    "title": self._course_title(page),
                    "is_college": is_college,
                    "item_num": pprops.get(ITEM_NUMBER_PROP_COURS, {}).get("number"),
                }
            )
        return out

    @profiled("logic.increment_review_counter")
    def increment_review_counter(self, page_id: str, is_college: bool) -> None:
        target = "Nombre lecture college" if is_college else "Nombre lecture"
        with span("notion.pages.retrieve"):
            page = self._cached_pages_retrieve(page_id)
        current = page.get("properties", {}).get(target, {}).get("number") or 0
        with span("notion.pages.update"):
            self.client.pages.update(page_id=page_id, properties={target: {"number": current + 1}})

    @profiled("logic.append_review_to_bilan")
    def append_review_to_daily_bilan(self, course_title: str) -> None:
        if hasattr(self, "append_daily_bilan"):
            self.append_daily_bilan([f"Cours {course_title} révisé ce jour"], "")
            return
        page = self.get_today_todo_page(TO_DO_DATABASE_ID)
        if not page:
            return
        with span("notion.blocks.children.append:review_line"):
            self.client.blocks.children.append(
                block_id=page["id"],
                children=[{
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"Cours {course_title} révisé ce jour"}}]},
                }],
            )

    # ------------------- Cache de NOM de propriété (Date/Statut) -------------------
    def _get_prop_cached(self, db_id: str, canonical: str, expected_type: str | None = None) -> str:
        """
        Retourne le **NOM** de propriété (ex: "Date", "Statut") en le lisant depuis un cache persistant.
        Si absent, lit le schéma UNE fois et choisit le meilleur candidat par type.
        Le nom est mémorisé avec schema_cache (get/set_prop_id).
        """
        name = get_prop_id(db_id, canonical)  # on mémorise le NOM sous la clé 'canonical'
        if name:
            return name

        schema = self._db_props(db_id)
        if canonical in schema:
            name = canonical
        elif expected_type:
            for pname, meta in schema.items():
                if meta.get("type") == expected_type:
                    name = pname
                    break
        if not name:
            raise KeyError(f"[NotionAPI] Propriété {canonical!r} introuvable dans la DB {db_id!r}")

        set_prop_id(db_id, canonical, name)  # on persiste le NOM
        return name

    # =================== To-Do quotidiennes ===================
    @staticmethod
    def date_label(d: datetime) -> str:
        return f"📅 {d.day} {d.strftime('%B %Y')}"

    # ---------- Cache helpers (To-Do par date) ----------
    def _cache_get_todo_page(self, database_id: str, date_str: str) -> Optional[dict]:
        return self._todo_page_cache.get(database_id, {}).get(date_str)

    def _cache_set_todo_page(self, database_id: str, date_str: str, page: Optional[dict]) -> None:
        if page is None:
            return
        self._todo_page_cache.setdefault(database_id, {})[date_str] = page

    def clear_todo_cache(self, database_id: Optional[str] = None) -> None:
        if database_id is None:
            self._todo_page_cache.clear()
        else:
            self._todo_page_cache.pop(database_id, None)

    # ---------- Requêtes/MAJ To-Do ----------
    @profiled("todo.get_page_by_date")
    def get_todo_page_by_date(self, database_id: str, date_str: str) -> Optional[dict]:
        try:
            # Cache mémoire app local
            cached = self._cache_get_todo_page(database_id, date_str)
            if cached:
                return cached

            date_prop = self._get_prop_cached(database_id, "Date", expected_type="date")
            with span("notion.databases.query:todo_by_date"):
                resp = self._cached_databases_query(
                    database_id=database_id,
                    filter={"property": date_prop, "date": {"equals": date_str}},
                    page_size=1,
                )
            results = resp.get("results", [])
            page = results[0] if results else None
            if page:
                self._cache_set_todo_page(database_id, date_str, page)
            return page
        except Exception:
            logger.exception("Erreur get_todo_page_by_date(%s)", date_str)
            return None

    @profiled("todo.create_minimal_page")
    def create_minimal_todo_page(self, database_id: str, title: str, iso_date: str) -> Optional[dict]:
        try:
            date_prop = self._get_prop_cached(database_id, "Date", expected_type="date")
            title_prop = self._title_prop(database_id) or "Name"
            props = {
                title_prop: {"title": [{"type": "text", "text": {"content": title}}]},
                date_prop: {"date": {"start": iso_date}},
            }
            with span("notion.pages.create:todo"):
                page = self.client.pages.create(parent={"database_id": database_id}, properties=props)
            with span("notion.blocks.children.append:heading_bilan"):
                self.client.blocks.children.append(
                    block_id=page["id"],
                    children=[{
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "BILAN DU JOUR"}}]},
                    }],
                )
            # write-through cache (création)
            self._cache_set_todo_page(database_id, iso_date, page)
            return page
        except Exception:
            logger.exception("Erreur create_minimal_todo_page(%s)", iso_date)
            return None

    def set_todo_status(self, page_id: str, database_id: str, value: str) -> None:
        status_prop = self._get_prop_cached(database_id, "Statut", expected_type="status")
        meta = self._db_props(database_id).get(status_prop, {}) or {}
        typ = meta.get("type") or "status"  # "status" ou "select"

        # Lire la valeur actuelle pour no-op si identique
        try:
            cur = self._cached_pages_retrieve(page_id)
            cur_val = (cur.get("properties", {}).get(status_prop, {}) or {}).get(typ, {}) or {}
            cur_name = cur_val.get("name")
            if cur_name == value:
                return
        except Exception:
            pass

        try:
            with span("notion.pages.update:todo_status"):
                self.client.pages.update(page_id=page_id, properties={status_prop: {typ: {"name": value}}})
        except Exception:
            logger.exception("Erreur mise à jour statut (%s=%s) sur %s", status_prop, value, page_id)
            return

        # --- write-through cache ---
        try:
            for date_map in self._todo_page_cache.values():
                for _, p in date_map.items():
                    if p.get("id") == page_id:
                        p.setdefault("properties", {}).setdefault(status_prop, {}).setdefault(typ, {})
                        p["properties"][status_prop][typ]["name"] = value
        except Exception:
            pass

    @profiled("todo.get_today_page")
    def get_today_todo_page(self, database_id: str) -> Optional[dict]:
        return self.get_todo_page_by_date(database_id, datetime.today().strftime("%Y-%m-%d"))

    @profiled("todo.get_today_checkboxes")
    def get_todo_checkboxes_for_date(
        self, database_id: str, d: Union[str, date, datetime]
    ) -> Tuple[Optional[dict], Dict[str, bool]]:
        if isinstance(d, datetime):
            day = d.date().isoformat()
        elif isinstance(d, date):
            day = d.isoformat()
        else:
            day = str(d)

        page = self.get_todo_page_by_date(database_id, day)
        if not page:
            return None, {}
        props = page.get("properties", {}) or {}
        checks = {k: v["checkbox"] for k, v in props.items() if v.get("type") == "checkbox"}
        return page, checks

    @profiled("todo.get_today_checkboxes")
    def get_today_todo_checkboxes(self, database_id: str) -> Tuple[Optional[dict], Dict[str, bool]]:
        page = self.get_today_todo_page(database_id)
        if not page:
            return None, {}
        props = page.get("properties", {}) or {}
        checks = {k: v["checkbox"] for k, v in props.items() if v.get("type") == "checkbox"}
        return page, checks

    @profiled("todo.list_todo_blocks")
    def list_todo_blocks(self, page_id: str) -> List[dict]:
        out: List[dict] = []
        try:
            start_cursor = None
            while True:
                with span("notion.blocks.children.list:page"):
                    if start_cursor:
                        resp = self.client.blocks.children.list(block_id=page_id, start_cursor=start_cursor)
                    else:
                        resp = self.client.blocks.children.list(block_id=page_id)
                for b in resp.get("results", []):
                    if b.get("type") == "to_do":
                        out.append(b)
                if not resp.get("has_more"):
                    break
                start_cursor = resp.get("next_cursor")
        except Exception:
            logger.exception("Erreur list_todo_blocks(%s)", page_id)
        return out

    @profiled("todo.set_todo_checked")
    def set_todo_checked(self, block_id: str, checked: bool) -> None:
        try:
            with span("notion.blocks.update:to_do.checked"):
                self.client.blocks.update(block_id=block_id, to_do={"checked": bool(checked)})
        except Exception:
            logger.exception("Erreur set_todo_checked(%s)", block_id)

    # ------------------- Pages génériques -------------------
    @profiled("notion.pages.create:generic")
    def create_page(
        self,
        database_id: str,
        title: str,
        properties: Optional[dict] = None,
        content: Optional[str] = None,
    ) -> Optional[dict]:
        try:
            title_prop = self._title_prop(database_id) or "Name"
            props = {title_prop: {"title": [{"text": {"content": title}}]}}
            if properties:
                props.update(properties)

            children = []
            if content:
                for line in content.strip().split("\n"):
                    if line.strip() == "":
                        children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}})
                    else:
                        children.append(
                            {
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]},
                            },
                        )

            with span("notion.pages.create"):
                return self.client.pages.create(parent={"database_id": database_id}, properties=props, children=children)
        except Exception:
            logger.exception("Erreur lors de la création de la page Notion")
            return None

    @profiled("notion.pages.update:generic")
    def update_page(self, page_id: str, properties: dict) -> Optional[dict]:
        """
        Update direct mais on sanitize quand même au cas où.
        """
        try:
            safe = _sanitize_props_for_update(properties or {})
            with span("notion.pages.update"):
                return self.client.pages.update(page_id=page_id, properties=safe)
        except Exception:
            logger.exception("Erreur lors de la mise à jour de la page %s", page_id)
            return None

    @profiled("todo.checkbox.update")
    def update_checkbox_property(self, page_id: str, property_name: str, value: bool) -> None:
        with span("notion.pages.update"):
            self.client.pages.update(page_id=page_id, properties={property_name: {"checkbox": value}})

        # --- write-through cache ---
        try:
            for date_map in self._todo_page_cache.values():
                for _, p in date_map.items():
                    if p.get("id") == page_id:
                        p.setdefault("properties", {}).setdefault(property_name, {"type": "checkbox"})
                        p["properties"][property_name]["checkbox"] = bool(value)
        except Exception:
            pass

    @profiled("todo.get_today_bilan_block")
    def get_today_bilan_block(self, database_id: str) -> str:
        page = self.get_today_todo_page(database_id)
        if not page:
            return ""
        blocks = self.get_page_blocks(page["id"])
        for i, b in enumerate(blocks):
            t = b.get("type", "")
            if t.startswith("heading"):
                rich = b[t].get("rich_text", [])
                if rich and "bilan du jour" in rich[0].get("plain_text", "").lower():
                    if i + 1 < len(blocks) and blocks[i + 1]["type"] == "paragraph":
                        rich2 = b[i + 1 if False else i + 1]  # silence l'interpréteur sur l'index
                        rich2 = blocks[i + 1]["paragraph"].get("rich_text", [])
                        return rich2[0]["plain_text"] if rich2 else ""
        return ""

    @profiled("todo.append_bilan")
    def append_bilan(self, text: str) -> None:
        page = self.get_today_todo_page(TO_DO_DATABASE_ID)
        if not page:
            return
        blocks = self.get_page_blocks(page["id"])
        for i, b in enumerate(blocks):
            t = b.get("type", "")
            if t.startswith("heading"):
                rich = b[t].get("rich_text", [])
                if rich and "bilan du jour" in rich[0].get("plain_text", "").lower():
                    if i + 1 < len(blocks) and blocks[i + 1]["type"] == "paragraph":
                        paragraph_id = blocks[i + 1]["id"]
                        old = blocks[i + 1]["paragraph"].get("rich_text", [])
                        old_text = old[0]["plain_text"] if old else ""
                        new_text = (old_text.strip() + ("\n" if old_text.strip() else "") + text).strip()
                        self.update_block_text(paragraph_id, new_text)
                    break

    @profiled("todo.append_daily_bilan")
    def append_daily_bilan(self, notes: List[str], comment: str) -> None:
        page = self.get_today_todo_page(TO_DO_DATABASE_ID)
        if not page:
            return
        page_id = page["id"]
        with span("notion.blocks.children.list"):
            blocks = self.client.blocks.children.list(block_id=page_id).get("results", [])
        header_idx = None
        for i, b in enumerate(blocks):
            t = b.get("type")
            if t in ("heading_1", "heading_2", "heading_3"):
                rich = b[t].get("rich_text", [])
                if rich and rich[0].get("plain_text", "").strip().upper() == "BILAN DU JOUR":
                    header_idx = i
                    break
        if header_idx is None:
            with span("notion.blocks.children.append:heading"):
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=[{
                        "object": "block", "type": "heading_2",
                        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "BILAN DU JOUR"}}]},
                    }],
                )
            with span("notion.blocks.children.list"):
                blocks = self.client.blocks.children.list(block_id=page_id).get("results", [])
            header_idx = len(blocks) - 1

        def _is_bullet(b): return b.get("type") == "bulleted_list_item"
        def _is_para(b): return b.get("type") == "paragraph"
        def _text_of(b, key): return "".join(t.get("plain_text", "") for t in b[key].get("rich_text", [])).strip()

        existing_bullets_ids, existing_bullets_texts = [], []
        existing_comment_id, existing_comment_text = None, None
        for b in blocks[header_idx + 1:]:
            if _is_bullet(b):
                existing_bullets_ids.append(b["id"])
                existing_bullets_texts.append(_text_of(b, "bulleted_list_item"))
            elif _is_para(b) and _text_of(b, "paragraph"):
                existing_comment_id = b["id"]
                existing_comment_text = _text_of(b, "paragraph")

        for bid in existing_bullets_ids:
            with span("notion.blocks.update:archive_bullet"):
                self.client.blocks.update(block_id=bid, archived=True)
        if existing_comment_id:
            with span("notion.blocks.update:archive_comment"):
                self.client.blocks.update(block_id=existing_comment_id, archived=True)

        merged_notes = [n.strip() for n in existing_bullets_texts if n.strip()]
        merged_notes += [n.strip() for n in (notes or []) if n.strip()]

        if merged_notes:
            with span("notion.blocks.children.append:bullets"):
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=[{
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": n}}]},
                    } for n in merged_notes],
                )

        final_comment = (comment or "").strip() or (existing_comment_text or "").strip()
        if final_comment:
            with span("notion.blocks.children.append:comment"):
                self.client.blocks.children.append(
                    block_id=page_id,
                    children=[{
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": final_comment}}]},
                    }],
                )

    # ------------------- Lecture générique des blocs d'une page -------------------
    @profiled("notion.blocks.children.list:generic_page")
    def get_page_blocks(self, page_id: str) -> List[dict]:
        out: List[dict] = []
        start_cursor = None
        while True:
            with span("notion.blocks.children.list"):
                if start_cursor:
                    resp = self.client.blocks.children.list(block_id=page_id, start_cursor=start_cursor)
                else:
                    resp = self.client.blocks.children.list(block_id=page_id)
            out.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            start_cursor = resp.get("next_cursor")
        return out


# --------------------------- Singleton Client ---------------------------
_NOTION_SINGLETON: Optional[NotionAPI] = None

def get_notion_client() -> NotionAPI:
    global _NOTION_SINGLETON
    if _NOTION_SINGLETON is None:
        _NOTION_SINGLETON = NotionAPI()
    return _NOTION_SINGLETON
