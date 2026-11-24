"""Fallback visuals: AI stills (Cloudflare Workers AI FLUX.1 schnell),
title cards, and Ken Burns photo motion.

Fallback chain per scene (enforced by run_job.py):
  real clip -> real photo + zoom -> AI still -> title card.
A scene NEVER gets a generic/unrelated clip.
"""
import base64
import os
from pathlib import Path

import requests

from .common import ROOT, log, run


def generate_ai_still(prompt, dest, cfg):
    """Cloudflare Workers AI FLUX.1 schnell. Free tier: 10,000 Neurons/day.

    Secrets: CLOUDFLARE_API_TOKEN (Workers AI: Read), CLOUDFLARE_ACCOUNT_ID.
    Returns the saved path, or None on failure (caller falls back to a card).
    """
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account:
        log.warning("Cloudflare secrets not set — skipping AI still")
        return None
    model = cfg["stills"]["cloudflare_model"]
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/ai/run/{model}")
    body = {
        "prompt": prompt,
        "num_steps": cfg["stills"].get("steps", 4),
    }
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=body, timeout=120,
        )
    except requests.RequestException as e:
        log.warning("Cloudflare AI request failed: %s", e)
        return None
    if r.status_code != 200:
        log.warning("Cloudflare AI HTTP %s: %s", r.status_code, r.text[:300])
        return None
    ctype = r.headers.get("content-type", "")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ctype.startswith("image/"):
        dest.write_bytes(r.content)
    else:
        try:
            data = r.json()
        except ValueError:
            log.warning("Cloudflare AI returned non-image, non-JSON")
            return None
        img = (data.get("result") or {}).get("image")
        if not img:
            log.warning("Cloudflare AI response has no image: %s", str(data)[:300])
            return None
        dest.write_bytes(base64.b64decode(img))
    log.info("AI still saved: %s (%d bytes)", dest, dest.stat().st_size)
    return str(dest)


def make_title_card(text, dest, cfg, subtext="STEEL ATLAS"):
    """1920x1080 branded title card (dark steel background, white text).

    Single PNG frame. (Earlier version asked ffmpeg for 150 H.264 frames
    into a .png — the image2 muxer rejects that: 'Cannot write more than
    one file with the same name'.)
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    safe = text.replace("'", "").replace(":", " - ")[:80]
    safe_sub = subtext.replace("'", "")[:40]
    run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x1a2733:s={w}x{h}:d=1",
        "-vf",
        f"drawtext=text='{safe}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        f":fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2-40,"
        f"drawtext=text='{safe_sub}':fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        f":fontsize=36:fontcolor=0x9fb4c7:x=(w-text_w)/2:y=(h-text_h)/2+60",
        "-frames:v", "1",
        str(dest),
    ])
    return str(dest)


def photo_candidates(keywords):
    """Free photo sources that need no key: Openverse + Wikimedia images."""
    return keywords
