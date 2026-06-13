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

raw_prompt = args.get("prompt", "a beautiful landscape, photorealistic, 8k") or ""
if not raw_prompt.strip():
    # Refuse silently — caller passed an empty prompt; running with just
    # ControlNet edges + model bias produced gender drift in the past.
    print(json.dumps({"error": "Empty prompt — refusing to generate from edges + model bias alone."}))
    sys.exit(1)
prompt_lower = raw_prompt.lower()

# Explicit style override from caller (e.g. "oil_painting") takes precedence
# over keyword detection in the prompt — the LLM-drafted prompt may not
# contain the keywords but we still know the intent.
explicit_style = (args.get("style") or "").lower()
subject_gender = (args.get("subject_gender") or "").lower()

# ── Style detection ─────────────────────────────────────────────────────────
# Painted/illustrated styles need a different checkpoint AND prompt steering
# than photorealism, otherwise the photo-realism models render "oil on canvas
# portrait" as a red-carpet headshot.
PAINTING_KEYWORDS = (
    "oil on canvas", "oil painting", "oil-on-canvas", "watercolor", "watercolour",
    "acrylic painting", "gouache", "ink wash", "pen and ink", "charcoal",
    "pastel", "impressionist", "expressionist", "renaissance", "baroque",
    "rembrandt", "sargent", "vermeer", "van gogh", "monet",
    "illustration", "illustrated", "concept art", "matte painting",
    "anime", "manga", "cartoon", "comic", "graphic novel", "storybook",
    "sketch", "etching", "lithograph", "woodcut", "engraving",
    "painterly", "brush strokes", "brush stroke", "canvas texture", "gallery",
)
is_painting = explicit_style in ("oil_painting", "painting", "illustration") or \
              any(kw in prompt_lower for kw in PAINTING_KEYWORDS)

PORTRAIT_KEYWORDS = (
    "avatar", "portrait", "headshot", "head shot", "bust",
    "character design", "character sheet", "face study",
)
is_portrait = any(kw in prompt_lower for kw in PORTRAIT_KEYWORDS)

needs_consistency = any(kw in prompt_lower for kw in PAIR_KEYWORDS)

prompt = raw_prompt
extra_negative_parts = []

if is_painting:
    if "oil" in prompt_lower or "painterly" in prompt_lower or "impressionist" in prompt_lower:
        style_suffix = (
            ", oil on canvas, traditional oil painting, visible brush strokes, "
            "rich impasto texture, canvas weave, painterly, masterwork, museum quality, "
            "John Singer Sargent style, Rembrandt lighting, gallery lit, "
            "fine art, classical portraiture"
        )
    else:
        style_suffix = ", traditional artwork, hand-painted, illustrated, fine art, gallery quality"
    prompt = raw_prompt + style_suffix
    extra_negative_parts.append(
        "photograph, photo, photorealistic, photorealism, dslr, raw photo, "
        "cinematic still, film still, hdr, 4k photo, 8k photo, candid photo, "
        "red carpet, paparazzi, celebrity headshot, magazine photo, instagram, "
        "sharp focus photography, bokeh, lens flare, smooth digital render, "
        "3d render, octane render, cgi, plastic skin, airbrushed"
    )

if is_portrait:
    extra_negative_parts.append(
        "extra fingers, missing fingers, malformed hands, asymmetric eyes, "
        "cross-eyed, lazy eye, distorted face, two heads, multiple people, crowd"
    )

if subject_gender == "male":
    extra_negative_parts.append(
        "woman, female, girl, feminine, breasts, cleavage, lipstick, makeup, "
        "long flowing hair, eyeshadow, mascara, earrings, necklace, dress, "
        "blouse, skirt, androgynous"
    )
elif subject_gender == "female":
    extra_negative_parts.append(
        "man, male, boy, masculine, beard, mustache, stubble, adam's apple, "
        "muscular jaw, broad shoulders, suit and tie, androgynous"
    )

if needs_consistency:
    prompt = prompt + CONSISTENCY_SUFFIX
    extra_negative_parts.append(CONSISTENCY_NEGATIVE)

base_negative = "blurry, distorted, low quality, watermark, text, nsfw, ugly, deformed, extra limbs, bad anatomy, out of frame"
if extra_negative_parts:
    base_negative = base_negative + ", " + ", ".join(extra_negative_parts)

if is_painting:
    default_cfg = 9.0
    default_steps = 40
elif needs_consistency:
    default_cfg = 8.5
    default_steps = 35
else:
    default_cfg = 7.0
    default_steps = 30

# Turbo/Lightning SDXL variants need very few steps + low CFG, otherwise they
# blow VRAM on activations and produce burnt/fried output. Auto-detect.
_pending_model_name = (args.get("model") or "").lower()

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

# ── Detect best available model (style-aware) ───────────────────────────────
def pick_model(prefer_painted: bool = False):
    try:
        r = requests.get(f"{SDWEBUI_URL}/sdapi/v1/sd-models", timeout=5)
        models = r.json()
        # DreamShaper handles painted/illustrated styles much better than
        # photoreal models like RealVis or Juggernaut, which collapse
        # "oil painting portrait" into red-carpet headshots.
        if prefer_painted:
            priority = ["dreamshaper", "sdxl", "base", "juggernaut", "realvis"]
        else:
            priority = ["juggernaut", "realvis", "dreamshaper", "sdxl", "base"]
        for kw in priority:
            for m in models:
                if kw.lower() in m.get("model_name","").lower():
                    return m["title"]
        return models[0]["title"] if models else None
    except:
        return None

model = args.get("model") or pick_model(prefer_painted=is_painting)

# Auto-tune for Turbo / Lightning models (use few steps + low CFG)
_model_lower = (model or "").lower()
_is_turbo = any(tag in _model_lower for tag in ("turbo", "lightning", "lcm", "hyper"))
if _is_turbo and "steps" not in args and "cfg_scale" not in args:
    steps = 8
    cfg_scale = 2.0
    sampler = args.get("sampler", "DPM++ SDE Karras")

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

# ── DataHub upload intentionally disabled ─────────────────────────────────────
# Telegram-driven generations are throwaway: Serge asks, Sam generates, Sam replies.
# We do NOT register these into the proj-ahb123 artifacts / Data Hub. Images still
# live on disk at OUTPUT_DIR if needed. (Removed 2026-06-12 per Serge.)

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
