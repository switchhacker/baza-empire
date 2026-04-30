"""Controlled vocabulary for image attributes.

The classifier prompt asks qwen3-vl to populate this exact key set with a
value from the given list. `normalize()` coerces the model's output into a
known value or "unknown" — never raises so a single bad image doesn't
break the indexer loop.
"""
from __future__ import annotations

from typing import Iterable

VOCAB: dict[str, set[str]] = {
    "image_type":     {"person", "object", "scene", "mixed", "text", "meme", "unknown"},
    "person_count":   {"0", "1", "2", "3+", "unknown"},
    "gender":         {"female", "male", "androgynous", "unknown"},
    "age_band":       {"child", "teen", "young-adult", "adult", "senior", "unknown"},
    "hair_color":     {"blonde", "brown", "black", "red", "gray", "dyed-other", "unknown"},
    "hair_style":     {"long", "short", "medium", "up", "bald", "covered", "unknown"},
    "build":          {"slim", "athletic", "average", "curvy", "heavy", "unknown"},
    "pose":           {"standing", "sitting", "lying", "crouching", "walking",
                       "dancing", "action", "unknown"},
    "viewpoint":      {"front", "back", "left-profile", "right-profile",
                       "three-quarter", "top", "unknown"},
    "mood":           {"neutral", "smiling", "serious", "surprised",
                       "pensive", "playful", "unknown"},
    "clothing_style": {"casual", "formal", "swimwear", "sportswear",
                       "lingerie", "costume", "none", "unknown"},
    "setting":        {"indoor", "outdoor-urban", "outdoor-nature",
                       "beach", "studio", "vehicle", "unknown"},
    "nsfw":           {"safe", "suggestive", "explicit", "unknown"},
    "parts_visible":  set(),   # special-cased in normalize(); set() is sentinel
}

# Body parts the cropper might extract; not constrained by VOCAB because we
# may add parts later (toes, ears) without breaking inference.
PART_VOCAB: set[str] = {
    "face", "eye", "eyes", "lips", "nose", "ear",
    "torso", "arm", "hand", "fingers",
    "leg", "thigh", "knee", "calf", "foot", "feet", "toes",
    "hair",
}

REQUIRED_KEYS: tuple[str, ...] = (
    "image_type", "person_count", "gender", "pose", "mood",
    "setting", "parts_visible", "nsfw",
)


def normalize(key: str, value) -> str:
    """Coerce a value to its canonical lowercase form. Unknown values for
    a known key collapse to 'unknown'. Unknown keys pass through trimmed."""
    if key == "parts_visible":
        if isinstance(value, (list, tuple)):
            items = [str(v).strip().lower() for v in value]
        else:
            items = [s.strip().lower() for s in str(value).split(",")]
        items = [s for s in items if s]
        return ",".join(items)

    s = "" if value is None else str(value).strip().lower()
    allowed = VOCAB.get(key)
    if allowed is None:
        return s  # unknown key — pass through
    if s in allowed:
        return s
    return "unknown"
