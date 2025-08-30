# services/drive_sync.py
from __future__ import annotations

import os
import difflib
import pickle
from typing import List, Dict, Optional

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from services.credentials import resolve_google_credentials_path
from services.logger import get_logger

log = get_logger(__name__)

# Portée Drive
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveSync:
    """
    Client Google Drive tolérant :
    - Résout automatiquement le credentials.json (env → fichier).
    - Si les credentials manquent, passe en mode désactivé (ne plante pas l'UI).
    - Fournit des helpers de recherche dans 'Médecine' / 'Collège' / 'Semestre / UE'.
    """

    def __init__(self, credentials_path: Optional[str] = None, token_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(base_dir)
        data_dir = os.path.join(project_root, "data")
        os.makedirs(data_dir, exist_ok=True)

        # Résolution des credentials (ENV prioritaire)
        if credentials_path is None:
            try:
                credentials_path = resolve_google_credentials_path()
            except FileNotFoundError as e:
                # Mode désactivé : toutes les méthodes publiques renverront [] proprement
                self._disabled = True
                self.credentials_path = None
                self.token_path = None
                self.service = None
                log.warning("DriveSync désactivé (credentials manquants) : %s", e)
                # cache dossier pour compat
                self._folder_cache: Dict[str, Dict] = {}
                return

        if token_path is None:
            token_path = os.path.join(data_dir, "token.pickle")

        self._disabled = False
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = self._authenticate()

        # cache méta-dossiers {id: {id,name,parents}}
        self._folder_cache: Dict[str, Dict] = {}

    # ---------------------- Auth
    def _authenticate(self):
        if self._disabled:
            return None

        creds = None
        if os.path.exists(self.token_path):
            try:
                with open(self.token_path, "rb") as token:
                    creds = pickle.load(token)
            except Exception:
                creds = None

        if not creds or not getattr(creds, "valid", False):
            if creds and getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
                try:
                    creds.refresh(Request())
                except Exception as e:
                    log.warning("Refresh des credentials Drive a échoué, on relance le flow: %s", e)
                    creds = None
            if not creds:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                # ouvre un serveur local pour consentement OAuth
                creds = flow.run_local_server(port=0)
            try:
                with open(self.token_path, "wb") as token:
                    pickle.dump(creds, token)
            except Exception as e:
                log.warning("Impossible d'écrire le token Drive: %s", e)

        try:
            svc = build("drive", "v3", credentials=creds)
            return svc
        except Exception as e:
            log.warning("Initialisation client Drive échouée: %s", e)
            self._disabled = True
            return None

    # ---------------------- Guards
    def _ensure_enabled(self) -> bool:
        if self._disabled or self.service is None:
            return False
        return True

    # ---------------------- Utils Drive
    def _find_folder_id(self, folder_name: Optional[str], parent_id: Optional[str] = None) -> Optional[str]:
        """ID d'un dossier par nom, optionnellement sous un parent."""
        if not self._ensure_enabled():
            return None
        if not folder_name:
            return None

        name = folder_name.replace("'", r"\'")
        q = "mimeType='application/vnd.google-apps.folder' and trashed=false and name='{0}'".format(name)
        if parent_id:
            q += f" and '{parent_id}' in parents"

        try:
            res = self.service.files().list(
                q=q,
                spaces="drive",
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name)",
                pageSize=50,
            ).execute()
        except Exception as e:
            log.warning("Recherche dossier '%s' a échoué: %s", folder_name, e)
            return None

        files = res.get("files", [])
        return files[0]["id"] if files else None

    def _get_files_in_folder(self, folder_id: Optional[str]) -> List[dict]:
        """Liste les PDF d'un dossier."""
        if not self._ensure_enabled():
            return []
        if not folder_id:
            return []
        try:
            res = self.service.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
                spaces="drive",
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name,webViewLink,parents)",
                pageSize=1000,
            ).execute()
        except Exception as e:
            log.warning("Listing des PDF du dossier %s a échoué: %s", folder_id, e)
            return []
        return res.get("files", [])

    def _get_parents(self, file_or_folder_id: Optional[str]) -> List[str]:
        """Parents (IDs) d’un fichier/dossier."""
        if not self._ensure_enabled():
            return []
        if not file_or_folder_id:
            return []
        try:
            data = self.service.files().get(
                fileId=file_or_folder_id,
                fields="id,parents",
                supportsAllDrives=True,
            ).execute()
            return data.get("parents", []) or []
        except Exception:
            return []

    def _is_under_ancestor(self, file_parents: List[str], ancestor_id: Optional[str], cache: Dict[str, List[str]]) -> bool:
        """Vrai si l’élément est sous l’ancêtre `ancestor_id`."""
        if not ancestor_id:
            return False
        stack = list(file_parents or [])
        while stack:
            pid = stack.pop()
            if pid == ancestor_id:
                return True
            if pid not in cache:
                cache[pid] = self._get_parents(pid)
            stack.extend(cache[pid])
        return False

    @staticmethod
    def _score_similarity(target: str, candidates: List[dict]) -> List[dict]:
        if not target:
            return candidates
        return sorted(
            candidates,
            key=lambda x: difflib.SequenceMatcher(
                None, (target or "").lower(), (x.get("name") or "").lower()
            ).ratio(),
            reverse=True,
        )

    # ---------------------- Méta-dossiers pour chemins lisibles
    def _get_folder_meta(self, folder_id: str) -> dict:
        """Méta dossier avec cache."""
        if not self._ensure_enabled():
            return {}
        if not folder_id:
            return {}
        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id]
        try:
            meta = self.service.files().get(
                fileId=folder_id,
                fields="id,name,parents",
                supportsAllDrives=True,
            ).execute()
        except Exception:
            meta = {}
        self._folder_cache[folder_id] = meta or {}
        return self._folder_cache[folder_id]

    def _build_sem_ue_path(self, file_obj: dict) -> str:
        """Chemin lisible 'Semestre X / UE…' ou 'Collège / …' à partir des parents Drive."""
        parents = (file_obj.get("parents") or [])
        if not parents or not self._ensure_enabled():
            return ""
        chain = []
        pid = parents[0]
        guard = 0
        while pid and guard < 16:
            meta = self._get_folder_meta(pid)
            name = meta.get("name")
            if name:
                chain.append(name)
            pp = meta.get("parents") or []
            pid = pp[0] if pp else None
            guard += 1
        if not chain:
            return ""
        chain.reverse()  # racine -> feuille

        if "Collège" in chain:
            i = chain.index("Collège")
            if i + 1 < len(chain):
                return f"Collège / {chain[i+1]}"
            return "Collège"
        for i, n in enumerate(chain):
            if str(n).startswith("Semestre"):
                ue = chain[i + 1] if i + 1 < len(chain) else None
                return f"{n} / {ue}" if ue else str(n)
        return " / ".join(chain[-2:]) if len(chain) >= 2 else chain[-1]

    # ---------------------- Recherche globale sous "Médecine"
    def search_pdf_medecine(self, query: str, limit: int = 100) -> List[dict]:
        """
        Recherche des PDF dont le nom contient `query`, restreint au sous-arbre 'Médecine'.
        Retourne des dicts {name, url, folder}.
        """
        if not self._ensure_enabled():
            return []

        query = (query or "").strip()
        if not query:
            return []

        med_id = self._find_folder_id("Médecine")
        if not med_id:
            return []

        esc = query.replace("'", r"\'")
        q = f"name contains '{esc}' and mimeType='application/pdf' and trashed=false"

        files: List[dict] = []
        page_token = None
        while True:
            try:
                resp = self.service.files().list(
                    q=q,
                    spaces="drive",
                    corpora="allDrives",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    fields="nextPageToken, files(id,name,webViewLink,parents)",
                    pageSize=200,
                    pageToken=page_token,
                ).execute()
            except Exception as e:
                log.warning("Recherche Drive a échoué: %s", e)
                break

            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token or len(files) >= 800:
                break

        # Garde sous Médecine
        parent_cache: Dict[str, List[str]] = {}
        files = [f for f in files if self._is_under_ancestor(f.get("parents"), med_id, parent_cache)]

        # Enrichit avec chemin lisible
        out = [
            {
                "name": f.get("name"),
                "url": f.get("webViewLink"),
                "folder": self._build_sem_ue_path(f),
            }
            for f in files
        ]

        return self._score_similarity(query, out)[:limit]

    # ---------------------- Collèges
    def list_pdfs_by_college(self, college_name: Optional[str], item_number: Optional[int] = None,
                             course_name: Optional[str] = None) -> List[dict]:
        """Médecine / Collège / {college_name} / (ITEM|ITEMS)"""
        if not self._ensure_enabled():
            return []
        if not college_name:
            return []

        med = self._find_folder_id("Médecine")
        col = self._find_folder_id("Collège", parent_id=med)
        tgt = self._find_folder_id(college_name, parent_id=col)

        items = self._find_folder_id("ITEM", parent_id=tgt) or self._find_folder_id("ITEMS", parent_id=tgt)
        if items:
            tgt = items
        if not tgt:
            return []

        files = self._get_files_in_folder(tgt)

        if item_number not in (None, ""):
            pattern = str(item_number).strip()
            files = [f for f in files if pattern in (f.get("name") or "")]

        if course_name:
            files = self._score_similarity(course_name, files)

        log.info("[DriveSync] Collège '%s' → %d PDF", college_name, len(files))
        return files[:10]

    def search_pdf_in_college(self, college_name: Optional[str], query: str) -> List[dict]:
        if not self._ensure_enabled():
            return []
        if not college_name:
            return []

        med = self._find_folder_id("Médecine")
        col = self._find_folder_id("Collège", parent_id=med)
        tgt = self._find_folder_id(college_name, parent_id=col)

        items = self._find_folder_id("ITEM", parent_id=tgt) or self._find_folder_id("ITEMS", parent_id=tgt)
        if items:
            tgt = items
        if not tgt:
            return []

        files = self._get_files_in_folder(tgt)
        ql = (query or "").lower()
        filtered = [f for f in files if ql in (f.get("name") or "").lower()]
        return self._score_similarity(query, filtered)

    # ---------------------- Semestre / UE
    def list_pdfs_by_semestre_ue(self, semestre_name: Optional[str], ue_name: Optional[str],
                                 course_name: Optional[str] = None) -> List[dict]:
        """Médecine / {semestre_name} / {ue_name}"""
        if not self._ensure_enabled():
            return []
        if not semestre_name or not ue_name:
            return []

        med = self._find_folder_id("Médecine")
        sem = self._find_folder_id(semestre_name, parent_id=med)
        ue = self._find_folder_id(ue_name, parent_id=sem)
        if not ue:
            return []

        files = self._get_files_in_folder(ue)
        if course_name:
            files = self._score_similarity(course_name, files)

        log.info("[DriveSync] %s/%s → %d PDF", semestre_name, ue_name, len(files))
        return files[:10]

    def search_pdf_in_semestre_ue(self, semestre_name: Optional[str], ue_name: Optional[str], query: str) -> List[dict]:
        if not self._ensure_enabled():
            return []
        if not semestre_name or not ue_name:
            return []

        med = self._find_folder_id("Médecine")
        sem = self._find_folder_id(semestre_name, parent_id=med)
        ue = self._find_folder_id(ue_name, parent_id=sem)
        if not ue:
            return []

        files = self._get_files_in_folder(ue)
        ql = (query or "").lower()
        filtered = [f for f in files if ql in (f.get("name") or "").lower()]
        return self._score_similarity(query, filtered)

    # ---------------------- Ids de dossiers (pour ouverture)
    def get_college_target_folder_id(self, college_name: Optional[str]) -> Optional[str]:
        if not self._ensure_enabled():
            return None
        if not college_name:
            return None
        med = self._find_folder_id("Médecine")
        col = self._find_folder_id("Collège", parent_id=med)
        tgt = self._find_folder_id(college_name, parent_id=col)
        items = self._find_folder_id("ITEM", parent_id=tgt) or self._find_folder_id("ITEMS", parent_id=tgt)
        return items or tgt

    def get_semestre_ue_folder_id(self, semestre_name: Optional[str], ue_name: Optional[str]) -> Optional[str]:
        if not self._ensure_enabled():
            return None
        if not semestre_name or not ue_name:
            return None
        med = self._find_folder_id("Médecine")
        sem = self._find_folder_id(semestre_name, parent_id=med)
        return self._find_folder_id(ue_name, parent_id=sem)

    @staticmethod
    def folder_web_url(folder_id: Optional[str]) -> Optional[str]:
        return f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else None
