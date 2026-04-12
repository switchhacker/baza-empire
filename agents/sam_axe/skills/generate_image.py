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

# ── Consistency Enhancement ─────────────────────────────────────────────────
# SD models generate each region independently, causing mismatched pairs
# (different faucets on same vanity, mismatched chairs, inconsistent bricks).
# We detect prompts with multiples and inject consistency-enforcing language.

PAIR_KEYWORDS = [
    "faucet", "faucets", "handle", "handles", "knob", "knobs",
    "chair", "chairs", "stool", "stools", "seat", "seats",
    "lamp", "lamps", "light", "lights", "pendant", "pendants", "sconce", "sconces",
    "pillow", "pillows", "cushion", "cushions",
    "cabinet", "cabinets", "drawer", "drawers", "door", "doors",
    "window", "windows", "shutter", "shutters",
    "column", "columns", "pillar", "pillars",
    "tile", "tiles", "brick", "bricks", "panel", "panels",
    "shelf", "shelves", "towel", "towels",
    "mirror", "mirrors", "frame", "frames",
    "vase", "vases", "pot", "pots", "planter", "planters",
    "barstool", "barstools", "dining chair", "dining chairs",
    "nightstand", "nightstands", "end table", "end tables",
    "pair", "pairs", "matching", "set", "twin", "double", "two", "three", "four",
]

CONSISTENCY_SUFFIX = (
    ", all matching items are identical in design style color and material, "
    "uniform consistent symmetrical, matching set, cohesive design, "
    "same pattern same finish same style throughout, "
    "no mismatched elements, unified aesthetic"
)

CONSISTENCY_NEGATIVE = (
    "mismatched, inconsistent, asymmetric design, different styles mixed, "
    "clashing patterns, varied finishes on same object type, "
    "non-uniform, eclectic mishmash, different colors on matching items, "
    "two different styles, mixed patterns, patchwork, uneven, "
    "different brick patterns, different lamp styles, mismatched pair, "
    "feng shui random mix, each one different"
)

raw_prompt = args.get("prompt", "a beautiful landscape, photorealistic, 8k")
prompt_lower = raw_prompt.lower()

# Detect if prompt involves multiple/paired items
needs_consistency = any(kw in prompt_lower for kw in PAIR_KEYWORDS)

if needs_consistency:
    prompt = raw_prompt + CONSISTENCY_SUFFIX
    base_negative = (
        "blurry, distorted, low quality, watermark, text, nsfw, ugly, deformed, "
        "extra limbs, bad anatomy, out of frame, " + CONSISTENCY_NEGATIVE
    )
    # Bump CFG for tighter prompt adherence on consistency-sensitive images
    default_cfg = 8.5
    default_steps = 35
else:
    prompt = raw_prompt
    base_negative = (
        "blurry, distorted, low quality, watermark, text, nsfw, ugly, deformed, "
        "extra limbs, bad anatomy, out of frame"
    )
    default_cfg = 7.0
    default_steps = 30

negative  = args.get("negative_prompt", base_negative)
steps     = int(args.get("steps", default_steps))
width     = int(args.get("width", 768))
height    = int(args.get("height", 768))
cfg_scale = float(args.get("cfg_scale", default_cfg))
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

# ── ControlNet — guided generation from reference image ─────────────────────
reference_image = args.get("reference_image", "")
controlnet_mode = args.get("controlnet_mode", "canny")

if reference_image and os.path.exists(reference_image):
    with open(reference_image, "rb") as rf:
        ref_b64 = base64.b64encode(rf.read()).decode()

    CN_MAP = {
        "canny":    ("canny",         "diffusers_xl_canny_full [2b69fca4]"),
        "depth":    ("depth_midas",   "diffusers_xl_depth_full [2f51180b]"),
        "openpose": ("openpose_full", "t2i-adapter_diffusers_xl_openpose [adfb64aa]"),
    }
    preprocessor, cn_model = CN_MAP.get(controlnet_mode, CN_MAP["canny"])

    payload.setdefault("alwayson_scripts", {})
    payload["alwayson_scripts"]["controlnet"] = {
        "args": [{
            "input_image": ref_b64,
            "module": preprocessor,
            "model": cn_model,
            "weight": float(args.get("controlnet_weight", 0.85)),
            "resize_mode": 1,
            "control_mode": 0,
            "guidance_start": 0.0,
            "guidance_end": 0.8,
            "pixel_perfect": True,
        }]
    }

# ── ADetailer — automatic face/hand enhancement ────────────────────────────
if args.get("adetailer", True):
    payload.setdefault("alwayson_scripts", {})
    payload["alwayson_scripts"]["ADetailer"] = {
        "args": [True, False, {
            "ad_model": "face_yolov8n.pt",
            "ad_confidence": 0.3,
            "ad_dilate_erode": 4,
            "ad_mask_blur": 4,
            "ad_denoising_strength": 0.4,
            "ad_inpaint_only_masked": True,
            "ad_inpaint_only_masked_padding": 32,
        }]
    }

# ── Generate variants one at a time (avoids Forge grid stitching) ───────────
num_variants = int(args.get("n_iter", 1)) or 1
all_images = []
plain_payload = {k: v for k, v in payload.items() if k != "alwayson_scripts"}

for vi in range(num_variants):
    fallbacks = [("full", payload), ("plain", plain_payload)]
    for label, attempt_payload in fallbacks:
        try:
            r = requests.post(f"{SDWEBUI_URL}/sdapi/v1/txt2img", json=attempt_payload, timeout=300)
            r.raise_for_status()
            data = r.json()
            imgs = data.get("images", [])
            if imgs:
                all_images.append(imgs[0])
            if label != "full":
                print(f"[variant {vi+1}: ran without extensions]", file=sys.stderr)
            break
        except requests.exceptions.Timeout:
            print(json.dumps({"error": "Image generation timed out (>5 min)."}))
            sys.exit(1)
        except Exception as e:
            if "500" in str(e) and label != "plain":
                continue
            print(json.dumps({"error": f"Generation failed: {str(e)}"}))
            sys.exit(1)

images = all_images
if not images:
    print(json.dumps({"error": "No images returned from SD WebUI."}))
    sys.exit(1)

os.makedirs(OUTPUT_DIR, exist_ok=True)
timestamp = int(time.time())
short_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
safe_name  = "_".join(prompt.lower().split()[:6])
safe_name  = "".join(c if c.isalnum() or c == "_" else "" for c in safe_name)

saved_paths = []
for idx, img_b64 in enumerate(images):
    variant_label = f"_v{idx+1}" if len(images) > 1 else ""
    filename = f"{timestamp}_{safe_name}_{short_hash}{variant_label}.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(img_b64))
    saved_paths.append(filepath)

# ── Register with artifacts dashboard ─────────────────────────────────────────
for fpath in saved_paths:
    try:
        import urllib.request
        DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
        boundary = "bazaartifactboundary"
        with open(fpath, "rb") as img_f:
            img_data = img_f.read()
        fname = os.path.basename(fpath)
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="project_id"\r\n\r\nproj-ahb123\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="sam_axe_{fname}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + img_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/artifacts/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception:
        pass

# ── Result ───────────────────────────────────────────────────────────────────
for i, p in enumerate(saved_paths):
    label = f"{'1st' if i==0 else '2nd'} try" if len(saved_paths) > 1 else ""
    print(f"{label}: {p}" if label else p)

print(json.dumps({
    "image_path": saved_paths[0] if saved_paths else "",
    "image_paths": saved_paths,
    "variant_count": len(saved_paths),
    "width":      width,
    "height":     height,
    "steps":      steps,
    "model":      model or "default",
    "prompt":     prompt,
}))
