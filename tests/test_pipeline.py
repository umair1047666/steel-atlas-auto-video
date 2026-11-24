"""Unit tests for the Steel Atlas pipeline. No network access needed.

Run:  python -m pytest tests/ -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import clip_search, subtitles  # noqa: E402
from pipeline.common import load_config  # noqa: E402


def test_config_loads():
    cfg = load_config()
    assert cfg["channel"]["name"] == "Steel Atlas"
    assert cfg["video"]["width"] == 1920
    assert cfg["video"]["height"] == 1080
    assert cfg["music"]["level_db"] == -20  # 10% under the voice
    assert cfg["music"]["hook_s"] == 30
    assert cfg["music"]["ending_s"] == 30
    assert cfg["subtitles"]["highlight_fraction"] == 0.05


def test_highlight_extraction():
    script = ("The bridge is tall. [HL]Its mast rises 343 metres[/HL], higher "
              "than the Eiffel Tower. [HL]Three years[/HL] to build.")
    hl = subtitles.extract_highlights(script)
    assert hl == ["Its mast rises 343 metres", "Three years"]
    assert "[HL]" not in subtitles.clean_script(script)


def test_highlight_timing_proportional():
    script = "AAA [HL]BBB[/HL] CCC"
    cues = subtitles.time_highlights(script, words_json=None, total_duration_s=30.0)
    assert len(cues) == 1
    phrase, start, end = cues[0]
    assert phrase == "BBB"
    assert 0 <= start < end <= 30.0


def test_highlight_timing_word_boundaries(tmp_path):
    script = "The mast rises 343 metres high."
    words = [
        {"word": "The", "offset_ms": 0, "duration_ms": 200},
        {"word": "mast", "offset_ms": 200, "duration_ms": 300},
        {"word": "rises", "offset_ms": 500, "duration_ms": 300},
        {"word": "343", "offset_ms": 800, "duration_ms": 300},
        {"word": "metres", "offset_ms": 1100, "duration_ms": 400},
        {"word": "high.", "offset_ms": 1500, "duration_ms": 300},
    ]
    words_file = tmp_path / "words.json"
    words_file.write_text(json.dumps(words), encoding="utf-8")
    script_hl = "The [HL]mast rises 343 metres[/HL] high."
    cues = subtitles.time_highlights(script_hl, words_json=str(words_file))
    assert len(cues) == 1
    phrase, start, end = cues[0]
    assert phrase == "mast rises 343 metres"
    assert start == 0.2
    assert end == 1.5


def test_ass_and_srt_written(tmp_path):
    cfg = load_config()
    cues = [("Its mast rises 343 metres", 1.0, 3.5)]
    ass = subtitles.write_ass(cues, tmp_path / "t.ass", cfg)
    srt = subtitles.write_srt(cues, tmp_path / "t.srt")
    ass_text = Path(ass).read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass_text
    assert "Its mast rises 343 metres" in ass_text
    assert "0:00:01.00" in ass_text
    srt_text = Path(srt).read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,500" in srt_text


def test_relevance_gate():
    kws = ["suspension bridge", "cables"]
    # overlapping title passes
    assert clip_search.keyword_overlap(kws, "Suspension bridge cables at dawn") >= 1
    # unrelated title fails
    assert clip_search.keyword_overlap(kws, "cute puppy playing in garden") == 0


def test_generic_blocklist():
    cfg = load_config()
    block = cfg["clip_search"]["generic_blocklist"]
    assert clip_search.is_generic("abstract background loop", [], block)
    assert not clip_search.is_generic("Millau Viaduct construction", [], block)


def test_video_file_picker():
    files = [
        {"width": 1280, "height": 720, "link": "a"},
        {"width": 1920, "height": 1080, "link": "b"},
        {"width": 640, "height": 360, "link": "c"},
    ]
    picked = clip_search.pick_video_file(files, 720)
    assert picked["height"] == 1080
    # nothing >= 720p -> None
    assert clip_search.pick_video_file(
        [{"width": 640, "height": 360, "link": "c"}], 720) is None


def test_sample_job_schema():
    job = json.loads(
        (Path(__file__).resolve().parent.parent / "queue" / "sample-job.json")
        .read_text(encoding="utf-8"))
    assert job["job_id"] == "sa-test-001"
    assert job["status"] == "queued"
    assert len(job["scenes"]) == 3
    for scene in job["scenes"]:
        assert scene["keywords"] and scene["duration_s"] > 0
    assert "[HL]" in job["script"]


def test_media_info_audio_only(tmp_path):
    """Regression test: media_info must work on audio-only files (voice)."""
    import subprocess

    from pipeline.common import media_info

    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2",
                    str(wav)], check=True)
    info = media_info(wav)
    assert info is not None
    assert info[0] == 0 and info[1] == 0  # no video stream
    assert abs(info[2] - 2.0) < 0.2
    assert info[3] is True


def _tone_freq(path, start_s, dur=1.0):
    """Estimate the dominant sine frequency of a 1s window via zero crossings."""
    import struct
    import subprocess
    import wave

    win = str(path) + ".win.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start_s),
                    "-t", str(dur), "-i", str(path), "-ac", "1", "-ar", "8000",
                    win], check=True)
    with wave.open(win, "rb") as w:
        n = w.getnframes()
        frames = w.readframes(n)
    samples = struct.unpack(f"<{n}h", frames)
    crossings = sum(1 for a, b in zip(samples, samples[1:])
                    if (a < 0) != (b < 0))
    return crossings / 2.0 / dur


def test_music_bed_hook_middle_ending(tmp_path):
    """Steel Atlas rule: first hook_s = hook, last ending_s = ending,
    middle loops to fill the video. A 3-tone track proves the order."""
    import subprocess

    from pipeline import render
    from pipeline.common import load_config, media_info

    # 10s track: 0-3s 440 Hz, 3-7s 660 Hz, 7-10s 880 Hz.
    parts = []
    for freq, dur, name in [(440, 3, "a.wav"), (660, 4, "b.wav"),
                            (880, 3, "c.wav")]:
        p = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"sine=frequency={freq}:duration={dur}",
                        str(p)], check=True)
        parts.append(p)
    list_file = tmp_path / "list.txt"
    list_file.write_text(
        "".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    track = tmp_path / "track.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat",
                    "-safe", "0", "-i", str(list_file), "-c", "copy",
                    str(track)], check=True)

    cfg = load_config()
    cfg["music"] = dict(cfg["music"])
    cfg["music"]["hook_s"] = 3
    cfg["music"]["ending_s"] = 3
    bed = render.build_music_bed(str(track), 20.0, cfg, tmp_path / "bed.flac")
    info = media_info(bed)
    assert info is not None
    assert abs(info[2] - 20.0) < 0.3
    # hook (440) at t=1, looped middle (660) at t=10, ending (880) at t=19.
    assert abs(_tone_freq(bed, 1.0) - 440) < 60
    assert abs(_tone_freq(bed, 10.0) - 660) < 60
    assert abs(_tone_freq(bed, 19.0) - 880) < 60


def test_music_bed_short_video_loops_whole_track(tmp_path):
    """Video shorter than hook+ending: loop the whole track."""
    import subprocess

    from pipeline import render
    from pipeline.common import load_config, media_info

    track = tmp_path / "track.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=10",
                    str(track)], check=True)
    cfg = load_config()
    cfg["music"] = dict(cfg["music"])
    cfg["music"]["hook_s"] = 30
    cfg["music"]["ending_s"] = 30
    bed = render.build_music_bed(str(track), 12.0, cfg, tmp_path / "bed.flac")
    info = media_info(bed)
    assert info is not None
    assert abs(info[2] - 12.0) < 0.3
    assert abs(_tone_freq(bed, 6.0) - 440) < 60
