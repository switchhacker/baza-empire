"""Render pipeline for social_studio. Pure functions, ffmpeg + PIL."""
from __future__ import annotations

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
                       brand_corner: bool = False) -> str:
    out_w, out_h = target_dims(platform)
    src_aspect = in_w / max(in_h, 1)
    tgt_aspect = out_w / out_h
    parts = []
    if src_aspect > tgt_aspect:
        # Wider than target → either crop or pad
        if fill_mode == "blurred":
            parts.append(
                f"split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},gblur=sigma=24[bgb];"
                f"[fg]scale={out_w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
            )
        else:  # letterbox / brand color / crop
            parts.append(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    else:
        # Taller or same → cover crop
        parts.append(
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}"
        )
    if hook_text:
        # Strip drawtext expansion characters and escape filter-graph special chars.
        # Order matters: backslash → quote → colon → comma → percent-brace.
        safe = (
            hook_text
            .replace("\\", "\\\\")
            .replace("'", r"\'")
            .replace(":", r"\:")
            .replace(",", r"\,")
            .replace("%{", "%%{")  # neutralize drawtext format expansion
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


def render_video(srcs, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60) -> str:
    """Concat sources, re-encode to target dims, optional hook overlay.
    srcs may be a list of paths (legacy) or a list of dicts
    {path, in_seconds, out_seconds}. Trim values applied per-clip via
    ffmpeg's concat demuxer with inpoint/outpoint directives."""
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
    w, h = _ffprobe(clips[0]["path"])
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
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
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", g,
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
