# Steel Atlas Auto-Video — Handoff to the Steel Atlas Muse Account

You are the Muse account for the **Steel Atlas** YouTube channel. This document
gives you everything you need to run the automatic video pipeline. The pipeline
is already built, tested, and working. You do NOT need to build it again.

## 1. The repo (already live)

- **Repo:** https://github.com/umair1047666/steel-atlas-auto-video (PUBLIC)
- **GitHub account:** umair1047666 (the user's single GitHub account — do NOT
  create a second GitHub account or a second repo).
- **Branch:** main

## 2. GitHub access (you must do this first)

Connect GitHub in THIS Muse account as **umair1047666** (the same GitHub
account the main Muse uses). The repo is public, but pushing job files and
dispatching workflows needs write access. If the GitHub connector in this
account is not connected, connect it and sign in as umair1047666.

- The "Meta Muse AI" GitHub App is already installed on umair1047666 with
  All-repositories access. Do NOT reinstall it.
- You CANNOT use the Contents API to push `.github/workflows/*.yml` files
  (403 — the App lacks workflows permission). Workflow files are already in
  place; do not touch them. For all other files, the Contents API
  (`create_or_update_file`) works.
- There is NO workflow-dispatch or workflow-run-status tool in the GitHub
  connector. Dispatching a workflow and checking its result MUST go through
  the browser (GitHub web UI, signed in as umair1047666).

## 3. Secrets — already set, do NOT re-enter them

All 9 secrets are already set in the repo (Drive-only uploads; NO YouTube
secrets — the user reviews finished videos on Drive first):
PEXELS_API_KEY, PIXABAY_API_KEY, CLOUDFLARE_API_TOKEN,
CLOUDFLARE_ACCOUNT_ID, DRIVE_REFRESH_TOKEN, DRIVE_CLIENT_ID,
DRIVE_CLIENT_SECRET, DRIVE_FOLDER_ID, DRIVE_MUSIC_FILE_ID.
GitHub secrets are write-only — you cannot read or copy them. Do NOT ask the
user for them again. Do NOT put any secret value in chat, files, or memory.

## 4. Fixed channel settings (already in config.yaml — do NOT change without the user)

- **Voice (Edge TTS fallback):** `en-US-ChristopherNeural` (final, user-chosen).
- **Music:** the_calculated_ascent.mp3 (Drive file 1pH4BGTac5xu950hcWGNcknzCriLSB_4f,
  2:47). LONGS: music **10% (-20 dB)**, ducked to ~4% (-28 dB) under voice.
  (Was 2% until 2026-11-24 — user said 2% was inaudible on phone speakers.)
  3-min track rule: first 30 s = hook, last 30 s = ending, middle looped.
- **Video:** 1920x1080, 30 fps, CRF 18, loudnorm -14 LUFS.
- **Voice: Edge TTS ONLY (user decision 2026-11-24).** Do NOT provide your own
  voice file. The pipeline generates the narration voice automatically with
  Edge TTS `en-US-ChristopherNeural` from the script text. Never place a
  `voice.mp3` in the job folder — a provided voice file always wins over
  Edge TTS, so providing one would override the TTS voice. (This supersedes
  the earlier plan where the channel account supplied its own voice file.)
- **Scenes:** YOU set each scene's `duration_s` in the job (5-10 s). One visual
  per scene, lasting exactly the scene duration. Visual pacing (3-5 s) = you
  write shorter scenes. No code animation/diagrams. Visual mix TARGET is
  60% motion / 40% stills (NOT enforced in config). Per scene the pipeline
  tries: real clip (Pexels/Pixabay/NASA/Wikimedia, relevance-gated, >=720p
  only) → real photo + zoom → AI still (Cloudflare FLUX.1) + zoom → title
  card. So the actual ratio depends on what clips are found. There is NO AI
  video generator — "AI motion" means AI stills with zoom/pan, not real AI video.

## 5. How to make a video (your job, every video)

1. Write the script (narration text) and the scenes. Mark highlight phrases
   for on-screen subtitles with `[HL]...[/HL]` (~5% of the narration).
2. Create the job: copy `queue/sample-job.json` as the format. A job has
   `job_id` (e.g. `sa-0002`), `title`, `script`, and `scenes` (each scene:
   `narration`, `search_terms`, `duration_s`). Put the job file at
   `jobs/<job_id>/job.json` in the repo (Contents API) AND sync it to the
   Drive job store folder `pipeline-jobs/<job_id>/`.
3. Make the voice: DO NOT make or provide a voice file. The pipeline
   generates the narration voice automatically with Edge TTS
   (ChristopherNeural) from your script. Never put a `voice.mp3` in the job
   folder — a provided voice file always wins and would override the TTS voice.
4. Dispatch the workflows IN ORDER, one at a time, waiting for each to finish
   before dispatching the next (dispatching all at once makes GitHub cancel
   the earlier ones — same concurrency group):
   - `01 - Clip Search` → finds/normalises clips, syncs to Drive
   - `02 - Voice` → generates voice (or uses your provided voice.mp3)
   - `03 - Render & QC` → builds final.mp4, QC must pass
   - `04 - Upload` → DRY-RUN by default (nothing goes live). Live upload needs
     manual dispatch + repo variable UPLOAD_LIVE=1. Keep it DRY-RUN unless the
     user says live.
   Dispatch + status checks go through the browser (GitHub web UI).
5. Check the QC line in the run log. It must say `QC passed` AND
   `audio_duration: OK: audio stream <N>s` (the audio STREAM duration must
   match the voice length — the container duration alone does not prove it).
6. The finished video lands in Drive: `pipeline-jobs/<job_id>/final.mp4`.
   Finished-video folder (Drive): https://drive.google.com/drive/folders/124tJ8yYEAWmvd-2fD_580pq0k5YYU1fF
   Give the user the Drive link to review. Do NOT upload to YouTube without
   his explicit OK (Drive-only review first).

## 6. Lessons already learned (do NOT repeat these mistakes)

- **"Provided voice file wins":** if a `voice.mp3` already sits in the job's
  Drive folder, stage 02 reuses it and NEVER calls Edge TTS. To force a fresh
  Edge TTS generation, delete `voice.mp3` from the Drive job store first.
  Drive delete is PERMANENT (not in Trash).
- **Stale state stalls a job:** never commit a job state with stages marked
  done unless they really are done. `claim_next_job` resets state on claim.
  If a stage says "no job waiting", reset `jobs/<job_id>/state.json` (GitHub
  AND Drive) to the previous stage — and include `state["voice"]` (runner
  path) + `state["voice_duration_s"]` when resetting to voice-done, or the
  render stage fails with "no voice file".
- **Dispatch one workflow at a time** and wait for completion.
- **Audio bugs fixed 2026-11-24 (do NOT reintroduce):** (a) music ducking was
  backwards — the CORRECT order is `[music][voice]sidechaincompress` then
  `amix` voice + ducked music (the old `[voice][music]sidechaincompress`
  discarded the music entirely); (b) ASS subtitles in the same filter graph
  as audio truncated audio to ~5 s — `final_encode` is TWO-PASS (pass 1 burns
  ASS into video only, pass 2 muxes video + mixed audio). (c) QC now checks
  the audio STREAM duration, not just the container.
- **Drive download quirk:** `hatch_gws_cli drive files download` returns a
  URI and writes a 0-byte file. Use Drive copy/rename instead of downloading.
- **Pexels key in chat:** a Pexels API key was once shared in chat — it must
  be regenerated on Pexels and the GitHub secret updated. Never share keys
  in chat; use the GitHub Secrets web UI.
- **OAuth scope:** the Drive refresh token's granted scopes are used as-is;
  do NOT force `scopes=[...]` in code (causes `invalid_scope`).
- **The OAuth app must stay published to Production** or the refresh token
  dies after 7 days.
- **`/tmp` is wiped on restarts** — do not keep scripts or state files there.
- **Never delete anything in any YouTube channel.** Never promise guaranteed
  views, earnings, or monetization. Reused-content policy is the real risk —
  original script + meaningful editing mitigates it, but monetization is NOT
  guaranteed.

## 7. What is proven / what is not

PROVEN on GitHub Actions: all 4 stages end-to-end (test job sa-test-001,
15.87 s, 1920x1080, QC passed, audio stream 15.84 s); Edge TTS on the runner;
Pexels live search; Drive job-store sync; music bed from the real track;
two-pass render; audio-duration QC. Final test video on Drive:
https://drive.google.com/file/d/1OHRQWGJLlhHqkSZYnpZANJM5eNUQoFos/view
NOT YET PROVEN: Cloudflare FLUX.1 stills with the token; Pixabay live search;
a real (live) Drive upload of a finished video; a full 20-min render.
A 20-min video renders in ~20-30 min on the runner (clip search 5-10 min,
voice 2-5 min, render 20-30 min, upload 2-5 min; ~30-50 min total,
unattended). The 6-hour GitHub job limit is not a problem.

## 8. The other 4 channels (NOT yours)

Money Dynasty, Cosmos Rising, Umbral Hour, Ages of Ruin are separate repos
to be built later (same template, own music track + Drive folder each).
Do NOT build them here. This repo is Steel Atlas only.
