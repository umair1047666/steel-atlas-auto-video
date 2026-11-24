# Queue — how a video job arrives

One JSON file per video, dropped in this folder (committed to the repo by
whichever Muse account wrote the script). The clip-search workflow claims the
**oldest** file whose `status` is `"queued"`, moves it to `jobs/<job-id>/`,
and the stages hand it along from there.

## Job format

```json
{
  "job_id": "sa-0001",
  "title": "How the Millau Viaduct Was Built",
  "description": "The tallest bridge on Earth ...",
  "tags": ["millau viaduct", "bridge engineering", "mega project"],
  "script": "Full narration text. Phrases wrapped in [HL]...[/HL] get highlight subtitles (~5% of the voice).",
  "scenes": [
    {
      "id": 1,
      "narration": "Narration for this scene, matching the script.",
      "keywords": ["suspension bridge", "cables", "tower"],
      "duration_s": 12,
      "fallback_prompt": "dramatic photograph of a suspension bridge tower at dusk"
    }
  ],
  "voice_file": null,
  "music_track": "track.mp3"
}
```

- `job_id`: unique, e.g. `sa-0001` (sa = Steel Atlas).
- `script`: the full narration. `[HL]...[/HL]` marks the ~5% highlight
  phrases that get burned-in subtitles.
- `scenes`: one per visual beat. `keywords` drive the stock search;
  `fallback_prompt` is the AI-still prompt when no real clip or photo fits.
  `duration_s` should roughly match the narration length of that scene.
- `voice_file`: if a Muse account already generated the narration audio, put
  the file at `jobs/<job-id>/voice.mp3` and set this to `"voice.mp3"`.
  If `null`, the pipeline generates the voice with Edge TTS (Piper fallback).
- `music_track`: file name inside `music/` (default `track.mp3`).
- `status`: `"queued"` when dropped here. The pipeline updates it.

See `sample-job.json` for a complete working example (used by the local test).
