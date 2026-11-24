"""Clip search: Pexels (primary) -> Pixabay (backup) -> NASA / Wikimedia (no key).

Rules enforced here:
- Full HD preferred (1920x1080). 720p is accepted and upscaled later.
  Anything below 720p is REJECTED.
- Relevance gate: a candidate must share at least one keyword token with the
  scene (title/tags/description overlap for sources that expose metadata;
  Pexels is gated by the search query itself). A clip that does not match
  the scene is NEVER used — the scene falls back to photo -> AI still ->
  title card instead (see stills.py).
- Generic filler (blocklist in config.yaml) is never used.
"""
import re
import time
from pathlib import Path

import requests

from .common import ROOT, load_config, log, media_info

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
NASA_SEARCH = "https://images-api.nasa.gov/search"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

STOPWORDS = {
    "the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with",
    "by", "from", "is", "are", "was", "were", "it", "its", "this", "that",
}


def tokens(text):
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if t not in STOPWORDS and len(t) > 2
    }


def keyword_overlap(keywords, *texts):
    """Number of scene keyword tokens found in the candidate metadata."""
    wanted = set()
    for kw in keywords:
        wanted |= tokens(kw)
    hay = set()
    for text in texts:
        hay |= tokens(text)
    return len(wanted & hay)


def is_generic(title, tags, blocklist):
    blob = f"{title} {' '.join(tags or [])}".lower()
    return any(b in blob for b in blocklist)


def pick_video_file(files, min_height):
    """Pick the best downloadable file: 1080p first, then 720p. None if <720p."""
    cands = [f for f in files if f.get("width") and f.get("height")]
    cands.sort(key=lambda f: (f["height"], f["width"]), reverse=True)
    for f in cands:
        if f["height"] >= 1080 and f["width"] >= 1920:
            return f
    for f in cands:
        if f["height"] >= 720:
            return f
    return None


def _download(url, dest, timeout=120):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "steel-atlas-auto-video/1.0 (contact: via GitHub)"}
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


# ---------------------------------------------------------------- Pexels
def search_pexels(keywords, per_page=5):
    import os

    key = os.environ.get("PEXELS_API_KEY", "")
    if not key:
        log.warning("PEXELS_API_KEY not set — skipping Pexels")
        return []
    query = " ".join(keywords[:3])
    try:
        r = requests.get(
            PEXELS_SEARCH,
            params={"query": query, "per_page": per_page,
                    "orientation": "landscape", "size": "large"},
            headers={"Authorization": key},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Pexels search failed: %s", e)
        return []
    return r.json().get("videos", [])


# ---------------------------------------------------------------- Pixabay
def search_pixabay(keywords, per_page=5):
    import os

    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        log.warning("PIXABAY_API_KEY not set — skipping Pixabay")
        return []
    query = " ".join(keywords[:3])
    try:
        r = requests.get(
            PIXABAY_SEARCH,
            params={"key": key, "q": query, "per_page": per_page,
                    "video_type": "film", "min_width": 1280, "min_height": 720},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Pixabay search failed: %s", e)
        return []
    return r.json().get("hits", [])


# ---------------------------------------------------------------- NASA (no key)
def nasa_video_url(item):
    """Fetch the NASA asset manifest and return a direct mp4 URL, or None.

    The NASA search API does NOT return a direct video file — it returns an
    `href` (asset manifest URL). The manifest is a JSON list of file URLs;
    pick an mp4 from it.
    """
    href = item.get("href", "")
    if not href:
        return None
    try:
        r = requests.get(href, timeout=30)
        r.raise_for_status()
        files = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("NASA manifest fetch failed: %s", e)
        return None
    if not isinstance(files, list):
        return None
    mp4s = [f for f in files if isinstance(f, str) and f.lower().endswith(".mp4")]
    if not mp4s:
        return None
    # Prefer a large/original mp4 when the name says so, else the first.
    for f in mp4s:
        if "orig" in f.lower() or "1280" in f or "1920" in f:
            return f
    return mp4s[0]


def search_nasa(keywords, per_page=5):
    query = " ".join(keywords[:3])
    try:
        r = requests.get(NASA_SEARCH, params={"q": query, "media_type": "video",
                                              "page_size": per_page}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("NASA search failed: %s", e)
        return []
    items = r.json().get("collection", {}).get("items", [])
    out = []
    for item in items:
        data = (item.get("data") or [{}])[0]
        out.append({"title": data.get("title", ""),
                    "description": data.get("description", ""),
                    "nasa_id": data.get("nasa_id", ""),
                    "href": item.get("href", "")})
    return out


# ---------------------------------------------------------------- Wikimedia (no key)
def search_commons(keywords, per_page=5):
    query = " ".join(keywords[:3]) + " filetype:video"
    try:
        r = requests.get(COMMONS_API, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": query, "srnamespace": 6, "srlimit": per_page,
        }, timeout=30, headers={"User-Agent": "steel-atlas-auto-video/1.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Wikimedia search failed: %s", e)
        return []
    return r.json().get("query", {}).get("search", [])


# ---------------------------------------------------------------- Openverse photos (no key)
def search_openverse_photo(keywords, per_page=5):
    """Free photos (CC0 / CC-BY) for the photo+zoom fallback. No key needed."""
    query = " ".join(keywords[:3])
    try:
        r = requests.get("https://api.openverse.org/v1/images/", params={
            "q": query, "license_type": "commercial", "page_size": per_page,
        }, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Openverse search failed: %s", e)
        return []
    return r.json().get("results", [])


def find_photo_for_scene(scene, cfg, dest_dir):
    """Return (path, source) for a topical photo, or None."""
    keywords = scene.get("keywords", [])
    min_overlap = cfg["clip_search"].get("min_keyword_overlap", 1)
    scene_id = scene.get("id", 0)
    for hit in search_openverse_photo(keywords, cfg["clip_search"]["per_scene_results"]):
        title = hit.get("title", "")
        tags = " ".join(t.get("name", "") for t in hit.get("tags", []) or [])
        if keyword_overlap(keywords, title, tags) < min_overlap:
            continue
        url = hit.get("foreign_landing_url") and hit.get("url")
        if not url:
            continue
        dest = dest_dir / f"scene-{scene_id:03d}-photo{Path(url).suffix or '.jpg'}"
        try:
            _download(url, dest)
        except requests.RequestException as e:
            log.warning("Openverse download failed: %s", e)
            continue
        if dest.stat().st_size > 5000:
            log.info("scene %s: Openverse photo %s", scene_id, dest.name)
            return str(dest), "openverse"
        dest.unlink(missing_ok=True)
    return None


def commons_file_url(title):
    """Resolve a Commons File: title to its direct media URL."""
    r = requests.get(COMMONS_API, params={
        "action": "query", "format": "json", "titles": title,
        "prop": "imageinfo", "iiprop": "url|size|mime",
    }, timeout=30, headers={"User-Agent": "steel-atlas-auto-video/1.0"})
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        return info.get("url"), info.get("width"), info.get("height"), info.get("mime")
    return None, None, None, None


# ---------------------------------------------------------------- per-scene
def find_clip_for_scene(scene, cfg, dest_dir, tries=3):
    """Return (path, source, kind) or None. kind is 'clip' | 'photo' | 'still' | 'card'.

    Tries, in order: Pexels -> Pixabay -> NASA -> Wikimedia video.
    Photo / AI-still / title-card fallbacks are handled by stills.py, not here.
    """
    keywords = scene.get("keywords", [])
    blocklist = cfg["clip_search"].get("generic_blocklist", [])
    min_overlap = cfg["clip_search"].get("min_keyword_overlap", 1)
    min_h = cfg["video"]["min_clip_height"]
    dest_dir = Path(dest_dir)
    scene_id = scene.get("id", 0)

    # --- Pexels (gated by the query itself) ---
    for video in search_pexels(keywords, cfg["clip_search"]["per_scene_results"]):
        title = video.get("url", "").split("/")[-1].replace("-", " ")
        if is_generic(title, [], blocklist):
            continue
        picked = pick_video_file(video.get("video_files", []), min_h)
        if not picked:
            continue
        dur = video.get("duration") or 0
        if dur < cfg["clip_search"]["min_clip_s"]:
            continue
        dest = dest_dir / f"scene-{scene_id:03d}-pexels.mp4"
        try:
            _download(picked["link"], dest)
        except requests.RequestException as e:
            log.warning("Pexels download failed: %s", e)
            continue
        info = media_info(dest)
        if info and info[1] >= min_h:
            log.info("scene %s: Pexels clip %s (%sx%s)", scene_id, dest.name, info[0], info[1])
            return str(dest), "pexels", "clip"
        dest.unlink(missing_ok=True)

    # --- Pixabay (gated by tags) ---
    for hit in search_pixabay(keywords, cfg["clip_search"]["per_scene_results"]):
        tags = hit.get("tags", "")
        if is_generic("", [tags], blocklist):
            continue
        if keyword_overlap(keywords, tags) < min_overlap:
            log.info("scene %s: Pixabay hit rejected (no keyword overlap): %s", scene_id, tags[:80])
            continue
        videos = hit.get("videos", {})
        # prefer large, then medium
        for size in ("large", "medium", "small", "tiny"):
            v = videos.get(size)
            if not v:
                continue
            if v.get("height", 0) < min_h:
                continue
            dest = dest_dir / f"scene-{scene_id:03d}-pixabay.mp4"
            try:
                _download(v["url"], dest)
            except requests.RequestException as e:
                log.warning("Pixabay download failed: %s", e)
                continue
            info = media_info(dest)
            if info and info[1] >= min_h:
                log.info("scene %s: Pixabay clip %s", scene_id, dest.name)
                return str(dest), "pixabay", "clip"
            dest.unlink(missing_ok=True)

    # --- NASA (gated by title/description) ---
    for item in search_nasa(keywords, cfg["clip_search"]["per_scene_results"]):
        title = item.get("title", "")
        desc = item.get("description", "")
        if is_generic(title, [], blocklist):
            continue
        if keyword_overlap(keywords, title, desc) < min_overlap:
            continue
        url = nasa_video_url(item)
        if not url:
            continue
        dest = dest_dir / f"scene-{scene_id:03d}-nasa.mp4"
        try:
            _download(url, dest)
        except requests.RequestException as e:
            log.warning("NASA download failed: %s", e)
            continue
        info = media_info(dest)
        if info and info[1] >= min_h:
            log.info("scene %s: NASA clip %s", scene_id, dest.name)
            return str(dest), "nasa", "clip"
        dest.unlink(missing_ok=True)

    # --- Wikimedia (gated by title) ---
    for hit in search_commons(keywords, cfg["clip_search"]["per_scene_results"]):
        title = hit.get("title", "")
        if is_generic(title, [], blocklist):
            continue
        if keyword_overlap(keywords, title) < min_overlap:
            continue
        url, w, h, mime = commons_file_url(title)
        if not url or not mime or not mime.startswith("video"):
            continue
        if (h or 0) < min_h:
            log.info("scene %s: Commons file below 720p, skipping: %s", scene_id, title)
            continue
        dest = dest_dir / f"scene-{scene_id:03d}-commons{Path(url).suffix or '.webm'}"
        try:
            _download(url, dest)
        except requests.RequestException as e:
            log.warning("Commons download failed: %s", e)
            continue
        info = media_info(dest)
        if info and info[1] >= min_h:
            log.info("scene %s: Wikimedia clip %s", scene_id, dest.name)
            return str(dest), "wikimedia", "clip"
        dest.unlink(missing_ok=True)

    log.info("scene %s: no matching real clip found — fallback chain applies", scene_id)
    return None
