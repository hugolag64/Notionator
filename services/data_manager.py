# services/data_manager.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock
from typing import Dict, List, Optional

from services.notion_client import NotionAPI, get_notion_client
from services.logger import get_logger
from services.profiler import profiled, span
from config import DATABASE_COURS_ID as COURSES_DATABASE_ID

logger = get_logger(__name__)
CACHE_FILE = os.path.join("data", "cache.json")


def _atomic_write(path: str, data: dict) -> None:
    """Écriture atomique du JSON pour éviter les fichiers corrompus."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class DataManager:
    """
    Gère le cache local des cours/UE (Notion) + sync fiable (avec état).
    Fournit une recherche locale robuste avec fallback Notion si nécessaire.
    """
    def __init__(self):
        # ✅ Singleton (réutilise connexions et rate-limit)
        self.notion: NotionAPI = get_notion_client()
        self._lock = Lock()
        self.cache: Dict = {"last_sync": None, "last_full_sync": None, "courses": {}, "ue": {}}
        self._syncing = False
        self._ensure_cache_file()
        self.load_cache()

    # ------------------ Gestion fichier cache ------------------

    def _ensure_cache_file(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        if not os.path.exists(CACHE_FILE):
            _atomic_write(CACHE_FILE, self.cache)

    @profiled("cache.load")
    def load_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                if not isinstance(data, dict):
                    raise json.JSONDecodeError("cache is not a dict", "", 0)
                # backfill clés manquantes
                data.setdefault("last_sync", None)
                data.setdefault("last_full_sync", None)
                data.setdefault("courses", {})
                data.setdefault("ue", {})
                self.cache = data
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache introuvable ou corrompu, réinitialisation.")
            self.save_cache()

    def save_cache(self):
        with self._lock:
            data = self.cache
        _atomic_write(CACHE_FILE, data)

    # ------------------ Sync Notion ------------------

    def sync(self, force_full: bool = False):
        """Alias simple : exécute une sync (par défaut delta)."""
        return self.sync_blocking(force_full=force_full)

    def sync_async(self, on_done=None, force_full: bool = False):
        if getattr(self, "_syncing", False):
            return
        self._syncing = True

        def _run():
            try:
                self.sync_blocking(force_full=force_full)
            finally:
                self._syncing = False
                if callable(on_done):
                    try:
                        on_done()
                    except Exception as cb_e:
                        logger.warning("Callback on_done a levé une exception: %s", cb_e)

        Thread(target=_run, daemon=True).start()

    def is_syncing(self) -> bool:
        return self._syncing

    def _parse_iso(self, s: Optional[str]):
        try:
            if not s:
                return None
            # Supporte "...Z" et offsets
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except Exception:
            return None

    # ----------- Normalisation robuste des réponses Notion -----------

    def _normalize_notion_list(self, name: str, raw) -> list[dict]:
        """
        Garantit une liste de dicts avec 'id'.
        - Accepte: dict avec 'results', liste hétérogène, None.
        - Filtre tout ce qui n'est pas dict/id manquant.
        """
        if raw is None:
            logger.warning("%s: reçu None", name)
            return []
        if isinstance(raw, dict) and "results" in raw:
            raw = raw.get("results") or []
        if not isinstance(raw, list):
            logger.warning("%s: attendu list, reçu %s", name, type(raw).__name__)
            return []
        out: list[dict] = []
        for i, it in enumerate(raw):
            if isinstance(it, dict) and it.get("id"):
                out.append(it)
            else:
                logger.warning("%s[%d] ignoré (type=%s)", name, i, type(it).__name__)
        return out

    # ----------- Validité d'une page cours -----------

    def _is_valid_course(self, page: dict) -> bool:
        """Garde uniquement les pages actives de la BDD 'Cours'."""
        if not isinstance(page, dict):
            return False
        if page.get("archived") is True:
            return False
        parent = page.get("parent") or {}
        if parent.get("type") != "database_id":
            return False
        if parent.get("database_id") != COURSES_DATABASE_ID:
            return False
        return True

    @profiled("dm.sync_blocking")
    def sync_blocking(self, force_full: bool = False):
        """
        Sync Notion -> cache.
        - Full fetch si premier run ou force_full=True.
        - Sinon delta basé sur last_edited_time (rapide).
        - Toujours un full « de sécurité » si le dernier full > 24h.
        """
        with self._lock:
            last_full_iso = self.cache.get("last_full_sync")
            last_sync_iso = self.cache.get("last_sync")

        now_iso = datetime.now(timezone.utc).isoformat()

        need_full = force_full or (not last_full_iso)
        if not need_full:
            try:
                last_full_dt = self._parse_iso(last_full_iso)
                if not last_full_dt or (datetime.now(timezone.utc) - last_full_dt) > timedelta(hours=24):
                    need_full = True
            except Exception:
                need_full = True

        # ---------------- FULL FETCH ----------------
        if need_full:
            logger.info("[DataManager] FULL sync en cours…")
            # 1) Cours (full)
            all_courses_raw = self.notion.get_cours()
            all_courses_raw = self._normalize_notion_list("courses", all_courses_raw)
            fresh_courses = [c for c in all_courses_raw if self._is_valid_course(c)]

            # 2) UE (full)
            ue_list = self.notion.get_ue()
            ue_list = self._normalize_notion_list("ue", ue_list)

            # 3) ÉCRITURE EN MÉMOIRE (reconstruction complète)
            with self._lock:
                self.cache["courses"] = {c["id"]: c for c in fresh_courses}
                self.cache["ue"] = {u["id"]: u for u in ue_list if isinstance(u, dict) and u.get("id")}
                self.cache["last_sync"] = now_iso
                self.cache["last_full_sync"] = now_iso

            self.save_cache()

        # ---------------- DELTA ----------------
        else:
            logger.info("[DataManager] Delta sync en cours…")
            since_dt = self._parse_iso(last_sync_iso) or self._parse_iso(last_full_iso)
            if not since_dt:
                # fallback : si mal formé, on force un full
                return self.sync_blocking(force_full=True)

            updated_pages = self.notion.get_updated_cours(since_dt)
            updated_pages = self._normalize_notion_list("courses.updated", updated_pages)

            if updated_pages:
                with self._lock:
                    for p in updated_pages:
                        if self._is_valid_course(p):
                            self.cache["courses"][p["id"]] = p
                    self.cache["last_sync"] = now_iso
                self.save_cache()

        # 4) Auto-link robuste (hors lock)
        try:
            self.notion.auto_link_items_by_number()
        except Exception as e:
            logger.warning("Auto-link ITEM ↔ Cours échoué: %s", e)

    # Compat historique
    def sync_with_notion(self):
        self.sync_blocking()

    def sync_background(self, on_done=None, force_full: bool = False):
        """
        Sync NON bloquante. Appelle on_done() en fin si fourni.
        """
        if self._syncing:
            logger.info("Sync déjà en cours, ignore.")
            return

        def _run():
            try:
                self._syncing = True
                self.sync_blocking(force_full=force_full)
            except Exception as e:
                logger.exception("Sync Notion échouée: %s", e)
            finally:
                self._syncing = False
                if on_done:
                    try:
                        on_done()
                    except Exception as cb_e:
                        logger.warning("Callback on_done a levé une exception: %s", cb_e)

        Thread(target=_run, daemon=True).start()

    # ------------------ Accès cours ------------------

    def get_courses(self) -> dict:
        """Retourne une copie du dict de cours (thread-safe)."""
        with self._lock:
            return dict(self.cache.get("courses", {}))

    def get_all_courses(self) -> List[dict]:
        with self._lock:
            return list(self.cache.get("courses", {}).values())

    def get_all_courses_college(self) -> List[dict]:
        out: List[dict] = []
        with self._lock:
            values = [v for v in self.cache.get("courses", {}).values() if self._is_valid_course(v)]
        for c in values:
            props = c.get("properties", {})
            if props.get("ITEM", {}).get("number") is None:
                continue
            parsed = self.parse_course(c, mode="college")
            if parsed:
                out.append(parsed)
        return out

    def get_courses_batch(self, offset=0, limit=30) -> List[dict]:
        with self._lock:
            all_courses = [v for v in self.cache.get("courses", {}).values() if self._is_valid_course(v)]
        return all_courses[offset: offset + limit]

    def get_course_by_id(self, course_id: str) -> Optional[dict]:
        with self._lock:
            return self.cache.get("courses", {}).get(course_id)

    def update_course_local(self, course_id: str, fields: dict):
        """
        Met à jour localement un cours et déclenche une maj async vers Notion.
        """
        with self._lock:
            if course_id in self.cache.get("courses", {}):
                props = self.cache["courses"][course_id].setdefault("properties", {})
                for k, v in fields.items():
                    if isinstance(v, str):
                        props[k] = {"url": v}
                    elif isinstance(v, dict) and "url" in v:
                        props[k] = {"url": v["url"]}
                    else:
                        props[k] = v
                snapshot = dict(self.cache)
            else:
                logger.warning("update_course_local: cours %s non trouvé", course_id)
                return
        _atomic_write(CACHE_FILE, snapshot)

        Thread(target=self.notion.update_cours, args=(course_id, fields), daemon=True).start()

    # ------------------ UE ------------------

    def get_all_ue(self) -> List[dict]:
        with self._lock:
            return list(self.cache.get("ue", {}).values())

    def get_ue_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        with self._lock:
            ue_items = list(self.cache.get("ue", {}).items())
        for ue_id, ue in ue_items:
            props = ue.get("properties", {})
            title = props.get("UE", {}).get("title", [])
            name = title[0]["text"]["content"] if title and title[0].get("text") else "Sans titre"
            mapping[ue_id] = name
        return mapping

    # ------------------ Parsing ------------------

    def parse_course(self, raw_course: dict, mode: str = "semestre", ue_map: Optional[Dict[str, str]] = None) -> dict:
        props = raw_course.get("properties", {})

        nom_arr = props.get("Cours", {}).get("title", [{}])
        nom = nom_arr[0]["text"]["content"] if nom_arr and nom_arr[0].get("text") else "Sans titre"

        item = props.get("ITEM", {}).get("number")

        if mode == "semestre":
            semestre_name = (props.get("Semestre", {}).get("select") or {}).get("name")
            if semestre_name and not str(semestre_name).startswith("Semestre "):
                semestre_name = f"Semestre {semestre_name}"

            ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
            ue_names = [ue_map[u] for u in ue_ids] if ue_map else []

            pdf_url = (props.get("URL PDF", {}) or {}).get("url")
            pdf_ok = bool(pdf_url)

            return {
                "id": raw_course["id"],
                "nom": nom,
                "item": item,
                "ue": ue_names,
                "ue_ids": ue_ids,
                "semestre": semestre_name,
                "url_pdf": pdf_url,
                "pdf_ok": pdf_ok,
                "anki_ok": props.get("Anki", {}).get("checkbox", False),
                "resume_ok": props.get("Résumé", {}).get("checkbox", False),
                "rappel_ok": props.get("Rappel fait", {}).get("checkbox", False),
            }

        elif mode == "college":
            college_labels = props.get("Collège", {}).get("multi_select", [])
            college = college_labels[0]["name"] if college_labels else None

            pdf_url = (props.get("URL PDF COLLEGE", {}) or {}).get("url")
            pdf_ok = bool(pdf_url)

            return {
                "id": raw_course["id"],
                "nom": nom,
                "item": item,
                "college": college,
                "url_pdf": pdf_url,
                "pdf_ok": pdf_ok,
                "anki_college_ok": props.get("Anki collège", {}).get("checkbox", False),
                "resume_college_ok": props.get("Résumé collège", {}).get("checkbox", False),
                "rappel_college_ok": props.get("Rappel collège", {}).get("checkbox", False),
            }

        return {}

    def get_parsed_courses(self, mode: str = "semestre", semestre_num: Optional[str] = None) -> List[dict]:
        ue_map = self.get_ue_map()

        if mode == "semestre":
            with self._lock:
                values = [v for v in self.cache.get("courses", {}).values() if self._is_valid_course(v)]
            courses = [self.parse_course(c, mode="semestre", ue_map=ue_map) for c in values]
            if semestre_num and semestre_num != "all":
                courses = [c for c in courses if c.get("semestre") == f"Semestre {semestre_num}"]
            return courses

        elif mode == "college":
            with self._lock:
                values = [v for v in self.cache.get("courses", {}).values() if self._is_valid_course(v)]
            return [
                self.parse_course(c, mode="college")
                for c in values
                if "properties" in c and c["properties"].get("ITEM", {}).get("number") is not None
            ]

        return []

    def get_all_colleges(self) -> List[str]:
        return self.notion.get_all_college_choices()

    def update_course(self, course_id: str, updates: dict):
        with self._lock:
            course = self.cache.get("courses", {}).get(course_id)
            if course is None:
                logger.warning("Impossible de mettre à jour le cours %s : non trouvé dans le cache.", course_id)
                return
            props = course.setdefault("properties", {})
            for key, value in updates.items():
                props[key] = {"url": value}
        self.save_cache()

    def get_ue_for_semester(self, semestre_label: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        with self._lock:
            ue_items = list(self.cache.get("ue", {}).items())
        for ue_id, ue in ue_items:
            props = ue.get("properties", {})
            name = props.get("UE", {}).get("title", [])
            name = name[0]["text"]["content"] if name and name[0].get("text") else "Sans titre"
            sem = (props.get("Semestre", {}).get("select") or {}).get("name")
            if sem == semestre_label:
                out.append((name, ue_id))
        out.sort(key=lambda x: x[0].lower())
        return out

    # ------------------ Patches locaux immédiats ------------------

    def update_url_local(self, course_id: str, prop_name: str, url: str):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning("update_url_local: cours %s introuvable", course_id)
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"url": url}
        self.save_cache()

    def update_relation_local(self, course_id: str, prop_name: str, ids: list[str]):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning("update_relation_local: cours %s introuvable", course_id)
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"relation": [{"id": i} for i in ids]}
        self.save_cache()

    def update_multi_select_local(self, course_id: str, prop_name: str, names: list[str]):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning("update_multi_select_local: cours %s introuvable", course_id)
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"multi_select": [{"name": n} for n in names]}
        self.save_cache()

    def update_checkbox_local(self, course_id: str, prop_name: str, value: bool):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning("update_checkbox_local: cours %s introuvable", course_id)
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"checkbox": bool(value)}
        self.save_cache()

    def patch_properties(self, course_id: str, props_patch: dict):
        """Met à jour IMMÉDIATEMENT le cache local avec des propriétés Notion déjà formées."""
        with self._lock:
            if course_id not in self.cache.get("courses", {}):
                return
            props = self.cache["courses"][course_id].setdefault("properties", {})
            for k, v in props_patch.items():
                props[k] = v
        self.save_cache()

    def refresh_course(self, course_id: str):
        """Rafraîchit une page cours précise depuis Notion et met à jour le cache."""
        fresh = self.notion.get_cours_by_id(course_id)
        if not fresh:
            logger.warning("refresh_course: cours %s introuvable côté Notion", course_id)
            return
        with self._lock:
            self.cache["courses"][course_id] = fresh
            self.cache["last_sync"] = datetime.now(timezone.utc).isoformat()
        self.save_cache()

    # ------------------ Recherche locale + fallback Notion ------------------

    def search_courses(self, query: str, include_college: bool = True, limit: int = 200) -> list[dict]:
        """
        Recherche tolérante :
          1) Cherche dans le cache local (aplatit toutes les chaînes).
          2) Si aucun résultat -> fallback : Notion.search() et filtre sur la BDD Cours.
        Retourne des dicts normalisés: {id, title, semestre, ue, college}
        """
        q = (query or "").strip()
        if not q:
            return []

        # 1) Cache local
        local_results = self._search_courses_in_cache(q, include_college=include_college, limit=limit)
        if local_results:
            return local_results

        # 2) Fallback Notion (si cache vide ou pas à jour)
        try:
            return self._search_courses_in_notion(q, limit=min(limit, 50))
        except Exception:
            logger.exception("Fallback Notion search a échoué.")
            return []

    # ---------------------- Helpers recherche ----------------------

    def _flatten_strings(self, obj) -> str:
        """Concatène récursivement toutes les chaînes d'un objet (dict/list/str...)."""
        out: List[str] = []

        def rec(x):
            if x is None:
                return
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    rec(v)
            elif isinstance(x, (list, tuple, set)):
                for v in x:
                    rec(v)
            else:
                if isinstance(x, (int, float, bool)):
                    out.append(str(x))

        rec(obj)
        return " ".join(out)

    def _extract_title_from_props(self, props: dict) -> str:
        """Récupère un titre lisible depuis des propriétés Notion hétérogènes."""
        # 1) Propriété explicite 'Cours' (title)
        c = props.get("Cours", {})
        if c.get("type") == "title" or "title" in c:
            parts = c.get("title", []) or []
            if parts:
                return "".join([p.get("plain_text", "") for p in parts]) or "Sans titre"

        # 2) Première propriété de type 'title'
        for v in props.values():
            if isinstance(v, dict) and v.get("type") == "title":
                parts = v.get("title", []) or []
                if parts:
                    return "".join([p.get("plain_text", "") for p in parts]) or "Sans titre"

        return "Sans titre"

    def _extract_semestre_from_props(self, props: dict):
        """Essaie d'obtenir un numéro de semestre (int) depuis les props."""
        sel = (props.get("Semestre", {}) or {}).get("select") or {}
        name = sel.get("name")
        if name is None:
            return None
        # name peut être "Semestre 4" ou "4"
        txt = str(name)
        digits = "".join(ch for ch in txt if ch.isdigit())
        if digits.isdigit():
            try:
                return int(digits)
            except Exception:
                return digits
        return txt  # dernier recours (ex: "S4")

    def _normalize_course_min_from_cache(self, raw: dict) -> dict:
        """Normalise un cours issu du cache Notion brut."""
        props = raw.get("properties", {}) if isinstance(raw, dict) else {}
        title = self._extract_title_from_props(props)
        sem = self._extract_semestre_from_props(props)

        # UE (optionnel - juste une info textuelle si dispo)
        ue = None  # on ne reconstruit pas les noms ici (coûteux)

        # Collège (optionnel)
        college = None
        col_prop = props.get("Collège") or props.get("College") or {}
        if isinstance(col_prop, dict):
            if col_prop.get("type") == "multi_select":
                ms = col_prop.get("multi_select") or []
                if ms:
                    college = ", ".join([x.get("name", "") for x in ms]) or None
            elif col_prop.get("type") == "select":
                s = col_prop.get("select") or {}
                college = s.get("name")

        return {
            "id": raw.get("id"),
            "title": title or "Sans titre",
            "semestre": sem,
            "ue": ue,
            "college": college,
        }

    def _search_courses_in_cache(self, query: str, include_college: bool = True, limit: int = 200) -> list[dict]:
        q = query.casefold()
        out: list[dict] = []

        with self._lock:
            courses = self.cache.get("courses", {})
            items = courses.values() if isinstance(courses, dict) else list(courses or [])

        for raw in items:
            # on aplatit tout le dict pour matcher "HTA" où qu'il soit
            haystack = self._flatten_strings(raw).casefold()

            if not include_college:
                rc = dict(raw)
                props = dict(rc.get("properties", {}))
                props.pop("Collège", None)
                rc["properties"] = props
                haystack = self._flatten_strings(rc).casefold()

            if q in haystack:
                out.append(self._normalize_course_min_from_cache(raw))
                if len(out) >= limit:
                    break

        # Tri : priorité titre qui commence/contient
        def score(item):
            t = (item.get("title") or "").casefold()
            if t.startswith(q): return 0
            if q in t: return 1
            return 2
        out.sort(key=score)
        return out

    def _search_courses_in_notion(self, query: str, limit: int = 50) -> list[dict]:
        """
        Fallback Notion global search → filtre pages dont le parent est la BDD Cours.
        Très tolérant aux schémas (pas besoin de connaître les propriétés exactes).
        """
        # ⚠️ Utilise le client bas-niveau du wrapper
        client = self.notion.client
        out: list[dict] = []

        resp = client.search(
            query=query,
            filter={"value": "page", "property": "object"},
            page_size=limit,
        )

        results = resp.get("results", []) or []
        for res in results:
            # Garder uniquement les pages de la BDD cours
            parent = res.get("parent") or {}
            if parent.get("type") != "database_id" or parent.get("database_id") != COURSES_DATABASE_ID:
                continue

            props = res.get("properties", {}) or {}

            # Titre
            title = "Sans titre"
            if "Cours" in props and (props["Cours"] or {}).get("type") == "title":
                parts = props["Cours"].get("title", []) or []
                if parts:
                    title = "".join([p.get("plain_text", "") for p in parts]) or title
            else:
                for v in props.values():
                    if (v or {}).get("type") == "title":
                        parts = v.get("title", []) or []
                        if parts:
                            title = "".join([p.get("plain_text", "") for p in parts]) or title
                        break

            # Semestre
            semestre = None
            sem_prop = props.get("Semestre", {})
            if (sem_prop or {}).get("type") == "select":
                name = (sem_prop.get("select") or {}).get("name")
                if name:
                    digits = "".join(ch for ch in str(name) if ch.isdigit())
                    if digits.isdigit():
                        try:
                            semestre = int(digits)
                        except Exception:
                            semestre = digits
                    else:
                        semestre = name

            # UE (optionnel) — on ignore, coûteux
            ue = None

            # Collège (optionnel)
            college = None
            col_prop = props.get("Collège") or props.get("College") or {}
            if (col_prop or {}).get("type") == "select":
                college = (col_prop.get("select") or {}).get("name")
            elif (col_prop or {}).get("type") == "multi_select":
                ms = col_prop.get("multi_select") or []
                college = ", ".join([x.get("name", "") for x in ms]) or None

            out.append({
                "id": res.get("id"),
                "title": title,
                "semestre": semestre,
                "ue": ue,
                "college": college,
            })

        # Tri basique
        q = query.casefold()
        def score(item):
            t = (item.get("title") or "").casefold()
            if t.startswith(q): return 0
            if q in t: return 1
            return 2
        out.sort(key=score)
        return out
