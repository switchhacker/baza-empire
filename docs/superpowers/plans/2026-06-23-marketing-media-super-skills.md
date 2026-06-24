# Marketing & Media Super Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four agent-facing "super skills" (`brand_kit`, `social_campaign`, `before_after_showcase`, `marketing_flyer`) plus one shared `media_kit` library that turn a topic / project / job photos into polished on-brand AHBCO marketing deliverables — local-first, photo-first, saved as artifacts and queued (never auto-published) to Social Studio.

**Architecture:** A single source of truth `brand.json` + a shared `skills/shared/media_kit.py` helper (Pillow compositing, local-Ollama copywriting, SD-WebUI backgrounds, binary artifact save, Social Studio queue). Four thin orchestrator skills import `media_kit` and follow the existing `##SKILL##` subprocess contract (args via `SKILL_ARGS`, JSON to stdout).

**Tech Stack:** Python 3, Pillow 12.1.1, `requests`, SQLite (`baza_projects.db`), local Ollama (`:11434`), Sam Tool Server (`:8000`) / SD WebUI Forge (`:7860`). Tests: pytest with monkeypatched network.

---

## Conventions (read once)

- **Run all commands from** `/home/switchhacker/baza-empire/agent-framework-v3` with the venv active:
  `source venv/bin/activate`.
- **Run tests with the venv python**: `python -m pytest <path> -v`.
- **Skill contract:** read `SKILL_ARGS` (JSON) from env, print one JSON object to stdout, exit 0. Skills live in `skills/shared/`.
- **Local-first (HARD):** copy = local Ollama only (exclude any model name containing `cloud`); imagery = local SD WebUI. No cloud LLM calls.
- **Never auto-publish:** queued social posts get `status='draft'` (Social Studio's awaiting-review state).
- **Commit after every task** (auto-git also runs hourly, but commit explicitly so each task is atomic).

### File structure (locked)

| File | Responsibility |
|------|----------------|
| `agents/sam_axe/brand/brand.json` | Brand source of truth (created by Task 1, refreshed by `brand_kit`) |
| `agents/sam_axe/brand/assets/` | Downloaded logo + any bundled assets |
| `skills/shared/media_kit.py` | Shared lib: brand, canvas, compositing, copy, SD bg, artifact save, social queue |
| `skills/shared/brand_kit.py` | Skill: detect/show/set brand |
| `skills/shared/social_campaign.py` | Skill: topic/project → per-platform post pack + queue |
| `skills/shared/before_after_showcase.py` | Skill: two photos/project → branded comparison graphic |
| `skills/shared/marketing_flyer.py` | Skill: offer/service → branded flyer/ad |
| `tests/test_media_kit.py` | Unit tests for media_kit |
| `tests/test_brand_kit.py` | Tests for brand_kit skill |
| `tests/test_social_campaign.py` | Tests for social_campaign skill |
| `tests/test_before_after_showcase.py` | Tests for before_after_showcase skill |
| `tests/test_marketing_flyer.py` | Tests for marketing_flyer skill |

---

## Task 1: `media_kit` — brand loading + defaults

**Files:**
- Create: `skills/shared/media_kit.py`
- Create: `tests/test_media_kit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_kit.py
import importlib.util, os, sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("media_kit", FRAMEWORK / "skills/shared/media_kit.py")
media_kit = importlib.util.module_from_spec(spec)
sys.modules["media_kit"] = media_kit
spec.loader.exec_module(media_kit)


def test_load_brand_returns_defaults_when_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(media_kit, "BRAND_PATH", missing)
    brand = media_kit.load_brand()
    assert brand["short_name"] == "AHBCO"
    assert brand["colors"]["primary"].startswith("#")
    assert "headline" in brand["fonts"] and "body" in brand["fonts"]


def test_hex_to_rgb():
    assert media_kit.hex_to_rgb("#0A3D62") == (10, 61, 98)
    assert media_kit.hex_to_rgb("FFFFFF") == (255, 255, 255)


def test_load_brand_merges_partial_file(tmp_path, monkeypatch):
    p = tmp_path / "brand.json"
    p.write_text('{"colors": {"primary": "#112233"}}')
    monkeypatch.setattr(media_kit, "BRAND_PATH", p)
    brand = media_kit.load_brand()
    assert brand["colors"]["primary"] == "#112233"   # override kept
    assert brand["colors"]["accent"].startswith("#")  # default filled in
    assert brand["short_name"] == "AHBCO"             # default filled in
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_kit.py -v`
Expected: FAIL — `media_kit.py` does not exist (import error / file not found).

- [ ] **Step 3: Write minimal implementation**

```python
# skills/shared/media_kit.py
#!/usr/bin/env python3
"""media_kit — shared helpers for AHBCO marketing/media super skills.

Brand source of truth, Pillow compositing, local-Ollama copywriting,
SD-WebUI backgrounds, binary artifact save, and Social Studio queueing.
All marketing skills import this module. Local-first, photo-first.
"""
import os, json, copy
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent.parent
BRAND_DIR     = FRAMEWORK_DIR / "agents" / "sam_axe" / "brand"
BRAND_PATH    = BRAND_DIR / "brand.json"
ASSETS_DIR    = BRAND_DIR / "assets"

# System fonts present on baza (verified): DejaVu (default) + Liberation (condensed alt).
_DEJAVU = "/usr/share/fonts/truetype/dejavu"
DEFAULT_BRAND = {
    "version": 1,
    "name": "All Home Building Co",
    "short_name": "AHBCO",
    "tagline": "Drown the competition.",
    "site": "https://ahb123.com",
    "colors": {
        "primary":   "#0A3D62",
        "secondary": "#1E90FF",
        "accent":    "#F39C12",
        "light":     "#F5F7FA",
        "dark":      "#13202E",
    },
    "fonts": {
        "headline": f"{_DEJAVU}/DejaVuSans-Bold.ttf",
        "body":     f"{_DEJAVU}/DejaVuSans.ttf",
    },
    "logo": "",          # absolute path once detected; "" => text wordmark fallback
    "voice": "confident, local, trustworthy, no jargon",
}


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_brand() -> dict:
    """Return brand.json merged over DEFAULT_BRAND (defaults fill any gaps)."""
    try:
        data = json.loads(Path(BRAND_PATH).read_text())
    except Exception:
        data = {}
    return _deep_merge(DEFAULT_BRAND, data)


def save_brand(brand: dict) -> dict:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    Path(BRAND_PATH).write_text(json.dumps(brand, indent=2))
    return {"path": str(BRAND_PATH)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_kit.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/media_kit.py tests/test_media_kit.py
git commit -m "feat(media): media_kit brand loading + defaults"
```

---

## Task 2: `media_kit` — local-Ollama copywriting (auto-pick model)

**Files:**
- Modify: `skills/shared/media_kit.py` (append)
- Modify: `tests/test_media_kit.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_media_kit.py

def test_pick_copy_model_excludes_cloud(monkeypatch):
    tags = {"models": [
        {"name": "gpt-oss:120b-cloud"},
        {"name": "qwen3-vl:latest"},        # vision -> skip
        {"name": "glm-ocr:latest"},          # ocr -> skip
        {"name": "qwen2.5:0.5b"},            # too small -> deprioritized
        {"name": "gemma4:26b-a4b-it-qat"},   # best general instruct
    ]}
    class R:
        status_code = 200
        def json(self): return tags
    monkeypatch.setattr(media_kit.requests, "get", lambda *a, **k: R())
    model = media_kit.pick_copy_model()
    assert model == "gemma4:26b-a4b-it-qat"
    assert "cloud" not in model


def test_pick_copy_model_none_when_unreachable(monkeypatch):
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(media_kit.requests, "get", boom)
    assert media_kit.pick_copy_model() is None


def test_write_copy_template_fallback_when_no_model(monkeypatch):
    monkeypatch.setattr(media_kit, "pick_copy_model", lambda: None)
    brand = media_kit.load_brand()
    out = media_kit.write_copy("kitchen remodel reveal", brand, kind="caption")
    assert out["caption"]                      # non-empty
    assert isinstance(out["hashtags"], list) and out["hashtags"]
    assert out["model"] == "template"


def test_write_copy_uses_model(monkeypatch):
    monkeypatch.setattr(media_kit, "pick_copy_model", lambda: "gemma4:26b-a4b-it-qat")
    payload = {"caption": "Fresh kitchen, fresh start.",
               "hashtags": ["#remodel", "#AHBCO"], "first_comment": "DM us!"}
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": json.dumps(payload)}}
    monkeypatch.setattr(media_kit.requests, "post", lambda *a, **k: R())
    import json
    brand = media_kit.load_brand()
    out = media_kit.write_copy("kitchen remodel", brand)
    assert out["caption"] == "Fresh kitchen, fresh start."
    assert "#AHBCO" in out["hashtags"]
    assert out["model"] == "gemma4:26b-a4b-it-qat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_kit.py -k copy_model or write_copy -v`
Expected: FAIL — `pick_copy_model` / `write_copy` / `media_kit.requests` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to skills/shared/media_kit.py
import re
import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Substrings that disqualify a model for copywriting.
_BAD_MODEL = ("cloud", "-vl", "vision", "ocr", "coder", "embed", "minicpm")
# Preference order: first substring match wins a higher rank.
_PREF = ("gemma4:26b", "qwen3.6:27b", "nemotron", "gemma4:12b",
         "ministral", "gemma4:e4b", "lfm2", "gemma4")


def pick_copy_model():
    """Pick the strongest installed LOCAL general chat model. None if unreachable."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=6)
        if r.status_code != 200:
            return None
        names = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None
    cands = [n for n in names if not any(b in n.lower() for b in _BAD_MODEL)]
    if not cands:
        return None

    def rank(n):
        for i, p in enumerate(_PREF):
            if p in n:
                return i
        return len(_PREF) + 1
    cands.sort(key=rank)
    return cands[0]


def _ollama_chat(model, prompt, timeout=90):
    r = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
    }, timeout=timeout)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def _template_copy(brief, brand):
    short = brand["short_name"]
    cap = f"{brief.strip().capitalize()} — done right by {short}. {brand['tagline']}"
    tags = ["#AHBCO", "#HomeBuilding", "#Remodel", "#Contractor", "#HomeImprovement"]
    return {"caption": cap, "hashtags": tags,
            "first_comment": f"Get a free quote from {short} today.",
            "model": "template"}


def write_copy(brief, brand, kind="caption"):
    """Generate {caption, hashtags[], first_comment, model} in brand voice.
    Local Ollama only; deterministic template fallback if no model reachable."""
    model = pick_copy_model()
    if not model:
        return _template_copy(brief, brand)
    prompt = (
        f"You are the marketing copywriter for {brand['name']} ({brand['short_name']}), "
        f"a home building & remodeling company. Brand voice: {brand['voice']}. "
        f"Tagline: {brand['tagline']}.\n"
        f"Write a {kind} for this brief: {brief}\n"
        f"Return ONLY a JSON object with keys: caption (string, <= 280 chars), "
        f"hashtags (array of 4-8 strings each starting with #), "
        f"first_comment (string)."
    )
    try:
        raw = _ollama_chat(model, prompt)
        data = json.loads(raw)
        caption = str(data.get("caption", "")).strip()
        tags = [str(t) for t in data.get("hashtags", []) if str(t).strip()]
        if not caption or not tags:
            raise ValueError("empty fields")
        return {"caption": caption, "hashtags": tags,
                "first_comment": str(data.get("first_comment", "")).strip(),
                "model": model}
    except Exception:
        out = _template_copy(brief, brand)
        out["model"] = "template"
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_kit.py -v`
Expected: PASS (all media_kit tests).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/media_kit.py tests/test_media_kit.py
git commit -m "feat(media): local-Ollama copywriting with auto model pick + template fallback"
```

---

## Task 3: `media_kit` — canvas + compositing primitives

**Files:**
- Modify: `skills/shared/media_kit.py` (append)
- Modify: `tests/test_media_kit.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_media_kit.py
from PIL import Image

def test_platforms_have_expected_sizes():
    assert media_kit.PLATFORMS["ig_square"] == (1080, 1080)
    assert media_kit.PLATFORMS["ig_reel"] == (1080, 1920)
    assert media_kit.PLATFORMS["fb"] == (1200, 630)
    assert media_kit.PLATFORMS["yt_thumb"] == (1280, 720)


def test_new_canvas_size_and_mode():
    img = media_kit.new_canvas("ig_square")
    assert img.size == (1080, 1080)
    assert img.mode == "RGB"


def test_load_photo_cover_fit(tmp_path):
    src = tmp_path / "p.png"
    Image.new("RGB", (2000, 500), (200, 100, 50)).save(src)
    out = media_kit.load_photo(str(src), (1080, 1080))
    assert out.size == (1080, 1080)   # cover-cropped to exact target


def test_draw_headline_and_logo_change_pixels():
    img = media_kit.new_canvas("ig_square", bg=(20, 20, 20))
    before = list(img.getdata())
    brand = media_kit.load_brand()
    media_kit.draw_headline(img, "KITCHEN REMODEL", (60, 700, 1020, 1000),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"])
    media_kit.scrim(img, side="bottom", height_frac=0.4)
    after = list(img.getdata())
    assert before != after            # something was drawn


def test_place_text_logo_fallback_when_no_file(tmp_path):
    img = media_kit.new_canvas("ig_square")
    brand = media_kit.load_brand()
    brand["logo"] = ""                 # force wordmark fallback
    # should not raise
    media_kit.place_logo(img, brand, corner="br")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_kit.py -k "platforms or canvas or photo or headline or logo" -v`
Expected: FAIL — `PLATFORMS` / `new_canvas` / etc. not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to skills/shared/media_kit.py
from PIL import Image, ImageDraw, ImageFont

PLATFORMS = {
    "ig_square":     (1080, 1080),
    "ig_reel":       (1080, 1920),
    "ig_feed_square":(1080, 1080),
    "tiktok":        (1080, 1920),
    "fb":            (1200, 630),
    "yt_thumb":      (1280, 720),
    "flyer_portrait":(1275, 1650),   # 8.5x11 @ 150 dpi
    "ad_square":     (1080, 1080),
    "ad_landscape":  (1200, 628),
}


def new_canvas(platform, bg=None):
    w, h = PLATFORMS[platform]
    if bg is None:
        bg = hex_to_rgb(load_brand()["colors"]["dark"])
    return Image.new("RGB", (w, h), bg)


def load_photo(path, size, mode="cover"):
    """Open a photo and cover-fit (crop) it to exactly `size`."""
    img = Image.open(path).convert("RGB")
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def fit_font(draw, text, font_path, max_width, max_size, min_size=18):
    size = max_size
    while size > min_size:
        f = _font(font_path, size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return _font(font_path, min_size)


def _wrap(draw, text, font, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_headline(img, text, box, color, font_path, align="left",
                  max_size=120, shadow=True):
    """Draw auto-fitted, wrapped headline text inside box=(x0,y0,x1,y1)."""
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    max_w = x1 - x0
    font = fit_font(draw, max(text.split(" "), key=len) if text else text,
                    font_path, max_w, max_size)
    lines = _wrap(draw, text, font, max_w)
    line_h = (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 12
    y = y0
    for line in lines:
        if align == "center":
            x = x0 + (max_w - draw.textlength(line, font=font)) / 2
        else:
            x = x0
        if shadow:
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=color)
        y += line_h
    return img


def scrim(img, side="bottom", height_frac=0.4, color=(0, 0, 0), max_alpha=190):
    """Overlay a vertical gradient for text legibility (bottom or top)."""
    w, h = img.size
    band = int(h * height_frac)
    overlay = Image.new("RGBA", (w, band), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(band):
        a = int(max_alpha * (i / band)) if side == "bottom" else int(max_alpha * (1 - i / band))
        od.line([(0, i), (w, i)], fill=(color[0], color[1], color[2], a))
    y = h - band if side == "bottom" else 0
    base = img.convert("RGBA")
    base.alpha_composite(overlay, (0, y))
    img.paste(base.convert("RGB"))
    return img


def place_logo(img, brand, corner="br", margin=48, max_w=320):
    """Place the logo image; fall back to a text wordmark if no logo file."""
    w, h = img.size
    logo_path = brand.get("logo") or ""
    draw = ImageDraw.Draw(img)
    if logo_path and Path(logo_path).exists():
        logo = Image.open(logo_path).convert("RGBA")
        scale = min(max_w / logo.width, 1.0)
        logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
        lw, lh = logo.size
        x = margin if "l" in corner else w - lw - margin
        y = margin if "t" in corner else h - lh - margin
        base = img.convert("RGBA"); base.alpha_composite(logo, (x, y))
        img.paste(base.convert("RGB"))
    else:
        font = _font(brand["fonts"]["headline"], 44)
        text = brand["short_name"]
        tw = draw.textlength(text, font=font)
        x = margin if "l" in corner else w - tw - margin
        y = margin if "t" in corner else h - 64 - margin
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=hex_to_rgb(brand["colors"]["accent"]))
    return img
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_kit.py -v`
Expected: PASS (all media_kit tests).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/media_kit.py tests/test_media_kit.py
git commit -m "feat(media): canvas + compositing primitives (photo cover-fit, headline, scrim, logo)"
```

---

## Task 4: `media_kit` — SD background, binary artifact save, Social Studio queue

**Files:**
- Modify: `skills/shared/media_kit.py` (append)
- Modify: `tests/test_media_kit.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_media_kit.py
import sqlite3

def test_gen_background_returns_path(monkeypatch, tmp_path):
    out_png = tmp_path / "bg.png"
    Image.new("RGB", (10, 10), (5, 5, 5)).save(out_png)
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"success": True, "output": {"path": str(out_png)}}
    monkeypatch.setattr(media_kit.requests, "post", lambda *a, **k: R())
    p = media_kit.gen_background("modern kitchen, soft light", 1080, 1080)
    assert p == str(out_png)


def test_gen_background_none_on_failure(monkeypatch):
    def boom(*a, **k): raise OSError("sd down")
    monkeypatch.setattr(media_kit.requests, "post", boom)
    assert media_kit.gen_background("x", 1080, 1080) is None


def test_save_deliverable_writes_png(tmp_path, monkeypatch):
    monkeypatch.setattr(media_kit, "ARTIFACTS_DIR", tmp_path)
    img = media_kit.new_canvas("ig_square")
    res = media_kit.save_deliverable(img, "campaign_ig.png",
                                     project_id="ahb123", agent_id="sam_axe",
                                     description="test")
    assert res["success"] is True
    assert Path(res["path"]).exists()
    assert Path(res["path"]).suffix == ".png"


def test_queue_social_post_inserts_draft(tmp_path, monkeypatch):
    db = tmp_path / "baza_projects.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE ahb_social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, preset_id INTEGER, project_id INTEGER,
        source_media_ids TEXT NOT NULL DEFAULT '[]', platform TEXT NOT NULL,
        variant TEXT NOT NULL, asset_path TEXT, cover_path TEXT, caption TEXT,
        hashtags TEXT, first_comment TEXT, status TEXT NOT NULL DEFAULT 'draft',
        score INTEGER, ai_meta TEXT DEFAULT '{}', render_params TEXT DEFAULT '{}',
        scheduled_at TEXT, posted_at TEXT, posted_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit(); con.close()
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(db))
    pid = media_kit.queue_social_post(platform="ig_square", variant="feed",
                                      asset_path="/x/a.png", caption="hi",
                                      hashtags=["#AHBCO"], project_id=4)
    con = sqlite3.connect(db)
    row = con.execute("SELECT platform, status, caption FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    assert row == ("ig_square", "draft", "hi")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_media_kit.py -k "background or deliverable or queue" -v`
Expected: FAIL — `gen_background` / `save_deliverable` / `queue_social_post` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# append to skills/shared/media_kit.py
import sqlite3
from datetime import datetime

ARTIFACTS_DIR  = FRAMEWORK_DIR / "dashboard" / "artifacts"
DASHBOARD_DB   = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"
TOOL_SERVER    = os.environ.get("BAZA_TOOL_SERVER", "http://localhost:8000")


def gen_background(prompt, width, height, timeout=200):
    """Generate a decorative background via Sam's SD WebUI tool. None on failure."""
    try:
        r = requests.post(f"{TOOL_SERVER}/tools/sam/generate-image",
                          json={"input": {"prompt": prompt, "width": width,
                                          "height": height}}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            return None
        return data.get("output", {}).get("path")
    except Exception:
        return None


def save_deliverable(image, file_name, project_id="shared",
                     agent_id="sam_axe", description="", tags=None):
    """Save a PIL image as a PNG artifact under dashboard/artifacts/<project_id>/."""
    try:
        dest_dir = Path(ARTIFACTS_DIR) / project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file_name
        image.save(dest, "PNG")
        return {"success": True, "path": str(dest),
                "url": f"/artifacts/{project_id}/{file_name}",
                "agent_id": agent_id, "description": description,
                "tags": tags or []}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _dash_db_path():
    return os.environ.get("BAZA_DASHBOARD_DB", str(DASHBOARD_DB))


def queue_social_post(platform, variant, asset_path, caption,
                      hashtags=None, first_comment="", project_id=None,
                      cover_path=None, ai_meta=None):
    """Insert a draft (awaiting-review) post into Social Studio. Returns row id.
    status='draft' => human approves in Social Studio before any publish."""
    con = sqlite3.connect(_dash_db_path(), timeout=8.0)
    try:
        cur = con.execute(
            """INSERT INTO ahb_social_posts
               (project_id, platform, variant, asset_path, cover_path, caption,
                hashtags, first_comment, status, ai_meta)
               VALUES (?,?,?,?,?,?,?,?, 'draft', ?)""",
            (project_id, platform, variant, asset_path, cover_path, caption,
             json.dumps(hashtags or []), first_comment,
             json.dumps(ai_meta or {})))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_media_kit.py -v`
Expected: PASS (all media_kit tests).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/media_kit.py tests/test_media_kit.py
git commit -m "feat(media): SD background, binary artifact save, Social Studio draft queue"
```

---

## Task 5: `brand_kit` skill (detect / show / set)

**Files:**
- Create: `skills/shared/brand_kit.py`
- Create: `tests/test_brand_kit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brand_kit.py
import importlib.util, json, os, sys, subprocess
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/brand_kit.py"


def run_skill(args, env=None):
    e = dict(os.environ)
    e["SKILL_ARGS"] = json.dumps(args)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_show_returns_brand(tmp_path):
    out = run_skill({"mode": "show"})
    assert out["brand"]["short_name"] == "AHBCO"


def test_set_patches_color(tmp_path, monkeypatch):
    # point brand at a temp file via env so we don't clobber the real one
    bp = tmp_path / "brand.json"
    out = run_skill({"mode": "set", "patch": {"colors": {"accent": "#ABCDEF"}}},
                    env={"BAZA_BRAND_PATH": str(bp)})
    assert out["brand"]["colors"]["accent"] == "#ABCDEF"
    assert json.loads(bp.read_text())["colors"]["accent"] == "#ABCDEF"


def test_detect_falls_back_when_site_down(tmp_path):
    bp = tmp_path / "brand.json"
    out = run_skill({"mode": "detect", "site": "http://127.0.0.1:9"},  # nothing listening
                    env={"BAZA_BRAND_PATH": str(bp)})
    assert out["source"] == "fallback"
    assert out["brand"]["short_name"] == "AHBCO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_brand_kit.py -v`
Expected: FAIL — `brand_kit.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# skills/shared/brand_kit.py
#!/usr/bin/env python3
"""brand_kit — establish/refresh the AHBCO brand source of truth (brand.json).

Modes:
  show   -> return current brand
  set    -> deep-merge a patch into brand.json (bumps version)
  detect -> scrape the site for logo + dominant colors; fallback to defaults
Usage: ##SKILL:brand_kit{"mode":"detect"}##
"""
import os, sys, json, re
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
import requests   # noqa: E402

# allow tests / ops to redirect the brand file
_override = os.environ.get("BAZA_BRAND_PATH")
if _override:
    media_kit.BRAND_PATH = Path(_override)


def _detect(site):
    """Best-effort scrape: og:image/logo img -> assets/logo.png; colors via Sam tool."""
    brand = media_kit.load_brand()
    brand["site"] = site
    try:
        html = requests.get(site, timeout=8).text
    except Exception:
        return None  # signal fallback
    # find a logo candidate
    logo_url = ""
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m:
        logo_url = m.group(1)
    if not logo_url:
        m = re.search(r'<img[^>]+(?:logo|brand)[^>]*src=["\']([^"\']+)', html, re.I)
        if m:
            logo_url = m.group(1)
    if logo_url:
        if logo_url.startswith("//"):
            logo_url = "https:" + logo_url
        elif logo_url.startswith("/"):
            logo_url = site.rstrip("/") + logo_url
        try:
            media_kit.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            dest = media_kit.ASSETS_DIR / "logo.png"
            dest.write_bytes(requests.get(logo_url, timeout=10).content)
            brand["logo"] = str(dest)
            # extract colors from the logo via Sam's color-palette tool
            try:
                r = requests.post(f"{media_kit.TOOL_SERVER}/tools/sam/color-palette",
                                  json={"input": {"image_path": str(dest), "colors": 5}},
                                  timeout=20)
                pal = [c["hex"] for c in r.json().get("output", {}).get("palette", [])]
                if len(pal) >= 3:
                    brand["colors"]["primary"] = pal[0]
                    brand["colors"]["secondary"] = pal[1]
                    brand["colors"]["accent"] = pal[2]
            except Exception:
                pass
        except Exception:
            pass
    return brand


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    mode = args.get("mode", "show")

    if mode == "show":
        print(json.dumps({"brand": media_kit.load_brand(), "skill": "brand_kit"}))
        return

    if mode == "set":
        brand = media_kit.load_brand()
        brand = media_kit._deep_merge(brand, args.get("patch", {}))
        brand["version"] = int(brand.get("version", 1)) + 1
        media_kit.save_brand(brand)
        print(json.dumps({"brand": brand, "skill": "brand_kit"}))
        return

    if mode == "detect":
        site = args.get("site", media_kit.load_brand()["site"])
        detected = _detect(site)
        if detected is None:
            brand = media_kit.load_brand()
            media_kit.save_brand(brand)
            print(json.dumps({"brand": brand, "source": "fallback",
                              "skill": "brand_kit"}))
            return
        detected["version"] = int(detected.get("version", 1)) + 1
        media_kit.save_brand(detected)
        print(json.dumps({"brand": detected, "source": "detected",
                          "skill": "brand_kit"}))
        return

    print(json.dumps({"error": f"unknown mode {mode}"}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_brand_kit.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/brand_kit.py tests/test_brand_kit.py
git commit -m "feat(media): brand_kit skill (detect/show/set brand.json)"
```

---

## Task 6: `social_campaign` skill

**Files:**
- Create: `skills/shared/social_campaign.py`
- Create: `tests/test_social_campaign.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_social_campaign.py
import importlib.util, json, os, sys, subprocess, sqlite3
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/social_campaign.py"


def _make_db(tmp_path):
    db = tmp_path / "baza_projects.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE ahb_social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, preset_id INTEGER, project_id INTEGER,
        source_media_ids TEXT NOT NULL DEFAULT '[]', platform TEXT NOT NULL,
        variant TEXT NOT NULL, asset_path TEXT, cover_path TEXT, caption TEXT,
        hashtags TEXT, first_comment TEXT, status TEXT NOT NULL DEFAULT 'draft',
        score INTEGER, ai_meta TEXT DEFAULT '{}', render_params TEXT DEFAULT '{}',
        scheduled_at TEXT, posted_at TEXT, posted_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit(); con.close()
    return db


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_campaign_renders_variants_and_queues(tmp_path):
    db = _make_db(tmp_path)
    photo = tmp_path / "job.jpg"
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(photo)
    out = run_skill(
        {"topic": "kitchen remodel reveal", "photo": str(photo),
         "platforms": ["ig_square", "fb"], "queue": True, "project_id": 4},
        env={"BAZA_DASHBOARD_DB": str(db),
             "BAZA_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
             "OLLAMA_HOST": "http://127.0.0.1:9",      # force template copy
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"}) # force photo-only (no SD)
    assert out["skill"] == "social_campaign"
    assert len(out["artifacts"]) == 2
    for a in out["artifacts"]:
        assert Path(a["path"]).exists()
    # both queued as draft
    con = sqlite3.connect(db)
    rows = con.execute("SELECT platform, status FROM ahb_social_posts").fetchall()
    con.close()
    assert sorted(r[0] for r in rows) == ["fb", "ig_square"]
    assert all(r[1] == "draft" for r in rows)


def test_campaign_no_queue_skips_db(tmp_path):
    db = _make_db(tmp_path)
    photo = tmp_path / "job.jpg"
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(photo)
    out = run_skill(
        {"topic": "bathroom", "photo": str(photo), "platforms": ["ig_square"],
         "queue": False},
        env={"BAZA_DASHBOARD_DB": str(db),
             "BAZA_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
             "OLLAMA_HOST": "http://127.0.0.1:9",
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"})
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_social_posts").fetchone()[0]
    con.close()
    assert n == 0
    assert out["queued"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_social_campaign.py -v`
Expected: FAIL — `social_campaign.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# skills/shared/social_campaign.py
#!/usr/bin/env python3
"""social_campaign — topic/project -> per-platform on-brand post pack.

Generates copy (local Ollama), composes a branded image per platform
(photo-first; SD background fallback), saves each as an artifact, and
(optionally) queues each as a DRAFT in Social Studio (never auto-publishes).
Usage: ##SKILL:social_campaign{"topic":"kitchen remodel","platforms":["ig_square","fb"],"queue":true}##
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402

# test/ops overrides
if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])

DEFAULT_PLATFORMS = ["ig_square", "ig_reel", "fb", "yt_thumb"]


def _compose(platform, photo, copy, brand):
    w, h = media_kit.PLATFORMS[platform]
    base = None
    if photo and Path(photo).exists():
        base = media_kit.load_photo(photo, (w, h))
    else:
        bg = media_kit.gen_background(
            f"professional home renovation, {copy['caption'][:60]}, clean, bright",
            w, h)
        if bg and Path(bg).exists():
            base = media_kit.load_photo(bg, (w, h))
    if base is None:
        base = media_kit.new_canvas(platform,
                                    bg=media_kit.hex_to_rgb(brand["colors"]["primary"]))
    media_kit.scrim(base, side="bottom", height_frac=0.45)
    headline = copy["caption"].split(".")[0][:70]
    media_kit.draw_headline(base, headline,
                            (int(w * 0.06), int(h * 0.62), int(w * 0.94), int(h * 0.9)),
                            color=(255, 255, 255),
                            font_path=brand["fonts"]["headline"])
    media_kit.place_logo(base, brand, corner="tr")
    return base


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    topic = args.get("topic", "")
    if not topic and not args.get("project_id"):
        print(json.dumps({"error": "topic or project_id required"})); return
    platforms = args.get("platforms") or DEFAULT_PLATFORMS
    do_queue = bool(args.get("queue", True))
    project_id = args.get("project_id")
    photo = args.get("photo", "")

    brand = media_kit.load_brand()
    copy = media_kit.write_copy(topic or f"project {project_id}", brand, kind="caption")

    artifacts, queued, warnings = [], [], []
    for plat in platforms:
        if plat not in media_kit.PLATFORMS:
            warnings.append(f"unknown platform {plat}"); continue
        img = _compose(plat, photo, copy, brand)
        fname = f"campaign_{(topic or 'project').replace(' ', '_')[:30]}_{plat}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(project_id or "shared"),
            description=f"Social campaign ({plat}): {topic}",
            tags=["social", "campaign", plat])
        if not saved.get("success"):
            warnings.append(f"save failed {plat}: {saved.get('error')}"); continue
        artifacts.append(saved)
        if do_queue:
            pid = media_kit.queue_social_post(
                platform=plat, variant="feed", asset_path=saved["path"],
                caption=copy["caption"], hashtags=copy["hashtags"],
                first_comment=copy.get("first_comment", ""),
                project_id=project_id,
                ai_meta={"copy_model": copy["model"], "topic": topic})
            queued.append({"platform": plat, "post_id": pid})

    print(json.dumps({"skill": "social_campaign", "copy": copy,
                      "artifacts": artifacts, "queued": queued,
                      "warnings": warnings}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_social_campaign.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/social_campaign.py tests/test_social_campaign.py
git commit -m "feat(media): social_campaign skill (per-platform pack + Social Studio draft queue)"
```

---

## Task 7: `before_after_showcase` skill

**Files:**
- Create: `skills/shared/before_after_showcase.py`
- Create: `tests/test_before_after_showcase.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_before_after_showcase.py
import json, os, sys, subprocess
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/before_after_showcase.py"


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_requires_two_photos(tmp_path):
    out = run_skill({"before": "", "after": ""},
                    env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
                         "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert "error" in out


def test_builds_showcase(tmp_path):
    b = tmp_path / "b.jpg"; a = tmp_path / "a.jpg"
    Image.new("RGB", (1200, 1600), (60, 60, 60)).save(b)
    Image.new("RGB", (1200, 1600), (200, 180, 160)).save(a)
    out = run_skill(
        {"before": str(b), "after": str(a), "title": "Ritz Water Damage",
         "details": "Full remediation", "platforms": ["ig_square"]},
        env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
             "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert out["skill"] == "before_after_showcase"
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert Path(art["path"]).exists()
    assert Image.open(art["path"]).size == (1080, 1080)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_before_after_showcase.py -v`
Expected: FAIL — skill does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# skills/shared/before_after_showcase.py
#!/usr/bin/env python3
"""before_after_showcase — branded BEFORE/AFTER comparison graphic from REAL photos.

Photo-first and required: never fabricates the work with AI. Saves an artifact
per requested platform; optional Social Studio draft queue.
Usage: ##SKILL:before_after_showcase{"before":"/path/b.jpg","after":"/path/a.jpg","title":"Kitchen Remodel"}##
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
from PIL import ImageDraw  # noqa: E402

if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])


def _label(img, text, box, brand):
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    pad = 16
    font = media_kit._font(brand["fonts"]["headline"], 40)
    tw = draw.textlength(text, font=font)
    draw.rectangle([x0, y0, x0 + tw + pad * 2, y0 + 60],
                   fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
    draw.text((x0 + pad, y0 + 8), text, font=font,
              fill=media_kit.hex_to_rgb(brand["colors"]["dark"]))


def _compose(platform, before, after, title, details, brand):
    w, h = media_kit.PLATFORMS[platform]
    canvas = media_kit.new_canvas(platform,
                                  bg=media_kit.hex_to_rgb(brand["colors"]["dark"]))
    half = w // 2
    img_h = int(h * 0.78)
    b_img = media_kit.load_photo(before, (half - 4, img_h))
    a_img = media_kit.load_photo(after, (half - 4, img_h))
    canvas.paste(b_img, (0, 0))
    canvas.paste(a_img, (half + 4, 0))
    # divider
    ImageDraw.Draw(canvas).rectangle([half - 4, 0, half + 4, img_h],
                                     fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
    _label(canvas, "BEFORE", (24, 24, half, 84), brand)
    _label(canvas, "AFTER", (half + 28, 24, w, 84), brand)
    # title strip
    media_kit.draw_headline(canvas, title or brand["name"],
                            (int(w * 0.05), img_h + 16, int(w * 0.95), h - 20),
                            color=media_kit.hex_to_rgb(brand["colors"]["light"]),
                            font_path=brand["fonts"]["headline"], max_size=64)
    if details:
        d = ImageDraw.Draw(canvas)
        f = media_kit._font(brand["fonts"]["body"], 28)
        d.text((int(w * 0.05), h - 48), details[:90], font=f,
               fill=media_kit.hex_to_rgb(brand["colors"]["secondary"]))
    media_kit.place_logo(canvas, brand, corner="br", max_w=220)
    return canvas


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    before, after = args.get("before", ""), args.get("after", "")
    if not (before and after and Path(before).exists() and Path(after).exists()):
        print(json.dumps({"error": "before and after photo paths are required "
                                   "(photo-first: AI does not fabricate the work)"}))
        return
    title = args.get("title", "")
    details = args.get("details", "")
    project_id = args.get("project_id")
    platforms = args.get("platforms") or ["ig_square"]
    do_queue = bool(args.get("queue", False))

    brand = media_kit.load_brand()
    artifacts, queued, warnings = [], [], []
    for plat in platforms:
        if plat not in media_kit.PLATFORMS:
            warnings.append(f"unknown platform {plat}"); continue
        img = _compose(plat, before, after, title, details, brand)
        fname = f"showcase_{(title or 'project').replace(' ', '_')[:30]}_{plat}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(project_id or "shared"),
            description=f"Before/After showcase: {title}",
            tags=["showcase", "before-after", plat])
        if not saved.get("success"):
            warnings.append(f"save failed {plat}: {saved.get('error')}"); continue
        artifacts.append(saved)
        if do_queue:
            pid = media_kit.queue_social_post(
                platform=plat, variant="showcase", asset_path=saved["path"],
                caption=f"{title} — see the transformation. {brand['tagline']}",
                hashtags=["#BeforeAndAfter", "#AHBCO", "#Remodel"],
                project_id=project_id, ai_meta={"kind": "showcase"})
            queued.append({"platform": plat, "post_id": pid})

    print(json.dumps({"skill": "before_after_showcase", "artifacts": artifacts,
                      "queued": queued, "warnings": warnings}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_before_after_showcase.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add skills/shared/before_after_showcase.py tests/test_before_after_showcase.py
git commit -m "feat(media): before_after_showcase skill (photo-first branded comparison)"
```

---

## Task 8: `marketing_flyer` skill

**Files:**
- Create: `skills/shared/marketing_flyer.py`
- Create: `tests/test_marketing_flyer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_marketing_flyer.py
import json, os, sys, subprocess
from pathlib import Path
from PIL import Image

FRAMEWORK = Path(__file__).resolve().parent.parent
SKILL = FRAMEWORK / "skills/shared/marketing_flyer.py"


def run_skill(args, env):
    e = dict(os.environ); e["SKILL_ARGS"] = json.dumps(args); e.update(env)
    p = subprocess.run([sys.executable, str(SKILL)], capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_flyer_requires_offer_or_headline(tmp_path):
    out = run_skill({}, env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
                             "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert "error" in out


def test_flyer_renders_sizes(tmp_path):
    out = run_skill(
        {"headline": "Spring Roofing Special", "subhead": "20% off this month",
         "bullets": ["Licensed & insured", "Free estimates", "10-year warranty"],
         "cta": "Call (555) 123-4567", "sizes": ["flyer_portrait", "ad_square"]},
        env={"BAZA_ARTIFACTS_DIR": str(tmp_path / "art"),
             "OLLAMA_HOST": "http://127.0.0.1:9",
             "BAZA_TOOL_SERVER": "http://127.0.0.1:9"})  # no SD -> brand-color bg
    assert out["skill"] == "marketing_flyer"
    assert len(out["artifacts"]) == 2
    sizes = {Path(a["path"]).name: Image.open(a["path"]).size for a in out["artifacts"]}
    assert (1275, 1650) in sizes.values()
    assert (1080, 1080) in sizes.values()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_marketing_flyer.py -v`
Expected: FAIL — skill does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# skills/shared/marketing_flyer.py
#!/usr/bin/env python3
"""marketing_flyer — branded flyer/ad from an offer/service brief.

Fills copy gaps via local Ollama; base = real photo if given, else SD background,
else brand-color panel. Renders at requested print/digital sizes; saves artifacts.
Usage: ##SKILL:marketing_flyer{"headline":"Spring Roofing Special","cta":"Call (555) 123-4567"}##
"""
import os, sys, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import media_kit  # noqa: E402
from PIL import ImageDraw  # noqa: E402

if os.environ.get("BAZA_ARTIFACTS_DIR"):
    media_kit.ARTIFACTS_DIR = Path(os.environ["BAZA_ARTIFACTS_DIR"])

DEFAULT_SIZES = ["flyer_portrait", "ad_square", "ad_landscape"]


def _base(size, photo, headline, brand):
    w, h = size
    if photo and Path(photo).exists():
        img = media_kit.load_photo(photo, (w, h))
    else:
        bg = media_kit.gen_background(
            f"home services background, {headline[:50]}, clean, professional", w, h)
        if bg and Path(bg).exists():
            img = media_kit.load_photo(bg, (w, h))
        else:
            img = media_kit.new_canvas_size(w, h,
                                            media_kit.hex_to_rgb(brand["colors"]["primary"]))
    media_kit.scrim(img, side="top", height_frac=0.55, max_alpha=200)
    return img


def _compose(size_name, headline, subhead, bullets, cta, photo, brand):
    w, h = media_kit.PLATFORMS[size_name]
    img = _base((w, h), photo, headline, brand)
    draw = ImageDraw.Draw(img)
    mx = int(w * 0.07)
    media_kit.draw_headline(img, headline, (mx, int(h * 0.06), w - mx, int(h * 0.3)),
                            color=(255, 255, 255), font_path=brand["fonts"]["headline"],
                            max_size=int(h * 0.085))
    y = int(h * 0.32)
    if subhead:
        sf = media_kit._font(brand["fonts"]["headline"], int(h * 0.04))
        draw.text((mx, y), subhead[:60], font=sf,
                  fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
        y += int(h * 0.07)
    bf = media_kit._font(brand["fonts"]["body"], int(h * 0.028))
    for b in (bullets or [])[:6]:
        draw.text((mx, y), f"✓  {b}", font=bf, fill=(255, 255, 255))
        y += int(h * 0.045)
    if cta:
        cf = media_kit._font(brand["fonts"]["headline"], int(h * 0.038))
        ctw = draw.textlength(cta, font=cf)
        bx0, by0 = mx, int(h * 0.88)
        draw.rectangle([bx0, by0, bx0 + ctw + 60, by0 + int(h * 0.06)],
                       fill=media_kit.hex_to_rgb(brand["colors"]["accent"]))
        draw.text((bx0 + 30, by0 + int(h * 0.013)), cta, font=cf,
                  fill=media_kit.hex_to_rgb(brand["colors"]["dark"]))
    media_kit.place_logo(img, brand, corner="br", max_w=int(w * 0.22))
    return img


def main():
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    headline = args.get("headline", "")
    offer = args.get("offer", "")
    if not headline and not offer:
        print(json.dumps({"error": "headline or offer required"})); return

    brand = media_kit.load_brand()
    subhead = args.get("subhead", "")
    bullets = args.get("bullets", [])
    cta = args.get("cta", "")
    photo = args.get("photo", "")
    sizes = args.get("sizes") or DEFAULT_SIZES

    # fill copy gaps from offer if needed
    if offer and (not headline or not bullets):
        copy = media_kit.write_copy(offer, brand, kind="flyer")
        headline = headline or copy["caption"].split(".")[0][:60]
        if not bullets:
            bullets = [t.lstrip("#") for t in copy["hashtags"][:3]]
        cta = cta or copy.get("first_comment", "")

    artifacts, warnings = [], []
    for sz in sizes:
        if sz not in media_kit.PLATFORMS:
            warnings.append(f"unknown size {sz}"); continue
        img = _compose(sz, headline, subhead, bullets, cta, photo, brand)
        fname = f"flyer_{headline.replace(' ', '_')[:30]}_{sz}.png"
        saved = media_kit.save_deliverable(
            img, fname, project_id=str(args.get("project_id") or "shared"),
            description=f"Marketing flyer: {headline}",
            tags=["flyer", "ad", sz])
        if not saved.get("success"):
            warnings.append(f"save failed {sz}: {saved.get('error')}"); continue
        artifacts.append(saved)

    print(json.dumps({"skill": "marketing_flyer", "headline": headline,
                      "artifacts": artifacts, "warnings": warnings}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add the `new_canvas_size` helper used above (media_kit)**

The flyer base needs an arbitrary-size canvas. Add to `skills/shared/media_kit.py` (just after `new_canvas`):

```python
def new_canvas_size(w, h, bg):
    return Image.new("RGB", (w, h), bg)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_marketing_flyer.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add skills/shared/marketing_flyer.py tests/test_marketing_flyer.py skills/shared/media_kit.py
git commit -m "feat(media): marketing_flyer skill (print + digital branded ads)"
```

---

## Task 9: Full regression + live smoke test

**Files:**
- None created; verification only. (May add `docs/superpowers/marketing-media-skills.md` usage note.)

- [ ] **Step 1: Run the full marketing test suite**

Run: `python -m pytest tests/test_media_kit.py tests/test_brand_kit.py tests/test_social_campaign.py tests/test_before_after_showcase.py tests/test_marketing_flyer.py -v`
Expected: PASS (all).

- [ ] **Step 2: Run the broader repo regression**

Run: `python -m pytest -q`
Expected: no NEW failures attributable to these files (note any pre-existing failures separately).

- [ ] **Step 3: Live smoke — brand detect (real network, non-fatal either way)**

Run:
```bash
SKILL_ARGS='{"mode":"detect"}' python skills/shared/brand_kit.py
```
Expected: a JSON object with `"source":"detected"` (if ahb123.com reachable) or `"source":"fallback"`. Either is acceptable — confirms the skill self-heals. If detected, verify `agents/sam_axe/brand/brand.json` now exists and `agents/sam_axe/brand/assets/logo.png` may be present.

- [ ] **Step 4: Live smoke — social campaign with template copy + photo-only**

Run (uses any existing image on disk as the photo):
```bash
PHOTO=$(find dashboard/artifacts -iname '*.jpg' -o -iname '*.png' | head -1)
SKILL_ARGS="{\"topic\":\"kitchen remodel reveal\",\"photo\":\"$PHOTO\",\"platforms\":[\"ig_square\"],\"queue\":false}" \
  python skills/shared/social_campaign.py | python -m json.tool
```
Expected: JSON with one artifact whose `path` exists and is 1080×1080. Open it to eyeball the composition.

- [ ] **Step 5: Write a short usage doc**

Create `docs/superpowers/marketing-media-skills.md` documenting the four skill markers, their args, and the `brand_kit detect` bootstrap step. Content:

```markdown
# Marketing & Media Super Skills — Usage

Bootstrap once: `##SKILL:brand_kit{"mode":"detect"}##` (scrapes ahb123.com → brand.json).

- `##SKILL:social_campaign{"topic":"kitchen remodel","platforms":["ig_square","fb"],"queue":true,"project_id":4}##`
- `##SKILL:before_after_showcase{"before":"/path/b.jpg","after":"/path/a.jpg","title":"Ritz Remediation","queue":false}##`
- `##SKILL:marketing_flyer{"headline":"Spring Roofing Special","subhead":"20% off","bullets":["Licensed","Free estimates"],"cta":"Call (555) 123-4567"}##`
- `##SKILL:brand_kit{"mode":"show"}##` / `##SKILL:brand_kit{"mode":"set","patch":{"colors":{"accent":"#F39C12"}}}##`

Local-first (Ollama copy, SD imagery); photo-first; queued posts are DRAFTS — approve in Social Studio before publishing.
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/marketing-media-skills.md
git commit -m "docs(media): marketing/media super skills usage guide"
```

---

## Self-Review Notes (author checklist — completed)

- **Spec coverage:** brand.json source of truth (Task 1, 5) ✓; media_kit shared lib (Tasks 1–4) ✓; brand_kit detect/show/set with ahb123.com scrape + fallback (Task 5) ✓; social_campaign per-platform + queue (Task 6) ✓; before_after_showcase photo-first required (Task 7) ✓; marketing_flyer print+digital (Task 8) ✓; local-first copy auto-pick (Task 2) ✓; photo-first + SD fallback (Tasks 6,8) ✓; artifact save + Social Studio draft queue, never auto-publish (Task 4) ✓; testing strategy per-skill + media_kit unit (every task) ✓.
- **Simplification vs spec:** spec called for bundling brand TTFs; system DejaVu/Liberation fonts are present on baza, so the default fonts point there and no font download is required (brand.json may still override). This is the only deviation and it removes a dependency.
- **Type/name consistency:** `media_kit` public names referenced by skills — `load_brand`, `save_brand`, `_deep_merge`, `hex_to_rgb`, `PLATFORMS`, `new_canvas`, `new_canvas_size`, `load_photo`, `_font`, `fit_font`, `draw_headline`, `scrim`, `place_logo`, `pick_copy_model`, `write_copy`, `gen_background`, `save_deliverable`, `queue_social_post`, `ARTIFACTS_DIR`, `TOOL_SERVER`, `BRAND_PATH`, `ASSETS_DIR` — all defined in Tasks 1–4/8 and used consistently in Tasks 5–8.
- **No placeholders:** every code step contains complete, runnable code.
```
