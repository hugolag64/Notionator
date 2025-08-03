from notion_client import Client
from config import (
    NOTION_TOKEN,
    DATABASE_COURS_ID,
    DATABASE_UE_ID,
    DATABASE_ITEMS_ID,
    ITEM_NUMBER_PROP_COURS,
    ITEM_RELATION_PROP,
    ITEM_NUMBER_PROP_LISTE
)
import re
from services.logger import get_logger  # Nouveau : import logger

logger = get_logger(__name__)


def get_all_notion_pages(client, database_id):
    """Récupère toutes les pages d'une database Notion, quelle que soit la taille."""
    all_pages = []
    next_cursor = None

    while True:
        query_params = {"database_id": database_id, "page_size": 100}
        if next_cursor:
            query_params["start_cursor"] = next_cursor

        resp = client.databases.query(**query_params)
        results = resp.get("results", [])
        all_pages.extend(results)
        if resp.get("has_more"):
            next_cursor = resp.get("next_cursor")
        else:
            break

    return all_pages


class NotionAPI:
    def __init__(self):
        self.client = Client(auth=NOTION_TOKEN)
        self.cours_db_id = DATABASE_COURS_ID
        self.ue_db_id = DATABASE_UE_ID
        logger.info("Client Notion initialisé")

    # ------------------- COURS (généraux) -------------------
    def get_cours(self):
        try:
            logger.debug("Récupération de tous les cours")
            response = self.client.databases.query(database_id=self.cours_db_id)
            cours = response.get("results", [])
            logger.info(f"{len(cours)} cours récupérés depuis Notion")
            return cours
        except Exception:
            logger.exception("Impossible de récupérer les cours")
            return []

    def get_cours_by_semestre(self, semestre_label: str):
        try:
            logger.debug(f"Récupération des cours pour Semestre {semestre_label}")
            response = self.client.databases.query(
                database_id=self.cours_db_id,
                filter={
                    "property": "Semestre",
                    "select": {"equals": f"Semestre {semestre_label}"}
                }
            )
            cours = response.get("results", [])
            logger.info(f"{len(cours)} cours récupérés pour Semestre {semestre_label}")
            return cours
        except Exception:
            logger.exception(f"Impossible de récupérer les cours du semestre {semestre_label}")
            return []

    def add_cours(self, title: str, properties: dict = None):
        try:
            logger.info(f"Ajout d'un cours : {title}")
            data = {
                "Cours": {
                    "title": [
                        {"text": {"content": title}}
                    ]
                }
            }
            if properties:
                data.update(properties)

            page = self.client.pages.create(
                parent={"database_id": self.cours_db_id},
                properties=data
            )
            logger.debug(f"Cours ajouté avec ID : {page.get('id')}")
            return page
        except Exception:
            logger.exception(f"Impossible d'ajouter le cours : {title}")
            return None

    # ------------------- UE -------------------
    def get_ue(self):
        try:
            logger.debug("Récupération des UE")
            response = self.client.databases.query(database_id=self.ue_db_id)
            ue = response.get("results", [])
            logger.info(f"{len(ue)} UE récupérées depuis Notion")
            return ue
        except Exception:
            logger.exception("Impossible de récupérer les UE")
            return []

    def add_ue(self, title: str, properties: dict = None):
        try:
            logger.info(f"Ajout d'une UE : {title}")
            data = {
                "UE": {
                    "title": [
                        {"text": {"content": title}}
                    ]
                }
            }
            if properties:
                data.update(properties)

            page = self.client.pages.create(
                parent={"database_id": self.ue_db_id},
                properties=data
            )
            logger.debug(f"UE ajoutée avec ID : {page.get('id')}")
            return page
        except Exception:
            logger.exception(f"Impossible d'ajouter l'UE : {title}")
            return None

    # ------------------- Parsing pour SemestreView -------------------
    def parse_cours(self, cours_page: dict, ue_map: dict):
        try:
            props = cours_page["properties"]
            nom = props["Cours"]["title"][0]["text"]["content"] if props["Cours"]["title"] else "Sans titre"
            ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]
            ue_names = [ue_map[ue_id] for ue_id in ue_ids if ue_id in ue_map]
            pdf_url = props.get("URL PDF", {}).get("url")
            pdf_ok = bool(pdf_url)
            anki_ok = props.get("Anki", {}).get("checkbox", False)
            resume_ok = props.get("Résumé", {}).get("checkbox", False)
            rappel_ok = props.get("Rappel fait", {}).get("checkbox", False)

            return {
                "nom": nom,
                "ue": ue_names,
                "pdf_ok": pdf_ok,
                "url_pdf": pdf_url,
                "anki_ok": anki_ok,
                "resume_ok": resume_ok,
                "rappel_ok": rappel_ok
            }
        except Exception:
            logger.exception("Erreur lors du parsing d'un cours pour SemestreView")
            return {}

    # ------------------- Helper UE Map -------------------
    def build_ue_map(self):
        try:
            logger.debug("Construction de la map UE ID -> Nom")
            ue_data = self.get_ue()
            mapping = {}
            for u in ue_data:
                props = u["properties"]
                if "UE" in props and props["UE"]["title"]:
                    nom = props["UE"]["title"][0]["text"]["content"]
                else:
                    nom = "Sans titre"
                mapping[u["id"]] = nom
            logger.debug(f"Map UE construite avec {len(mapping)} éléments")
            return mapping
        except Exception:
            logger.exception("Erreur lors de la construction de la map UE")
            return {}

    # ------------------- Cours pour CollegeView -------------------
    def get_cours_with_item(self):
        try:
            logger.debug("Récupération des cours avec ITEM non vide")
            response = self.client.databases.query(
                database_id=self.cours_db_id,
                filter={
                    "property": "ITEM",
                    "number": {"is_not_empty": True}
                }
            )
            cours = response.get("results", [])
            logger.info(f"{len(cours)} cours avec ITEM récupérés")
            return cours
        except Exception:
            logger.exception("Impossible de récupérer les cours avec ITEM")
            return []

    def parse_cours_college(self, cours_page: dict):
        try:
            props = cours_page["properties"]

            # Nom et item
            nom = props["Cours"]["title"][0]["text"]["content"] if props["Cours"]["title"] else "Sans titre"
            item = props.get("ITEM", {}).get("number")
            item_text = str(int(item)) if item is not None else ""

            # Nom du collège
            def nettoyer_nom(nom: str) -> str:
                return re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ\s-]", "", nom).strip()

            college_multi = props.get("Collège", {}).get("multi_select", [])
            college_names = [nettoyer_nom(c["name"]) for c in college_multi]
            college_text = ", ".join(college_names) if college_names else "-"

            # PDF collège
            url_pdf_college = props.get("URL PDF COLLEGE", {}).get("url")
            pdf_ok = bool(url_pdf_college)

            # Nouvelles propriétés spécifiques collège
            anki_college_ok = props.get("Anki collège", {}).get("checkbox", False)
            resume_college_ok = props.get("Résumé collège", {}).get("checkbox", False)
            rappel_college_ok = props.get("Rappel fait collège", {}).get("checkbox", False)

            # Lectures spaced repetition collège
            lecture_j3_college_ok = props.get("Lecture J3 collège", {}).get("checkbox", False)
            lecture_j7_college_ok = props.get("Lecture J7 collège", {}).get("checkbox", False)
            lecture_j14_college_ok = props.get("Lecture J14 collège", {}).get("checkbox", False)
            lecture_j30_college_ok = props.get("Lecture J30 collège", {}).get("checkbox", False)

            # Fiche EDN rollup
            fiche_url = None
            fiche_edn_prop = props.get("Fiche EDN", {})
            try:
                arr = fiche_edn_prop["rollup"]["array"]
                if arr and arr[0]["type"] == "rich_text":
                    rich = arr[0]["rich_text"]
                    if rich and "text" in rich[0]:
                        fiche_url = rich[0]["text"]["link"]["url"]
            except Exception:
                fiche_url = None

            return {
                "nom": nom,
                "item": item_text,
                "college": college_text,
                "pdf_ok": pdf_ok,
                "url_pdf": url_pdf_college,
                "anki_college_ok": anki_college_ok,
                "resume_college_ok": resume_college_ok,
                "rappel_college_ok": rappel_college_ok,
                "lecture_j3_college_ok": lecture_j3_college_ok,
                "lecture_j7_college_ok": lecture_j7_college_ok,
                "lecture_j14_college_ok": lecture_j14_college_ok,
                "lecture_j30_college_ok": lecture_j30_college_ok,
                "fiche_url": fiche_url,
            }
        except Exception:
            import traceback
            traceback.print_exc()
            return {}

    def parse_cours_semestre(self, cours_page):
        """Transforme une page Notion cours en dict prêt pour SemestreView."""
        props = cours_page.get("properties", {})

        # Nom du cours
        nom = props.get("Cours", {}).get("title", [{}])
        nom = nom[0]["text"]["content"] if nom and nom[0].get("text") else "Sans titre"

        # Semestre (normalisé avec préfixe)
        semestre = (props.get("Semestre", {}).get("select") or {}).get("name")
        if semestre and not semestre.startswith("Semestre "):
            semestre = f"Semestre {semestre}"

        # URL PDF
        pdf_url = props.get("URL PDF", {}).get("url")
        pdf_ok = bool(pdf_url)

        # Checkboxes
        anki_ok = props.get("Anki", {}).get("checkbox", False)
        resume_ok = props.get("Résumé", {}).get("checkbox", False)
        rappel_ok = props.get("Rappel fait", {}).get("checkbox", False)

        # UE liées
        ue_ids = [rel["id"] for rel in props.get("UE", {}).get("relation", [])]

        return {
            "id": cours_page.get("id"),
            "nom": nom,
            "semestre": semestre,
            "ue_ids": ue_ids,
            "pdf_ok": pdf_ok,
            "url_pdf": pdf_url,
            "anki_ok": anki_ok,
            "resume_ok": resume_ok,
            "rappel_ok": rappel_ok
        }

    def update_cours(self, cours_id, fields):
        try:
            self.client.pages.update(page_id=cours_id, properties=fields)
            logger.info(f"Mise à jour du cours {cours_id} sur Notion avec {fields}")
        except Exception:
            logger.exception(f"Erreur lors de la MAJ du cours {cours_id}")

    def get_cours_by_id(self, cours_id):
        try:
            return self.client.pages.retrieve(page_id=cours_id)
        except Exception:
            logger.exception(f"Erreur récupération du cours {cours_id}")
            return None

    def get_ue_by_id(self, ue_id):
        try:
            return self.client.pages.retrieve(page_id=ue_id)
        except Exception:
            logger.exception(f"Erreur récupération de l'UE {ue_id}")
            return None

    # ----------- Récupération de tous les choix Collège multi-select -----------
    def get_all_college_choices(self):
        try:
            db_info = self.client.databases.retrieve(self.cours_db_id)
            college_prop = db_info["properties"].get("Collège")
            if college_prop and college_prop["type"] == "multi_select":
                return [opt["name"] for opt in college_prop["multi_select"]["options"]]
            return []
        except Exception:
            logger.exception("Impossible de récupérer les choix du multi-select Collège")
            return []

    # Liaison avec ITEMS
    def auto_link_items_by_number(self):
        """
        Lie chaque page de la base Cours à la page correspondante de Liste ITEM via la propriété 'ITEM lié',
        en utilisant le numéro 'ITEM' (number dans Cours, texte dans Liste ITEM).
        """
        try:
            cours = self.client.databases.query(database_id=DATABASE_COURS_ID)["results"]

            for page in cours:
                props = page.get("properties", {})
                numero_val = props.get(ITEM_NUMBER_PROP_COURS, {}).get("number")
                item_lie = props.get(ITEM_RELATION_PROP, {}).get("relation", [])

                if numero_val is None or item_lie:
                    continue  # On saute si pas de numéro ou déjà lié

                numero_val_str = str(int(numero_val)).strip()

                items = self.client.databases.query(
                    database_id=DATABASE_ITEMS_ID,
                    filter={
                        "property": ITEM_NUMBER_PROP_LISTE,
                        "rich_text": {"equals": numero_val_str}
                    }
                )["results"]

                if items:
                    item_id = items[0]["id"]
                    self.client.pages.update(
                        page_id=page["id"],
                        properties={ITEM_RELATION_PROP: {"relation": [{"id": item_id}]}}
                    )
            logger.info("Liaison automatique ITEM terminée.")
        except Exception:
            logger.exception("Erreur lors de la liaison automatique des ITEMS.")

    # ----------- NOUVELLE MÉTHODE : Sync partielle -----------
    def get_updated_cours(self, since_datetime):
        """
        Récupère les cours dont last_edited_time >= since_datetime
        """
        try:
            iso_time = since_datetime.isoformat()
            logger.debug(f"Récupération des cours modifiés depuis {iso_time}")
            response = self.client.databases.query(
                database_id=self.cours_db_id,
                filter={
                    "timestamp": "last_edited_time",
                    "last_edited_time": {
                        "on_or_after": iso_time
                    }
                }
            )
            cours = response.get("results", [])
            logger.info(f"{len(cours)} cours mis à jour depuis {iso_time}")
            return cours
        except Exception:
            logger.exception("Impossible de récupérer les cours mis à jour partiellement")
            return []
