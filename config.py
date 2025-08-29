from dotenv import load_dotenv
import os

# Charger automatiquement le fichier .env à la racine
load_dotenv()

# --- Variables Notion ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_COURS_ID = os.getenv("DATABASE_COURS_ID")
DATABASE_UE_ID = os.getenv("DATABASE_UE_ID")
DATABASE_ITEMS_ID = "1c9b9fc31e6981dda626e622d9ac878c"
TO_DO_DATABASE_ID = os.environ.get("NOTION_TODO_DATABASE_ID")

COURSES_DATABASE_ID = DATABASE_COURS_ID
UE_DATABASE_ID = DATABASE_UE_ID


# --- Variables Google ---
GOOGLE_OAUTH_CLIENT_FILE = "data/google_oauth_client.json"
GOOGLE_CALENDAR_ID = "59a815f759b30b3ff3ada79eedb049d0d0a9ea8a2bc851325210ff8dc63239fb@group.calendar.google.com"
GOOGLE_TIMEZONE = "Indian/Reunion"


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


# --- Limite taille PDF (en Ko) ---
MAX_PDF_SIZE_KB = 80_000  # 80 Mo


# --- Focus Mode (valeurs par défaut modifiables plus tard via Settings) ---
FOCUS_DEFAULTS = {
    "WORK_MIN": 25,            # durée travail (minutes)
    "SHORT_BREAK_MIN": 5,      # pause courte
    "LONG_BREAK_MIN": 15,      # pause longue
    "SESSIONS_BEFORE_LONG": 4, # long break après N sessions de travail
    "SPOTIFY_URL": "https://open.spotify.com/playlist/37i9dQZF1DWUofLlXqRWZz?si=ba76cf627ef54ef9",  # playlist par défaut
}

# --- QuickStats: mapping des propriétés Notion sur la DB Cours ---
COURSE_PROP_PDF = "PDF"        # nom exact de la propriété qui matérialise le PDF lié
COURSE_PROP_SUMMARY = "Résumé"  # propriété qui matérialise "résumé fait"
COURSE_PROP_ANKI = "Anki"       # propriété qui matérialise "cartes Anki créées"