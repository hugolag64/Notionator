import json
import os
from datetime import datetime, timezone
from threading import Thread
from services.notion_client import NotionAPI
from services.logger import get_logger

logger = get_logger(__name__)

CACHE_FILE = os.path.join("data", "cache.json")


class DataManager:
    """
    Gère le cache local (cours + UE) avec sync partielle Notion + parsing centralisé
    """

    def __init__(self):
        self.notion = NotionAPI()
        self.cache = {
            "last_sync": None,  # ISO datetime string
            "courses": {},      # {id: {...}}
            "ue": {}            # {id: {...}}
        }
        self._ensure_cache_file()
        self.load_cache()

    # ------------------ Gestion fichier cache ------------------

    def _ensure_cache_file(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        if not os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)

    def load_cache(self):
        """Charge le cache depuis le fichier JSON"""
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Cache introuvable ou corrompu, réinitialisation.")
            self.save_cache()

    def save_cache(self):
        """Sauvegarde le cache actuel dans le fichier JSON"""
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    # ------------------ Sync partielle ------------------

    def sync_with_notion(self):
        """
        Récupère uniquement les cours modifiés depuis la dernière sync.
        Si aucune dernière sync, récupère tout.
        """
        last_sync = self.cache.get("last_sync")
        if last_sync:
            since_dt = datetime.fromisoformat(last_sync)
            updated_courses = self.notion.get_updated_cours(since_dt)
        else:
            updated_courses = self.notion.get_cours()

        logger.info(f"{len(updated_courses)} cours mis à jour depuis la dernière sync")

        # Fusionner les cours
        for course in updated_courses:
            self.cache["courses"][course["id"]] = course

        # Recharger UE à chaque fois (moins fréquent)
        ue_list = self.notion.get_ue()
        self.cache["ue"] = {u["id"]: u for u in ue_list}

        # Mettre à jour timestamp de sync
        self.cache["last_sync"] = datetime.now(timezone.utc).isoformat()

        self.save_cache()

    def sync_background(self):
        """Lance la sync partielle en arrière-plan"""
        Thread(target=self.sync_with_notion, daemon=True).start()

    # ------------------ Accès cours (bruts) ------------------

    def get_all_courses(self):
        """Retourne la liste de tous les cours bruts"""
        return list(self.cache["courses"].values())

    def get_all_courses_college(self):
        """
        Retourne tous les cours parsés pour la vue Collège
        (utilise parse_cours_college de NotionAPI)
        """
        notion = self.notion
        return [
            notion.parse_cours_college(c)
            for c in self.cache["courses"].values()
            if "properties" in c and c["properties"].get("ITEM", {}).get("number") is not None
        ]

    def get_courses_batch(self, offset=0, limit=30):
        """Retourne un batch de cours bruts (pour lazy loading)"""
        all_courses = list(self.cache["courses"].values())
        return all_courses[offset: offset + limit]

    def get_course_by_id(self, course_id):
        return self.cache["courses"].get(course_id)

    def update_course_local(self, course_id, fields):
        """
        Met à jour localement un cours et déclenche une maj async vers Notion.
        """
        if course_id in self.cache["courses"]:
            props = self.cache["courses"][course_id]["properties"]
            for k, v in fields.items():
                props[k] = v

        self.save_cache()
        Thread(target=self.notion.update_cours, args=(course_id, fields), daemon=True).start()

    # ------------------ Accès UE ------------------

    def get_all_ue(self):
        return list(self.cache["ue"].values())

    def get_ue_map(self):
        """
        Retourne un mapping {id_ue: nom_ue} pour parsing rapide
        """
        mapping = {}
        for ue_id, ue in self.cache["ue"].items():
            props = ue["properties"]
            if "UE" in props and props["UE"]["title"]:
                nom = props["UE"]["title"][0]["text"]["content"]
            else:
                nom = "Sans titre"
            mapping[ue_id] = nom
        return mapping

    # ------------------ Parsing centralisé ------------------

    def parse_course(self, raw_course, mode="semestre", ue_map=None):
        """
        Transforme un cours brut Notion en format utilisable par l'UI.
        mode: "semestre" ou "college"
        """
        props = raw_course.get("properties", {})

        # Nom
        nom = props.get("Cours", {}).get("title", [{}])
        nom = nom[0]["text"]["content"] if nom and nom[0].get("text") else "Sans titre"

        # Item
        item = props.get("ITEM", {}).get("number")

        # PDF
        pdf_url = props.get("URL PDF", {}).get("url")
        pdf_ok = bool(pdf_url)

        # Mode SEMESTRE
        if mode == "semestre":
            # Semestre
            semestre_name = (props.get("Semestre", {}).get("select") or {}).get("name")
            if semestre_name and not semestre_name.startswith("Semestre "):
                semestre_name = f"Semestre {semestre_name}"

            # UE liées
            ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
            ue_names = [ue_map[ue_id] for ue_id in ue_ids if ue_map and ue_id in ue_map]

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

        # Mode COLLEGE
        elif mode == "college":
            college_labels = props.get("Collège", {}).get("multi_select", [])
            college = college_labels[0]["name"] if college_labels else None

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
        """
        Retourne les cours parsés selon le mode :
        - mode="semestre" -> filtrage par semestre si semestre_num précisé
        - mode="college"  -> tous les cours avec ITEM défini
        """
        ue_map = self.get_ue_map()

        if mode == "semestre":
            courses = [
                self.parse_course(c, mode="semestre", ue_map=ue_map)
                for c in self.cache["courses"].values()
            ]

            # Filtrer par semestre si demandé
            if semestre_num and semestre_num != "all":
                courses = [
                    c for c in courses
                    if c.get("semestre") == f"Semestre {semestre_num}"
                ]

            return courses

        elif mode == "college":
            return [
                self.parse_course(c, mode="college")
                for c in self.cache["courses"].values()
                if "properties" in c and c["properties"].get("ITEM", {}).get("number") is not None
            ]
