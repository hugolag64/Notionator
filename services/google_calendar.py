# services/google_calendar.py
from __future__ import annotations
import os
import pickle
from datetime import datetime, timedelta
from typing import Optional
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes: création/lecture d'événements
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Couleurs d'événement Google Calendar (IDs 1..11)
# Référence API: 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana,
# 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato
COLOR_ID = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",

    # alias FR utiles
    "lavande": "1",
    "sauge": "2",
    "raisin": "3",
    "flamant": "4",
    "banane": "5",
    "mandarine": "6",
    "paon": "7",
    "graphite_fr": "8",
    "myrtille": "9",
    "basilic": "10",
    "tomate": "11",

    # alias spécifiques demandés
    "fleur_de_cerisier": "4",  # proche du rose (Flamingo)
    "fleur de cerisier": "4",
}

# Par défaut projet (Réunion)
DEFAULT_TZ = "Indian/Reunion"

# Choix demandés
COURSE_COLOR_ID = COLOR_ID["basilic"]            # vert basilic
ITEM_COLOR_ID = COLOR_ID["fleur_de_cerisier"]    # rose "cherry blossom" ~ Flamingo


class GoogleCalendarClient:
    """
    Auth OAuth2 utilisateur (réutilise data/credentials.json et data/token_calendar.pickle).
    Fournit des helpers pour créer des événements colorés.
    """
    def __init__(self, credentials_path: Optional[str] = None, token_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(base_dir)
        data_dir = os.path.join(project_root, "data")
        os.makedirs(data_dir, exist_ok=True)

        self.credentials_path = credentials_path or os.path.join(data_dir, "credentials.json")
        self.token_path = token_path or os.path.join(data_dir, "token_calendar.pickle")
        self.service = self._authenticate()

    # ---------- OAuth ----------
    def _authenticate(self):
        creds = None
        if os.path.exists(self.token_path):
            with open(self.token_path, "rb") as token:
                creds = pickle.load(token)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "wb") as token:
                pickle.dump(creds, token)
        return build("calendar", "v3", credentials=creds)

    # ---------- Core ----------
    def create_event(
        self,
        calendar_id: str,
        title: str,
        start_dt: datetime,
        duration_minutes: int = 30,
        timezone: str = DEFAULT_TZ,
        description: str | None = None,
        color_id: str | None = None,
        location: str | None = None,
        reminders_override: list[dict] | None = None,
        attendees: list[dict] | None = None,
    ):
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": timezone},
            "description": description,
            "colorId": color_id,
            "location": location,
        }
        if reminders_override is not None:
            body["reminders"] = {"useDefault": False, "overrides": reminders_override}
        if attendees:
            body["attendees"] = attendees

        # retire les clés None
        body = {k: v for k, v in body.items() if v is not None}

        return self.service.events().insert(calendarId=calendar_id, body=body).execute()

    # ---------- Helpers couleurs ----------
    def resolve_color_id(self, name_or_id: str | None) -> str | None:
        """
        Accepte un ID "1".."11" ou un nom/alias ("basilic", "fleur de cerisier", etc.).
        Renvoie un ID valide ou None.
        """
        if not name_or_id:
            return None
        s = str(name_or_id).strip().lower()
        if s.isdigit() and s in {str(i) for i in range(1, 12)}:
            return s
        return COLOR_ID.get(s.replace(" ", "_")) or COLOR_ID.get(s)

    # ---------- Raccourcis demandés ----------
    def create_course_event(
        self,
        calendar_id: str,
        title: str,
        start_dt: datetime,
        duration_minutes: int = 60,
        timezone: str = DEFAULT_TZ,
        description: str | None = None,
        location: str | None = None,
    ):
        """Événement 'Cours' en vert basilic."""
        return self.create_event(
            calendar_id=calendar_id,
            title=title,
            start_dt=start_dt,
            duration_minutes=duration_minutes,
            timezone=timezone,
            description=description,
            location=location,
            color_id=COURSE_COLOR_ID,
        )

    def create_item_event(
        self,
        calendar_id: str,
        title: str,
        start_dt: datetime,
        duration_minutes: int = 30,
        timezone: str = DEFAULT_TZ,
        description: str | None = None,
        location: str | None = None,
    ):
        """Événement 'Item' en rose fleur de cerisier."""
        return self.create_event(
            calendar_id=calendar_id,
            title=title,
            start_dt=start_dt,
            duration_minutes=duration_minutes,
            timezone=timezone,
            description=description,
            location=location,
            color_id=ITEM_COLOR_ID,
        )

    # ---------- Outils (optionnels) ----------
    def list_event_colors(self) -> dict:
        """
        Récupère la palette API (IDs -> hex). Utile pour vérifier les teintes exactes.
        """
        return self.service.colors().get().execute().get("event", {})
