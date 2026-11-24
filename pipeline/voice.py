"""Voice: a provided voice file always wins; otherwise Edge TTS (free, no
key), with Piper as the fallback. English narration.

Also returns word-boundary timings (Edge TTS) so highlight subtitles can be
timed exactly. Falls back to proportional timing when boundaries are
unavailable.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .common import job_dir, log, run

STRIP_HL = re.compile(r"\[HL\]|\[/HL\]")


def clean_text(text):
    return STRIP_HL.sub("", text).strip()


def find_provided_voice(job, jdir):
    """A Muse account may already have generated the narration and placed it
    in the job folder. Accept voice.mp3 / voice.wav / narration.mp3."""
    name = (job or {}).get("voice_file")
    candidates = []
    if name:
        candidates.append(Path(jdir) / name)
    candidates += [
        Path(jdir) / "voice.mp3",
        Path(jdir) / "voice.wav",
        Path(jdir) / "narration.mp3",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 1000:
            log.info("using provided voice file: %s", path)
            return str(path)
    return None


def _edge_tts_sync(text, voice, out_mp3, out_words_json):
    """Generate with edge-tts, capturing word boundaries for subtitle timing."""
    import edge_tts

    async def _gen():
        communicate = edge_tts.Communicate(text, voice)
        words = []
        with open(out_mp3, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "word": chunk["text"],
                        "offset_ms": chunk["offset"] // 10_000,
                        "duration_ms": chunk["duration"] // 10_000,
                    })
        with open(out_words_json, "w", encoding="utf-8") as f:
            json.dump(words, f)

    asyncio.run(_gen())


def _piper_sync(text, cfg, out_wav):
    """Piper CLI fallback. Downloads the voice model on first use."""
    piper = shutil.which("piper")
    if not piper:
        raise RuntimeError("piper binary not installed (apt/pip install piper-tts)")
    model_url = cfg["voice"]["piper_model_url"]
    config_url = cfg["voice"]["piper_config_url"]
    model_dir = Path.home() / ".local" / "share" / "piper"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / Path(model_url).name
    model_cfg = model_dir / Path(config_url).name
    if not model.exists():
        run(["curl", "-sL", "-o", str(model), model_url])
        run(["curl", "-sL", "-o", str(model_cfg), config_url])
    proc = subprocess.run(
        [piper, "--model", str(model), "--output_file", str(out_wav)],
        input=text.encode("utf-8"), capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"piper failed: {proc.stderr.decode()[-500:]}")


def generate_voice(job, cfg, jdir):
    """Return (voice_path, words_json_or_None)."""
    jdir = Path(jdir)
    provided = find_provided_voice(job, jdir)
    if provided:
        return provided, None
    text = clean_text(job.get("script", ""))
    if not text:
        raise RuntimeError("job has no script text to narrate")
    out_mp3 = jdir / "voice.mp3"
    out_words = jdir / "voice-words.json"
    engine = cfg["voice"].get("engine", "edge-tts")
    errors = []
    if engine == "edge-tts":
        try:
            _edge_tts_sync(text, cfg["voice"]["edge_voice"], str(out_mp3), str(out_words))
            log.info("Edge TTS voice generated: %s", out_mp3)
            return str(out_mp3), str(out_words)
        except Exception as e:  # noqa: BLE001 - fall through to Piper
            errors.append(f"edge-tts: {e}")
            log.warning("Edge TTS failed (%s) — trying Piper", e)
            # remove the partial file Edge TTS may have left behind
            for p in (out_mp3, out_words):
                if Path(p).exists() and Path(p).stat().st_size == 0:
                    Path(p).unlink()
    try:
        out_wav = jdir / "voice.wav"
        _piper_sync(text, cfg, str(out_wav))
        log.info("Piper voice generated: %s", out_wav)
        return str(out_wav), None
    except Exception as e:  # noqa: BLE001
        errors.append(f"piper: {e}")
    raise RuntimeError("all voice engines failed: " + " | ".join(errors))
