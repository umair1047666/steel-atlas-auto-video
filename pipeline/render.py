"""Render: normalise -> concat (copy) -> final encode with audio mix,
ducked music, loudnorm, burned-in highlight subtitles.

Quality rules (see README):
- Clips are normalised ONCE to identical 1920x1080/30fps intermediates
  (CRF 16-18). The concat demuxer then joins them with `-c copy` — no
  second encode. The ONLY re-encode is the final delivery pass (CRF 18).
- 720p clips are upscaled with lanczos; <720p was already rejected.
- Photos / AI stills move with a slow Ken Burns zoom (zoompan).
- Music: level_db under the voice (Steel Atlas: -34 dB = 2%), sidechain
  ducking to ~1% under the voice, fade in/out, final loudnorm -14 LUFS
  (always followed by aresample 48000, because loudnorm upsamples to
  192 kHz). The track is a short (~3 min) file: its first hook_s seconds
  are the hook, its last ending_s seconds the ending, and the middle
  loops to fill the video (see build_music_bed).
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


def build_music_bed(track, duration_s, cfg, dest):
    """Build a music bed of exactly duration_s from one short track.

    Steel Atlas rule: the track is ~3 minutes. The FIRST hook_s seconds
    are the hook (video start), the LAST ending_s seconds are the ending
    (video end), and the MIDDLE section loops to fill the video body.
    Without this, a 3-minute track would go silent after 3 minutes of a
    20-minute video. Output is lossless FLAC (re-encoded once more in the
    final pass, so no extra lossy generation).
    """
    m = cfg["music"]
    hook_s = float(m.get("hook_s", 30))
    ending_s = float(m.get("ending_s", 30))
    track_info = media_info(track)
    if track_info is None:
        raise RuntimeError(f"cannot probe music track: {track}")
    track_len = track_info[2]
    if track_len <= 0:
        raise RuntimeError(f"music track has zero duration: {track}")
    dest = Path(dest)
    sr = 48000
    if track_len > hook_s + ending_s and duration_s > hook_s + ending_s:
        mid_len = track_len - hook_s - ending_s
        mid_dur = duration_s - hook_s - ending_s
        mid_samples = max(int(round(mid_len * sr)), 1)
        filt = (
            f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo,asplit=3[ina][inb][inc];"
            f"[ina]atrim=0:{hook_s:.2f},asetpts=PTS-STARTPTS[hook];"
            f"[inb]atrim={hook_s:.2f}:{track_len - ending_s:.2f},asetpts=PTS-STARTPTS,"
            f"aloop=loop=-1:size={mid_samples},atrim=0:{mid_dur:.2f},asetpts=PTS-STARTPTS[mid];"
            f"[inc]atrim={track_len - ending_s:.2f}:{track_len:.2f},asetpts=PTS-STARTPTS[end];"
            f"[hook][mid][end]concat=n=3:v=0:a=1,atrim=0:{duration_s:.2f},asetpts=PTS-STARTPTS[aout]"
        )
    else:
        # Track too short to split, or video shorter than hook+ending:
        # loop the whole track.
        track_samples = max(int(round(track_len * sr)), 1)
        filt = (
            f"[0:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
            f"aloop=loop=-1:size={track_samples},atrim=0:{duration_s:.2f},"
            f"asetpts=PTS-STARTPTS[aout]"
        )
    run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(track),
        "-filter_complex", filt,
        "-map", "[aout]",
        "-c:a", "flac",
        str(dest),
    ])
    return str(dest)


def build_audio_filter(voice_idx, music_idx, cfg, duration_s):
    """FFmpeg audio filter graph: music at level_db under the voice,
    ducked under voice, loudnorm -14 LUFS.

    voice_idx / music_idx are the ffmpeg input numbers (video is input 0).
    The music input must already be a bed of duration_s (see
    build_music_bed) — this filter only trims/fades/mixes it.
    Output label is [aout].

    CORRECT ducking: the MUSIC is compressed using the VOICE as the
    sidechain ([music][voice]sidechaincompress), then voice + ducked
    music are mixed with amix. (The old code had it backwards —
    [voice][music]sidechaincompress — which compressed the voice and
    DISCARDED the music entirely, so no background music was audible.)
    """
    m = cfg["music"]
    vol = 10 ** (m["level_db"] / 20.0)  # -34 dB -> 0.02 (2%)
    fade = min(m["fade_s"], max(duration_s / 4, 1))
    filt = (
        f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{duration_s:.2f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={fade},afade=t=out:st={duration_s - fade:.2f}:d={fade},"
        f"volume={vol:.3f}[music];"
        f"[music][{voice_idx}:a]sidechaincompress=threshold={m['duck_threshold']}:"
        f"ratio={m['duck_ratio']}:attack={m['duck_attack_ms']}:"
        f"release={m['duck_release_ms']}:makeup=1[ducked];"
        f"[{voice_idx}:a][ducked]amix=inputs=2:duration=first:normalize=0[mixed];"
        f"[mixed]loudnorm=I={m['loudnorm_I']}:TP={m['loudnorm_TP']}:"
        f"LRA={m['loudnorm_LRA']},aresample=48000[aout]"
    )
    return filt


def final_encode(video, voice, music, ass_path, dest, cfg):
    """Final encode: video + mixed audio + burned-in ASS subtitles.

    The VOICE is the master clock: the final video is exactly as long as the
    narration. If the video is shorter, the last frame is frozen (tpad) so
    no narration is ever cut off; if longer, it is trimmed to the voice.

    TWO PASSES (not one): burning ASS subtitles in the same filter graph
    as the audio chain TRUNCATES the audio to ~5 s (ffmpeg filter-graph
    scheduling bug — reproduced locally: audio stream 5.2 s while video
    is 11.9 s). Pass 1 burns the subtitles into the video only (no
    audio); pass 2 muxes that video (copy, no re-encode) with the mixed
    audio. Video is still encoded exactly once.
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
    # Build the music bed first (hook + looped middle + ending), so a
    # short track covers the whole video instead of going silent.
    bed = Path(dest).with_name("music_bed.flac")
    bed_path = build_music_bed(music, duration_s, cfg, bed)
    # --- Pass 1: burn subtitles into the video (video only, no audio). ---
    video_chain = "[0:v]"
    if video_dur < voice_dur:
        pad = voice_dur - video_dur
        video_chain += f"tpad=stop_duration={pad:.2f}:stop_mode=clone,"
        log.info("video %.1fs shorter than voice %.1fs — freezing last frame %.1fs",
                 video_dur, voice_dur, pad)
    video_with_subs = Path(dest).with_name("video_subs.mp4")
    if ass_path and Path(ass_path).exists():
        video_chain += f"ass='{ass_path}'"
        run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(video),
            "-vf", video_chain,
            "-c:v", "libx264", "-preset", v["preset"], "-crf", str(v["crf"]),
            "-profile:v", v["profile"], "-pix_fmt", v["pix_fmt"],
            "-r", str(v["fps"]), "-an",
            "-t", f"{duration_s:.2f}",
            str(video_with_subs),
        ])
        video_src = str(video_with_subs)
    else:
        video_src = str(video)
    # --- Pass 2: mux video (copy) + mixed audio (voice + ducked music). ---
    audio_filt = build_audio_filter(1, 2, cfg, duration_s)
    run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_src), "-i", str(voice), "-i", str(bed_path),
        "-filter_complex", audio_filt,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", v["audio_bitrate"], "-ar", str(v["audio_rate"]),
        "-movflags", "+faststart",
        "-t", f"{duration_s:.2f}",
        str(dest),
    ])
    return str(dest)
