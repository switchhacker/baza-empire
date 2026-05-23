"""Settings + brand kit accessors + prompt loader for social_studio."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))


def _settings_dir() -> str:
    return os.environ.get("BAZA_SOCIAL_SETTINGS_DIR", _HERE)


def _settings_path() -> str:
    return os.path.join(_settings_dir(), "social_settings.json")


def _brand_kit_path() -> str:
    return os.path.join(_settings_dir(), "social_brand_kit.json")


def _prompts_dir() -> str:
    return os.path.join(_HERE, "prompts", "social")


DEFAULTS_SETTINGS: Dict[str, Any] = {
    "default_copy_model": "gpt-oss:20b",
    "fast_copy_model": "gemma3:12b",
    "vision_model": "qwen3-vl:latest",
    "tts_engine": "piper",
    "cloud_models_enabled": False,
    "cloud_copy_model": "gpt-oss:120b-cloud",
    "autopilot_master": False,
    "daily_post_cap": 4,
    "cool_down_days": 14,
    "burn_in_subtitles_default": True,
    "translation_targets": ["es"],
}

DEFAULTS_BRAND: Dict[str, Any] = {
    "logo_path": "static/social/brand/logo.png",
    "primary_color": "#10b981",
    "secondary_color": "#0e0e1e",
    "font_default": "Inter-Bold",
    "intro_clip_path": None,
    "outro_clip_path": None,
    "hashtag_floor": ["#allhomebuilding", "#ahbco", "#newyorkhomes"],
    "first_comment_floor": "—\nDM for a free estimate.",
    "hic_number": "",
    "founded_year": "",
}


def _read_json_or_default(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(default, f, indent=2)
        return dict(default)
    with open(path) as f:
        data = json.load(f)
    merged = dict(default)
    merged.update(data)
    return merged


def load_settings() -> Dict[str, Any]:
    return _read_json_or_default(_settings_path(), DEFAULTS_SETTINGS)


def save_settings(s: Dict[str, Any]) -> None:
    with open(_settings_path(), "w") as f:
        json.dump(s, f, indent=2)


def load_brand_kit() -> Dict[str, Any]:
    return _read_json_or_default(_brand_kit_path(), DEFAULTS_BRAND)


def save_brand_kit(b: Dict[str, Any]) -> None:
    with open(_brand_kit_path(), "w") as f:
        json.dump(b, f, indent=2)


def load_prompt(name: str) -> str:
    path = os.path.join(_prompts_dir(), f"{name}.md")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"[Prompt '{name}' not found at {path}]"


import re


def bootstrap_brand_from_sq_bundle(sq_dir: str) -> Dict[str, Any]:
    """Read sq_bundle HTML files for HIC# + founding year. Idempotently
    fills hic_number / founded_year on the brand kit when they are empty.
    Returns the (possibly updated) brand kit. Skips if sq_dir doesn't exist."""
    b = load_brand_kit()
    if not os.path.isdir(sq_dir):
        return b
    text = ""
    for fn in os.listdir(sq_dir):
        if fn.endswith(".html"):
            try:
                with open(os.path.join(sq_dir, fn)) as f:
                    text += "\n" + f.read()
            except Exception:
                continue
    m_hic = re.search(r"HIC#?\s*([0-9A-Z-]+)", text, re.IGNORECASE)
    m_year = re.search(r"(?:Est\.?|Founded|since)\s*(20\d{2}|19\d{2})", text, re.IGNORECASE)
    changed = False
    if m_hic and not b.get("hic_number"):
        b["hic_number"] = m_hic.group(1)
        changed = True
    if m_year and not b.get("founded_year"):
        b["founded_year"] = m_year.group(1)
        changed = True
    if changed:
        save_brand_kit(b)
    return b
