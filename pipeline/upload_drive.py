"""Google Drive upload — USER OAuth only.

A service account has its own 15 GB quota and does NOT inherit the user's
5 TB Drive. This module therefore uses the user's own OAuth credentials
(client id/secret + refresh token), exactly like the existing video-scheduler.

SAFETY: uploads are DRY-RUN by default. A real upload happens only when the
caller passes live=True AND the env var UPLOAD_LIVE=1 is set. The GitHub
workflow 04-upload.yml runs dry-run first, then live only after QC passed.
"""
import os
from pathlib import Path

from .common import log

DRY_RUN_ENV = "UPLOAD_LIVE"


def is_live():
    return os.environ.get(DRY_RUN_ENV, "") == "1"


def upload_to_drive(local_path, folder_id, cfg, live=False):
    """Upload a finished video to Drive. Returns (file_id, web_link).

    In dry-run mode (default) nothing is uploaded; returns a fake marker so
    the pipeline can be tested end-to-end without touching Drive.
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise RuntimeError(f"file to upload does not exist: {local_path}")
    if not live or not is_live():
        log.info("DRY-RUN: would upload %s (%d bytes) to Drive folder %s",
                 local_path, local_path.stat().st_size, folder_id)
        return "DRY-RUN-NO-UPLOAD", "https://drive.google.com/drive/folders/DRY-RUN"

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    client_id = os.environ.get("DRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("DRIVE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("DRIVE_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "Drive upload needs DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET and "
            "DRIVE_REFRESH_TOKEN (GitHub Secrets). See SECRETS.md.")
    creds = Credentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
        # No scopes forced: see jobstore.py — forcing drive.file on
        # refresh fails with invalid_scope when the grant used a
        # different Drive scope. The refresh uses the granted scopes.
    )
    service = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(str(local_path), mimetype="video/mp4", resumable=True)
    body = {"name": local_path.name, "parents": [folder_id] if folder_id else []}
    request = service.files().create(body=body, media_body=media, fields="id,webViewLink")
    response = None
    while response is None:
        _, response = request.next_chunk()
    file_id = response["id"]
    link = response.get("webViewLink", "")
    log.info("Drive upload complete: %s -> %s", local_path.name, link)
    return file_id, link
