import os
from dotenv import load_dotenv

# Charger variables depuis .env
load_dotenv()

# Apparence
THEME = "light"

# Fichiers et chemins
DATA_DIR = "data"
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")

# Variables Notion
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_COURS_ID = os.getenv("DATABASE_COURS_ID")
DATABASE_UE_ID = os.getenv("DATABASE_UE_ID")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Google
GOOGLE_CREDENTIALS = "credentials.json"

# Créer data si inexistant
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
