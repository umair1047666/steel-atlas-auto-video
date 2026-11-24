# Test report — Steel Atlas auto-video pipeline (pilot)

Date: 2026-11-24. Test job: `sa-test-001` ("How the Millau Viaduct Was Built",
3 scenes, ~12 s narration). Full log: `jobs/sa-test-001/state.json`.

## PASSED (verified on this machine)

- **Unit tests:** 10/10 pass (`pytest tests/ -q`) — config load, [HL] parsing,
  word-boundary + proportional subtitle timing, ASS/SRT generation, relevance
  gate, generic blocklist, video-file picker, sample-job schema, audio-only
  media probing.
- **Workflow YAML:** all 5 files parse as valid YAML.
- **Clip search:** Pexels/Pixabay correctly skipped without keys (warning,
  no crash). Wikimedia video search returned no topical match (rejected,
  not misused). Openverse photos found for scenes 2–3 (real Millau Viaduct
  photo, topical). Scene 1 fell through to the branded title card, exactly
  as the fallback chain specifies.
- **Photo → Ken Burns:** zoompan render worked (scenes 2–3 are moving
  1920×1080 clips).
- **Title card:** FFmpeg drawtext card rendered correctly (visually checked).
- **Voice (Piper fallback):** `en_US-ryan-medium` model downloaded from
  HuggingFace, 11.8 s narration generated, probed OK.
- **Render:** concat demuxer `-c copy` joined the intermediates; single
  final encode (libx264 CRF 18, preset medium, yuv420p, High, AAC 192k/48k,
  +faststart). Output: **1920×1080, 11.93 s, h264 + AAC, 3.1 MB**.
- **Music mix + ducking:** music at −20 dB under voice, sidechain ducking
  measured in isolation: **music drops 8.1 dB during voice bursts vs gaps**
  (threshold 0.03, ratio 8). Fade in/out + loudnorm −14 LUFS applied.
- **Highlight subtitles:** 1 cue (`Its tallest mast rises 343 metres`,
  2.78–4.58 s) burned in — **visually verified on the extracted frame**
  (yellow, black outline, bottom-centre). SRT sidecar also written.
- **Auto-QC:** passed — resolution 1920×1080, duration within ±5% of voice,
  audio present (mean −15.0 dB), no black segment over 1 s.
- **Upload dry-run:** Drive + YouTube both reported DRY-RUN, nothing uploaded.
- **No secrets in code:** grep scan clean; all credentials are env-var
  placeholders (GitHub Secrets).

## UNTESTED (do not treat as working)

- **Edge TTS (primary voice):** could NOT connect from this VM —
  `speech.platform.bing.com:443` fails TLS through the VM proxy
  (`SSL: WRONG_VERSION_NUMBER`). The library works by direct connection,
  which GitHub Actions runners have, but that is **unverified**. Piper is
  the proven fallback. First Actions run will prove Edge TTS.
- **Pexels / Pixabay search:** no API keys in this environment, so the
  primary clip sources were skipped. Code paths are written and unit-tested
  for the picker/gate logic, but a live search is unverified until the user
  adds `PEXELS_API_KEY` / `PIXABAY_API_KEY`.
- **Cloudflare FLUX.1 AI stills:** no API token here — skipped. Unverified
  until `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` are set.
- **Real Drive / YouTube upload:** dry-run only. Live upload needs the
  OAuth secrets + an explicit `--live` + `UPLOAD_LIVE=1`, and has never run.
- **GitHub Actions runs:** YAML parses, but no workflow has ever executed
  on GitHub (repo not yet created — no auth on this machine, see below).
- **20-minute video:** only a ~12 s test rendered. A full 20-minute job
  exercises the same code paths, but render time (~30–75 min) and the
  14 GB disk limit are unverified at that scale.
- **Wikimedia video download:** search worked; no topical video matched the
  test keywords, so the download path is untested.

## Bugs found and fixed during testing

1. `media_info()` returned None for audio-only files (voice) — fixed, with a
   regression test.
2. Edge TTS left a 0-byte `voice.mp3` on failure — now cleaned up.
3. Frame extraction used MJPEG (rejected by FFmpeg 8 on limited-range YUV)
   — switched to PNG.
4. Design flaw caught before testing: separate workflow runs lose files
   (fresh runners). Fixed with the Drive job store (`pipeline/jobstore.py`).
