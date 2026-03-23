"""
Baza Empire — Sam Axe Full Imaging Toolkit (30+ tools)
--------------------------------------------------------
Mounted into the main tool server. All tools available at /tools/sam/<name>
"""

import os
import re
import json
import time
import base64
import logging
import subprocess
import glob
import hashlib
from typing import Optional, Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

SD_API      = "http://localhost:7860"
OLLAMA_API  = "http://localhost:11434"
VISION_MODEL = "llava:13b"

# ─── Models ───────────────────────────────────────────────────────────────────

class ToolRequest(BaseModel):
    input: dict = {}
    task_id: Optional[str] = None

class ToolResponse(BaseModel):
    success: bool
    output: Any
    tool: str
    task_id: Optional[str] = None
    duration_ms: int
    error: Optional[str] = None

def ok(tool, output, start, task_id=None):
    return ToolResponse(success=True, output=output, tool=tool, task_id=task_id,
                        duration_ms=int((time.time()-start)*1000))

def err(tool, e, start, task_id=None):
    logger.error(f"[{tool}] {e}")
    return ToolResponse(success=False, output=None, tool=tool, task_id=task_id,
                        duration_ms=int((time.time()-start)*1000), error=str(e))

# ─── Helpers ──────────────────────────────────────────────────────────────────

def to_b64(path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(path_or_url) as r:
            return base64.b64encode(r.read()).decode()
    with open(path_or_url, "rb") as f:
        return base64.b64encode(f.read()).decode()

def save_b64(b64: str, name: str) -> str:
    path = f"/tmp/{name}"
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return path

def pil_open(path_or_url: str):
    from PIL import Image
    import io, urllib.request
    if path_or_url.startswith("http"):
        with urllib.request.urlopen(path_or_url) as r:
            return Image.open(io.BytesIO(r.read())).copy()
    return Image.open(path_or_url)

def pil_save(img, name: str) -> str:
    path = f"/tmp/{name}"
    img.save(path)
    return path

def ts() -> str:
    return str(int(time.time()))


# ═══════════════════════════════════════════════════════════════════════════════
# GENERATION (8 tools)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/sam/generate-image")
async def generate_image(req: ToolRequest):
    """Text-to-image via Stable Diffusion."""
    t = time.time()
    try:
        inp = req.input
        prompt = inp.get("prompt", "")
        if not prompt:
            raise ValueError("prompt required")
        model = _pick_model(prompt)
        await _set_model(model)
        payload = {
            "prompt": prompt + ", masterpiece, best quality, ultra detailed, sharp focus, 8k",
            "negative_prompt": inp.get("negative_prompt",
                "blurry, low quality, watermark, text, ugly, EasyNegative"),
            "width":  inp.get("width", 1024),
            "height": inp.get("height", 1024),
            "steps":  inp.get("steps", 6),
            "cfg_scale": inp.get("cfg_scale", 2.0),
            "seed":   inp.get("seed", -1),
            "sampler_name": "DPM++ SDE Karras",
            "batch_size": inp.get("batch_size", 1),
        }
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/txt2img", json=payload)
            r.raise_for_status()
            data = r.json()
        b64 = data["images"][0]
        info = json.loads(data.get("info", "{}"))
        path = save_b64(b64, f"gen_{ts()}.png")
        return ok("sam/generate-image", {"path": path, "seed": info.get("seed", -1),
                                          "prompt": prompt, "size": f"{payload['width']}x{payload['height']}"}, t, req.task_id)
    except Exception as e:
        return err("sam/generate-image", e, t, req.task_id)


@router.post("/tools/sam/image-variations")
async def image_variations(req: ToolRequest):
    """Generate N variations of an existing image."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        n = inp.get("count", 4)
        strength = inp.get("strength", 0.45)
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64],
                "prompt": inp.get("prompt", "high quality, detailed"),
                "negative_prompt": "blurry, low quality",
                "denoising_strength": strength,
                "steps": 20,
                "batch_size": n,
                "cfg_scale": 7,
            })
            r.raise_for_status()
            data = r.json()
        paths = [save_b64(img, f"var_{ts()}_{i}.png") for i, img in enumerate(data["images"][:n])]
        return ok("sam/image-variations", {"paths": paths, "count": len(paths)}, t, req.task_id)
    except Exception as e:
        return err("sam/image-variations", e, t, req.task_id)


@router.post("/tools/sam/inpaint-image")
async def inpaint_image(req: ToolRequest):
    """Inpaint — fill a masked region with AI-generated content."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        mask_path = inp.get("mask_path", "")
        prompt = inp.get("prompt", "")
        if not image_path or not mask_path or not prompt:
            raise ValueError("image_path, mask_path, and prompt required")
        b64_img  = to_b64(image_path)
        b64_mask = to_b64(mask_path)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64_img],
                "mask": b64_mask,
                "prompt": prompt,
                "negative_prompt": "blurry, low quality",
                "inpainting_fill": 1,
                "denoising_strength": inp.get("strength", 0.75),
                "steps": 25,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"inpaint_{ts()}.png")
        return ok("sam/inpaint-image", {"path": path, "prompt": prompt}, t, req.task_id)
    except Exception as e:
        return err("sam/inpaint-image", e, t, req.task_id)


@router.post("/tools/sam/outpaint-image")
async def outpaint_image(req: ToolRequest):
    """Outpaint — extend the canvas of an image in any direction."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        direction = inp.get("direction", "all")  # left, right, top, bottom, all
        pixels = inp.get("pixels", 128)
        prompt = inp.get("prompt", "seamless extension, same style")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image, ImageDraw
        img = pil_open(image_path)
        w, h = img.size
        pad = pixels
        dirs = {"all": (pad,pad,pad,pad), "left":(pad,0,0,0), "right":(0,0,pad,0),
                "top":(0,pad,0,0), "bottom":(0,0,0,pad)}
        l, top_p, r, bot_p = dirs.get(direction, (pad,pad,pad,pad))
        new_w, new_h = w+l+r, h+top_p+bot_p
        canvas = Image.new("RGB", (new_w, new_h), (128,128,128))
        canvas.paste(img, (l, top_p))
        mask = Image.new("L", (new_w, new_h), 255)
        draw = ImageDraw.Draw(mask)
        draw.rectangle([l, top_p, l+w-1, top_p+h-1], fill=0)
        canvas_path = pil_save(canvas, f"outpaint_canvas_{ts()}.png")
        mask_path_tmp = pil_save(mask, f"outpaint_mask_{ts()}.png")
        b64_img  = to_b64(canvas_path)
        b64_mask = to_b64(mask_path_tmp)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64_img],
                "mask": b64_mask,
                "prompt": prompt,
                "inpainting_fill": 1,
                "denoising_strength": 0.85,
                "steps": 30,
                "width": new_w,
                "height": new_h,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"outpaint_{ts()}.png")
        return ok("sam/outpaint-image", {"path": path, "new_size": f"{new_w}x{new_h}"}, t, req.task_id)
    except Exception as e:
        return err("sam/outpaint-image", e, t, req.task_id)


@router.post("/tools/sam/style-transfer")
async def style_transfer(req: ToolRequest):
    """Apply a style prompt to an existing image (img2img style transfer)."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        style = inp.get("style", "oil painting, impressionist")
        strength = inp.get("strength", 0.65)
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64],
                "prompt": style + ", high quality, detailed",
                "negative_prompt": "blurry, low quality, watermark",
                "denoising_strength": strength,
                "steps": 30,
                "cfg_scale": 8,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"styled_{ts()}.png")
        return ok("sam/style-transfer", {"path": path, "style": style, "strength": strength}, t, req.task_id)
    except Exception as e:
        return err("sam/style-transfer", e, t, req.task_id)


@router.post("/tools/sam/sketch-to-image")
async def sketch_to_image(req: ToolRequest):
    """Convert a sketch or line drawing to a realistic image."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        prompt = inp.get("prompt", "realistic, detailed, high quality")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64],
                "prompt": prompt,
                "negative_prompt": "sketch, drawing, outline, blurry",
                "denoising_strength": 0.80,
                "steps": 30,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"sketch2img_{ts()}.png")
        return ok("sam/sketch-to-image", {"path": path, "prompt": prompt}, t, req.task_id)
    except Exception as e:
        return err("sam/sketch-to-image", e, t, req.task_id)


@router.post("/tools/sam/generate-logo")
async def generate_logo(req: ToolRequest):
    """Generate a logo using SD with logo-optimized prompt construction."""
    t = time.time()
    try:
        inp = req.input
        name = inp.get("name", "")
        style = inp.get("style", "modern minimalist")
        colors = inp.get("colors", "dark blue, white")
        if not name:
            raise ValueError("name required")
        prompt = (f"professional logo for '{name}', {style}, {colors}, "
                  "vector style, clean design, no text, transparent background, "
                  "logo design, brand identity, high quality")
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/txt2img", json={
                "prompt": prompt,
                "negative_prompt": "photo, realistic, blurry, watermark, low quality",
                "width": 512, "height": 512,
                "steps": 30, "cfg_scale": 9,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"logo_{ts()}.png")
        return ok("sam/generate-logo", {"path": path, "name": name, "style": style}, t, req.task_id)
    except Exception as e:
        return err("sam/generate-logo", e, t, req.task_id)


@router.post("/tools/sam/batch-generate")
async def batch_generate(req: ToolRequest):
    """Generate multiple images from a list of prompts."""
    t = time.time()
    try:
        inp = req.input
        prompts = inp.get("prompts", [])
        if not prompts:
            raise ValueError("prompts list required")
        results = []
        async with httpx.AsyncClient(timeout=300) as c:
            for i, prompt in enumerate(prompts[:10]):
                r = await c.post(f"{SD_API}/sdapi/v1/txt2img", json={
                    "prompt": prompt,
                    "negative_prompt": "blurry, low quality",
                    "width": 512, "height": 512, "steps": 20,
                })
                if r.status_code == 200:
                    data = r.json()
                    path = save_b64(data["images"][0], f"batch_{ts()}_{i}.png")
                    results.append({"prompt": prompt, "path": path, "success": True})
                else:
                    results.append({"prompt": prompt, "success": False})
        return ok("sam/batch-generate", {"results": results, "count": len(results)}, t, req.task_id)
    except Exception as e:
        return err("sam/batch-generate", e, t, req.task_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS (9 tools)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/sam/analyze-image")
async def analyze_image(req: ToolRequest):
    """Deep analysis — describe content, objects, scene, mood. Uses LLaVA."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        question = inp.get("question", "Describe this image in detail. List all objects, people, text, colors, and activities you see.")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{OLLAMA_API}/api/generate", json={
                "model": VISION_MODEL, "prompt": question,
                "images": [b64], "stream": False
            })
            r.raise_for_status()
        return ok("sam/analyze-image", {"analysis": r.json().get("response",""), "question": question}, t, req.task_id)
    except Exception as e:
        return err("sam/analyze-image", e, t, req.task_id)


@router.post("/tools/sam/tag-image")
async def tag_image(req: ToolRequest):
    """Auto-generate keyword tags for an image. Uses LLaVA."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_API}/api/generate", json={
                "model": VISION_MODEL,
                "prompt": "List 15 keyword tags for this image. Output: comma-separated only, no explanation.",
                "images": [b64], "stream": False
            })
            r.raise_for_status()
        raw = r.json().get("response", "")
        tags = [t.strip().lower() for t in raw.split(",") if t.strip()]
        return ok("sam/tag-image", {"tags": tags, "count": len(tags)}, t, req.task_id)
    except Exception as e:
        return err("sam/tag-image", e, t, req.task_id)


@router.post("/tools/sam/ocr-image")
async def ocr_image(req: ToolRequest):
    """Extract all text from an image (OCR). Uses LLaVA or tesseract."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        # Try tesseract first (fast + accurate for text)
        if not image_path.startswith("http"):
            result = subprocess.run(
                f"tesseract {image_path} stdout -l eng 2>/dev/null",
                shell=True, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                return ok("sam/ocr-image", {"text": result.stdout.strip(), "method": "tesseract"}, t, req.task_id)
        # Fallback to LLaVA
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_API}/api/generate", json={
                "model": VISION_MODEL,
                "prompt": "Extract and transcribe ALL text visible in this image. Output the text only, preserving layout.",
                "images": [b64], "stream": False
            })
            r.raise_for_status()
        return ok("sam/ocr-image", {"text": r.json().get("response",""), "method": "llava"}, t, req.task_id)
    except Exception as e:
        return err("sam/ocr-image", e, t, req.task_id)


@router.post("/tools/sam/detect-objects")
async def detect_objects(req: ToolRequest):
    """Detect and list objects in an image with approximate locations."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_API}/api/generate", json={
                "model": VISION_MODEL,
                "prompt": "List every distinct object you can see in this image. For each object, note its position (top-left, center, etc) and approximate size (small/medium/large). Format as JSON array: [{\"object\": \"name\", \"position\": \"...\", \"size\": \"...\"}]",
                "images": [b64], "stream": False
            })
            r.raise_for_status()
        raw = r.json().get("response", "")
        try:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            objects = json.loads(match.group(0)) if match else [{"raw": raw}]
        except Exception:
            objects = [{"raw": raw}]
        return ok("sam/detect-objects", {"objects": objects, "count": len(objects)}, t, req.task_id)
    except Exception as e:
        return err("sam/detect-objects", e, t, req.task_id)


@router.post("/tools/sam/color-palette")
async def color_palette(req: ToolRequest):
    """Extract the dominant color palette from an image."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        n_colors = inp.get("colors", 6)
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image
        img = pil_open(image_path).convert("RGB").resize((150, 150))
        pixels = list(img.getdata())
        # Simple k-means-lite: quantize
        quantized = img.quantize(colors=n_colors)
        palette_raw = quantized.getpalette()[:n_colors*3]
        palette = []
        for i in range(n_colors):
            r2, g, b = palette_raw[i*3], palette_raw[i*3+1], palette_raw[i*3+2]
            hex_color = f"#{r2:02x}{g:02x}{b:02x}"
            palette.append({"hex": hex_color, "rgb": [r2, g, b]})
        return ok("sam/color-palette", {"palette": palette, "count": len(palette)}, t, req.task_id)
    except Exception as e:
        return err("sam/color-palette", e, t, req.task_id)


@router.post("/tools/sam/image-similarity")
async def image_similarity(req: ToolRequest):
    """Compare two images and return a similarity score (0-100)."""
    t = time.time()
    try:
        inp = req.input
        image_a = inp.get("image_a", "")
        image_b = inp.get("image_b", "")
        if not image_a or not image_b:
            raise ValueError("image_a and image_b required")
        from PIL import Image
        import numpy as np
        img_a = pil_open(image_a).convert("RGB").resize((64, 64))
        img_b = pil_open(image_b).convert("RGB").resize((64, 64))
        arr_a = np.array(img_a).flatten().astype(float)
        arr_b = np.array(img_b).flatten().astype(float)
        # Cosine similarity
        dot = np.dot(arr_a, arr_b)
        norm = np.linalg.norm(arr_a) * np.linalg.norm(arr_b)
        sim = round((dot / norm) * 100, 2) if norm > 0 else 0
        return ok("sam/image-similarity", {"similarity_pct": sim,
                                            "interpretation": "high" if sim>85 else "medium" if sim>60 else "low"}, t, req.task_id)
    except Exception as e:
        return err("sam/image-similarity", e, t, req.task_id)


@router.post("/tools/sam/read-exif")
async def read_exif(req: ToolRequest):
    """Read EXIF metadata from an image (camera, location, date, settings)."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path or image_path.startswith("http"):
            raise ValueError("local image_path required for EXIF")
        from PIL import Image
        from PIL.ExifTags import TAGS
        img = Image.open(image_path)
        raw_exif = img._getexif() or {}
        exif = {TAGS.get(k, k): str(v) for k, v in raw_exif.items()
                if isinstance(v, (str, int, float, bytes)) and len(str(v)) < 200}
        return ok("sam/read-exif", {"exif": exif, "count": len(exif),
                                     "size": f"{img.width}x{img.height}", "mode": img.mode}, t, req.task_id)
    except Exception as e:
        return err("sam/read-exif", e, t, req.task_id)


@router.post("/tools/sam/nsfw-check")
async def nsfw_check(req: ToolRequest):
    """Check if an image contains NSFW content. Uses LLaVA."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{OLLAMA_API}/api/generate", json={
                "model": VISION_MODEL,
                "prompt": "Is this image safe for work (SFW) or not safe for work (NSFW)? Answer with just: SFW or NSFW, then one sentence explanation.",
                "images": [b64], "stream": False
            })
            r.raise_for_status()
        raw = r.json().get("response", "")
        is_nsfw = raw.upper().startswith("NSFW")
        return ok("sam/nsfw-check", {"nsfw": is_nsfw, "sfw": not is_nsfw, "detail": raw}, t, req.task_id)
    except Exception as e:
        return err("sam/nsfw-check", e, t, req.task_id)


@router.post("/tools/sam/detect-faces")
async def detect_faces(req: ToolRequest):
    """Detect faces in an image — count, positions, expressions."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        # Try OpenCV face detection first
        import numpy as np
        import cv2
        import urllib.request, io
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img_cv = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img_cv = cv2.imread(image_path)
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        face_list = [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
                     for (x, y, w, h) in faces]
        return ok("sam/detect-faces", {"faces": face_list, "count": len(face_list), "method": "opencv-haar"}, t, req.task_id)
    except Exception as e:
        return err("sam/detect-faces", e, t, req.task_id)


# ═══════════════════════════════════════════════════════════════════════════════
# EDITING (10 tools)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/sam/edit-image")
async def edit_image(req: ToolRequest):
    """Edit an image with a natural language instruction via img2img."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        instruction = inp.get("instruction", "")
        if not image_path or not instruction:
            raise ValueError("image_path and instruction required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64],
                "prompt": instruction,
                "negative_prompt": "blurry, low quality, artifacts",
                "denoising_strength": inp.get("strength", 0.6),
                "steps": 25, "cfg_scale": 7,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"edited_{ts()}.png")
        return ok("sam/edit-image", {"path": path, "instruction": instruction}, t, req.task_id)
    except Exception as e:
        return err("sam/edit-image", e, t, req.task_id)


@router.post("/tools/sam/enhance-image")
async def enhance_image(req: ToolRequest):
    """Upscale + enhance via R-ESRGAN (SD WebUI or realesrgan binary)."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        scale = inp.get("scale", 4)
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/extra-single-image", json={
                "image": b64,
                "resize_mode": 0,
                "upscaling_resize": scale,
                "upscaler_1": "R-ESRGAN 4x+"
            })
            if r.status_code == 200:
                out_b64 = r.json().get("image", "")
                if out_b64:
                    path = save_b64(out_b64, f"enhanced_{ts()}.png")
                    return ok("sam/enhance-image", {"path": path, "scale": scale, "method": "R-ESRGAN"}, t, req.task_id)
        raise RuntimeError("SD WebUI upscaler unavailable")
    except Exception as e:
        return err("sam/enhance-image", e, t, req.task_id)


@router.post("/tools/sam/remove-background")
async def remove_background(req: ToolRequest):
    """Remove image background using rembg."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        from rembg import remove
        img = pil_open(image_path)
        out = remove(img)
        path = pil_save(out, f"nobg_{ts()}.png")
        return ok("sam/remove-background", {"path": path, "method": "rembg"}, t, req.task_id)
    except Exception as e:
        return err("sam/remove-background", e, t, req.task_id)


@router.post("/tools/sam/crop-resize")
async def crop_resize(req: ToolRequest):
    """Crop and/or resize an image to exact dimensions."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image
        img = pil_open(image_path)
        if "crop" in inp:
            box = inp["crop"]  # [left, top, right, bottom]
            img = img.crop(box)
        if "width" in inp or "height" in inp:
            w = inp.get("width", img.width)
            h = inp.get("height", img.height)
            img = img.resize((w, h), Image.LANCZOS)
        path = pil_save(img, f"cropped_{ts()}.png")
        return ok("sam/crop-resize", {"path": path, "size": f"{img.width}x{img.height}"}, t, req.task_id)
    except Exception as e:
        return err("sam/crop-resize", e, t, req.task_id)


@router.post("/tools/sam/rotate-flip")
async def rotate_flip(req: ToolRequest):
    """Rotate or flip an image."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image
        img = pil_open(image_path)
        if "rotate" in inp:
            img = img.rotate(inp["rotate"], expand=True)
        if inp.get("flip_horizontal"):
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        if inp.get("flip_vertical"):
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        path = pil_save(img, f"rotated_{ts()}.png")
        return ok("sam/rotate-flip", {"path": path}, t, req.task_id)
    except Exception as e:
        return err("sam/rotate-flip", e, t, req.task_id)


@router.post("/tools/sam/add-watermark")
async def add_watermark(req: ToolRequest):
    """Add text or image watermark to an image."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        text = inp.get("text", "© Baza Empire")
        opacity = inp.get("opacity", 128)
        position = inp.get("position", "bottom-right")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image, ImageDraw, ImageFont
        img = pil_open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        font_size = max(20, img.width // 30)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pad = 15
        positions = {
            "bottom-right": (img.width - tw - pad, img.height - th - pad),
            "bottom-left": (pad, img.height - th - pad),
            "top-right": (img.width - tw - pad, pad),
            "top-left": (pad, pad),
            "center": ((img.width - tw) // 2, (img.height - th) // 2),
        }
        x, y = positions.get(position, positions["bottom-right"])
        draw.text((x+1, y+1), text, font=font, fill=(0,0,0,opacity))
        draw.text((x, y), text, font=font, fill=(255,255,255,opacity))
        out = Image.alpha_composite(img, overlay).convert("RGB")
        path = pil_save(out, f"watermarked_{ts()}.jpg")
        return ok("sam/add-watermark", {"path": path, "text": text, "position": position}, t, req.task_id)
    except Exception as e:
        return err("sam/add-watermark", e, t, req.task_id)


@router.post("/tools/sam/color-grade")
async def color_grade(req: ToolRequest):
    """Adjust brightness, contrast, saturation, and color temperature."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image, ImageEnhance, ImageFilter
        img = pil_open(image_path).convert("RGB")
        if "brightness" in inp:
            img = ImageEnhance.Brightness(img).enhance(inp["brightness"])
        if "contrast" in inp:
            img = ImageEnhance.Contrast(img).enhance(inp["contrast"])
        if "saturation" in inp:
            img = ImageEnhance.Color(img).enhance(inp["saturation"])
        if "sharpness" in inp:
            img = ImageEnhance.Sharpness(img).enhance(inp["sharpness"])
        if inp.get("blur"):
            img = img.filter(ImageFilter.GaussianBlur(radius=inp["blur"]))
        path = pil_save(img, f"graded_{ts()}.jpg")
        return ok("sam/color-grade", {"path": path, "adjustments": {k: v for k,v in inp.items() if k != "image_path"}}, t, req.task_id)
    except Exception as e:
        return err("sam/color-grade", e, t, req.task_id)


@router.post("/tools/sam/convert-format")
async def convert_format(req: ToolRequest):
    """Convert image to a different format (jpg, png, webp, gif, bmp)."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        fmt = inp.get("format", "webp").lower()
        quality = inp.get("quality", 90)
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image
        img = pil_open(image_path)
        if fmt in ("jpg", "jpeg"):
            img = img.convert("RGB")
        out_path = f"/tmp/converted_{ts()}.{fmt}"
        save_kwargs = {"quality": quality} if fmt in ("jpg","jpeg","webp") else {}
        img.save(out_path, format=fmt.upper() if fmt != "jpg" else "JPEG", **save_kwargs)
        size_kb = os.path.getsize(out_path) // 1024
        return ok("sam/convert-format", {"path": out_path, "format": fmt, "size_kb": size_kb}, t, req.task_id)
    except Exception as e:
        return err("sam/convert-format", e, t, req.task_id)


@router.post("/tools/sam/make-collage")
async def make_collage(req: ToolRequest):
    """Arrange multiple images into a grid collage."""
    t = time.time()
    try:
        inp = req.input
        images = inp.get("images", [])
        cols = inp.get("cols", 3)
        cell_size = inp.get("cell_size", 300)
        padding = inp.get("padding", 10)
        bg_color = tuple(inp.get("bg_color", [30, 30, 30]))
        if not images:
            raise ValueError("images list required")
        from PIL import Image
        rows = (len(images) + cols - 1) // cols
        W = cols * cell_size + (cols + 1) * padding
        H = rows * cell_size + (rows + 1) * padding
        canvas = Image.new("RGB", (W, H), bg_color)
        for i, img_path in enumerate(images[:cols*rows]):
            try:
                img = pil_open(img_path).convert("RGB")
                img.thumbnail((cell_size, cell_size), Image.LANCZOS)
                col = i % cols
                row = i // cols
                x = padding + col * (cell_size + padding)
                y = padding + row * (cell_size + padding)
                offset_x = (cell_size - img.width) // 2
                offset_y = (cell_size - img.height) // 2
                canvas.paste(img, (x + offset_x, y + offset_y))
            except Exception:
                pass
        path = pil_save(canvas, f"collage_{ts()}.jpg")
        return ok("sam/make-collage", {"path": path, "size": f"{W}x{H}", "images_used": len(images)}, t, req.task_id)
    except Exception as e:
        return err("sam/make-collage", e, t, req.task_id)


@router.post("/tools/sam/make-gif")
async def make_gif(req: ToolRequest):
    """Create an animated GIF from a list of images."""
    t = time.time()
    try:
        inp = req.input
        images = inp.get("images", [])
        fps = inp.get("fps", 2)
        loop = inp.get("loop", 0)
        if len(images) < 2:
            raise ValueError("At least 2 images required")
        from PIL import Image
        frames = []
        size = None
        for img_path in images[:30]:
            try:
                frame = pil_open(img_path).convert("RGB")
                if size is None:
                    size = (min(frame.width, 800), min(frame.height, 600))
                frame = frame.resize(size, Image.LANCZOS)
                frames.append(frame)
            except Exception:
                pass
        if not frames:
            raise ValueError("No valid frames")
        path = f"/tmp/anim_{ts()}.gif"
        duration_ms = int(1000 / fps)
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       loop=loop, duration=duration_ms, optimize=True)
        size_kb = os.path.getsize(path) // 1024
        return ok("sam/make-gif", {"path": path, "frames": len(frames), "fps": fps, "size_kb": size_kb}, t, req.task_id)
    except Exception as e:
        return err("sam/make-gif", e, t, req.task_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ORGANIZATION (5 tools)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/sam/smart-scan")
async def smart_scan(req: ToolRequest):
    """Batch scan a folder — analyze + tag each image. Google Photos style."""
    t = time.time()
    try:
        inp = req.input
        folder = inp.get("folder", "")
        limit = inp.get("limit", 10)
        if not folder or not os.path.isdir(folder):
            raise ValueError(f"Invalid folder: {folder}")
        files = []
        for pat in ["*.jpg", "*.jpeg", "*.png", "*.webp"]:
            files.extend(glob.glob(os.path.join(folder, pat)))
        files = sorted(files)[:limit]
        catalog = []
        async with httpx.AsyncClient(timeout=90) as c:
            for fpath in files:
                try:
                    b64 = to_b64(fpath)
                    r = await c.post(f"{OLLAMA_API}/api/generate", json={
                        "model": VISION_MODEL,
                        "prompt": "One sentence description, then 5 comma-separated tags on a new line.",
                        "images": [b64], "stream": False
                    })
                    raw = r.json().get("response", "")
                    parts = raw.split("\n", 1)
                    desc = parts[0].strip()
                    tags = [t.strip() for t in parts[1].split(",")] if len(parts) > 1 else []
                    catalog.append({"file": os.path.basename(fpath), "path": fpath,
                                    "description": desc, "tags": tags})
                except Exception as fe:
                    catalog.append({"file": os.path.basename(fpath), "error": str(fe)})
        return ok("sam/smart-scan", {"scanned": len(catalog), "folder": folder, "catalog": catalog}, t, req.task_id)
    except Exception as e:
        return err("sam/smart-scan", e, t, req.task_id)


@router.post("/tools/sam/find-duplicates")
async def find_duplicates(req: ToolRequest):
    """Find duplicate or near-duplicate images in a folder using perceptual hashing."""
    t = time.time()
    try:
        inp = req.input
        folder = inp.get("folder", "")
        if not folder or not os.path.isdir(folder):
            raise ValueError(f"Invalid folder: {folder}")
        from PIL import Image
        files = []
        for pat in ["*.jpg", "*.jpeg", "*.png", "*.webp"]:
            files.extend(glob.glob(os.path.join(folder, pat)))
        # Perceptual hash (average hash)
        def avg_hash(path):
            img = Image.open(path).convert("L").resize((8,8), Image.LANCZOS)
            pixels = list(img.getdata())
            avg = sum(pixels) / len(pixels)
            return "".join("1" if p >= avg else "0" for p in pixels)
        hashes = {}
        duplicates = []
        for f in files:
            try:
                h = avg_hash(f)
                if h in hashes:
                    duplicates.append({"original": hashes[h], "duplicate": f})
                else:
                    hashes[h] = f
            except Exception:
                pass
        return ok("sam/find-duplicates", {"duplicates": duplicates, "count": len(duplicates),
                                           "total_scanned": len(files)}, t, req.task_id)
    except Exception as e:
        return err("sam/find-duplicates", e, t, req.task_id)


@router.post("/tools/sam/batch-rename")
async def batch_rename(req: ToolRequest):
    """Rename images in a folder with a pattern (prefix + sequential number)."""
    t = time.time()
    try:
        inp = req.input
        folder = inp.get("folder", "")
        prefix = inp.get("prefix", "image")
        start = inp.get("start", 1)
        dry_run = inp.get("dry_run", True)
        if not folder or not os.path.isdir(folder):
            raise ValueError(f"Invalid folder: {folder}")
        files = sorted([f for ext in ["*.jpg","*.jpeg","*.png","*.webp"]
                        for f in glob.glob(os.path.join(folder, ext))])
        renames = []
        for i, fpath in enumerate(files):
            ext = os.path.splitext(fpath)[1]
            new_name = f"{prefix}_{start+i:04d}{ext}"
            new_path = os.path.join(folder, new_name)
            if not dry_run:
                os.rename(fpath, new_path)
            renames.append({"from": os.path.basename(fpath), "to": new_name})
        return ok("sam/batch-rename", {"renames": renames, "count": len(renames), "dry_run": dry_run}, t, req.task_id)
    except Exception as e:
        return err("sam/batch-rename", e, t, req.task_id)


@router.post("/tools/sam/build-gallery")
async def build_gallery(req: ToolRequest):
    """Generate a static HTML gallery page from a folder of images."""
    t = time.time()
    try:
        inp = req.input
        folder = inp.get("folder", "")
        title = inp.get("title", "Baza Empire Gallery")
        output_path = inp.get("output", "/tmp/gallery.html")
        if not folder or not os.path.isdir(folder):
            raise ValueError(f"Invalid folder: {folder}")
        files = []
        for pat in ["*.jpg","*.jpeg","*.png","*.webp","*.gif"]:
            files.extend(glob.glob(os.path.join(folder, pat)))
        files = sorted(files)
        items = "\n".join(
            f'<div class="item"><img src="{f}" loading="lazy"><p>{os.path.basename(f)}</p></div>'
            for f in files
        )
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body{{background:#111;color:#eee;font-family:sans-serif;margin:0;padding:20px}}
  h1{{text-align:center;color:#0af}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}}
  .item{{background:#222;border-radius:8px;overflow:hidden;text-align:center}}
  .item img{{width:100%;height:160px;object-fit:cover}}
  .item p{{margin:6px;font-size:11px;color:#aaa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
</style></head><body>
<h1>🖼 {title}</h1>
<p style="text-align:center;color:#666">{len(files)} images</p>
<div class="grid">{items}</div>
</body></html>"""
        with open(output_path, "w") as f:
            f.write(html)
        return ok("sam/build-gallery", {"path": output_path, "images": len(files), "title": title}, t, req.task_id)
    except Exception as e:
        return err("sam/build-gallery", e, t, req.task_id)


@router.post("/tools/sam/image-metadata")
async def image_metadata(req: ToolRequest):
    """Get full metadata for an image — size, format, mode, file size, hash."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image
        img = pil_open(image_path)
        file_size = os.path.getsize(image_path) if not image_path.startswith("http") else None
        md5 = None
        if not image_path.startswith("http"):
            with open(image_path, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
        return ok("sam/image-metadata", {
            "width": img.width, "height": img.height,
            "format": img.format, "mode": img.mode,
            "file_size_kb": (file_size // 1024) if file_size else None,
            "md5": md5, "path": image_path,
            "megapixels": round((img.width * img.height) / 1_000_000, 2)
        }, t, req.task_id)
    except Exception as e:
        return err("sam/image-metadata", e, t, req.task_id)


# ═══════════════════════════════════════════════════════════════════════════════
# RESTORATION & QUALITY (12 tools)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/tools/sam/regen-image")
async def regen_image(req: ToolRequest):
    """Fully regenerate an image using its original prompt + new seed or higher steps."""
    t = time.time()
    try:
        inp = req.input
        prompt = inp.get("prompt", "")
        if not prompt:
            raise ValueError("prompt required for regen")
        model = _pick_model(prompt)
        await _set_model(model)
        payload = {
            "prompt": prompt + ", masterpiece, best quality, ultra detailed, 8k",
            "negative_prompt": inp.get("negative_prompt",
                "blurry, low quality, jpeg artifacts, noise, watermark, oversaturated, EasyNegative"),
            "width":  inp.get("width", 1024),
            "height": inp.get("height", 1024),
            "steps":  inp.get("steps", 8),
            "cfg_scale": inp.get("cfg_scale", 2.0),
            "seed":   inp.get("seed", -1),
            "sampler_name": "DPM++ SDE Karras",
        }
        async with httpx.AsyncClient(timeout=240) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/txt2img", json=payload)
            r.raise_for_status()
            data = r.json()
        info = json.loads(data.get("info", "{}"))
        path = save_b64(data["images"][0], f"regen_{ts()}.png")
        return ok("sam/regen-image", {"path": path, "seed": info.get("seed", -1),
                                       "steps": payload["steps"]}, t, req.task_id)
    except Exception as e:
        return err("sam/regen-image", e, t, req.task_id)


@router.post("/tools/sam/denoise-image")
async def denoise_image(req: ToolRequest):
    """Remove noise and grain from an image using OpenCV denoising."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        strength = inp.get("strength", 10)  # 1-30, higher = more smoothing
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request, io
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        denoised = cv2.fastNlMeansDenoisingColored(img, None, strength, strength, 7, 21)
        path = f"/tmp/denoised_{ts()}.png"
        cv2.imwrite(path, denoised)
        return ok("sam/denoise-image", {"path": path, "strength": strength}, t, req.task_id)
    except Exception as e:
        return err("sam/denoise-image", e, t, req.task_id)


@router.post("/tools/sam/deblur-image")
async def deblur_image(req: ToolRequest):
    """Sharpen and deblur an image using unsharp mask + Wiener deconvolution."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        amount = inp.get("amount", 2.0)   # sharpening amount
        radius = inp.get("radius", 1.5)
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Unsharp mask
        blur = cv2.GaussianBlur(img, (0, 0), radius)
        sharp = cv2.addWeighted(img, 1 + amount, blur, -amount, 0)
        path = f"/tmp/deblurred_{ts()}.png"
        cv2.imwrite(path, sharp)
        return ok("sam/deblur-image", {"path": path, "amount": amount, "radius": radius}, t, req.task_id)
    except Exception as e:
        return err("sam/deblur-image", e, t, req.task_id)


@router.post("/tools/sam/fix-pixels")
async def fix_pixels(req: ToolRequest):
    """Pixel-level correction — fix stuck/dead pixels, remove artifacts, clean hot spots."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        threshold = inp.get("threshold", 30)  # pixel deviation threshold
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Detect outlier pixels via median filter comparison
        median = cv2.medianBlur(img, 3)
        diff = cv2.absdiff(img, median)
        mask = (diff.max(axis=2) > threshold).astype(np.uint8) * 255
        fixed = img.copy()
        fixed[mask == 255] = median[mask == 255]
        fixed_pixels = int(mask.sum() // 255)
        path = f"/tmp/fixpixels_{ts()}.png"
        cv2.imwrite(path, fixed)
        return ok("sam/fix-pixels", {"path": path, "pixels_fixed": fixed_pixels,
                                      "threshold": threshold}, t, req.task_id)
    except Exception as e:
        return err("sam/fix-pixels", e, t, req.task_id)


@router.post("/tools/sam/restore-image")
async def restore_image(req: ToolRequest):
    """Full restoration pipeline — denoise → deblur → enhance. Old/damaged photo repair."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Step 1: denoise
        denoised = cv2.fastNlMeansDenoisingColored(img, None, 8, 8, 7, 21)
        # Step 2: unsharp mask
        blur = cv2.GaussianBlur(denoised, (0, 0), 1.2)
        sharp = cv2.addWeighted(denoised, 1.5, blur, -0.5, 0)
        # Step 3: CLAHE contrast enhancement
        lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge((l, a, b))
        result = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        path = f"/tmp/restored_{ts()}.png"
        cv2.imwrite(path, result)
        return ok("sam/restore-image", {"path": path, "steps": ["denoise", "deblur", "clahe_enhance"]}, t, req.task_id)
    except Exception as e:
        return err("sam/restore-image", e, t, req.task_id)


@router.post("/tools/sam/auto-enhance")
async def auto_enhance(req: ToolRequest):
    """Auto-enhance an image — auto white balance, exposure fix, vibrance boost."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Auto white balance (gray world assumption)
        result = img.copy().astype(np.float32)
        avg_b = np.mean(result[:,:,0])
        avg_g = np.mean(result[:,:,1])
        avg_r = np.mean(result[:,:,2])
        avg_all = (avg_b + avg_g + avg_r) / 3
        result[:,:,0] = np.clip(result[:,:,0] * (avg_all / avg_b), 0, 255)
        result[:,:,1] = np.clip(result[:,:,1] * (avg_all / avg_g), 0, 255)
        result[:,:,2] = np.clip(result[:,:,2] * (avg_all / avg_r), 0, 255)
        result = result.astype(np.uint8)
        # Auto CLAHE on L channel
        lab = cv2.cvtColor(result, cv2.COLOR_BGR2LAB)
        l, a, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        l = clahe.apply(l)
        result = cv2.cvtColor(cv2.merge((l, a, b_ch)), cv2.COLOR_LAB2BGR)
        path = f"/tmp/autoenhanced_{ts()}.png"
        cv2.imwrite(path, result)
        return ok("sam/auto-enhance", {"path": path,
                                        "applied": ["auto_white_balance", "clahe_exposure"]}, t, req.task_id)
    except Exception as e:
        return err("sam/auto-enhance", e, t, req.task_id)


@router.post("/tools/sam/hdr-tone-map")
async def hdr_tone_map(req: ToolRequest):
    """Apply HDR-style tone mapping for dramatic cinematic look."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        gamma = inp.get("gamma", 1.0)
        saturation = inp.get("saturation", 1.2)
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        img_f = img.astype(np.float32) / 255.0
        # Reinhard tone mapping
        tonemap = cv2.createTonemapReinhard(gamma=gamma, intensity=0.0,
                                             light_adapt=0.0, color_adapt=0.0)
        mapped = tonemap.process(img_f)
        mapped = np.clip(mapped * 255, 0, 255).astype(np.uint8)
        # Boost saturation
        hsv = cv2.cvtColor(mapped, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:,:,1] = np.clip(hsv[:,:,1] * saturation, 0, 255)
        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        path = f"/tmp/hdr_{ts()}.png"
        cv2.imwrite(path, result)
        return ok("sam/hdr-tone-map", {"path": path, "gamma": gamma, "saturation": saturation}, t, req.task_id)
    except Exception as e:
        return err("sam/hdr-tone-map", e, t, req.task_id)


@router.post("/tools/sam/jpeg-artifact-fix")
async def jpeg_artifact_fix(req: ToolRequest):
    """Remove JPEG compression artifacts and blocking from over-compressed images."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Mild bilateral filter removes blocking while preserving edges
        result = cv2.bilateralFilter(img, d=9, sigmaColor=75, sigmaSpace=75)
        # Light unsharp to recover edge crispness
        blur = cv2.GaussianBlur(result, (0, 0), 1.0)
        result = cv2.addWeighted(result, 1.3, blur, -0.3, 0)
        path = f"/tmp/jpeg_fixed_{ts()}.png"
        cv2.imwrite(path, result)
        return ok("sam/jpeg-artifact-fix", {"path": path, "method": "bilateral+unsharp"}, t, req.task_id)
    except Exception as e:
        return err("sam/jpeg-artifact-fix", e, t, req.task_id)


@router.post("/tools/sam/colorize-image")
async def colorize_image(req: ToolRequest):
    """Colorize a black & white image using SD img2img colorization."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        prompt = inp.get("prompt", "colorized photograph, vibrant natural colors, realistic")
        if not image_path:
            raise ValueError("image_path required")
        from PIL import Image as PILImage
        img = pil_open(image_path).convert("RGB")
        # Ensure it's grayscale-looking for SD
        tmp = pil_save(img, f"bw_input_{ts()}.png")
        b64 = to_b64(tmp)
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/img2img", json={
                "init_images": [b64],
                "prompt": prompt,
                "negative_prompt": "black and white, grayscale, monochrome, blurry",
                "denoising_strength": 0.55,
                "steps": 30,
                "cfg_scale": 7,
            })
            r.raise_for_status()
            data = r.json()
        path = save_b64(data["images"][0], f"colorized_{ts()}.png")
        return ok("sam/colorize-image", {"path": path, "prompt": prompt}, t, req.task_id)
    except Exception as e:
        return err("sam/colorize-image", e, t, req.task_id)


@router.post("/tools/sam/super-resolution")
async def super_resolution(req: ToolRequest):
    """AI super-resolution — upscale small/low-res images to high detail using SD hi-res fix."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        target_w = inp.get("target_width", 1024)
        target_h = inp.get("target_height", 1024)
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        # Use SD extra-single-image with LDSR or R-ESRGAN 4x+
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/extra-single-image", json={
                "image": b64,
                "resize_mode": 1,
                "upscaling_resize_w": target_w,
                "upscaling_resize_h": target_h,
                "upscaler_1": "R-ESRGAN 4x+",
                "upscaler_2": "ESRGAN_4x",
                "extras_upscaler_2_visibility": 0.3,
            })
            r.raise_for_status()
            out_b64 = r.json().get("image", "")
        path = save_b64(out_b64, f"superres_{ts()}.png")
        return ok("sam/super-resolution", {"path": path,
                                            "target_size": f"{target_w}x{target_h}",
                                            "method": "R-ESRGAN 4x+ + ESRGAN blend"}, t, req.task_id)
    except Exception as e:
        return err("sam/super-resolution", e, t, req.task_id)


@router.post("/tools/sam/face-restore")
async def face_restore(req: ToolRequest):
    """Restore and enhance faces in an image using GFPGAN via SD WebUI."""
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        b64 = to_b64(image_path)
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{SD_API}/sdapi/v1/extra-single-image", json={
                "image": b64,
                "resize_mode": 0,
                "upscaling_resize": 1,
                "upscaler_1": "None",
                "gfpgan_visibility": inp.get("strength", 0.8),
                "codeformer_visibility": inp.get("codeformer", 0.5),
                "codeformer_weight": 0.5,
            })
            r.raise_for_status()
            out_b64 = r.json().get("image", "")
        path = save_b64(out_b64, f"facerestored_{ts()}.png")
        return ok("sam/face-restore", {"path": path,
                                        "gfpgan": inp.get("strength", 0.8),
                                        "codeformer": inp.get("codeformer", 0.5)}, t, req.task_id)
    except Exception as e:
        return err("sam/face-restore", e, t, req.task_id)


@router.post("/tools/sam/bit-depth-enhance")
async def bit_depth_enhance(req: ToolRequest):
    """
    Bit depth enhancement — dequantize 8-bit banding artifacts,
    smooth tonal gradients, expand dynamic range via histogram stretching.
    """
    t = time.time()
    try:
        inp = req.input
        image_path = inp.get("image_path", "")
        if not image_path:
            raise ValueError("image_path required")
        import cv2, numpy as np, urllib.request
        if image_path.startswith("http"):
            with urllib.request.urlopen(image_path) as r:
                arr = np.frombuffer(r.read(), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(image_path)
        # Step 1: Histogram stretching (per channel)
        result = np.zeros_like(img, dtype=np.float32)
        for ch in range(3):
            channel = img[:,:,ch].astype(np.float32)
            p2, p98 = np.percentile(channel, 2), np.percentile(channel, 98)
            if p98 > p2:
                result[:,:,ch] = np.clip((channel - p2) / (p98 - p2) * 255, 0, 255)
            else:
                result[:,:,ch] = channel
        result = result.astype(np.uint8)
        # Step 2: Add slight dithering noise to break up banding
        noise = np.random.randint(0, 3, result.shape, dtype=np.uint8)
        result = cv2.add(result, noise)
        # Step 3: Mild bilateral smooth to blend gradients
        result = cv2.bilateralFilter(result, 5, 35, 35)
        path = f"/tmp/bitdepth_{ts()}.png"
        cv2.imwrite(path, result)
        return ok("sam/bit-depth-enhance", {"path": path,
                                              "steps": ["histogram_stretch", "dither", "bilateral_smooth"]}, t, req.task_id)
    except Exception as e:
        return err("sam/bit-depth-enhance", e, t, req.task_id)
