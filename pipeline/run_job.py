"""Orchestrator: claim a queued job and run it through the stages.

Usage:
    python -m pipeline.run_job --stage clip-search [--job sa-0001]
    python -m pipeline.run_job --stage voice       [--job sa-0001]
    python -m pipeline.run_job --stage render      [--job sa-0001]
    python -m pipeline.run_job --stage upload      [--job sa-0001] [--live]
    python -m pipeline.run_job --stage all         [--job sa-0001] [--live]

Rules:
- If --job is omitted, the oldest job whose stage matches is claimed.
- A stage that fails STOPS the pipeline: the state is marked FAILED with a
  clear reason, the exit code is non-zero, and no later stage runs.
  Nothing proceeds silently past a failure.
- Upload is DRY-RUN unless --live AND env UPLOAD_LIVE=1. The first real
  upload only happens after the render stage passed QC.
- No secrets are ever read from files — only from the environment.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import clip_search, jobstore, qc, render, stills, subtitles, voice
from .common import (
    ROOT, fail, job_dir, load_config, load_job, load_state, log,
    mark_stage, media_info, queue_dir, save_job, save_state,
)
from .upload_drive import upload_to_drive
from .upload_youtube import upload_to_youtube

STAGES = ["clip-search", "voice", "render", "upload"]


def claim_next_job(stage):
    """Oldest queued job that has not finished this stage yet."""
    qdir = queue_dir()
    jobs = []
    for path in sorted(qdir.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("ignoring invalid queue file: %s", path.name)
            continue
        if job.get("status") == "queued" and job.get("job_id"):
            jobs.append((path, job))
    if not jobs:
        return None
    path, job = jobs[0]
    jdir = job_dir(job["job_id"])
    jdir.mkdir(parents=True, exist_ok=True)
    if not (jdir / "job.json").exists():
        save_job(job["job_id"], job)
        path.unlink()  # claimed: remove from the queue folder
    # Reset any stale state (e.g. a local e2e test state that was committed
    # to the repo): a stale state.json can mark later stages done, so the
    # next stage finds "no job waiting" and the pipeline stalls.
    save_state(job["job_id"], {"job_id": job["job_id"], "stage": "queued",
                               "stages": {}})
    return job["job_id"]


def next_job_for_stage(stage):
    """Find the job waiting for this stage.

    clip-search claims from queue/. Later stages find the job whose state
    shows the previous stage finished (and this stage not yet done).
    """
    if stage == "clip-search":
        return claim_next_job(stage)
    prev = {"voice": "clip-search", "render": "voice", "upload": "render"}[stage]
    root = ROOT / "jobs"
    if not root.exists():
        return None
    candidates = []
    for jdir in sorted(root.iterdir()):
        if not jdir.is_dir():
            continue
        state = load_state(jdir.name)
        stages = state.get("stages", {})
        if state.get("stage") == prev and stages.get(prev, {}).get("ok"):
            if not stages.get(stage, {}).get("ok"):
                candidates.append(jdir.name)
    return candidates[0] if candidates else None


def _scene_visual(job_id, scene, cfg, jdir):
    """Fallback chain for one scene. Returns (intermediate_clip, source, kind).

    real clip -> real photo + zoom -> AI still -> title card.
    A black clip is rejected and the chain continues to the next fallback.
    """
    jdir = Path(jdir)
    scene_id = scene.get("id", 0)
    duration_s = float(scene.get("duration_s") or 8)
    work = jdir / "clips"
    work.mkdir(parents=True, exist_ok=True)

    found = clip_search.find_clip_for_scene(scene, cfg, work)
    if found:
        src, source, _ = found
        dest = work / f"scene-{scene_id:03d}.mp4"
        render.normalise_clip(src, dest, cfg, duration_s=duration_s)
        # Reject only a genuinely black clip (long black run or mostly black),
        # not a normal fade-in from black at the start.
        blacks = qc.black_segments(str(dest), min_duration=0.5)
        total_black = sum(b["duration"] for b in blacks)
        worst = max((b["duration"] for b in blacks), default=0)
        if worst <= 2.0 and total_black <= 0.25 * duration_s:
            return str(dest), source, "clip"
        log.warning("scene %s: clip is mostly black (worst %.1fs, total %.1fs) — "
                    "falling through to photo", scene_id, worst, total_black)

    photo = clip_search.find_photo_for_scene(scene, cfg, work)
    if photo:
        src, source = photo
        dest = work / f"scene-{scene_id:03d}.mp4"
        render.still_to_clip(src, dest, duration_s, cfg)
        return str(dest), source, "photo"

    prompt = (scene.get("fallback_prompt")
              or " ".join(scene.get("keywords", []))
              or "engineering megaproject, dramatic wide shot")
    still = stills.generate_ai_still(prompt, work / f"scene-{scene_id:03d}-still.jpg", cfg)
    if still:
        dest = work / f"scene-{scene_id:03d}.mp4"
        render.still_to_clip(still, dest, duration_s, cfg)
        return str(dest), "cloudflare-flux", "still"

    card = stills.make_title_card(
        (scene.get("keywords") or [cfg["channel"]["name"]])[0].title(),
        work / f"scene-{scene_id:03d}-card.mp4", cfg,
        subtext=cfg["channel"]["name"].upper())
    dest = work / f"scene-{scene_id:03d}.mp4"
    render.normalise_clip(card, dest, cfg, duration_s=duration_s)
    return str(dest), "title-card", "card"


def stage_clip_search(job_id, cfg):
    job = load_job(job_id)
    jdir = job_dir(job_id)
    manifest = []
    for scene in job.get("scenes", []):
        clip, source, kind = _scene_visual(job_id, scene, cfg, jdir)
        manifest.append({"scene": scene.get("id"), "clip": clip,
                         "source": source, "kind": kind})
        log.info("scene %s -> %s (%s)", scene.get("id"), kind, source)
    state = load_state(job_id)
    state["manifest"] = manifest
    save_state(job_id, state)
    return manifest


def stage_voice(job_id, cfg):
    job = load_job(job_id)
    jdir = job_dir(job_id)
    voice_path, words_json = voice.generate_voice(job, cfg, jdir)
    info = media_info(voice_path)
    if not info or info[2] <= 0:
        raise RuntimeError(f"voice file is empty or unreadable: {voice_path}")
    state = load_state(job_id)
    state["voice"] = voice_path
    state["voice_words"] = words_json
    state["voice_duration_s"] = round(info[2], 2)
    save_state(job_id, state)
    log.info("voice ready: %s (%.1fs)", voice_path, info[2])
    return voice_path


def stage_render(job_id, cfg):
    job = load_job(job_id)
    jdir = Path(job_dir(job_id))
    state = load_state(job_id)
    manifest = state.get("manifest")
    if not manifest:
        raise RuntimeError("no clip manifest — run clip-search first")
    voice_path = state.get("voice")
    if not voice_path or not Path(voice_path).exists():
        raise RuntimeError("no voice file — run voice stage first")

    clips = [m["clip"] for m in manifest]
    for clip in clips:
        if not Path(clip).exists():
            raise RuntimeError(f"manifest clip missing: {clip}")
    concat = render.concat_copy(clips, jdir / "concat.mp4")

    music_name = job.get("music_track") or cfg["music"]["track"].split("/")[-1]
    music = ROOT / "music" / music_name
    if not music.exists():
        # Try the Drive music store (the track is never committed to git).
        fetched = jobstore.fetch_music(music)
        if not fetched:
            raise RuntimeError(
                f"music track missing: {music} — upload a royalty-free track "
                "to Drive and set secret DRIVE_MUSIC_FILE_ID "
                "(see music/README.md)")

    voice_dur = state.get("voice_duration_s") or media_info(voice_path)[2]
    cues = subtitles.time_highlights(
        job.get("script", ""), state.get("voice_words"),
        total_duration_s=voice_dur)
    ass = subtitles.write_ass(cues, jdir / "highlights.ass", cfg)
    srt = subtitles.write_srt(cues, jdir / "highlights.srt")
    log.info("highlight subtitles: %d cues (%s, %s)", len(cues), ass, srt)

    final = render.final_encode(concat, voice_path, str(music), ass,
                                jdir / "final.mp4", cfg)
    ok, report = qc.check_file(final, cfg, expected_duration_s=voice_dur,
                               tolerance=0.05)
    state["qc"] = report
    save_state(job_id, state)
    if not ok:
        raise RuntimeError(f"QC FAILED: {json.dumps(report, indent=2)}")
    log.info("QC passed: %s", json.dumps(report["checks"]))
    state["final"] = final
    save_state(job_id, state)
    return final


def stage_upload(job_id, cfg, live=False):
    job = load_job(job_id)
    jdir = Path(job_dir(job_id))
    state = load_state(job_id)
    final = state.get("final")
    if not final or not Path(final).exists():
        raise RuntimeError("no finished video — run render first")
    if not state.get("qc", {}).get("ok"):
        raise RuntimeError("refusing to upload: QC did not pass")

    folder_id = os.environ.get("DRIVE_FOLDER_ID", "")
    drive_id, drive_link = upload_to_drive(final, folder_id, cfg, live=live)
    state["drive"] = {"file_id": drive_id, "link": drive_link}
    save_state(job_id, state)

    video_id, video_url = upload_to_youtube(final, job, cfg, live=live)
    state["youtube"] = {"video_id": video_id, "url": video_url}
    state["status"] = "done" if live else "dry-run-done"
    save_state(job_id, state)
    log.info("upload stage complete (live=%s): drive=%s youtube=%s",
             live, drive_link, video_url)
    return state


def main():
    parser = argparse.ArgumentParser(description="Steel Atlas auto-video pipeline")
    parser.add_argument("--stage", required=True,
                        choices=STAGES + ["all"])
    parser.add_argument("--job", default=None, help="job id (default: claim oldest)")
    parser.add_argument("--live", action="store_true",
                        help="allow REAL uploads (also needs UPLOAD_LIVE=1)")
    args = parser.parse_args()

    cfg = load_config()
    job_id = args.job or next_job_for_stage(args.stage if args.stage != "all" else "clip-search")
    if not job_id:
        log.info("no job waiting for stage '%s' — nothing to do", args.stage)
        return 0
    log.info("job %s — stage %s", job_id, args.stage)

    stages = STAGES if args.stage == "all" else [args.stage]
    try:
        for stage in stages:
            # Restore the job's media from the Drive job store (no-op locally).
            if stage != "clip-search":
                jobstore.pull_job(job_id)
            if stage == "clip-search":
                stage_clip_search(job_id, cfg)
            elif stage == "voice":
                stage_voice(job_id, cfg)
            elif stage == "render":
                stage_render(job_id, cfg)
            elif stage == "upload":
                stage_upload(job_id, cfg, live=args.live)
            mark_stage(job_id, stage, ok=True)
            # Persist the job's media to the Drive job store (no-op locally).
            if stage != "upload":
                jobstore.push_job(job_id)
            log.info("stage %s OK for %s", stage, job_id)
    except Exception as e:  # noqa: BLE001 - fail loudly, never silently
        mark_stage(job_id, stage, ok=False, detail=str(e))
        log.error("stage %s FAILED for %s: %s", stage, job_id, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
