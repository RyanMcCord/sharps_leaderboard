"""ImageQueue adapter: a Google Drive folder, read with a service account.

Setup: create a service account in Google Cloud with the Drive API enabled, save its JSON
key as the GOOGLE_SERVICE_ACCOUNT_JSON secret, and share the Drive folder with the service
account's email address (Viewer is enough). The bot only ever reads the folder.
"""
import json
import re

from xbots import config

from ..domain.queue import QueueFile
from ..ports import ImageQueue

API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class DriveError(RuntimeError):
    pass


def service_account_session(info_json=None):
    """An HTTP session signed in as the service account, read-only."""
    raw = info_json or config.GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw:
        raise DriveError("GOOGLE_SERVICE_ACCOUNT_JSON is not set. Add the service account's JSON key as a "
                         "GitHub secret (or to .env when running locally).")
    try:
        info = json.loads(raw)
    except ValueError as e:
        raise DriveError("GOOGLE_SERVICE_ACCOUNT_JSON isn't valid JSON. Paste the whole key file.") from e
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
    return AuthorizedSession(creds), info.get("client_email", "the service account")


class DriveImageQueue(ImageQueue):
    def __init__(self, folder_id, session=None, account_email="the service account"):
        folder_id = (folder_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise DriveError("Set AI_ART_DRIVE_FOLDER_ID to the folder's id: the last part of its URL, "
                             "after drive.google.com/drive/folders/.")
        self.folder_id = folder_id
        self._session, self.account_email = session, account_email

    @property
    def session(self):
        if self._session is None:
            self._session, self.account_email = service_account_session()
        return self._session

    def _check(self, r, what):
        if r.status_code == 404:
            raise DriveError(f"Drive says the {what} wasn't found. Share the folder with {self.account_email} "
                             f"and check AI_ART_DRIVE_FOLDER_ID.")
        if r.status_code in (401, 403):
            raise DriveError(f"Drive refused access to the {what} ({r.status_code}). Share the folder with "
                             f"{self.account_email}, and enable the Drive API in its Google Cloud project.")
        if not r.ok:
            raise DriveError(f"{r.status_code} from Google Drive: {r.text[:300]}")

    def list_files(self):
        params = {
            "q": f"'{self.folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, createdTime, size)",
            "orderBy": "createdTime",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        files = []
        while True:
            r = self.session.get(f"{API}/files", params=params, timeout=30)
            self._check(r, "folder")
            body = r.json()
            for f in body.get("files", []):
                files.append(QueueFile(f["id"], f["name"], f.get("mimeType", ""), f.get("createdTime", ""),
                                       int(f["size"]) if f.get("size") else None))
            if not body.get("nextPageToken"):
                return files
            params["pageToken"] = body["nextPageToken"]

    def download(self, file_id):
        r = self.session.get(f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                             timeout=120)
        self._check(r, "file")
        return r.content
