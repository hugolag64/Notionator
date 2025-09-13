from __future__ import annotations
import os, json, pathlib
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes minimum pour test Drive
SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

BASE = pathlib.Path(__file__).parent
CRED_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", BASE / "credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", BASE / "token.json")

def check_creds():
    # Lire credentials.json
    if not pathlib.Path(CRED_PATH).exists():
        print(f"❌ credentials.json introuvable : {CRED_PATH}")
        return

    with open(CRED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    client_id = data.get("installed", {}).get("client_id")
    project_id = data.get("installed", {}).get("project_id")
    print(f"🔑 ClientID: {client_id}")
    print(f"📂 ProjectID: {project_id}")

    creds = None
    # Charger le token s’il existe
    if pathlib.Path(TOKEN_PATH).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Rafraîchissement du token…")
            creds.refresh(Request())

    # Si pas de token ou invalide, lancer le flow OAuth
    if not creds or not creds.valid:
        print("⚠️ Pas de token valide, lancement du flow OAuth…")
        flow = InstalledAppFlow.from_client_secrets_file(str(CRED_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"✅ Nouveau token enregistré : {TOKEN_PATH}")

    # Test API Drive
    try:
        service = build("drive", "v3", credentials=creds)
        about = service.about().get(fields="user(displayName)").execute()
        print(f"✅ Connecté à Drive en tant que : {about['user']['displayName']}")
    except Exception as e:
        print(f"❌ Erreur lors de l'appel à Drive API : {e}")

if __name__ == "__main__":
    check_creds()
