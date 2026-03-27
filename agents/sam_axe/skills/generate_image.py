#!/usr/bin/env python3
"""
Sam Axe — generate_image skill
Calls SD WebUI Forge API (txt2img) and saves the result to disk.
Returns: JSON with image_path and metadata.
"""
import os
import sys
import json
import base64
import time
import hashlib
import requests
from pathlib import Path

SDWEBUI_URL = os.environ.get("SDWEBUI_URL", "http://localhost:7860")
OUTPUT_DIR  = os.environ.get("IMAGE_OUTPUT_DIR", "/mnt/empirepool/media/generated")
FALLBACK_DIR = os.path.expanduser("~/stable-diffusion-webui-forge/outputs/txt2img-images")

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

prompt    = args.get("prompt", "a beautiful landscape, photorealistic, 8k")
negative  = args.get("negative_prompt",
    "blurry, distorted, low quality, watermark, text, nsfw, ugly, deformed, "
    "extra limbs, bad anatomy, out of frame")
steps     = int(args.get("steps", 30))
width     = int(args.get("width", 1024))
height    = int(args.get("height", 1024))
cfg_scale = float(args.get("cfg_scale", 7.0))
sampler   = args.get("sampler", "DPM++ 2M Karras")

# ── Check SD WebUI is up ────────────────────────────────────────────────────
try:
    health = requests.get(f"{SDWEBUI_URL}/sdapi/v1/options", timeout=5)
    if health.status_code != 200:
        print(json.dumps({"error": f"SD WebUI not ready (status {health.status_code}). Start baza-sd-webui service."}))
        sys.exit(1)
except requests.exceptions.ConnectionError:
    print(json.dumps({"error": "SD WebUI is offline. Run: sudo systemctl start baza-sd-webui"}))
    sys.exit(1)
except requests.exceptions.Timeout:
    print(json.dumps({"error": "SD WebUI timed out — still loading? Try again in 30s."}))
    sys.exit(1)

# ── Detect best available model (prefer SDXL) ───────────────────────────────
def pick_model():
    try:
        r = requests.get(f"{SDWEBUI_URL}/sdapi/v1/sd-models", timeout=5)
        models = r.json()
        # Prefer Juggernaut, then RealVis, then DreamShaper, then any SDXL
        priority = ["juggernaut", "realvis", "dreamshaper", "sdxl", "base"]
        for kw in priority:
            for m in models:
                if kw.lower() in m.get("model_name","").lower():
                    return m["title"]
        return models[0]["title"] if models else None
    except:
        return None

model = args.get("model") or pick_model()

# ── Set model if specified ───────────────────────────────────────────────────
if model:
    try:
        requests.post(f"{SDWEBUI_URL}/sdapi/v1/options",
                      json={"sd_model_checkpoint": model}, timeout=30)
    except:
        pass

# ── Generate ─────────────────────────────────────────────────────────────────
payload = {
    "prompt":          prompt,
    "negative_prompt": negative,
    "steps":           steps,
    "width":           width,
    "height":          height,
    "cfg_scale":       cfg_scale,
    "sampler_name":    sampler,
    "batch_size":      1,
    "n_iter":          1,
    "save_images":     True,
    "send_images":     True,
    "override_settings": {
        "sd_vae": "sdxl_vae.safetensors",
    }
}

try:
    r = requests.post(f"{SDWEBUI_URL}/sdapi/v1/txt2img", json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()
except requests.exceptions.Timeout:
    print(json.dumps({"error": "Image generation timed out (>5 min). Try fewer steps or smaller size."}))
    sys.exit(1)
except Exception as e:
    print(json.dumps({"error": f"Generation failed: {str(e)}"}))
    sys.exit(1)

# ── Save image ───────────────────────────────────────────────────────────────
images = data.get("images", [])
if not images:
    print(json.dumps({"error": "No images returned from SD WebUI."}))
    sys.exit(1)

os.makedirs(OUTPUT_DIR, exist_ok=True)
timestamp = int(time.time())
short_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
safe_name  = "_".join(prompt.lower().split()[:6])
safe_name  = "".join(c if c.isalnum() or c == "_" else "" for c in safe_name)
filename   = f"{timestamp}_{safe_name}_{short_hash}.png"
filepath   = os.path.join(OUTPUT_DIR, filename)

with open(filepath, "wb") as f:
    f.write(base64.b64decode(images[0]))

# ── Also save to artifacts dashboard ────────────────────────────────────────
try:
    artifacts_dir = os.path.join(
        os.path.dirname(__file__), "../../../../dashboard/artifacts/sam-generated"
    )
    os.makedirs(artifacts_dir, exist_ok=True)
    import shutil
    shutil.copy2(filepath, os.path.join(artifacts_dir, filename))
except:
    pass

# ── Result ───────────────────────────────────────────────────────────────────
print(json.dumps({
    "image_path": filepath,
    "filename":   filename,
    "width":      width,
    "height":     height,
    "steps":      steps,
    "model":      model or "default",
    "prompt":     prompt,
}))
