"""Render pipeline for social_studio. Pure functions, ffmpeg + PIL."""
from __future__ import annotations

import math
import os
import shlex
import subprocess
import tempfile
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD = os.path.join(HERE, "static", "fonts", "Inter-Bold.ttf")
FONT_REG = os.path.join(HERE, "static", "fonts", "Inter-Regular.ttf")

DIMS = {
    "tiktok": (1080, 1920),
    "ig_reel": (1080, 1920),
    "ig_story": (1080, 1920),
    "ig_feed_square": (1080, 1080),
    "ig_feed_portrait": (1080, 1350),
}


def target_dims(platform: str) -> Tuple[int, int]:
    if platform not in DIMS:
        raise ValueError(f"unknown platform: {platform}")
    return DIMS[platform]


def build_filter_graph(in_w: int, in_h: int, platform: str,
                       fill_mode: str = "blurred",
                       hook_text: Optional[str] = None,
                       brand_corner: bool = False,
                       lut_path: Optional[str] = None,
                       logo_path: Optional[str] = None,
                       logo_position: str = "br",
                       logo_opacity: float = 0.7,
                       subtitles_path: Optional[str] = None,
                       ken_burns: bool = False) -> str:
    out_w, out_h = target_dims(platform)
    src_aspect = in_w / max(in_h, 1)
    tgt_aspect = out_w / out_h
    parts = []
    if src_aspect > tgt_aspect:
        if fill_mode == "blurred":
            parts.append(
                f"split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},gblur=sigma=24[bgb];"
                f"[fg]scale={out_w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            parts.append(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    else:
        parts.append(
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}"
        )
    if ken_burns:
        parts.append("zoompan=z='min(zoom+0.0008,1.2)':d=125:s={}x{}".format(out_w, out_h))
    if lut_path:
        parts.append(f"lut3d={shlex.quote(lut_path)}")
    if logo_path:
        pos = {
            "tl": "10:10",
            "tr": "main_w-overlay_w-10:10",
            "bl": "10:main_h-overlay_h-10",
            "br": "main_w-overlay_w-10:main_h-overlay_h-10",
        }.get(logo_position, "main_w-overlay_w-10:main_h-overlay_h-10")
        parts.append(
            f"movie={shlex.quote(logo_path)},format=rgba,colorchannelmixer=aa={logo_opacity}[logo];"
            f"[in][logo]overlay={pos}"
        )
    if subtitles_path:
        sub_safe = subtitles_path.replace(":", r"\:").replace(",", r"\,")
        parts.append(
            f"subtitles='{sub_safe}':force_style='Fontname=Inter,FontSize=18,PrimaryColour=&H00FFFFFF,"
            f"BackColour=&H80000000,BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginV=80'"
        )
    if hook_text:
        safe = (
            hook_text
            .replace("\\", "\\\\")
            .replace("'", r"\'")
            .replace(":", r"\:")
            .replace(",", r"\,")
            .replace("%{", "%%{")
        )
        parts.append(
            f"drawtext=fontfile={FONT_BOLD}:text='{safe}':"
            f"fontcolor=white:fontsize=72:line_spacing=10:"
            f"box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.10"
        )
    return ",".join(parts)


def _ffprobe(path: str) -> Tuple[int, int]:
    """Return (width, height) of the first video stream / image."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path],
        capture_output=True, text=True, check=True,
    )
    parts = r.stdout.strip().split("x")
    if len(parts) != 2:
        raise ValueError(f"ffprobe returned unexpected dimensions for {path}: {r.stdout!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"ffprobe dimensions not numeric for {path}: {r.stdout!r}")


def render_still(src: str, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred") -> str:
    w, h = _ffprobe(src)
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", g, "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


_LUT_DIR = os.path.join(HERE, "static", "social", "luts")


def build_edits_filter_chain(edits: dict) -> str:
    """Translate an edits dict (T16 sidecar) into an ffmpeg -vf chain. Returns
    "" when no edits are configured. The chain is meant to be applied BEFORE
    the platform-fit filters so downstream code keeps working unchanged.

    Supported keys:
      crop: {x, y, w, h}      → crop=W:H:X:Y
      rotate: float degrees   → ±90/180/270 use transpose; other angles use rotate=
      brightness/contrast/saturation: -1..1 → eq=brightness=:contrast=:saturation=
      filter: cinematic/vibrant/moody/bw/warm → lut3d=<file>
    """
    if not edits:
        return ""
    parts = []
    crop = edits.get("crop") if isinstance(edits.get("crop"), dict) else None
    if crop:
        try:
            parts.append("crop={w}:{h}:{x}:{y}".format(
                w=int(crop["w"]), h=int(crop["h"]),
                x=int(crop.get("x", 0)), y=int(crop.get("y", 0)),
            ))
        except (KeyError, TypeError, ValueError):
            pass
    rot = edits.get("rotate")
    if isinstance(rot, (int, float)) and abs(rot) > 0.01:
        deg = float(rot) % 360
        if abs(deg - 90) < 0.5:
            parts.append("transpose=1")
        elif abs(deg - 180) < 0.5:
            parts.append("transpose=1,transpose=1")
        elif abs(deg - 270) < 0.5:
            parts.append("transpose=2")
        else:
            # Free rotation. Pad with black so corners don't get cropped to source size.
            rad = math.radians(deg)
            parts.append(
                f"rotate={rad:.6f}:ow=rotw({rad:.6f}):oh=roth({rad:.6f}):c=black"
            )
    eq_parts = []
    b = edits.get("brightness")
    c = edits.get("contrast")
    s = edits.get("saturation")
    if isinstance(b, (int, float)) and abs(b) > 0.001:
        # ffmpeg eq: brightness in [-1, 1]
        eq_parts.append(f"brightness={float(b):.3f}")
    if isinstance(c, (int, float)) and abs(c) > 0.001:
        # contrast: 1.0 = unchanged; map [-1, 1] → [0, 2]
        eq_parts.append(f"contrast={1.0 + float(c):.3f}")
    if isinstance(s, (int, float)) and abs(s) > 0.001:
        # saturation: 1.0 = unchanged; map [-1, 1] → [0, 2]
        eq_parts.append(f"saturation={1.0 + float(s):.3f}")
    if eq_parts:
        parts.append("eq=" + ":".join(eq_parts))
    f = edits.get("filter")
    if f and f != "none":
        lut_path = os.path.join(_LUT_DIR, f"{f}.cube")
        if os.path.exists(lut_path):
            parts.append(f"lut3d={shlex.quote(lut_path)}")
    return ",".join(parts)


def preprocess_with_edits(src_path: str, edits: dict,
                          out_dir: Optional[str] = None) -> str:
    """Apply edits to `src_path` and return the path to the processed file.
    No-op (returns src_path) when edits is empty / produces an empty filter
    chain. Output goes under `out_dir` (defaulted to a tempdir).
    The processed file preserves the original codec family — video stays
    video (h264/aac), images stay image."""
    chain = build_edits_filter_chain(edits or {})
    if not chain:
        return src_path
    out_dir = out_dir or tempfile.mkdtemp(prefix="edits_")
    base = os.path.basename(src_path)
    name, ext = os.path.splitext(base)
    ext_low = ext.lower()
    is_video = ext_low in (".mp4", ".mov", ".webm", ".mkv", ".m4v")
    out_path = os.path.join(out_dir, f"{name}_edit{ext if is_video else '.jpg'}")
    if is_video:
        cmd = ["ffmpeg", "-y", "-i", src_path,
               "-vf", chain,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "copy",
               out_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", src_path,
               "-vf", chain,
               "-frames:v", "1", "-q:v", "2",
               out_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[social_render] preprocess_with_edits failed for {src_path}: {e}", flush=True)
        return src_path
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return src_path
    return out_path


def _beat_timestamps(music_path: str) -> list:
    """Return a sorted list of beat timestamps (seconds) using librosa.
    Returns [] on any failure — callers must handle missing beats."""
    try:
        import librosa
        y, sr = librosa.load(music_path, sr=22050, mono=True)
        _, beats = librosa.beat.beat_track(y=y, sr=sr)
        return [float(t) for t in librosa.frames_to_time(beats, sr=sr)]
    except Exception as e:
        print(f"[social_render] beat tracking failed: {e}", flush=True)
        return []


def _snap_outpoints_to_beats(clips: list, beats: list) -> list:
    """For each clip, set outpoint to the nearest beat after the running total.
    Mutates clip dicts in place; returns the new list."""
    if not beats:
        return clips
    cursor = 0.0
    for c in clips:
        in_s = float(c.get("in_seconds") or 0.0)
        cur_out = c.get("out_seconds")
        if cur_out is None:
            # No prior outpoint — give it a default 3s
            cur_out = in_s + 3.0
        nominal_clip_len = float(cur_out) - in_s
        target = cursor + nominal_clip_len
        # Nearest beat ≥ target (don't shorten below 0.5s)
        candidates = [b for b in beats if b >= cursor + 0.5]
        if not candidates:
            cursor = target
            continue
        snapped = min(candidates, key=lambda b: abs(b - target))
        new_len = snapped - cursor
        c["out_seconds"] = in_s + new_len
        cursor = snapped
    return clips


def render_video(srcs, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60,
                 lut_path: Optional[str] = None,
                 logo_path: Optional[str] = None,
                 logo_position: str = "br",
                 logo_opacity: float = 0.7,
                 subtitles_path: Optional[str] = None,
                 music_path: Optional[str] = None,
                 music_volume_db: float = -18.0,
                 voiceover_path: Optional[str] = None,
                 voiceover_volume_db: float = -14.0,
                 intro_path: Optional[str] = None,
                 outro_path: Optional[str] = None,
                 ken_burns: bool = True,
                 beat_sync: bool = False) -> str:
    if not srcs:
        raise ValueError("no sources")
    clips = []
    for s in srcs:
        if isinstance(s, str):
            clips.append({"path": s, "in_seconds": None, "out_seconds": None})
        else:
            clips.append(s)
    if not clips:
        raise ValueError("no sources")
    if intro_path and os.path.exists(intro_path):
        clips = [{"path": intro_path, "in_seconds": None, "out_seconds": None}] + clips
    if outro_path and os.path.exists(outro_path):
        clips = clips + [{"path": outro_path, "in_seconds": None, "out_seconds": None}]

    if beat_sync and music_path and os.path.exists(music_path):
        beats = _beat_timestamps(music_path)
        if beats:
            clips = _snap_outpoints_to_beats(clips, beats)

    w, h = _ffprobe(clips[0]["path"])
    g = build_filter_graph(
        w, h, platform, fill_mode, hook_text, brand_corner,
        lut_path=lut_path, logo_path=logo_path,
        logo_position=logo_position, logo_opacity=logo_opacity,
        subtitles_path=subtitles_path,
        ken_burns=ken_burns,
    )

    tmpdir = os.path.dirname(out) or "."
    fd, list_path = tempfile.mkstemp(suffix=".concat.txt", dir=tmpdir, text=True)
    try:
        with os.fdopen(fd, "w") as f:
            for c in clips:
                f.write(f"file {shlex.quote(os.path.abspath(c['path']))}\n")
                if c.get("in_seconds") is not None:
                    f.write(f"inpoint {float(c['in_seconds'])}\n")
                if c.get("out_seconds") is not None:
                    f.write(f"outpoint {float(c['out_seconds'])}\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
        ]
        audio_inputs = []
        if music_path and os.path.exists(music_path):
            cmd += ["-stream_loop", "-1", "-i", music_path]
            audio_inputs.append(("music", len(audio_inputs) + 1))
        if voiceover_path and os.path.exists(voiceover_path):
            cmd += ["-i", voiceover_path]
            audio_inputs.append(("vo", len(audio_inputs) + 1))

        if audio_inputs:
            audio_parts = ["[0:a]volume=1.0[a0]"]
            mix_inputs = ["[a0]"]
            for label, idx in audio_inputs:
                if label == "music":
                    db = music_volume_db
                    audio_parts.append(f"[{idx}:a]volume={10**(db/20):.4f}[am]")
                    mix_inputs.append("[am]")
                elif label == "vo":
                    db = voiceover_volume_db
                    audio_parts.append(f"[{idx}:a]volume={10**(db/20):.4f}[av]")
                    mix_inputs.append("[av]")
            has_music = any(l == "music" for l, _ in audio_inputs)
            has_vo = any(l == "vo" for l, _ in audio_inputs)
            if has_music and has_vo:
                audio_parts.append("[am][av]sidechaincompress=threshold=0.05:ratio=8:attack=10:release=200[amd]")
                mix_inputs = [x if x != "[am]" else "[amd]" for x in mix_inputs]
            audio_parts.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=2[aout]")
            audio_parts.append("[aout]loudnorm=I=-14:LRA=11:TP=-1.0[afinal]")
            audio_parts.append(f"[0:v]{g}[vfinal]")
            cmd += [
                "-filter_complex", ";".join(audio_parts),
                "-map", "[vfinal]", "-map", "[afinal]",
            ]
        else:
            cmd += ["-vf", g]

        cmd += [
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", str(max_seconds),
            out,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)
    return out


def extract_cover(src: str, out: str, t_seconds: float = 0.5) -> str:
    cmd = ["ffmpeg", "-y", "-ss", str(t_seconds), "-i", src,
           "-frames:v", "1", "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
