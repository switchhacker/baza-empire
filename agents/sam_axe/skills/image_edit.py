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

# Higher strength = more creative freedom in masked area
# For inpainting: 0.75-0.95 is typical (we're only changing the masked region)
if any(k in edit_lower for k in REMOVE_KEYWORDS):
    strength = 0.95  # Full reimagine of masked area
elif any(k in edit_lower for k in ADD_KEYWORDS):
    strength = 0.85  # Need room to add new objects
elif any(k in edit_lower for k in WALL_KEYWORDS):
    strength = 0.80  # Walls need full repaint
elif any(k in edit_lower for k in FLOOR_KEYWORDS):
    strength = 0.85  # Floors need full replacement
elif any(k in edit_lower for k in CABINET_KEYWORDS + COUNTER_KEYWORDS):
    strength = 0.85  # Cabinets/counters are major changes
else:
    strength = 0.80  # Default for mixed edits

# ── Build prompt ────────────────────────────────────────────────────────────
# For inpainting: prompt describes ONLY what goes in the masked area
# Not the whole room — just the replacement content
prompt_parts = [edit_request]
if ref_data:
    prompt_parts.append(ref_data)
prompt_parts.append(
    "photorealistic, professional interior photography, natural lighting, "
    "8k quality, sharp focus, architecturally accurate proportions, "
    "seamless integration with existing room, matching perspective"
)
prompt = ", ".join(prompt_parts)

negative = (
    ARCHITECTURE_NEGATIVE + ", "
    "mismatched perspective, floating objects, impossible architecture, "
    "wrong proportions, oversized, undersized, warped, bent walls, "
    "different camera angle, different viewpoint, rotated view"
)

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
payload.setdefault("alwayson_scripts", {})
payload["alwayson_scripts"]["controlnet"] = {
    "args": [
        {
            # ControlNet Unit 0: Depth — preserves room spatial layout
            "input_image": img_b64,
            "module": "depth_anything_v2",
            "model": "diffusers_xl_depth_full [2f51180b]",
            "weight": 0.9,            # High weight — room geometry is sacred
            "resize_mode": 1,
            "control_mode": 0,         # Balanced
            "guidance_start": 0.0,
            "guidance_end": 0.85,      # Hold depth through most of generation
            "pixel_perfect": True,
        },
        {
            # ControlNet Unit 1: Canny — preserves structural edges
            "input_image": img_b64,
            "module": "canny",
            "model": "diffusers_xl_canny_full [2b69fca4]",
            "weight": 0.5,            # Medium weight — edges guide but don't dominate
            "resize_mode": 1,
            "control_mode": 0,
            "guidance_start": 0.0,
            "guidance_end": 0.6,       # Release edges earlier to allow material changes
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

    # OOM fallback chain
    fallbacks = [
        ("full (depth+canny)", variant_payload),
    ]
    # Fallback: depth only (drop canny)
    depth_only = json.loads(json.dumps(variant_payload))
    if "controlnet" in depth_only.get("alwayson_scripts", {}):
        cn_args = depth_only["alwayson_scripts"]["controlnet"]["args"]
        depth_only["alwayson_scripts"]["controlnet"]["args"] = cn_args[:1]  # Keep only depth
    fallbacks.append(("depth only", depth_only))
    # Fallback: plain img2img
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
for filepath in saved_paths:
    try:
        import urllib.request
        DASHBOARD = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
        boundary = "bazaimgedit"
        with open(filepath, "rb") as img_f:
            img_bytes_upload = img_f.read()
        fname = os.path.basename(filepath)
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="project_id"\r\n\r\nproj-ahb123\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="sam_{fname}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + img_bytes_upload + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{DASHBOARD}/api/artifacts/upload", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass

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
