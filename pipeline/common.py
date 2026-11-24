"""Shared helpers: config, paths, logging, job state, ffmpeg probing.

No secrets are read from files. Secrets come only from environment variables
(GitHub Secrets in Actions, exported vars locally).
"""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def job_dir(job_id):
    return ROOT / "jobs" / job_id


def queue_dir():
    return ROOT / "queue"


def load_job(job_id):
    path = job_dir(job_id) / "job.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_job(job_id, job):
    path = job_dir(job_id) / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)


def load_state(job_id):
    path = job_dir(job_id) / "state.json"
    if not path.exists():
        return {"job_id": job_id, "stage": "queued", "stages": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(job_id, state):
    path = job_dir(job_id) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def mark_stage(job_id, stage, ok=True, detail=""):
    state = load_state(job_id)
    state["stages"][stage] = {"ok": ok, "detail": detail}
    state["stage"] = stage if ok else f"{stage}-FAILED"
    save_state(job_id, state)
    return state


def run(cmd, check=True, capture=True):
    """Run a shell command, log it, return CompletedProcess."""
    log.info("$ %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        log.error("FAILED (exit %s): %s", result.returncode, result.stderr[-2000:])
        raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd)}")
    return result


def ffprobe(path):
    """Return ffprobe JSON for a media file."""
    result = run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout or "{}")


def media_info(path):
    """(width, height, duration_s, has_audio) or None.
    Audio-only files return (0, 0, duration, True)."""
    info = ffprobe(path)
    if not info:
        return None
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or 0)
    if video is None and audio is None:
        return None
    if video is None:
        return (0, 0, duration, True)
    return (
        int(video.get("width") or 0),
        int(video.get("height") or 0),
        duration,
        audio is not None,
    )


def require_env(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required secret: {name} (set it in GitHub Secrets)")
    return value


def fail(msg):
    log.error("FATAL: %s", msg)
    sys.exit(1)
