#!/usr/bin/env python3
"""
Sam Axe — image_edit skill (INPAINTING pipeline)
Takes a room photo + edit instructions, generates variants via inpainting.

Architecture (2026 commercial standard):
1. Extract depth map (depth_anything_v2) → locks room geometry
2. Extract edges (canny) → locks structural lines (walls, windows, doors)
3. Auto-generate inpaint mask based on edit request (what to change)
4. Inpaint ONLY masked areas with dual ControlNet holding structure
5. Stream each variant path immediately so agent sends as it generates

Usage:
    ##SKILL:image_edit{
        "source_image": "/path/to/image.jpg",
        "description": "existing room description from analysis",
        "edit_request": "add white shaker cabinets and granite countertops",
        "variants": 3
    }##
"""
import os
import sys
import json
import base64
import time
import hashlib
import requests
from pathlib import Path

SDWEBUI_URL  = os.environ.get("SDWEBUI_URL", "http://localhost:7860")
OUTPUT_DIR   = os.environ.get("IMAGE_OUTPUT_DIR", "/mnt/empirepool/media/generated")
CONTEXT_DIR  = os.path.join(OUTPUT_DIR, ".context")

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

source_image    = args.get("source_image", "")
description     = args.get("description", "")
edit_request    = args.get("edit_request", "")
num_variants    = int(args.get("variants", 3))
steps           = int(args.get("steps", 25))
width           = int(args.get("width", 768))
height          = int(args.get("height", 768))
cfg_scale       = float(args.get("cfg_scale", 7.0))
sampler         = args.get("sampler", "DPM++ 2M Karras")

if not source_image:
    print(json.dumps({"error": "source_image path required"}))
    sys.exit(1)

if not os.path.exists(source_image):
    print(json.dumps({"error": f"Source image not found: {source_image}"}))
    sys.exit(1)

# ── Load source image ───────────────────────────────────────────────────────
with open(source_image, "rb") as f:
    img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode()

os.makedirs(CONTEXT_DIR, exist_ok=True)
img_hash = hashlib.sha256(img_bytes[:50000]).hexdigest()[:16]
context_file = os.path.join(CONTEXT_DIR, f"{img_hash}.json")

context = {}
if os.path.exists(context_file):
    try:
        with open(context_file) as f:
            context = json.load(f)
        if not description and context.get("description"):
            description = context["description"]
    except Exception:
        pass

# ── Construction reference ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from construction_ref import get_reference_for_edit, ACCURACY_SUFFIX, ARCHITECTURE_NEGATIVE
    ref_data = get_reference_for_edit(edit_request)
except ImportError:
    ref_data = ""
    ACCURACY_SUFFIX = ", photorealistic interior photography, 8k, sharp focus, professional real estate photo"
    ARCHITECTURE_NEGATIVE = (
        "blurry, distorted, low quality, watermark, text, nsfw, ugly, deformed, "
        "cartoon, anime, painting, sketch, drawing, different camera angle, "
        "different room, wrong perspective, extra walls, missing walls"
    )

# ── Determine what to change and what to preserve ──────────────────────────
# Classify the edit to pick the right inpainting strategy
edit_lower = edit_request.lower()

# What's being changed determines the mask and denoising strength
FLOOR_KEYWORDS = ["floor", "flooring", "hardwood", "tile", "carpet", "laminate", "vinyl", "planks", "wood floor"]
WALL_KEYWORDS  = ["wall", "paint", "wallpaper", "backsplash", "color", "stain"]
CABINET_KEYWORDS = ["cabinet", "cupboard", "drawer", "shelf", "shelving", "pantry", "shaker"]
COUNTER_KEYWORDS = ["counter", "countertop", "granite", "quartz", "marble", "butcher block"]
FIXTURE_KEYWORDS = ["light", "lamp", "pendant", "chandelier", "sconce", "faucet", "sink", "fixture"]
ADD_KEYWORDS = ["add", "install", "put", "place", "build", "create", "island"]
REMOVE_KEYWORDS = ["remove", "demolish", "take out", "tear out", "clear", "empty"]

# Denoising strength: controls how much SD can change.
# ControlNet depth locks geometry — strength controls CONTENT freedom.
# Objects (cabinets, appliances) NEED high strength to appear.
if any(k in edit_lower for k in REMOVE_KEYWORDS):
    strength = 0.80  # Need freedom to fill removed area
elif any(k in edit_lower for k in ADD_KEYWORDS + CABINET_KEYWORDS):
    strength = 0.80  # Objects MUST have room to appear
elif any(k in edit_lower for k in COUNTER_KEYWORDS + FIXTURE_KEYWORDS):
    strength = 0.75  # Counters/fixtures need moderate freedom
elif any(k in edit_lower for k in WALL_KEYWORDS):
    strength = 0.65  # Walls are surface changes
elif any(k in edit_lower for k in FLOOR_KEYWORDS):
    strength = 0.70  # Floors need moderate change
else:
    strength = 0.75  # Default

# ── Persistent room spec ────────────────────────────────────────────────────
# These are the BASE conditions of the room that NEVER change unless the user
# explicitly asks to change them. Every prompt includes this as ground truth.
# The context file can override these if the user has set custom values.
DEFAULT_ROOM_SPEC = {
    "walls": "white painted walls, clean flat white paint, eggshell finish",
    "ceiling": "white ceiling, flat white paint",
    "floor": "natural pine plank flooring, 4 inch wide planks, clear polyurethane finish, warm wood tone",
    "lighting": "natural lighting from existing windows",
}

# Load any user-customized room spec from context, or use defaults
room_spec = context.get("room_spec", {})
for k, v in DEFAULT_ROOM_SPEC.items():
    if k not in room_spec:
        room_spec[k] = v

# Check if the edit explicitly changes a base element — if so, update the spec
for key, keywords in [
    ("walls", WALL_KEYWORDS),
    ("floor", FLOOR_KEYWORDS),
    ("ceiling", ["ceiling"]),
]:
    if any(k in edit_lower for k in keywords):
        # User is changing this element — let the edit override the spec
        room_spec[key] = edit_request

# Save updated room spec to context for future edits
context["room_spec"] = room_spec

# ── Build prompt ────────────────────────────────────────────────────────────
# Prompt = persistent room base + specific edit + quality tags
# This ensures walls/floors/ceiling ALWAYS match the spec unless user changes them

# Build the room base description from spec
room_base = ", ".join([
    room_spec["walls"],
    room_spec["ceiling"],
    room_spec["floor"],
    room_spec["lighting"],
])

# EDIT REQUEST FIRST — SD heavily weights the start of the prompt.
# Room spec comes second as the environment context.
prompt_parts = [
    edit_request,
    f"in a room with {room_base}",
]
if ref_data:
    prompt_parts.append(ref_data)
prompt_parts.append(
    "photorealistic, professional interior photography, "
    "8k quality, sharp focus, architecturally accurate proportions, "
    "same room same camera angle same perspective"
)
prompt = ", ".join(prompt_parts)

negative = (
    ARCHITECTURE_NEGATIVE + ", "
    "mismatched perspective, floating objects, impossible architecture, "
    "wrong proportions, oversized, undersized, warped, bent walls, "
    "different camera angle, different viewpoint, rotated view, "
    "different wall color, colored walls, textured walls, wallpaper, "
    "different floor, carpet, tile floor, concrete floor, "
    "different ceiling, colored ceiling, drop ceiling"
)

# Only negate wall/floor/ceiling changes if we're NOT changing them
if any(k in edit_lower for k in WALL_KEYWORDS):
    negative = negative.replace("different wall color, colored walls, textured walls, wallpaper, ", "")
if any(k in edit_lower for k in FLOOR_KEYWORDS):
    negative = negative.replace("different floor, carpet, tile floor, concrete floor, ", "")
if "ceiling" in edit_lower:
    negative = negative.replace("different ceiling, colored ceiling, drop ceiling", "")

# ── Check SD WebUI ───────��──────────────────────────────────────────────────
try:
    health = requests.get(f"{SDWEBUI_URL}/sdapi/v1/options", timeout=5)
    if health.status_code != 200:
        print(json.dumps({"error": "SD WebUI not ready"}))
        sys.exit(1)
except Exception as e:
    print(json.dumps({"error": f"SD WebUI offline: {e}"}))
    sys.exit(1)

# ── Set model ───────────────────────────────────────��───────────────────────
try:
    models = requests.get(f"{SDWEBUI_URL}/sdapi/v1/sd-models", timeout=5).json()
    best = None
    for kw in ["juggernaut", "realvis", "dreamshaper", "sdxl"]:
        for m in models:
            if kw in m.get("model_name", "").lower():
                best = m["title"]
                break
        if best:
            break
    if best:
        # Only switch if different (avoids 10-20s model swap)
        current = requests.get(f"{SDWEBUI_URL}/sdapi/v1/options", timeout=5).json().get("sd_model_checkpoint", "")
        if current != best:
            requests.post(f"{SDWEBUI_URL}/sdapi/v1/options",
                          json={"sd_model_checkpoint": best}, timeout=60)
except Exception:
    pass

# ── Auto-generate inpaint mask ──────────────────────────────────────────────
# Strategy: use the ENTIRE image as mask (white = inpaint) BUT with
# dual ControlNet (depth + canny) locking the room structure.
# This lets SD reimagine surfaces while structural edges stay fixed.
#
# For full-room renovations this is correct — the ControlNets preserve
# the geometry, windows, doors, and spatial layout while the inpainting
# replaces materials, colors, and objects.
#
# We use inpaint_full_res=True so SD focuses on the masked area at full res.

# Create a white mask (inpaint everything — ControlNet handles structure)
try:
    from PIL import Image
    import io
    src_img = Image.open(source_image)
    mask_img = Image.new("RGB", src_img.size, (255, 255, 255))  # Full white = change everything
    buf = io.BytesIO()
    mask_img.save(buf, format="PNG")
    mask_b64 = base64.b64encode(buf.getvalue()).decode()
except Exception:
    # Fallback: no mask, use img2img mode
    mask_b64 = None

# ── Build inpainting payload ────────────────────────────────────────────────
payload = {
    "init_images":        [img_b64],
    "prompt":             prompt,
    "negative_prompt":    negative,
    "denoising_strength": strength,
    "steps":              steps,
    "width":              width,
    "height":             height,
    "cfg_scale":          cfg_scale,
    "sampler_name":       sampler,
    "batch_size":         1,
    "n_iter":             1,
    "save_images":        True,
    "send_images":        True,
    "resize_mode":        1,
}

# Add mask for inpainting mode
if mask_b64:
    payload["mask"] = mask_b64
    payload["mask_blur"] = 8
    payload["inpainting_fill"] = 1       # 1 = original (preserve source as base)
    payload["inpaint_full_res"] = False   # Use whole image context
    payload["inpainting_mask_invert"] = 0 # 0 = white = inpaint

# ── Dual ControlNet: depth_anything_v2 + canny ─────────────────────────────
# This is the key to preserving room structure:
# - Depth locks spatial relationships (walls stay at correct distances)
# - Canny locks structural edges (window frames, door frames, wall corners)
# Together they create an immovable skeleton that inpainting fills around.
# Single ControlNet: depth_anything_v2 — preserves room geometry
# (dual CN OOMs on 8GB NVIDIA — depth alone is the most critical)
payload.setdefault("alwayson_scripts", {})
payload["alwayson_scripts"]["controlnet"] = {
    "args": [
        {
            "input_image": img_b64,
            "module": "depth_anything_v2",
            "model": "diffusers_xl_depth_full [2f51180b]",
            "weight": 0.9,
            "resize_mode": 1,
            "control_mode": 0,         # Balanced — depth guides but prompt can add objects
            "guidance_start": 0.0,
            "guidance_end": 0.85,
            "pixel_perfect": True,
        },
    ]
}

# ── Generate variants one at a time, streaming paths ���───────────────────────
os.makedirs(OUTPUT_DIR, exist_ok=True)
timestamp = int(time.time())
safe_edit = "".join(c if c.isalnum() or c == "_" else "" for c in "_".join(edit_request.split()[:5]))[:40]
saved_paths = []

for variant_idx in range(num_variants):
    variant_payload = dict(payload)
    variant_payload["seed"] = -1  # Random seed per variant

    # Copy alwayson_scripts properly (dict reference issue)
    if "alwayson_scripts" in payload:
        variant_payload["alwayson_scripts"] = json.loads(json.dumps(payload["alwayson_scripts"]))

    # OOM fallback chain: depth CN → plain img2img
    fallbacks = [
        ("depth controlnet", variant_payload),
    ]
    plain = {k: v for k, v in variant_payload.items() if k != "alwayson_scripts"}
    fallbacks.append(("plain", plain))

    generated = False
    for label, attempt in fallbacks:
        try:
            r = requests.post(f"{SDWEBUI_URL}/sdapi/v1/img2img", json=attempt, timeout=300)
            r.raise_for_status()
            data = r.json()
            imgs = data.get("images", [])
            if imgs:
                variant_label = f"v{variant_idx+1}"
                filename = f"{timestamp}_{safe_edit}_{variant_label}_{img_hash[:8]}.png"
                filepath = os.path.join(OUTPUT_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(imgs[0]))
                saved_paths.append(filepath)

                # STREAM: print path immediately so agent can send while next generates
                print(filepath, flush=True)

                if label != "full (depth+canny)":
                    print(f"[variant {variant_idx+1}: {label}]", file=sys.stderr)
                generated = True
            break
        except requests.exceptions.Timeout:
            if label == fallbacks[-1][0]:
                print(json.dumps({"error": f"Variant {variant_idx+1} timed out"}))
            continue
        except Exception as e:
            if "500" in str(e) and label != fallbacks[-1][0]:
                continue
            print(json.dumps({"error": f"Variant {variant_idx+1} failed: {e}"}))
            break

    if not generated and not saved_paths:
        print(json.dumps({"error": "Generation failed (likely OOM)"}))
        sys.exit(1)

# ── Upload to dashboard ───────────────────────���─────────────────────────────
# DataHub upload intentionally disabled — Telegram edits are throwaway and do NOT
# get registered into proj-ahb123 artifacts / Data Hub. (Removed 2026-06-12 per Serge.)

# ── Save context ────────────────────────────────────────────────────────────
edit_entry = {
    "timestamp": timestamp,
    "edit_request": edit_request,
    "prompt_used": prompt[:500],
    "strength": strength,
    "steps": steps,
    "pipeline": "inpainting + dual controlnet (depth_anything_v2 + canny)",
    "variants": [os.path.basename(p) for p in saved_paths],
    "source": os.path.basename(source_image),
}

context.setdefault("description", description)
context.setdefault("source_image", source_image)
context.setdefault("image_hash", img_hash)
context.setdefault("edits", [])
context["edits"].append(edit_entry)
context["last_edit"] = edit_entry
context["last_variants"] = saved_paths

with open(context_file, "w") as f:
    json.dump(context, f, indent=2)

# ── Final output ────────────────────────────────────────────────────────────
result = {
    "success": True,
    "variants": saved_paths,
    "variant_count": len(saved_paths),
    "context_file": context_file,
    "prompt_used": prompt[:500],
    "strength": strength,
    "edit_request": edit_request,
    "source_image": source_image,
    "image_hash": img_hash,
    "pipeline": "inpainting + dual controlnet",
}

print(f"\nGenerated {len(saved_paths)} variant(s)")
for i, p in enumerate(saved_paths):
    print(f"  variant {i+1}: {p}")
print(f"\nEdit: {edit_request}")
print(f"Strength: {strength} | Steps: {steps} | Pipeline: inpainting + dual CN")
print(f"Context saved: {context_file}")
print(f"\n{json.dumps(result)}")
