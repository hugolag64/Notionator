from dotenv import load_dotenv
import os

# Charger automatiquement le fichier .env à la racine
load_dotenv()

# --- Variables Notion ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_COURS_ID = os.getenv("DATABASE_COURS_ID")
DATABASE_UE_ID = os.getenv("DATABASE_UE_ID")
DATABASE_ITEMS_ID = "1c9b9fc31e6981dda626e622d9ac878c"

# --- Propriétés Notion personnalisées (noms des champs dans tes bases Notion) ---
ITEM_NUMBER_PROP_COURS = "ITEM"          # number dans Cours
ITEM_RELATION_PROP = "ITEM lié"          # relation dans Cours
ITEM_NUMBER_PROP_LISTE = "ITEMS"         # rich_text (texte) dans Liste ITEM


# --- Variable OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Vérification minimale pour éviter erreurs si variable manquante
if not NOTION_TOKEN:
    raise ValueError("⚠️ Variable NOTION_TOKEN manquante dans le .env")
if not DATABASE_COURS_ID or not DATABASE_UE_ID:
    raise ValueError("⚠️ Variables DATABASE_COURS_ID ou DATABASE_UE_ID manquantes dans le .env")
if not OPENAI_API_KEY:
    print("ℹ️ OPENAI_API_KEY manquant (pas bloquant si non utilisé pour l’instant)")
