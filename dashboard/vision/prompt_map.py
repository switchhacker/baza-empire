"""Map a taxonomy path to an SD Forge txt2img prompt + negative prompt.

Heuristic-only — Specter does not yet learn from the DB. Each prompt is
phrased to produce a clean, well-lit, photorealistic reference image; the
negative filters watermarks, weird anatomy, and low quality.
"""
from __future__ import annotations

NEGATIVE = (
    "watermark, text, signature, logo, low quality, blurry, cropped, "
    "extra fingers, fused fingers, bad anatomy, bad hands, deformed, "
    "mutation, extra limbs, asymmetric eyes"
)

GENDER_NOUN = {"female": "woman", "male": "man", "androgynous": "person"}


def _person_prompt(parts: list[str]) -> dict:
    # parts: e.g. ['Catalogue', 'People', 'Female', 'Blonde']
    gender = parts[2].lower() if len(parts) > 2 else "female"
    noun = GENDER_NOUN.get(gender, "person")
    extras = []
    if len(parts) > 3:
        attr = parts[3].lower()
        if attr in ("blonde", "brunette", "black", "red"):
            color = {"brunette": "brown"}.get(attr, attr)
            extras.append(f"{color} hair")
        elif attr == "gray":
            extras.append("gray hair")
    extras_s = ", " + ", ".join(extras) if extras else ""
    return {
        "prompt": f"professional photo of a {noun}{extras_s}, "
                  f"neutral expression, studio lighting, photorealistic, sharp focus",
        "negative": NEGATIVE,
    }


def _face_crop_prompt(parts: list[str]) -> dict:
    # /Catalogue/Faces/Female/Eyes
    gender = parts[2].lower() if len(parts) > 2 else "female"
    part = parts[3].lower() if len(parts) > 3 else "face"
    noun = GENDER_NOUN.get(gender, "person")
    if part == "eyes":
        return {"prompt": f"close-up macro photograph of {noun}'s eye, sharp focus, "
                          f"natural lighting, photorealistic", "negative": NEGATIVE}
    if part == "lips":
        return {"prompt": f"close-up macro photograph of {noun}'s lips, sharp focus, "
                          f"natural lighting, photorealistic", "negative": NEGATIVE}
    return {"prompt": f"close-up portrait of a {noun}'s face, photorealistic, "
                      f"neutral background, soft lighting", "negative": NEGATIVE}


def _body_part_prompt(parts: list[str]) -> dict:
    part = parts[2].lower() if len(parts) > 2 else "torso"
    return {"prompt": f"close-up macro photograph of human {part}, photorealistic, "
                      f"clean background, studio lighting", "negative": NEGATIVE}


def _style_prompt(parts: list[str]) -> dict:
    style = parts[2].lower() if len(parts) > 2 else "casual"
    return {"prompt": f"flat-lay photograph of {style} clothing on a neutral "
                      f"background, photorealistic, well-lit, catalog style",
            "negative": NEGATIVE}


def _scene_prompt(parts: list[str]) -> dict:
    setting = parts[2].lower() if len(parts) > 2 else "outdoor"
    return {"prompt": f"photograph of an empty {setting} scene with no people, "
                      f"photorealistic, natural lighting, sharp focus",
            "negative": NEGATIVE + ", person, people, crowd, human"}


def _mood_prompt(parts: list[str]) -> dict:
    mood = parts[2].lower() if len(parts) > 2 else "neutral"
    return {"prompt": f"portrait photograph of a person with a {mood} expression, "
                      f"photorealistic, soft lighting", "negative": NEGATIVE}


def prompt_for_path(path: str) -> dict:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "Catalogue":
        raise ValueError(f"unsupported path for generation: {path}")
    section = parts[1]
    if section == "People":
        return _person_prompt(parts)
    if section == "Faces":
        return _face_crop_prompt(parts)
    if section == "Body":
        return _body_part_prompt(parts)
    if section == "Style":
        return _style_prompt(parts)
    if section == "Scenes":
        return _scene_prompt(parts)
    if section == "Mood":
        return _mood_prompt(parts)
    raise ValueError(f"no prompt template for {path}")
