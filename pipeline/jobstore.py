"""Job store: Google Drive holds the job's media between workflow runs.

Why this exists: each GitHub Actions workflow runs on a FRESH runner. Files
downloaded in stage 1 (clips, intermediates) do NOT survive to stage 2.
The repo (git) carries only small files (job.json, state.json). All media
(normalised clips, voice, final video) is synced to a per-job folder on the
user's Drive between stages, and restored at the start of the next stage.

Same user-OAuth credentials as upload_drive.py (NOT a service account).
Sync is DRY-RUN safe: in dry-run mode nothing is uploaded or downloaded;
the local job dir is used as-is (which is exactly what the local test does).
"""
import os
import re
from pathlib import Path

from .common import ROOT, job_dir, log

# Files that travel between stages. Raw source downloads (scene-001-pexels.mp4
# etc.) are NOT synced — they can be re-downloaded, and they would double
# the transfer. Only normalised intermediates (scene-001.mp4) travel.
KEEP_PATTERNS = [
    "job.json",
    "state.json",
    "voice.mp3",
    "voice.wav",
    "voice-words.json",
    "highlights.ass",
    "highlights.srt",
    "concat.mp4",
    "final.mp4",
]
KEEP_CLIP_RE = re.compile(r"^clips/scene-\d+\.mp4$")


def _is_live():
    return os.environ.get("UPLOAD_LIVE", "") == "1" or os.environ.get("JOBSTORE_LIVE", "") == "1"


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id = os.environ.get("DRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("DRIVE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("DRIVE_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "job store needs DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET and "
            "DRIVE_REFRESH_TOKEN (GitHub Secrets). See SECRETS.md.")
    creds = Credentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
        # No scopes forced here: the refresh token was granted its Drive
        # scope in OAuth Playground, and forcing drive.file on refresh
        # fails with invalid_scope when the grant used a different
        # Drive scope (e.g. the full drive scope). The refresh returns
        # an access token with the originally granted scopes.
    )
    return build("drive", "v3", credentials=creds)


def _find_folder(service, name, parent_id):
    q = (f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and trashed = false")
    if parent_id:
        q += f" and '{parent_id}' in parents"
    resp = service.files().list(q=q, fields="files(id,name)", pageSize=5).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def _ensure_folder(service, name, parent_id):
    folder_id = _find_folder(service, name, parent_id)
    if folder_id:
        return folder_id
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    created = service.files().create(body=body, fields="id").execute()
    return created["id"]


def _job_files(jdir):
    """(relative_path, absolute_path) for every file that must travel."""
    out = []
    jdir = Path(jdir)
    for name in KEEP_PATTERNS:
        p = jdir / name
        if p.exists():
            out.append((name, p))
    clips = jdir / "clips"
    if clips.exists():
        for p in sorted(clips.iterdir()):
            rel = f"clips/{p.name}"
            if KEEP_CLIP_RE.match(rel) and p.exists():
                out.append((rel, p))
    return out


def push_job(job_id):
    """Upload the job's keep-list to Drive folder pipeline-jobs/<job_id>/."""
    jdir = job_dir(job_id)
    files = _job_files(jdir)
    if not files:
        log.warning("job %s has no files to push", job_id)
        return
    if not _is_live():
        log.info("DRY-RUN: would push %d files of job %s to Drive job store", len(files), job_id)
        return
    from googleapiclient.http import MediaFileUpload

    service = _service()
    root_id = os.environ.get("DRIVE_JOBS_FOLDER_ID") or os.environ.get("DRIVE_FOLDER_ID", "")
    jobs_root = _ensure_folder(service, "pipeline-jobs", root_id or None)
    job_folder = _ensure_folder(service, job_id, jobs_root)
    for rel, path in files:
        # Replace an existing file of the same name (update, not duplicate).
        existing = service.files().list(
            q=f"name = '{path.name}' and '{job_folder}' in parents and trashed = false",
            fields="files(id)", pageSize=2).execute().get("files", [])
        media = MediaFileUpload(str(path), resumable=True)
        if existing:
            service.files().update(fileId=existing[0]["id"], media_body=media).execute()
        else:
            service.files().create(
                body={"name": path.name, "parents": [job_folder]},
                media_body=media, fields="id").execute()
        log.info("job store: pushed %s", rel)
    log.info("job %s pushed to Drive job store (%d files)", job_id, len(files))


def pull_job(job_id):
    """Download the job's keep-list from Drive into the local job dir."""
    jdir = job_dir(job_id)
    if not _is_live():
        log.info("DRY-RUN: would pull job %s from Drive job store (using local files)", job_id)
        return
    from googleapiclient.http import MediaIoBaseDownload
    import io

    service = _service()
    root_id = os.environ.get("DRIVE_JOBS_FOLDER_ID") or os.environ.get("DRIVE_FOLDER_ID", "")
    jobs_root = _find_folder(service, "pipeline-jobs", root_id or None)
    if not jobs_root:
        raise RuntimeError(f"no pipeline-jobs folder on Drive — job {job_id} was never pushed")
    job_folder = _find_folder(service, job_id, jobs_root)
    if not job_folder:
        raise RuntimeError(f"job {job_id} not found in Drive job store")
    resp = service.files().list(
        q=f"'{job_folder}' in parents and trashed = false",
        fields="files(id,name)", pageSize=100).execute()
    for f in resp.get("files", []):
        name = f["name"]
        if name in KEEP_PATTERNS:
            dest = jdir / name
        elif re.match(r"^scene-\d+\.mp4$", name):
            dest = jdir / "clips" / name
        else:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = service.files().get_media(fileId=f["id"])
        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        log.info("job store: pulled %s", name)
    log.info("job %s pulled from Drive job store", job_id)


def fetch_music(dest_path):
    """Download the royalty-free music track from Drive.

    The track is NOT committed to git (licensing + size). The user uploads
    track.mp3 to his Drive once (web) and sets secret DRIVE_MUSIC_FILE_ID
    to that file's ID. Returns the path, or None if the secret is unset.
    """
    import io
    from googleapiclient.http import MediaIoBaseDownload

    file_id = os.environ.get("DRIVE_MUSIC_FILE_ID", "")
    if not file_id:
        return None
    service = _service()
    request = service.files().get_media(fileId=file_id)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    log.info("music track fetched from Drive: %s (%d bytes)",
             dest_path, dest_path.stat().st_size)
    return str(dest_path)
