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
        data = json.loads(Path(BRAND_PATH).read_text())
    except Exception:
        data = {}
    return _deep_merge(DEFAULT_BRAND, data)


def save_brand(brand: dict) -> dict:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    Path(BRAND_PATH).write_text(json.dumps(brand, indent=2))
    return {"path": str(BRAND_PATH)}
