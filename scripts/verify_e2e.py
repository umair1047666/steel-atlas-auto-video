"""Verify the end-to-end test output. Prints PASS/FAIL per check and exits
non-zero if anything fails. Also proves music ducking in isolation:
a constant music tone ducked by a burst voice must measure much quieter
during voice bursts than during gaps.
"""
import json
import subprocess
import sys
from pathlib import Path

JOB = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("jobs/sa-test-001")
ROOT = Path(__file__).resolve().parent.parent


def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr[-1000:]}")
    return r


def probe(path):
    r = run(["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout)


def mean_volume(path, start=None, end=None):
    cmd = ["ffmpeg", "-v", "info"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(path), "-af", "volumedetect", "-vn", "-sn", "-dn",
            "-f", "null", "-"]
    r = run(cmd)
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    return None


def main():
    failures = []
    final = JOB / "final.mp4"
    state = json.loads((JOB / "state.json").read_text(encoding="utf-8"))
    job = json.loads((JOB / "job.json").read_text(encoding="utf-8"))

    # 1. final video exists, 1080p, has audio
    if not final.exists():
        failures.append("final.mp4 missing")
    else:
        info = probe(final)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        a = [s for s in info["streams"] if s["codec_type"] == "audio"]
        dur = float(info["format"]["duration"])
        print(f"final: {v['width']}x{v['height']} {v['codec_name']}, "
              f"audio={bool(a)}, duration={dur:.2f}s, "
              f"size={final.stat().st_size // 1024} KB")
        if (v["width"], v["height"]) != (1920, 1080):
            failures.append(f"resolution is {v['width']}x{v['height']}, not 1920x1080")
        if not a:
            failures.append("no audio stream in final.mp4")
        if dur < 5:
            failures.append(f"duration suspiciously short: {dur:.2f}s")

    # 2. QC passed in state
    qc = state.get("qc", {})
    print("QC report:", json.dumps(qc.get("checks", {})))
    if not qc.get("ok"):
        failures.append("QC did not pass")

    # 3. manifest: every scene got a visual, and which kind
    for m in state.get("manifest", []):
        print(f"scene {m['scene']}: {m['kind']} via {m['source']}")

    # 4. subtitles: ASS + SRT exist, cues present
    ass = JOB / "highlights.ass"
    srt = JOB / "highlights.srt"
    if not ass.exists() or not srt.exists():
        failures.append("subtitle files missing")
    else:
        cues = ass.read_text(encoding="utf-8").count("Dialogue:")
        print(f"subtitle cues burned in: {cues}")
        if cues < 1:
            failures.append("no subtitle cues generated")

    # 5. frames at cue times (visual check by the operator)
    if final.exists():
        frames = JOB / "frames"
        frames.mkdir(exist_ok=True)
        _info = probe(final)
        _dur = float(_info["format"]["duration"])
        for i, frac in enumerate((0.15, 0.5, 0.85)):
            t = round(_dur * frac, 2)
            run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(final),
                 "-frames:v", "1", str(frames / f"frame-{i}-t{t}s.png")])
        print(f"frames extracted to {frames} (check subtitle burn-in visually)")

    # 6. ducking proof (isolated): constant music + burst voice
    duck_dir = JOB / "duck-test"
    duck_dir.mkdir(exist_ok=True)
    voice = duck_dir / "burst-voice.wav"
    music = duck_dir / "const-music.wav"
    # burst voice: 1 kHz tone ON 0-2s and 4-6s, silent 2-4s
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=1000:duration=6",
         "-af", "volume='if(between(t,0,2),0.9,if(between(t,4,6),0.9,0))':eval=frame",
         str(voice)])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=200:duration=6", "-af", "volume=0.5", str(music)])
    ducked = duck_dir / "ducked-music.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(music), "-i", str(voice),
         "-filter_complex",
         "[0:a]volume=0.1[m];[m][1:a]sidechaincompress=threshold=0.03:ratio=8:"
         "attack=200:release=400:makeup=1[out]",
         "-map", "[out]", str(ducked)])
    on_vol = mean_volume(ducked, 0.5, 1.5)    # voice speaking
    off_vol = mean_volume(ducked, 2.5, 3.5)   # voice silent
    print(f"ducking: music during voice = {on_vol} dB, during gap = {off_vol} dB")
    if on_vol is None or off_vol is None:
        failures.append("could not measure ducking volumes")
    elif not (off_vol - on_vol > 6):
        failures.append(
            f"ducking too weak: gap-on difference only {off_vol - on_vol:.1f} dB "
            f"(expected >6 dB)")
    else:
        print(f"ducking OK: music drops {off_vol - on_vol:.1f} dB under voice")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
