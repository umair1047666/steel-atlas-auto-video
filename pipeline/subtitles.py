"""Highlight subtitles: only [HL]...[/HL] phrases (~5% of the narration).

Timing sources, best first:
  1. Edge TTS word boundaries (exact).
  2. Proportional timing inside the scene's audio span (fallback).
Outputs: an ASS file (burned in during the single final encode) and an SRT
sidecar (uploaded to YouTube as a caption track).
"""
import json
import re
from pathlib import Path

HL_RE = re.compile(r"\[HL\](.*?)\[/HL\]", re.DOTALL)


def extract_highlights(script):
    """Return list of highlight phrases in script order."""
    return [m.group(1).strip() for m in HL_RE.finditer(script or "") if m.group(1).strip()]


def clean_script(script):
    return HL_RE.sub(lambda m: m.group(1), script or "")


def _fmt_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _fmt_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _word_list(words_json):
    if not words_json:
        return []
    try:
        with open(words_json, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _find_phrase_words(phrase, words):
    """Find the phrase's words in order in the word-boundary list.
    Returns (start_ms, end_ms) or None."""
    phrase_words = [w.lower().strip(".,!?;:\"'") for w in phrase.split() if w.strip()]
    if not phrase_words or not words:
        return None
    spoken = [w.get("word", "").lower().strip(".,!?;:\"'") for w in words]
    n = len(phrase_words)
    for i in range(len(spoken) - n + 1):
        if spoken[i:i + n] == phrase_words:
            start = words[i]["offset_ms"]
            end = words[i + n - 1]["offset_ms"] + words[i + n - 1]["duration_ms"]
            return start / 1000.0, end / 1000.0
    return None


def time_highlights(script, words_json=None, total_duration_s=None):
    """Return [(phrase, start_s, end_s)] for every [HL] phrase.

    Exact when word boundaries exist; otherwise proportional by character
    position in the cleaned script (approximate — documented, not hidden).
    """
    highlights = extract_highlights(script)
    if not highlights:
        return []
    cleaned = clean_script(script)
    words = _word_list(words_json)
    cues = []
    for phrase in highlights:
        span = _find_phrase_words(phrase, words) if words else None
        if span is None and total_duration_s:
            # proportional fallback
            pos = cleaned.find(phrase)
            if pos < 0:
                continue
            frac = pos / max(len(cleaned), 1)
            span = (frac * total_duration_s,
                    min(total_duration_s, (frac + len(phrase) / max(len(cleaned), 1)) * total_duration_s))
        if span is None:
            continue
        cues.append((phrase, round(span[0], 2), round(span[1], 2)))
    return cues


def write_ass(cues, dest, cfg):
    sub = cfg["subtitles"]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\nPlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Highlight,{sub['font']},{sub['font_size']},"
        f"{sub['primary_color']},&H000000FF&,{sub['outline_color']},&H80000000&,"
        f"-1,0,0,0,100,100,0,0,1,{sub['outline']},{sub['shadow']},"
        f"{sub['alignment']},40,40,60,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = [header]
    for phrase, start, end in cues:
        text = phrase.replace("\n", " ")
        lines.append(f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},"
                     f"Highlight,,0,0,0,,{text}\n")
    Path(dest).write_text("".join(lines), encoding="utf-8")
    return str(dest)


def write_srt(cues, dest):
    lines = []
    for i, (phrase, start, end) in enumerate(cues, 1):
        lines.append(f"{i}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{phrase}\n")
    Path(dest).write_text("\n".join(lines), encoding="utf-8")
    return str(dest)
