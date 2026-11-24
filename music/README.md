# Music

Place **one royalty-free track** here as `track.mp3` (the name in
`config.yaml` → `music.track`). It is looped under the narration for the
whole video by the pipeline (FFmpeg: −20 dB under the voice, sidechain
ducking, fade in/out, final loudnorm −14 LUFS).

**Safe sources (free, YouTube-safe):**
- YouTube Audio Library (studio.youtube.com → Audio Library) — safest,
  no attribution needed for most tracks, no Content ID risk.
- Pixabay Music (pixabay.com/music/) — free licence, keep the licence page
  as proof; Content ID claims can still happen, so keep the download receipt.

**How the track reaches the pipeline:**
1. Download one track from a source above (on the user's own computer).
2. Upload `track.mp3` to Google Drive (web).
3. Set the GitHub Secret `DRIVE_MUSIC_FILE_ID` to that file's ID
   (the last part of its Drive share URL).
4. The render stage downloads it from Drive at the start of every render.
   The file is never committed to git (licensing + size).

**Rules:**
- One track per channel is enough to start; rotate tracks by uploading a
  new file and updating the `DRIVE_MUSIC_FILE_ID` secret.
- For the local test, `scripts/test_e2e.sh` generates a stand-in tone.
- Never use a commercial song. Never use a track with an unknown licence.
