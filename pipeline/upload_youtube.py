"""YouTube upload (videos.insert) — kept deliberately separate from the
Drive upload so the user can swap in his existing Cloudflare video-scheduler
instead (it already handles YouTube with per-slot OAuth). See README.

SAFETY: same dry-run rule as Drive — DRY-RUN by default; a real upload
happens only when live=True AND env UPLOAD_LIVE=1. The workflow runs
dry-run first, then live only after QC passed.

Uploads are chunked + resumable with retries ONLY for transient errors
(5xx / timeouts). A 4xx or quota error is terminal — it is logged and the
job stops; it is never retried in a loop.
"""
import os
import time
from pathlib import Path

from .common import log

TRANSIENT = (500, 502, 503, 504)


def is_live():
    return os.environ.get("UPLOAD_LIVE", "") == "1"


def upload_to_youtube(video_path, job, cfg, live=False):
    """Upload to YouTube. Returns (video_id, url). Dry-run by default."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise RuntimeError(f"file to upload does not exist: {video_path}")
    title = job.get("title", video_path.stem)[:100]
    if not live or not is_live():
        log.info("DRY-RUN: would upload to YouTube: title=%r (%d bytes)",
                 title, video_path.stat().st_size)
        return "DRY-RUN-NO-UPLOAD", "https://www.youtube.com/watch?v=DRY-RUN"

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    client_id = os.environ.get("YT_CLIENT_ID", "")
    client_secret = os.environ.get("YT_CLIENT_SECRET", "")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise RuntimeError(
            "YouTube upload needs YT_CLIENT_ID, YT_CLIENT_SECRET and "
            "YT_REFRESH_TOKEN (GitHub Secrets). See SECRETS.md.")
    creds = Credentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    youtube = build("youtube", "v3", credentials=creds)
    privacy = os.environ.get("YOUTUBE_PRIVACY") or cfg["upload"].get("youtube_privacy", "private")
    body = {
        "snippet": {
            "title": title,
            "description": job.get("description", "")[:5000],
            "tags": (job.get("tags") or [])[:30],
            "categoryId": cfg["upload"].get("youtube_category", "28"),
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    attempt = 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            status = e.resp.status if e.resp is not None else 0
            if status in TRANSIENT and attempt < 3:
                attempt += 1
                wait = 10 * attempt
                log.warning("YouTube transient HTTP %s — retry %s in %ss", status, attempt, wait)
                time.sleep(wait)
                continue
            # 4xx / quota / anything else is TERMINAL — never retried.
            raise RuntimeError(f"YouTube upload failed (HTTP {status}, terminal): {e}")
        except (TimeoutError, ConnectionError) as e:
            if attempt < 3:
                attempt += 1
                wait = 10 * attempt
                log.warning("YouTube network error — retry %s in %ss: %s", attempt, wait, e)
                time.sleep(wait)
                continue
            raise RuntimeError(f"YouTube upload failed after retries: {e}")
    video_id = response["id"]
    log.info("YouTube upload complete: %s -> https://www.youtube.com/watch?v=%s",
             video_path.name, video_id)
    return video_id, f"https://www.youtube.com/watch?v={video_id}"
