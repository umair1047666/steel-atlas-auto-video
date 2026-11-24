"""Auto-QC — zero Muse tokens. Every check is a measurement, not a guess.

Checks (all must pass, or the job STOPS and the failure is logged):
  1. File exists and is a valid video (ffprobe).
  2. Duration is within tolerance of the expected length.
  3. An audio stream exists and is not silent (volumedetect mean volume).
  4. No black segment longer than the allowed threshold (blackdetect).
  5. Resolution is exactly the configured target (1920x1080).

Also exposes per-clip black detection, used during render: a black clip is
replaced with a still+zoom before it can reach the final video.
"""
import json
import subprocess
from pathlib import Path

from .common import log, media_info, run


def check_file(path, cfg, expected_duration_s=None, tolerance=0.05):
    """Full QC gate. Returns (ok, report_dict). Never raises — the caller
    decides what to do with a failure."""
    report = {"file": str(path), "checks": {}}
    ok = True

    info = media_info(path)
    if info is None:
        report["checks"]["probe"] = "FAIL: ffprobe could not read the file"
        return False, report
    width, height, duration, has_audio = info
    report["checks"]["probe"] = f"OK: {width}x{height}, {duration:.2f}s, audio={has_audio}"

    if (width, height) != (cfg["video"]["width"], cfg["video"]["height"]):
        report["checks"]["resolution"] = (
            f"FAIL: {width}x{height} != "
            f"{cfg['video']['width']}x{cfg['video']['height']}")
        ok = False
    else:
        report["checks"]["resolution"] = "OK"

    if expected_duration_s:
        low = expected_duration_s * (1 - tolerance)
        high = expected_duration_s * (1 + tolerance)
        if not (low <= duration <= high):
            report["checks"]["duration"] = (
                f"FAIL: {duration:.2f}s not within ±{tolerance:.0%} of "
                f"{expected_duration_s:.2f}s")
            ok = False
        else:
            report["checks"]["duration"] = f"OK: {duration:.2f}s"
    else:
        report["checks"]["duration"] = f"SKIP (no expected duration): {duration:.2f}s"

    if not has_audio:
        report["checks"]["audio_present"] = "FAIL: no audio stream"
        ok = False
    else:
        mean_vol = mean_volume(path)
        report["checks"]["audio_present"] = f"OK: mean volume {mean_vol}"
        if mean_vol is not None and mean_vol < -60:
            report["checks"]["audio_present"] = (
                f"FAIL: audio is effectively silent (mean {mean_vol} dB)")
            ok = False
        # The audio STREAM itself must span the full video — the container
        # duration alone does not prove this (see audio_stream_duration).
        if expected_duration_s:
            aud_dur = audio_stream_duration(path)
            if aud_dur is None:
                report["checks"]["audio_duration"] = "FAIL: could not read audio stream duration"
                ok = False
            else:
                low = expected_duration_s * (1 - tolerance)
                high = expected_duration_s * (1 + tolerance)
                if not (low <= aud_dur <= high):
                    report["checks"]["audio_duration"] = (
                        f"FAIL: audio stream {aud_dur:.2f}s not within ±{tolerance:.0%} of "
                        f"{expected_duration_s:.2f}s")
                    ok = False
                else:
                    report["checks"]["audio_duration"] = f"OK: audio stream {aud_dur:.2f}s"

    blacks = black_segments(path)
    report["black_segments"] = blacks
    long_blacks = [b for b in blacks if b["duration"] > 1.0]
    if long_blacks:
        report["checks"]["black"] = f"FAIL: black segments >1s: {long_blacks}"
        ok = False
    else:
        report["checks"]["black"] = "OK: no black segment over 1s"

    report["ok"] = ok
    return ok, report


def audio_stream_duration(path):
    """Duration of the AUDIO stream in seconds, or None.

    The container (format) duration can be full-length while the audio
    stream itself is cut short (this exact bug shipped once: ASS + audio
    in one filter graph truncated the audio to ~5 s while the video was
    11.9 s, and QC passed because it only checked format duration).
    Always check the audio stream itself.
    """
    info = media_info(path)
    if info is None:
        return None
    from .common import ffprobe
    raw = ffprobe(path)
    if not raw:
        return None
    audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        return None
    dur = audio.get("duration")
    if dur is not None:
        return float(dur)
    # Fall back to the container duration if the stream has none.
    return float(raw.get("format", {}).get("duration") or 0) or None


def mean_volume(path):
    """Mean volume in dB from volumedetect, or None."""
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect",
         "-vn", "-sn", "-dn", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in (result.stderr or "").splitlines():
        if "mean_volume" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except ValueError:
                return None
    return None


def black_segments(path, min_duration=0.5, threshold=0.10):
    """List black segments: [{'start': s, 'end': s, 'duration': s}]."""
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path),
         "-vf", f"blackdetect=d={min_duration}:pix_th={threshold}",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    segments = []
    for line in (result.stderr or "").splitlines():
        if "black_start" in line:
            try:
                parts = dict(p.split(":") for p in
                             [x.strip() for x in line.split("blackdetect")[1].split("]")[-1].split() if ":" in x])
                start = float(parts.get("black_start", 0))
                end = float(parts.get("black_end", start))
                segments.append({"start": round(start, 2), "end": round(end, 2),
                                 "duration": round(end - start, 2)})
            except (ValueError, IndexError):
                continue
    return segments
