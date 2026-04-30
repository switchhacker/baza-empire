"""Client for SD WebUI Forge txt2img endpoint at http://127.0.0.1:11435."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

SD_URL = "http://127.0.0.1:11435/sdapi/v1/txt2img"
TIMEOUT = 180


def txt2img(prompt: str, negative: str, *, width: int = 768, height: int = 1024,
            steps: int = 25, cfg: float = 6.5, seed: int = -1) -> bytes:
    """Returns the first generated image as bytes (PNG)."""
    payload = json.dumps({
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width, "height": height,
        "steps": steps, "cfg_scale": cfg,
        "seed": seed, "sampler_name": "DPM++ 2M Karras",
        "n_iter": 1, "batch_size": 1,
        "send_images": True, "save_images": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        SD_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read())
    images = data.get("images") or []
    if not images:
        raise RuntimeError("SD Forge returned no images")
    return base64.b64decode(images[0])


def save_generated(content: bytes, taxonomy_path: str) -> str:
    """Write to artifacts/.vision-generated/<bin-slug>/<ts>.png; returns abs_path."""
    slug = taxonomy_path.strip("/").replace("/", "_").lower()
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "artifacts", ".vision-generated", slug)
    os.makedirs(base, exist_ok=True)
    p = os.path.join(base, f"gen_{int(time.time())}.png")
    with open(p, "wb") as fh:
        fh.write(content)
    return p
