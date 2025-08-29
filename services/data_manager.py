# services/data_manager.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from threading import Thread, Lock

from services.notion_client import NotionAPI, get_notion_client
from services.logger import get_logger
from services.profiler import profiled, span
from config import DATABASE_COURS_ID as COURSES_DATABASE_ID

logger = get_logger(__name__)
CACHE_FILE = os.path.join("data", "cache.json")


class DataManager:
    """
    Gère le cache local des cours/UE (Notion) + sync fiable (avec état).
    Fournit une recherche locale robuste avec fallback Notion si nécessaire.
    """
    def __init__(self):
        self.notion = NotionAPI()
        self._lock = Lock()
        self.cache = {"last_sync": None, "courses": {}, "ue": {}}
        self._syncing = False
        self._ensure_cache_file()
        self.load_cache()

    # ------------------ Gestion fichier cache ------------------

    def _ensure_cache_file(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        if not os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)

    @profiled("cache.save")
    def load_cache(self):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self.cache = data
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache introuvable ou corrompu, réinitialisation.")
            self.save_cache()

    def save_cache(self):
        with self._lock:
            data = self.cache
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------ Sync Notion ------------------

    def sync(self, force=True):
        """Alias pour compatibilité. Exécute une sync bloquante."""
        return self.sync_blocking()

    def sync_async(self, on_done=None, force=True):
        if getattr(self, "_syncing", False):
            return
        self._syncing = True

        def _run():
            try:
                self.sync_blocking()
            finally:
                self._syncing = False
                if callable(on_done):
                    on_done()

        Thread(target=_run, daemon=True).start()

    def is_syncing(self) -> bool:
        return self._syncing

    def _parse_iso(self, s: str):
        try:
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

    def sync_blocking(self):
        """
        Sync BLOQUANTE Notion -> cache.
        ⚠️ FULL FETCH pour supprimer les entrées disparues/archivées/hors BDD.
        """
        # 1) Cours (full)
        all_courses_raw = self.notion.get_cours()
        all_courses_raw = self._normalize_notion_list("courses", all_courses_raw)
        fresh_courses = [c for c in all_courses_raw if self._is_valid_course(c)]

        # 2) UE (full)
        ue_list = self.notion.get_ue()
        ue_list = self._normalize_notion_list("ue", ue_list)

        # 3) ÉCRITURE EN MÉMOIRE (reconstruction complète du dict)
        with self._lock:
            self.cache["courses"] = {c["id"]: c for c in fresh_courses}
            self.cache["ue"] = {u["id"]: u for u in ue_list if isinstance(u, dict) and u.get("id")}
            self.cache["last_sync"] = datetime.now(timezone.utc).isoformat()

        # 4) Persistance
        self.save_cache()

        # 5) Auto-link robuste (hors lock)
        try:
            self.notion.auto_link_items_by_number()
        except Exception as e:
            logger.warning(f"Auto-link ITEM ↔ Cours échoué: {e}")

    # Compat historique
    def sync_with_notion(self):
        self.sync_blocking()

    def sync_background(self, on_done=None):
        """
        Sync NON bloquante. Appelle on_done() en fin si fourni.
        """
        if self._syncing:
            logger.info("Sync déjà en cours, ignore.")
            return

        def _run():
            try:
                self._syncing = True
                self.sync_blocking()
            except Exception as e:
                logger.exception(f"Sync Notion échouée: {e}")
            finally:
                self._syncing = False
                if on_done:
                    try:
                        on_done()
                    except Exception as cb_e:
                        logger.warning(f"Callback on_done a levé une exception: {cb_e}")

        Thread(target=_run, daemon=True).start()

    # ------------------ Accès cours ------------------

    def get_courses(self) -> dict:
        """Retourne une copie du dict de cours (thread-safe)."""
        with self._lock:
            return dict(self.cache.get("courses", {}))

    def get_all_courses(self):
        with self._lock:
            return list(self.cache.get("courses", {}).values())

    def get_all_courses_college(self):
        out = []
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

    def get_courses_batch(self, offset=0, limit=30):
        with self._lock:
            all_courses = [v for v in self.cache.get("courses", {}).values() if self._is_valid_course(v)]
        return all_courses[offset: offset + limit]

    def get_course_by_id(self, course_id):
        with self._lock:
            return self.cache.get("courses", {}).get(course_id)

    def update_course_local(self, course_id, fields):
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
                data = json.dumps(self.cache, ensure_ascii=False, indent=2)
            else:
                logger.warning(f"update_course_local: cours {course_id} non trouvé")
                return
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(data)

        Thread(target=self.notion.update_cours, args=(course_id, fields), daemon=True).start()

    # ------------------ UE ------------------

    def get_all_ue(self):
        with self._lock:
            return list(self.cache.get("ue", {}).values())

    def get_ue_map(self):
        mapping = {}
        with self._lock:
            ue_items = list(self.cache.get("ue", {}).items())
        for ue_id, ue in ue_items:
            props = ue.get("properties", {})
            title = props.get("UE", {}).get("title", [])
            name = title[0]["text"]["content"] if title and title[0].get("text") else "Sans titre"
            mapping[ue_id] = name
        return mapping

    # ------------------ Parsing ------------------

    def parse_course(self, raw_course, mode="semestre", ue_map=None):
        props = raw_course.get("properties", {})

        nom = props.get("Cours", {}).get("title", [{}])
        nom = nom[0]["text"]["content"] if nom and nom[0].get("text") else "Sans titre"

        item = props.get("ITEM", {}).get("number")

        if mode == "semestre":
            semestre_name = (props.get("Semestre", {}).get("select") or {}).get("name")
            if semestre_name and not str(semestre_name).startswith("Semestre "):
                semestre_name = f"Semestre {semestre_name}"

            ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
            ue_names = [ue_map[ue_id] for ue_id in ue_ids] if ue_map else []

            pdf_url = props.get("URL PDF", {}).get("url")
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

            pdf_url = props.get("URL PDF COLLEGE", {}).get("url")
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

    def get_parsed_courses(self, mode="semestre", semestre_num=None):
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

    def get_all_colleges(self):
        return self.notion.get_all_college_choices()

    def update_course(self, course_id, updates: dict):
        with self._lock:
            course = self.cache.get("courses", {}).get(course_id)
            if course is None:
                logger.warning(f"Impossible de mettre à jour le cours {course_id} : non trouvé dans le cache.")
                return
            props = course.setdefault("properties", {})
            for key, value in updates.items():
                props[key] = {"url": value}
        self.save_cache()

    def get_ue_for_semester(self, semestre_label: str) -> list[tuple[str, str]]:
        out = []
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
                logger.warning(f"update_url_local: cours {course_id} introuvable")
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"url": url}
        self.save_cache()

    def update_relation_local(self, course_id: str, prop_name: str, ids: list[str]):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning(f"update_relation_local: cours {course_id} introuvable")
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"relation": [{"id": i} for i in ids]}
        self.save_cache()

    def update_multi_select_local(self, course_id: str, prop_name: str, names: list[str]):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning(f"update_multi_select_local: cours {course_id} introuvable")
                return
            props = c.setdefault("properties", {})
            props[prop_name] = {"multi_select": [{"name": n} for n in names]}
        self.save_cache()

    def update_checkbox_local(self, course_id: str, prop_name: str, value: bool):
        with self._lock:
            c = self.cache.get("courses", {}).get(course_id)
            if not c:
                logger.warning(f"update_checkbox_local: cours {course_id} introuvable")
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
        fresh = self.notion.get_course(course_id)  # implémentez côté NotionAPI
        self.cache["courses"][course_id] = fresh
        self.save_cache()  # ← fix (_save_cache → save_cache)

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
        out = []
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
        ue = None
        ue_prop = props.get("UE", {})
        if isinstance(ue_prop, dict) and ue_prop.get("type") == "relation":
            # pas les noms ici, juste les IDs
            ue = None

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
        client = get_notion_client()
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

            # UE (optionnel)
            ue = None
            if "UE" in props and (props["UE"] or {}).get("type") == "rich_text":
                ue = "".join([p.get("plain_text", "") for p in props["UE"].get("rich_text", [])]) or None

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
