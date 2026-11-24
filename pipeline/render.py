"""Render: normalise -> concat (copy) -> final encode with audio mix,
ducked music, loudnorm, burned-in highlight subtitles.

Quality rules (see README):
- Clips are normalised ONCE to identical 1920x1080/30fps intermediates
  (CRF 16-18). The concat demuxer then joins them with `-c copy` — no
  second encode. The ONLY re-encode is the final delivery pass (CRF 18).
- 720p clips are upscaled with lanczos; <720p was already rejected.
- Photos / AI stills move with a slow Ken Burns zoom (zoompan).
- Music: -20 dB under the voice, sidechain ducking, fade in/out,
  final loudnorm -14 LUFS (always followed by aresample 48000, because
  loudnorm upsamples to 192 kHz).
- Subtitles burn in during the single final encode — no extra generation.
"""
import math
from pathlib import Path

from .common import log, media_info, run


def normalise_clip(src, dest, cfg, duration_s=None):
    """Re-encode one clip to the canonical intermediate format.
    If duration_s is given, trim exactly to that length."""
    v = cfg["video"]
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if duration_s:
        cmd += ["-t", f"{duration_s:.2f}"]
    cmd += [
        "-i", str(src),
        "-vf",
        f"scale={v['width']}:{v['height']}:force_original_aspect_ratio=increase,"
        f"crop={v['width']}:{v['height']},setsar=1,fps={v['fps']},"
        f"format={v['pix_fmt']}",
        "-an", "-c:v", "libx264", "-preset", v["preset"],
        "-crf", str(v["intermediate_crf"]), "-profile:v", v["profile"],
        "-video_track_timescale", "30000",
        str(dest),
    ]
    run(cmd)
    return str(dest)


def still_to_clip(image, dest, duration_s, cfg):
    """A photo / AI still becomes a moving clip (slow Ken Burns zoom)."""
    v = cfg["video"]
    frames = max(int(duration_s * v["fps"]), 30)
    run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(image),
        "-vf",
        f"scale=3840:2160,zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)'"
        f":y='ih/2-(ih/zoom/2)':d={frames}:s={v['width']}x{v['height']}:fps={v['fps']},"
        f"format={v['pix_fmt']}",
        "-frames:v", str(frames), "-an", "-c:v", "libx264",
        "-preset", v["preset"], "-crf", str(v["intermediate_crf"]),
        "-profile:v", v["profile"], "-video_track_timescale", "30000",
        str(dest),
    ])
    return str(dest)


def concat_copy(clips, dest):
    """Join identical intermediates with the concat demuxer — no re-encode."""
    dest = Path(dest)
    list_file = dest.parent / "concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{Path(clip).resolve()}'\n")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", str(dest)])
    return str(dest)


def build_audio_filter(voice_idx, music_idx, cfg, duration_s):
    """FFmpeg audio filter graph: music -20 dB, ducked under voice,
    loudnorm -14 LUFS.

    voice_idx / music_idx are the ffmpeg input numbers (video is input 0).
    Output label is [aout].
    """
    m = cfg["music"]
    vol = 10 ** (m["level_db"] / 20.0)  # -20 dB -> 0.1
    fade = min(m["fade_s"], max(duration_s / 4, 1))
    filt = (
        f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{duration_s:.2f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={fade},afade=t=out:st={duration_s - fade:.2f}:d={fade},"
        f"volume={vol:.3f}[music];"
        f"[{voice_idx}:a][music]sidechaincompress=threshold={m['duck_threshold']}:"
        f"ratio={m['duck_ratio']}:attack={m['duck_attack_ms']}:"
        f"release={m['duck_release_ms']}:makeup=1[ducked];"
        f"[ducked]loudnorm=I={m['loudnorm_I']}:TP={m['loudnorm_TP']}:"
        f"LRA={m['loudnorm_LRA']},aresample=48000[aout]"
    )
    return filt


def final_encode(video, voice, music, ass_path, dest, cfg):
    """Single final encode: video + mixed audio + burned-in ASS subtitles.

    The VOICE is the master clock: the final video is exactly as long as the
    narration. If the video is shorter, the last frame is frozen (tpad) so
    no narration is ever cut off; if longer, it is trimmed to the voice.
    One filter_complex handles video (ASS burn-in, optional tpad) and audio
    (music mix + ducking + loudnorm), so there is exactly one re-encode.
    """
    v = cfg["video"]
    video_info = media_info(video)
    voice_info = media_info(voice)
    if video_info is None:
        raise RuntimeError(f"cannot probe concatenated video: {video}")
    if voice_info is None:
        raise RuntimeError(f"cannot probe voice audio: {voice}")
    video_dur = video_info[2]
    voice_dur = voice_info[2]
    if voice_dur <= 0:
        raise RuntimeError("voice audio has zero duration")
    duration_s = voice_dur
    audio_filt = build_audio_filter(1, 2, cfg, duration_s)
    video_chain = "[0:v]"
    if video_dur < voice_dur:
        pad = voice_dur - video_dur
        video_chain += f"tpad=stop_duration={pad:.2f}:stop_mode=clone,"
        log.info("video %.1fs shorter than voice %.1fs — freezing last frame %.1fs",
                 video_dur, voice_dur, pad)
    if ass_path and Path(ass_path).exists():
        video_chain += f"ass='{ass_path}'"
        video_chain += "[vout];"
        vmap = "[vout]"
    else:
        video_chain = video_chain.rstrip(",")
        if video_chain != "[0:v]":
            video_chain += "[vout];"
            vmap = "[vout]"
        else:
            video_chain = ""
            vmap = "0:v"
    filt = video_chain + audio_filt
    run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video), "-i", str(voice), "-i", str(music),
        "-filter_complex", filt,
        "-map", vmap, "-map", "[aout]",
        "-c:v", "libx264", "-preset", v["preset"], "-crf", str(v["crf"]),
        "-profile:v", v["profile"], "-pix_fmt", v["pix_fmt"],
        "-r", str(v["fps"]),
        "-c:a", "aac", "-b:a", v["audio_bitrate"], "-ar", str(v["audio_rate"]),
        "-movflags", "+faststart",
        "-t", f"{duration_s:.2f}",
        str(dest),
    ])
    return str(dest)
