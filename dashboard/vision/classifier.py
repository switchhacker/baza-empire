"""Structured-attribute classifier on top of qwen3-vl via Ollama.

Produces ONE inference per image; parses the model's JSON response strictly
but defensively (strips code fences, finds the first `{...}` block) and
normalizes every value through dashboard.vision.vocab.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
from typing import Optional

from PIL import Image

from dashboard.vision.vocab import REQUIRED_KEYS, VOCAB, normalize

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3-vl:latest"
FALLBACK_MODEL = "llava:13b"
DOWNSCALE_PX = 384
PER_IMAGE_TIMEOUT = 90  # seconds

PROMPT = """You are an image cataloguer. Respond with ONLY a single JSON object, no prose, no thinking, no code fences.

Required keys (use exactly these names; values must come from the listed options):

image_type:     person | object | scene | mixed | text | meme | unknown
person_count:   0 | 1 | 2 | 3+ | unknown
gender:         female | male | androgynous | unknown
age_band:       child | teen | young-adult | adult | senior | unknown
hair_color:     blonde | brown | black | red | gray | dyed-other | unknown
hair_style:     long | short | medium | up | bald | covered | unknown
build:          slim | athletic | average | curvy | heavy | unknown
pose:           standing | sitting | lying | crouching | walking | dancing | action | unknown
viewpoint:      front | back | left-profile | right-profile | three-quarter | top | unknown
mood:           neutral | smiling | serious | surprised | pensive | playful | unknown
clothing_style: casual | formal | swimwear | sportswear | lingerie | costume | none | unknown
setting:        indoor | outdoor-urban | outdoor-nature | beach | studio | vehicle | unknown
nsfw:           safe | suggestive | explicit | unknown

parts_visible: array of strings from {face, eyes, lips, hair, torso, arm, hand, leg, foot}.
caption:       one natural-language sentence.
tags:          12 comma-separated keywords.

If no person is present, set all person attributes (gender, age_band, hair_color,
hair_style, build, pose, viewpoint, mood, clothing_style) to "unknown".
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class ClassifierError(RuntimeError):
    pass


class GPUContention(ClassifierError):
    pass


def _downscale_to_b64(path: str, max_px: int = DOWNSCALE_PX) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def parse_classifier_response(raw: str) -> dict:
    """Pull the first JSON object out of the model output, normalize every
    value, ensure all REQUIRED_KEYS are present. Raises ValueError on any
    irrecoverable shape problem."""
    if not raw:
        raise ValueError("empty response")
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        raise ValueError("no JSON object found in response")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("response is not an object")

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"missing required keys: {missing}")

    normalized: dict[str, str] = {}
    for k, v in data.items():
        if k in ("caption", "tags"):
            normalized[k] = ("" if v is None else str(v)).strip()
        else:
            normalized[k] = normalize(k, v)
    return normalized


def _post_ollama(b64: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_predict": 1500, "temperature": 0.2, "num_ctx": 3072},
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
        "think": False,  # Ollama 0.30+: qwen3-vl otherwise spends num_predict in `thinking`, content comes back empty
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PER_IMAGE_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return (data.get("message") or {}).get("content", "") or ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 500 and ("resource" in body.lower() or "failed to load" in body.lower()):
            raise GPUContention(body) from e
        raise ClassifierError(f"HTTP {e.code}: {body}") from e


def classify(path: str) -> tuple[dict, str]:
    """Run one classifier pass against `path`. Returns (normalized_attrs, model_id).
    Raises GPUContention to let the caller back off, or ClassifierError on hard errors."""
    b64 = _downscale_to_b64(path)
    last_err: Optional[Exception] = None
    for model in (MODEL, FALLBACK_MODEL):
        try:
            raw = _post_ollama(b64, model)
            return parse_classifier_response(raw), model
        except GPUContention:
            raise
        except (ValueError, ClassifierError) as e:
            last_err = e
    raise ClassifierError(f"both models failed: {last_err}")
