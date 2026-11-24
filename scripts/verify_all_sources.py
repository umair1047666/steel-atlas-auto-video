#!/usr/bin/env python3
"""Full verification of every source + stage of the Steel Atlas pipeline.

Tests (live, against the real services):
  1. NASA image/video API (no key)
  2. Wikimedia Commons video search (no key)
  3. Openverse photos (no key) + download one photo
  4. Pexels / Pixabay — verified to SKIP cleanly without a key (live key test
     happens on GitHub Actions, where the secrets live)
  5. Subtitles — [HL] extraction, ASS + SRT generation from the sample job
  6. Music — build_music_bed() with the REAL Drive track (hook/middle/ending)
  7. Title card — 1920x1080 generation
  8. Relevance gate — generic blocklist + keyword overlap

Usage:  python scripts/verify_all_sources.py [--track /path/to/track.mp3]
Exit 0 = every check passed. Prints PASS/FAIL per check.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.clip_search import (  # noqa: E402
    is_generic, keyword_overlap, search_commons, search_nasa,
    search_openverse_photo, search_pexels, search_pixabay,
)
from pipeline.common import load_config  # noqa: E402
from pipeline.render import build_music_bed  # noqa: E402
from pipeline.stills import make_title_card  # noqa: E402
from pipeline.subtitles import (  # noqa: E402
    extract_highlights, time_highlights, write_ass, write_srt,
)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60)
    return float(out.stdout.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", default="/tmp/real_track.mp3")
    args = ap.parse_args()
    cfg = load_config()
    tmp = ROOT / "jobs" / "_verify"
    tmp.mkdir(parents=True, exist_ok=True)

    # ---- 1. NASA (no key) — space topics only; NASA has no bridge footage
    nasa = search_nasa(["apollo", "moon", "landing"], per_page=5)
    check("NASA search returns results", len(nasa) > 0, f"({len(nasa)} items)")
    if nasa:
        check("NASA item has title", bool(nasa[0].get("title")),
              f"-> {nasa[0].get('title', '')[:60]}")
    nasa2 = search_nasa(["rocket", "launch"], per_page=5)
    check("NASA search (rocket launch)", len(nasa2) > 0, f"({len(nasa2)} items)")
    # A non-space query SHOULD return nothing — that is correct behaviour.
    nasa3 = search_nasa(["millau", "viaduct"], per_page=5)
    check("NASA correctly returns 0 for a bridge query", len(nasa3) == 0,
          f"({len(nasa3)} items)")

    # ---- 2. Wikimedia Commons (no key) -------------------------------
    commons = search_commons(["millau", "viaduct"], per_page=5)
    check("Wikimedia Commons video search", len(commons) > 0,
          f"({len(commons)} items)")
    if commons:
        check("Commons item has title", bool(commons[0].get("title")),
              f"-> {commons[0].get('title', '')[:60]}")

    # ---- 3. Openverse photos (no key) + download ---------------------
    # (retry once — Openverse occasionally 504s on the first call)
    photos = search_openverse_photo(["millau", "viaduct"], per_page=5)
    if not photos:
        photos = search_openverse_photo(["millau", "viaduct"], per_page=5)
    check("Openverse photo search", len(photos) > 0, f"({len(photos)} items)")
    if photos:
        from pipeline.clip_search import _download
        url = photos[0].get("url")
        dest = tmp / "openverse-test.jpg"
        try:
            _download(url, dest)
            ok = dest.exists() and dest.stat().st_size > 10000
            check("Openverse photo downloads (valid file)",
                  ok, f"({dest.stat().st_size} bytes)")
        except Exception as e:  # noqa: BLE001
            check("Openverse photo downloads (valid file)", False, f"({e})")

    # ---- 4. Pexels / Pixabay skip cleanly without keys ---------------
    check("Pexels skips cleanly without key", search_pexels(["bridge"]) == [])
    check("Pixabay skips cleanly without key", search_pixabay(["bridge"]) == [])

    # ---- 5. Relevance gate -------------------------------------------
    check("Generic title blocked", is_generic("Abstract background loop video",
                                              "", cfg["clip_search"]["generic_blocklist"]))
    check("Topical title allowed", not is_generic("Millau Viaduct cable stayed bridge",
                                                  "", cfg["clip_search"]["generic_blocklist"]))
    check("Keyword overlap counts", keyword_overlap(
        ["millau", "viaduct"], "Millau Viaduct cable bridge") >= 1)

    # ---- 6. Subtitles from the sample job ----------------------------
    job = json.loads((ROOT / "queue" / "sample-job.json").read_text())
    highlights = extract_highlights(job["script"])
    check("Subtitles: [HL] highlight extracted", len(highlights) == 1,
          f"({highlights})")
    # Fake word timings in Edge TTS format (offset_ms / duration_ms).
    words = []
    t = 0.0
    for w in job["script"].replace("[HL]", "").replace("[/HL]", "").split():
        words.append({"word": w, "offset_ms": int(t * 1000),
                      "duration_ms": 330})
        t += 0.33
    words_path = tmp / "words.json"
    words_path.write_text(json.dumps(words))
    cues = time_highlights(job["script"], words_json=str(words_path),
                           total_duration_s=24.0)
    check("Subtitles: highlight timed", len(cues) == 1 and cues[0][1] >= 0,
          f"({cues})")
    ass_path = tmp / "test.ass"
    srt_path = tmp / "test.srt"
    write_ass(cues, ass_path, cfg)
    write_srt(cues, srt_path)
    ass_text = ass_path.read_text()
    check("Subtitles: ASS contains highlight text",
          "343 metres" in ass_text)
    check("Subtitles: SRT contains highlight text",
          "343 metres" in srt_path.read_text())

    # ---- 7. Music bed with the REAL track ----------------------------
    track = Path(args.track)
    if track.exists():
        check("Music: real track present", True,
              f"({track.name}, {ffprobe_duration(track):.1f}s)")
        for dur, label in [(60, "60s video"), (1200, "20-min video")]:
            bed = tmp / f"music-bed-{dur}.flac"
            build_music_bed(track, dur, cfg, bed)
            got = ffprobe_duration(bed)
            check(f"Music bed ({label}) duration ~{dur}s",
                  abs(got - dur) < 1.0, f"(got {got:.1f}s)")
    else:
        check("Music: real track present", False, "(track file missing)")

    # ---- 8. Title card ------------------------------------------------
    card = tmp / "title-card.png"
    make_title_card("How the Millau Viaduct Was Built", card, cfg)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(card)],
            capture_output=True, text=True, timeout=60)
        w, h = out.stdout.strip().split(",")
        check("Title card is 1920x1080", (int(w), int(h)) == (1920, 1080),
              f"({w}x{h})")
    except Exception as e:  # noqa: BLE001
        check("Title card is 1920x1080", False, f"({e})")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed.")
    if failed:
        print("FAILED:", ", ".join(r[0] for r in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
