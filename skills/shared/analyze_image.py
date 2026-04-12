#!/usr/bin/env python3
"""
Baza Empire — ArchiteCT Image Analysis Skill
Sends images to a vision-capable model via LiteLLM proxy for analysis.
Supports: room analysis, blueprint reading, receipt OCR, general description.

SKILL_ARGS:
  image_path: "/path/to/image.jpg"
  prompt: "Describe this room. Note dimensions, materials, condition."
  mode: "analyze" | "describe_for_agents" | "extract_text"  (default: analyze)
"""
import os
import sys
import json
import base64
import urllib.request
import urllib.error

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
image_path = args.get("image_path", "")
prompt = args.get("prompt", "")
mode = args.get("mode", "analyze")

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "baza-litellm")
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-4o-mini")

# ── Mode-specific system prompts ─────────────────────────────────────────────

SYSTEM_PROMPTS = {
    "analyze": (
        "You are an expert construction analyst for a Philadelphia residential GC (All Home Building Co). "
        "Analyze the image and provide:\n"
        "1. What you see (room type, layout, materials, fixtures)\n"
        "2. Approximate dimensions if visible\n"
        "3. Current condition (good/fair/poor, any damage)\n"
        "4. Work needed or opportunities for renovation\n"
        "5. Estimated scope category (kitchen/bathroom/addition/etc.)\n"
        "Be specific and practical. Use construction terminology."
    ),
    "describe_for_agents": (
        "You are a visual analysis engine creating an EXHAUSTIVE description of this image. "
        "Another AI (an image generation model) must be able to recreate this image from your description alone. "
        "A human operator and AI agents will also use this description as their complete context for editing, "
        "transforming, and discussing this image. Therefore you MUST capture EVERYTHING.\n\n"
        "Output a structured markdown document covering ALL of the following:\n\n"
        "## Scene Overview\n"
        "What is this image of? (room, exterior, landscape, object, blueprint, etc.) "
        "Camera angle/perspective (eye-level, bird's-eye, wide-angle, close-up). "
        "Lighting conditions (natural/artificial, direction, intensity, time of day if applicable). "
        "Overall mood/atmosphere.\n\n"
        "## Objects & Elements Inventory\n"
        "List EVERY identifiable object, structure, surface, and element in the image. "
        "For EACH object note:\n"
        "- What it is (specific type, e.g. 'double-hung vinyl window' not just 'window')\n"
        "- Material (wood, metal, concrete, fabric, glass, etc.)\n"
        "- Color/finish (be precise: 'warm oak stain', 'matte charcoal gray', 'off-white eggshell')\n"
        "- Estimated size/dimensions relative to the scene\n"
        "- Position in the frame (left/right/center, foreground/midground/background, floor/wall/ceiling)\n"
        "- Condition (new, worn, damaged, stained, etc.)\n"
        "- Any text, labels, or markings visible on it\n\n"
        "## Spatial Layout & Geometry\n"
        "Describe the spatial arrangement: what is next to what, what overlaps, what is behind what. "
        "Estimate room/space dimensions if applicable. Note floor plan shape, ceiling height, "
        "door/window placement relative to walls. Describe depth and layering of the scene.\n\n"
        "## Surfaces & Materials Detail\n"
        "For every visible surface (floors, walls, ceiling, counters, etc.):\n"
        "- Material type (hardwood, tile, drywall, brick, stucco, etc.)\n"
        "- Color and pattern (solid, striped, textured, grain direction)\n"
        "- Finish (matte, satin, glossy, rough, polished)\n"
        "- Condition and any damage/wear visible\n\n"
        "## Colors & Palette\n"
        "Dominant colors, accent colors, and the overall color temperature (warm/cool/neutral). "
        "Note any color contrasts or harmonies.\n\n"
        "## Architectural / Structural Details\n"
        "Moldings, trim, baseboards, crown molding, wainscoting, columns, arches, beams, "
        "hardware (handles, hinges, fixtures), electrical (outlets, switches, panels), "
        "plumbing fixtures if visible. Note style (modern, traditional, industrial, etc.).\n\n"
        "## Condition Assessment\n"
        "Overall condition rating. Note any damage, wear, staining, missing elements, "
        "code concerns, or areas needing repair/renovation.\n\n"
        "## Image Generation Prompt\n"
        "Write a single dense paragraph (Stable Diffusion style prompt) that could reproduce "
        "this exact image. Include style keywords, camera angle, lighting, and all key elements.\n\n"
        "BE EXHAUSTIVE. Miss nothing. If you can see it, describe it. "
        "This description is the ONLY context the team has — they cannot see the image themselves."
    ),
    "extract_text": (
        "Extract ALL text visible in this image. This is likely a receipt, invoice, or document. "
        "Output the text exactly as written, preserving layout. Then provide a structured summary:\n"
        "- Vendor/Business Name:\n- Date:\n- Total Amount:\n- Line Items (if visible):\n- Tax:\n"
        "If any field is unclear, note it as '(unclear)'."
    ),
}

# ── Validate inputs ──────────────────────────────────────────────────────────

if not image_path:
    print(json.dumps({"error": "image_path is required"}))
    sys.exit(1)

if not os.path.exists(image_path):
    print(json.dumps({"error": f"Image not found: {image_path}"}))
    sys.exit(1)

# ── Encode image ─────────────────────────────────────────────────────────────

ext = os.path.splitext(image_path)[1].lower()
mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp"}
mime_type = mime_map.get(ext, "image/jpeg")

with open(image_path, "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")

# ── Build request ────────────────────────────────────────────────────────────

system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["analyze"])
user_prompt = prompt if prompt else {
    "analyze": "Analyze this image for a construction/renovation project.",
    "describe_for_agents": "Create a structured description of this image for other agents.",
    "extract_text": "Extract all text from this image.",
}.get(mode, "Describe this image.")

payload = json.dumps({
    "model": VISION_MODEL,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {
                "url": f"data:{mime_type};base64,{image_data}"
            }}
        ]}
    ],
    "max_tokens": 4000,
    "temperature": 0.3,
}).encode("utf-8")

# ── Send to vision API — try multiple backends ──────────────────────────────

def _try_openai_compatible(url, api_key, model, messages, timeout=60):
    """Send vision request to any OpenAI-compatible endpoint."""
    req_payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 4000,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
        return result.get("choices", [{}])[0].get("message", {}).get("content", ""), result.get("usage", {}), model

def _try_ollama_vision(image_path, prompt, system_prompt, timeout=120):
    """Send to local Ollama with a vision model (qwen3-vl or llava)."""
    img_b64 = base64.b64encode(open(image_path, "rb").read()).decode("utf-8")
    # Try vision models in preference order
    vision_models = ["qwen3-vl:latest", "llava:13b"]
    # Smart port selection: check which port already has a vision model loaded
    # NVIDIA (11435) is reserved for SD WebUI — only AMD (11434/11437) and CPU (11436)
    ports = [11434, 11437, 11436]
    hot_port = None
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ps", timeout=2) as r:
                ps = json.loads(r.read())
                loaded = [m["name"] for m in ps.get("models", [])]
                for vm in vision_models:
                    if vm in loaded:
                        hot_port = port
                        break
            if hot_port:
                break
        except Exception:
            continue
    # If a port already has the model, try it first; otherwise default order
    if hot_port:
        ports = [hot_port] + [p for p in ports if p != hot_port]

    for model in vision_models:
        for port in ports:
            try:
                # High num_predict because vision models (qwen3-vl) use thinking tokens
                # before generating content — need 1500+ for thinking + 1000+ for output
                ollama_payload = json.dumps({
                    "model": model,
                    "stream": False,
                    "options": {"num_predict": 3000, "temperature": 0.3, "num_ctx": 8192},
                    "messages": [
                        {"role": "system", "content": system_prompt[:800]},
                        {"role": "user", "content": prompt, "images": [img_b64]}
                    ]
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"http://localhost:{port}/api/chat",
                    data=ollama_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    result = json.loads(resp.read())
                    content = result.get("message", {}).get("content", "")
                    if content:
                        return content, {"total_tokens": result.get("eval_count", 0)}, f"ollama/{model} (port {port})"
            except Exception:
                continue
    return None, {}, ""

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": [
        {"type": "text", "text": user_prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}}
    ]}
]

content = None
usage = {}
model_used = ""
errors = []

# Strategy 1: Local Ollama vision FIRST (free, no API key, always available)
try:
    content, usage, model_used = _try_ollama_vision(image_path, user_prompt, system_prompt)
except Exception as e:
    errors.append(f"Ollama vision: {str(e)[:100]}")

# Strategy 2: LiteLLM proxy (only if Ollama failed AND proxy is healthy)
if not content:
    try:
        urllib.request.urlopen(f"{LITELLM_URL}/health", timeout=2)
        content, usage, model_used = _try_openai_compatible(
            f"{LITELLM_URL}/v1/chat/completions", LITELLM_KEY, VISION_MODEL, messages, timeout=30
        )
    except Exception as e:
        errors.append(f"LiteLLM: {str(e)[:80]}")

# Strategy 3: Direct OpenAI (if key available)
if not content:
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        try:
            content, usage, model_used = _try_openai_compatible(
                "https://api.openai.com/v1/chat/completions", openai_key, "gpt-4o-mini", messages
            )
        except Exception as e:
            errors.append(f"OpenAI direct: {str(e)[:100]}")

# Strategy 4: Direct Gemini via OpenAI-compat (if key available)
if not content:
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            content, usage, model_used = _try_openai_compatible(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                gemini_key, "gemini-2.0-flash", messages
            )
        except Exception as e:
            errors.append(f"Gemini direct: {str(e)[:100]}")

if content:
    print(f"IMAGE ANALYSIS ({mode}): {os.path.basename(image_path)}")
    print(f"Model: {model_used}")
    print(f"Tokens: {usage.get('total_tokens', 'N/A')}")
    print("---")
    print(content)
    print()
    print(json.dumps({
        "success": True,
        "mode": mode,
        "image": os.path.basename(image_path),
        "model": model_used,
        "analysis": content,
        "tokens": usage.get("total_tokens", 0),
    }))
else:
    print(json.dumps({
        "success": False,
        "error": "All vision backends failed: " + "; ".join(errors),
        "hint": "Need one of: LiteLLM on port 4000, OPENAI_API_KEY, GEMINI_API_KEY, or Ollama with llava:13b pulled."
    }))
