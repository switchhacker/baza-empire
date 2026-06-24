#!/usr/bin/env python3
"""media_kit — shared helpers for AHBCO marketing/media super skills.

Brand source of truth, Pillow compositing, local-Ollama copywriting,
SD-WebUI backgrounds, binary artifact save, and Social Studio queueing.
All marketing skills import this module. Local-first, photo-first.
"""
import os, json, copy
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent.parent
BRAND_DIR     = FRAMEWORK_DIR / "agents" / "sam_axe" / "brand"
BRAND_PATH    = BRAND_DIR / "brand.json"
ASSETS_DIR    = BRAND_DIR / "assets"

# System fonts present on baza (verified): DejaVu (default) + Liberation (condensed alt).
_DEJAVU = "/usr/share/fonts/truetype/dejavu"
DEFAULT_BRAND = {
    "version": 1,
    "name": "All Home Building Co",
    "short_name": "AHBCO",
    "tagline": "Drown the competition.",
    "site": "https://ahb123.com",
    "colors": {
        "primary":   "#0A3D62",
        "secondary": "#1E90FF",
        "accent":    "#F39C12",
        "light":     "#F5F7FA",
        "dark":      "#13202E",
    },
    "fonts": {
        "headline": f"{_DEJAVU}/DejaVuSans-Bold.ttf",
        "body":     f"{_DEJAVU}/DejaVuSans.ttf",
    },
    "logo": "",          # absolute path once detected; "" => text wordmark fallback
    "voice": "confident, local, trustworthy, no jargon",
}


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_brand() -> dict:
    """Return brand.json merged over DEFAULT_BRAND (defaults fill any gaps)."""
    try:
        data = json.loads(BRAND_PATH.read_text())
    except Exception:
        data = {}
    return _deep_merge(DEFAULT_BRAND, data)


def save_brand(brand: dict) -> dict:
    BRAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRAND_PATH.write_text(json.dumps(brand, indent=2))
    return {"path": str(BRAND_PATH)}


import requests


def _ollama_url():
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Substrings that disqualify a model for copywriting.
_BAD_MODEL = ("cloud", "-vl", "vision", "ocr", "coder", "embed", "minicpm")
# Preference order: first substring match wins a higher rank.
_PREF = ("gemma4:26b", "qwen3.6:27b", "nemotron", "gemma4:12b",
         "ministral", "gemma4:e4b", "lfm2", "gemma4")


def pick_copy_model():
    """Pick the strongest installed LOCAL general chat model. None if unreachable."""
    try:
        r = requests.get(f"{_ollama_url()}/api/tags", timeout=6)
        if r.status_code != 200:
            return None
        names = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None
    cands = [n for n in names if not any(b in n.lower() for b in _BAD_MODEL)]
    if not cands:
        return None

    def rank(n):
        for i, p in enumerate(_PREF):
            if p in n:
                return i
        return len(_PREF) + 1
    cands.sort(key=rank)
    return cands[0]


def _ollama_chat(model, prompt, timeout=90):
    r = requests.post(f"{_ollama_url()}/api/chat", json={
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
    }, timeout=timeout)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def _template_copy(brief, brand):
    short = brand["short_name"]
    cap = f"{brief.strip().capitalize()} — done right by {short}. {brand['tagline']}"
    tags = ["#AHBCO", "#HomeBuilding", "#Remodel", "#Contractor", "#HomeImprovement"]
    return {"caption": cap, "hashtags": tags,
            "first_comment": f"Get a free quote from {short} today.",
            "model": "template"}


def write_copy(brief, brand, kind="caption"):
    """Generate {caption, hashtags[], first_comment, model} in brand voice.
    Local Ollama only; deterministic template fallback if no model reachable."""
    model = pick_copy_model()
    if not model:
        return _template_copy(brief, brand)
    prompt = (
        f"You are the marketing copywriter for {brand['name']} ({brand['short_name']}), "
        f"a home building & remodeling company. Brand voice: {brand['voice']}. "
        f"Tagline: {brand['tagline']}.\n"
        f"Write a {kind} for this brief: {brief}\n"
        f"Return ONLY a JSON object with keys: caption (string, <= 280 chars), "
        f"hashtags (array of 4-8 strings each starting with #), "
        f"first_comment (string)."
    )
    try:
        raw = _ollama_chat(model, prompt)
        data = json.loads(raw)
        caption = str(data.get("caption", "")).strip()
        tags = [str(t) for t in data.get("hashtags", []) if str(t).strip()]
        if not caption or not tags:
            raise ValueError("empty fields")
        return {"caption": caption, "hashtags": tags,
                "first_comment": str(data.get("first_comment", "")).strip(),
                "model": model}
    except Exception:
        return _template_copy(brief, brand)


from PIL import Image, ImageDraw, ImageFont

PLATFORMS = {
    "ig_square":     (1080, 1080),
    "ig_reel":       (1080, 1920),
    "ig_feed_square":(1080, 1080),
    "tiktok":        (1080, 1920),
    "fb":            (1200, 630),
    "yt_thumb":      (1280, 720),
    "flyer_portrait":(1275, 1650),   # 8.5x11 @ 150 dpi
    "ad_square":     (1080, 1080),
    "ad_landscape":  (1200, 628),
}


def new_canvas(platform, bg=None):
    w, h = PLATFORMS[platform]
    if bg is None:
        bg = hex_to_rgb(load_brand()["colors"]["dark"])
    return Image.new("RGB", (w, h), bg)


def load_photo(path, size, mode="cover"):
    """Open a photo and cover-fit (crop) it to exactly `size`."""
    img = Image.open(path).convert("RGB")
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def fit_font(draw, text, font_path, max_width, max_size, min_size=18):
    size = max_size
    while size > min_size:
        f = _font(font_path, size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return _font(font_path, min_size)


def _wrap(draw, text, font, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_headline(img, text, box, color, font_path, align="left",
                  max_size=120, shadow=True):
    """Draw auto-fitted, wrapped headline text inside box=(x0,y0,x1,y1)."""
    text = (text or "").strip()
    if not text:
        return img
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    max_w = x1 - x0
    font = fit_font(draw, max(text.split(" "), key=len) if text else text,
                    font_path, max_w, max_size)
    lines = _wrap(draw, text, font, max_w)
    line_h = (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 12
    y = y0
    for line in lines:
        if align == "center":
            x = x0 + (max_w - draw.textlength(line, font=font)) / 2
        else:
            x = x0
        if shadow:
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=color)
        y += line_h
    return img


def scrim(img, side="bottom", height_frac=0.4, color=(0, 0, 0), max_alpha=190):
    """Overlay a vertical gradient for text legibility (bottom or top)."""
    w, h = img.size
    band = int(h * height_frac)
    overlay = Image.new("RGBA", (w, band), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(band):
        a = int(max_alpha * (i / band)) if side == "bottom" else int(max_alpha * (1 - i / band))
        od.line([(0, i), (w, i)], fill=(color[0], color[1], color[2], a))
    y = h - band if side == "bottom" else 0
    base = img.convert("RGBA")
    base.alpha_composite(overlay, (0, y))
    img.paste(base.convert("RGB"))
    return img


def place_logo(img, brand, corner="br", margin=48, max_w=320):
    """Place the logo image; fall back to a text wordmark if no logo file."""
    w, h = img.size
    logo_path = brand.get("logo") or ""
    draw = ImageDraw.Draw(img)
    if logo_path and Path(logo_path).exists():
        logo = Image.open(logo_path).convert("RGBA")
        scale = min(max_w / logo.width, 1.0)
        logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
        lw, lh = logo.size
        x = margin if "l" in corner else w - lw - margin
        y = margin if "t" in corner else h - lh - margin
        base = img.convert("RGBA"); base.alpha_composite(logo, (x, y))
        img.paste(base.convert("RGB"))
    else:
        font = _font(brand["fonts"]["headline"], 44)
        text = brand["short_name"]
        tw = draw.textlength(text, font=font)
        th = font.getbbox(text)[3]
        x = margin if "l" in corner else w - tw - margin
        y = margin if "t" in corner else h - th - margin
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=hex_to_rgb(brand["colors"]["accent"]))
    return img
