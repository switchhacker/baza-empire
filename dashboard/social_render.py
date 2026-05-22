"""Render pipeline for social_studio. Pure functions, ffmpeg + PIL."""
from __future__ import annotations

import os
import shlex
import subprocess
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
        safe = hook_text.replace("'", r"\\'")
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
    w, h = r.stdout.strip().split("x")
    return int(w), int(h)


def render_still(src: str, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred") -> str:
    w, h = _ffprobe(src)
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", g, "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def render_video(srcs: List[str], out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60) -> str:
    """Concat sources, re-encode to target dims, optional hook overlay."""
    if not srcs:
        raise ValueError("no sources")
    w, h = _ffprobe(srcs[0])
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    tmpdir = os.path.dirname(out) or "."
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w") as f:
        for s in srcs:
            f.write(f"file {shlex.quote(os.path.abspath(s))}\n")
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
    return out


def extract_cover(src: str, out: str, t_seconds: float = 0.5) -> str:
    cmd = ["ffmpeg", "-y", "-ss", str(t_seconds), "-i", src,
           "-frames:v", "1", "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
