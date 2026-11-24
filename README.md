# Steel Atlas — Automated Video Pipeline

A free, fully automatic video pipeline for the **Steel Atlas** YouTube channel
(engineering & mega-projects documentaries).

**What it does:** scripts arrive as JSON files in `queue/`. GitHub Actions picks
them up, searches real stock footage (Pexels / Pixabay / NASA / Wikimedia),
generates narration, renders a 1080p video with ducked background music and
highlight subtitles, runs an automatic quality check, then uploads the finished
video to Google Drive and YouTube. No human touches anything after the script
is queued.

**Cost:** $0. Public repo = unlimited free GitHub Actions minutes. Pexels /
Pixabay / NASA / Wikimedia are free. Cloudflare Workers AI (FLUX.1 schnell)
has a free daily allowance. Edge TTS is free. No paid service anywhere in
this pipeline.

## How it works

One workflow per function, so one failure never stops the others. Workflows
run serially (one `concurrency` group) and hand the job to the next stage
through `jobs/<job-id>/state.json`:

| # | Workflow | Does |
|---|----------|------|
| 1 | `01-clip-search.yml` | Claims the next queued job, searches stock footage per scene (relevance-gated, Full HD only), downloads clips, falls back to photo+zoom / AI still / title card |
| 2 | `02-voice.yml` | Uses a provided voice file if present, else Edge TTS (Piper fallback) |
| 3 | `03-render-qc.yml` | Normalises clips, renders 1080p, mixes ducked music, burns highlight subtitles, runs auto-QC |
| 4 | `04-upload.yml` | Uploads to Google Drive (user OAuth) then YouTube |
| 5 | `05-keepalive.yml` | Commits a heartbeat so scheduled workflows never auto-disable after 60 days of no activity |

## Rules the pipeline enforces

- **Never a generic clip.** Every clip must match the scene keywords
  (relevance-gated search). If no clip matches, the scene falls back to:
  real photo + zoom → AI still (Cloudflare FLUX.1) → title card.
- **Full HD or nothing.** Clips must be 1920×1080, or 720p upscaled with
  lanczos. Anything below 720p is rejected.
- **One encode generation for clips.** Clips are normalised once to identical
  1920×1080/30fps intermediates, then joined with the concat demuxer (`-c copy`).
  The only re-encode is the final delivery pass (CRF 18).
- **Music is always under the voice.** Music sits at −20 dB and ducks ~17 dB
  whenever narration speaks. Final mix loudnormed to −14 LUFS.
- **Subtitles are highlights only (~5%).** Only phrases marked `[HL]...[/HL]`
  in the script get subtitles — about 60 seconds per 20-minute video.
- **No secrets in code, ever.** Every key/token lives in GitHub Secrets.
  See [SECRETS.md](SECRETS.md).

## Repo layout

```
config.yaml            # per-channel config (copy this file to clone for another channel)
queue/                 # script JSONs land here (one file per video)
jobs/                  # per-job working state (created by the pipeline)
music/                 # royalty-free music track(s) — see music/README.md
pipeline/              # Python pipeline (clip search, voice, render, QC, upload)
tests/                 # unit tests (no network needed)
.github/workflows/     # the 4 stage workflows + keepalive
```

## Cloning for another channel

This repo is the **template** for all 5 channels. To clone it:

1. Copy the repo, rename it (e.g. `umbral-hour-auto-video`).
2. Edit only `config.yaml` (channel name, voice, music track, upload targets).
3. Add that channel's secrets (same secret names, different values).
4. Done — the code never changes.

## Local test

```bash
pip install -r requirements.txt
python -m pytest tests/ -q            # unit tests (no network)
bash scripts/test_e2e.sh              # 45-second end-to-end render + QC
```

## Status

Pilot channel: Steel Atlas. See `queue/sample-job.json` for the job format.
