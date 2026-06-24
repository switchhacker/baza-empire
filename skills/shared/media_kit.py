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


import re
import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Substrings that disqualify a model for copywriting.
_BAD_MODEL = ("cloud", "-vl", "vision", "ocr", "coder", "embed", "minicpm")
# Preference order: first substring match wins a higher rank.
_PREF = ("gemma4:26b", "qwen3.6:27b", "nemotron", "gemma4:12b",
         "ministral", "gemma4:e4b", "lfm2", "gemma4")


def pick_copy_model():
    """Pick the strongest installed LOCAL general chat model. None if unreachable."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=6)
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
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
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
        out = _template_copy(brief, brand)
        out["model"] = "template"
        return out
