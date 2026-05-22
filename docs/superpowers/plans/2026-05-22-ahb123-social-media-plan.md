# ahb123 Social Media Studio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the ahb123 Social Media Studio (Phase 1 design: `docs/superpowers/specs/2026-05-22-ahb123-social-media-design.md`): a new `#tab-social` inside `ahb123.html` that turns existing project media into ready-to-post TikTok + Instagram content via local AI, with presets, auto-pilot drafting, and a Telegram drop.

**Architecture:** New Flask Blueprint `dashboard/social_studio.py` (mounted in `dashboard/app.py`), new render module `dashboard/social_render.py` driving `ffmpeg` + PIL, three new SQLite tables in `baza_projects.db`, single-file UI added to `dashboard/templates/ahb123.html` namespaced under `window.SocialStudio`. Local-first AI via existing Ollama on `127.0.0.1:11434`; SD image-gen via existing `tools/sam_imaging.py`. Body-level modals (existing hard rule).

**Tech Stack:** Python 3 / Flask Blueprint / SQLite (existing `baza_projects.db`) / ffmpeg / Pillow / piper (TTS, opt) / whisper.cpp (subs, opt) / vanilla JS + CSS in `ahb123.html` / systemd user units for autopilot.

---

## File Structure

**New files:**
- `dashboard/social_studio.py` — Flask Blueprint, all `/api/ahb/social/*` routes, DB helpers, AI call helpers, autopilot orchestration
- `dashboard/social_render.py` — pure renderer: clip preprocessing, concat, overlays, encode, cover-pick
- `dashboard/social_settings.py` — typed accessor over `social_settings.json` + `social_brand_kit.json`
- `dashboard/prompts/social/caption_system.md`
- `dashboard/prompts/social/hashtag_system.md`
- `dashboard/prompts/social/hooks_system.md`
- `dashboard/prompts/social/score_system.md`
- `dashboard/prompts/social/cover_vision.md`
- `dashboard/social_settings.json` — created at first read, seeded from defaults
- `dashboard/social_brand_kit.json` — created at first read, bootstrapped from sq_bundle when available
- `dashboard/static/social/music/free/.gitkeep` — empty music dir placeholder
- `dashboard/static/social/brand/.gitkeep`
- `dashboard/static/fonts/Inter-Bold.ttf` — shipped font (OFL)
- `dashboard/static/fonts/Inter-Regular.ttf`
- `baza-social-autopilot.service` — systemd user oneshot
- `baza-social-autopilot.timer` — hourly
- `tests/test_social_db.py`
- `tests/test_social_render.py`
- `tests/test_social_blueprint.py`
- `tests/test_social_autopilot.py`

**Modified files:**
- `dashboard/app.py` — register `social_bp`, call `_ensure_social_tables()` once at import
- `dashboard/templates/ahb123.html` — add `<div class="sub-tab" data-tab="social">`, `<div class="tab-pane" id="tab-social">`, body-level modals, `<style>` and `<script>` additions for `window.SocialStudio`

---

## Conventions for this plan

- All file paths are absolute from repo root `/home/switchhacker/baza-empire/agent-framework-v3/`.
- After **any** edit to `dashboard/templates/ahb123.html`, finishing step is `sudo systemctl restart baza-dashboard` (template cache rule).
- Commit after each task. Auto-commit timer runs hourly, but explicit commits per task preserve history granularity.
- Tests are run with `pytest tests/test_social_*.py -v` from repo root.
- Manual smoke checks documented inline. They are required steps, not optional.
- "Restart dashboard" means: `sudo systemctl restart baza-dashboard` (or `systemctl --user restart baza-dashboard` if running as user unit — check with `systemctl status baza-dashboard` first).

---

The remaining sections are split across follow-up writes (Tasks 1–14) appended to this same file in order, to keep within output limits while preserving plan integrity. Each task is fully self-contained per the writing-plans skill: files, TDD steps, exact code, exact commands, commit step.

---

## Task 1: DB migrations + table tests

**Files:**
- Create: `dashboard/social_studio.py` (stub w/ `_ensure_social_tables` only)
- Modify: `dashboard/app.py` — call `_ensure_social_tables()` next to the existing `_ensure_docprep_tables()` call near line 13719
- Test: `tests/test_social_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_social_db.py`:

```python
"""Tests for social_studio schema migrations."""
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def db_path(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="social_db_")
    p = os.path.join(tmp, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    import importlib
    if "social_studio" in sys.modules:
        del sys.modules["social_studio"]
    return p


def test_ensure_social_tables_creates_all_three(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"ahb_social_presets", "ahb_social_posts", "ahb_social_jobs"} <= names


def test_ensure_social_tables_is_idempotent(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    posts_cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_posts)")}
    assert "preset_id" in posts_cols
    assert "first_comment" in posts_cols


def test_indexes_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    idx = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    assert "idx_social_posts_status" in idx
    assert "idx_social_posts_project" in idx
    assert "idx_social_posts_scheduled" in idx
    assert "idx_social_jobs_status" in idx
```

- [ ] **Step 2: Run test to verify it fails**

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pytest tests/test_social_db.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'social_studio'`.

- [ ] **Step 3: Create the stub module with the migration function**

`dashboard/social_studio.py`:

```python
"""Social Media Studio Blueprint for ahb123.

Routes mount under /api/ahb/social/*. This file owns the schema migration
and the Flask blueprint. Render logic lives in social_render.py; settings
accessors live in social_settings.py.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

from flask import Blueprint

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _ensure_social_tables(db_path: Optional[str] = None) -> None:
    """Create ahb_social_* tables and indexes. Idempotent."""
    path = db_path or _db_path()
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            platform_targets TEXT NOT NULL DEFAULT '["tiktok","ig_reel","ig_feed_square"]',
            prompt_template TEXT,
            hashtag_pool TEXT,
            tone TEXT DEFAULT 'pro',
            length TEXT DEFAULT 'medium',
            style TEXT DEFAULT 'trade',
            music_style TEXT DEFAULT 'none',
            voiceover_style TEXT DEFAULT 'none',
            source_filter TEXT DEFAULT '{}',
            cadence TEXT DEFAULT 'off',
            n_per_week INTEGER DEFAULT 0,
            max_per_day INTEGER DEFAULT 1,
            auto_approve INTEGER DEFAULT 0,
            score_threshold INTEGER DEFAULT 75,
            last_run_at TEXT,
            next_run_at TEXT,
            active INTEGER DEFAULT 1,
            is_seed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preset_id INTEGER,
            project_id INTEGER,
            source_media_ids TEXT NOT NULL DEFAULT '[]',
            platform TEXT NOT NULL,
            variant TEXT NOT NULL,
            asset_path TEXT,
            cover_path TEXT,
            caption TEXT,
            hashtags TEXT,
            first_comment TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            score INTEGER,
            ai_meta TEXT DEFAULT '{}',
            render_params TEXT DEFAULT '{}',
            scheduled_at TEXT,
            posted_at TEXT,
            posted_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_status ON ahb_social_posts(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_project ON ahb_social_posts(project_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON ahb_social_posts(scheduled_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS ahb_social_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            kind TEXT NOT NULL,
            input TEXT NOT NULL DEFAULT '{}',
            output_path TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            model_used TEXT,
            tokens INTEGER,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_social_jobs_status ON ahb_social_jobs(status)")
        con.commit()
        con.close()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_tables deferred — DB busy: {e}", flush=True)


social_bp = Blueprint("social_studio", __name__)


# Routes are added in later tasks.
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/test_social_db.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Wire migration into dashboard startup**

In `dashboard/app.py`, locate line `_ensure_docprep_tables()` (around line 13719) and append immediately after:

```python
from dashboard.social_studio import _ensure_social_tables, social_bp as _social_bp
_ensure_social_tables()
app.register_blueprint(_social_bp)
```

If `dashboard.social_studio` import path fails (because `dashboard/` is run as `__main__` not a package), fall back to:

```python
try:
    from dashboard.social_studio import _ensure_social_tables, social_bp as _social_bp
except ImportError:
    from social_studio import _ensure_social_tables, social_bp as _social_bp
_ensure_social_tables()
app.register_blueprint(_social_bp)
```

- [ ] **Step 6: Smoke-restart dashboard, confirm tables**

```
sudo systemctl restart baza-dashboard
sleep 2
sqlite3 dashboard/baza_projects.db ".tables" | tr ' ' '\n' | grep social
```

Expected output: `ahb_social_jobs ahb_social_posts ahb_social_presets`

- [ ] **Step 7: Commit**

```
git add dashboard/social_studio.py dashboard/app.py tests/test_social_db.py
git commit -m "social: schema migrations + Blueprint scaffold

3 new tables (ahb_social_presets/posts/jobs) idempotently created at
dashboard boot. Blueprint registered but no routes yet — Task 2 adds
prompts + Task 3 the AI endpoints.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Settings + Brand-Kit accessor + prompts

**Files:**
- Create: `dashboard/social_settings.py`
- Create: `dashboard/prompts/social/caption_system.md`
- Create: `dashboard/prompts/social/hashtag_system.md`
- Create: `dashboard/prompts/social/hooks_system.md`
- Create: `dashboard/prompts/social/score_system.md`
- Create: `dashboard/prompts/social/cover_vision.md`
- Test: extend `tests/test_social_db.py` (or new `tests/test_social_settings.py`)

- [ ] **Step 1: Write the failing test**

`tests/test_social_settings.py`:

```python
import json
import os
import sys
import tempfile
import pytest


@pytest.fixture()
def tmp_settings(monkeypatch):
    d = tempfile.mkdtemp(prefix="ss_")
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if "social_settings" in sys.modules:
        del sys.modules["social_settings"]
    return d


def test_load_settings_creates_default_file(tmp_settings):
    import social_settings
    s = social_settings.load_settings()
    assert s["default_copy_model"] == "gpt-oss:20b"
    assert s["autopilot_master"] is False
    assert os.path.exists(os.path.join(tmp_settings, "social_settings.json"))


def test_save_settings_round_trip(tmp_settings):
    import social_settings
    s = social_settings.load_settings()
    s["daily_post_cap"] = 7
    social_settings.save_settings(s)
    s2 = social_settings.load_settings()
    assert s2["daily_post_cap"] == 7


def test_load_brand_kit_creates_default(tmp_settings):
    import social_settings
    b = social_settings.load_brand_kit()
    assert b["primary_color"].startswith("#")
    assert "#allhomebuilding" in b["hashtag_floor"]


def test_load_prompt_returns_content(tmp_settings):
    import social_settings
    p = social_settings.load_prompt("caption_system")
    assert isinstance(p, str) and len(p) > 20
```

Run: `pytest tests/test_social_settings.py -v` → FAIL.

- [ ] **Step 2: Implement `dashboard/social_settings.py`**

```python
"""Settings + brand kit accessors + prompt loader for social_studio."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))


def _settings_dir() -> str:
    return os.environ.get("BAZA_SOCIAL_SETTINGS_DIR", _HERE)


def _settings_path() -> str:
    return os.path.join(_settings_dir(), "social_settings.json")


def _brand_kit_path() -> str:
    return os.path.join(_settings_dir(), "social_brand_kit.json")


def _prompts_dir() -> str:
    return os.path.join(_HERE, "prompts", "social")


DEFAULTS_SETTINGS: Dict[str, Any] = {
    "default_copy_model": "gpt-oss:20b",
    "fast_copy_model": "gemma3:12b",
    "vision_model": "qwen3-vl:latest",
    "tts_engine": "piper",
    "cloud_models_enabled": False,
    "cloud_copy_model": "gpt-oss:120b-cloud",
    "autopilot_master": False,
    "daily_post_cap": 4,
    "cool_down_days": 14,
    "burn_in_subtitles_default": True,
}

DEFAULTS_BRAND: Dict[str, Any] = {
    "logo_path": "static/social/brand/logo.png",
    "primary_color": "#10b981",
    "secondary_color": "#0e0e1e",
    "font_default": "Inter-Bold",
    "intro_clip_path": None,
    "outro_clip_path": None,
    "hashtag_floor": ["#allhomebuilding", "#ahbco", "#newyorkhomes"],
    "first_comment_floor": "—\nDM for a free estimate.",
    "hic_number": "",
    "founded_year": "",
}


def _read_json_or_default(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(default, f, indent=2)
        return dict(default)
    with open(path) as f:
        data = json.load(f)
    merged = dict(default)
    merged.update(data)
    return merged


def load_settings() -> Dict[str, Any]:
    return _read_json_or_default(_settings_path(), DEFAULTS_SETTINGS)


def save_settings(s: Dict[str, Any]) -> None:
    with open(_settings_path(), "w") as f:
        json.dump(s, f, indent=2)


def load_brand_kit() -> Dict[str, Any]:
    return _read_json_or_default(_brand_kit_path(), DEFAULTS_BRAND)


def save_brand_kit(b: Dict[str, Any]) -> None:
    with open(_brand_kit_path(), "w") as f:
        json.dump(b, f, indent=2)


def load_prompt(name: str) -> str:
    path = os.path.join(_prompts_dir(), f"{name}.md")
    with open(path) as f:
        return f.read()
```

- [ ] **Step 3: Write the prompt files**

`dashboard/prompts/social/caption_system.md`:

```markdown
# Social Caption System Prompt v1

You are the in-house social media writer for All Home Building Co LLC, a NY general contractor. You write captions for TikTok and Instagram only.

Rules, no exceptions:
- Use ONLY the facts the user provides in the source description. Do not invent names, addresses, prices, materials, or timelines.
- Match the requested platform conventions exactly:
  - tiktok: short, hook in first 4 words, no hashtags inline, conversational
  - ig_reel: short caption, hook in first line, max 2200 chars
  - ig_feed_square / ig_feed_portrait: 1–3 short paragraphs, can include emoji sparingly, end with one CTA line
  - ig_story: optional caption only; if provided, max 12 words
- Match the requested tone exactly: pro, casual, hype, educational, trade, funny.
- Match the requested length exactly: short = 1–2 lines, medium = 3–5 lines, long = 6+ lines.
- Never use "we leverage", "synergy", "in today's fast-paced world", or other AI-tells.
- Output the caption text only. No preamble, no quotes, no markdown.
```

`dashboard/prompts/social/hashtag_system.md`:

```markdown
# Hashtag System Prompt v1

You generate hashtag sets for social posts by a NY general contractor.

Rules:
- Output ONLY a JSON array of strings. No preamble. No code fence.
- Each item starts with `#`, lowercase, no spaces, no punctuation.
- Required mix: 30% niche (e.g. #brooklynrenovation), 50% mid (#homerenovation), 20% broad (#construction).
- Always include the brand-kit floor tags the caller provides.
- Limits: tiktok ≤ 6, ig_reel ≤ 25, ig_feed_* ≤ 30, ig_story ≤ 3.
```

`dashboard/prompts/social/hooks_system.md`:

```markdown
# Hooks System Prompt v1

Produce N social-video hooks based on the source description.

Rules:
- Output ONLY a JSON array of strings. No preamble. No code fence.
- Each hook ≤ 60 characters, no hashtags, no quotes, no emoji.
- Hooks should pattern-interrupt: question, bold claim, contrarian, number-led, or curiosity gap.
- No clickbait that misrepresents the work.
```

`dashboard/prompts/social/score_system.md`:

```markdown
# Score System Prompt v1

You are a critical social-content editor. Score the provided draft.

Rules:
- Output ONLY JSON: {"score": <0-100 int>, "notes": "<1 paragraph>"}.
- Rubric: hook quality (30) + clarity (20) + CTA fit (20) + hashtag fit (15) + platform fit (15).
- Notes must call out the single biggest weakness, not a list of small ones.
```

`dashboard/prompts/social/cover_vision.md`:

```markdown
# Cover-Pick Vision Prompt v1

You will be shown N candidate frames (1 per message). Choose the single most arresting cover for a social video.

Rules:
- Output ONLY JSON: {"index": <0-based int>, "reason": "<1 sentence>"}.
- Prefer faces, eye contact, dramatic lighting, action mid-motion.
- Avoid blurry, occluded, or off-center subjects.
- If two frames are tied, prefer the earlier one.
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/test_social_settings.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add dashboard/social_settings.py dashboard/prompts/social/ tests/test_social_settings.py
git commit -m "social: settings, brand kit, system prompts

JSON-backed settings + brand kit accessors (bootstrap defaults on first
read). Five tunable system prompts in dashboard/prompts/social/ so they
can be edited without redeploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Source picker endpoint + presets CRUD

**Files:**
- Modify: `dashboard/social_studio.py` (add routes)
- Test: `tests/test_social_blueprint.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_social_blueprint.py`:

```python
import json
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="ss_bp_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    for m in ("social_studio", "social_settings"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    # Seed a couple of media-like rows for the source picker
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE image_captions (
        id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
        caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
    )""")
    con.execute("INSERT INTO image_captions VALUES (1,42,'a.jpg','x','work','ok','2026-05-22')")
    con.commit(); con.close()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    return app.test_client()


def test_presets_create_and_list(client):
    r = client.post("/api/ahb/social/presets", json={"name": "Test Preset"})
    assert r.status_code == 200
    pid = r.get_json()["id"]
    r2 = client.get("/api/ahb/social/presets")
    items = r2.get_json()["items"]
    assert any(p["id"] == pid for p in items)


def test_presets_update_and_delete(client):
    pid = client.post("/api/ahb/social/presets", json={"name": "Tmp"}).get_json()["id"]
    r = client.put(f"/api/ahb/social/presets/{pid}", json={"tone": "hype", "active": 0})
    assert r.status_code == 200
    item = client.get("/api/ahb/social/presets").get_json()["items"]
    assert any(p["id"] == pid and p["tone"] == "hype" for p in item)
    r = client.delete(f"/api/ahb/social/presets/{pid}")
    assert r.status_code == 200


def test_sources_returns_media(client):
    r = client.get("/api/ahb/social/sources?project_id=42")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert any(i["sub_path"] == "a.jpg" for i in items)
```

Run: `pytest tests/test_social_blueprint.py -v` → FAIL (routes missing).

- [ ] **Step 2: Add the routes**

Append to `dashboard/social_studio.py` (before any later content):

```python
import json
from datetime import datetime
from flask import jsonify, request

try:
    from dashboard import social_settings as _settings
except ImportError:
    import social_settings as _settings


def _conn():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _row_to_preset(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("platform_targets", "source_filter"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else []
        except Exception:
            d[k] = []
    return d


PRESET_WRITABLE = {
    "name", "description", "platform_targets", "prompt_template",
    "hashtag_pool", "tone", "length", "style", "music_style",
    "voiceover_style", "source_filter", "cadence", "n_per_week",
    "max_per_day", "auto_approve", "score_threshold", "active",
}


@social_bp.route("/api/ahb/social/presets", methods=["GET"])
def social_presets_list():
    con = _conn()
    rows = con.execute("SELECT * FROM ahb_social_presets ORDER BY id DESC").fetchall()
    con.close()
    return jsonify({"items": [_row_to_preset(r) for r in rows]})


@social_bp.route("/api/ahb/social/presets", methods=["POST"])
def social_presets_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    cols, vals = ["name"], [name]
    for k, v in data.items():
        if k == "name" or k not in PRESET_WRITABLE:
            continue
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    con = _conn()
    cur = con.execute(
        f"INSERT INTO ahb_social_presets ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        vals,
    )
    con.commit()
    pid = cur.lastrowid
    con.close()
    return jsonify({"id": pid})


@social_bp.route("/api/ahb/social/presets/<int:pid>", methods=["PUT"])
def social_presets_update(pid: int):
    data = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k, v in data.items():
        if k not in PRESET_WRITABLE:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if not sets:
        return jsonify({"error": "no writable fields"}), 400
    sets.append("updated_at=?")
    vals.append(datetime.utcnow().isoformat(timespec="seconds"))
    vals.append(pid)
    con = _conn()
    con.execute(f"UPDATE ahb_social_presets SET {','.join(sets)} WHERE id=?", vals)
    con.commit(); con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/presets/<int:pid>", methods=["DELETE"])
def social_presets_delete(pid: int):
    con = _conn()
    con.execute("DELETE FROM ahb_social_presets WHERE id=?", (pid,))
    con.commit(); con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/sources", methods=["GET"])
def social_sources():
    project_id = request.args.get("project_id", type=int)
    media_type = request.args.get("type")  # 'photo' | 'video' | None
    q = (request.args.get("q") or "").strip().lower()
    days = request.args.get("days", type=int)
    limit = min(request.args.get("limit", default=200, type=int), 500)
    sql = "SELECT id, project_id, sub_path, caption, tags, indexed_at FROM image_captions WHERE 1=1"
    args = []
    if project_id is not None:
        sql += " AND project_id=?"; args.append(project_id)
    if q:
        sql += " AND (LOWER(caption) LIKE ? OR LOWER(tags) LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if days:
        sql += " AND date(indexed_at) >= date('now', ?)"; args.append(f"-{int(days)} days")
    if media_type == "video":
        sql += " AND (LOWER(sub_path) LIKE '%.mp4' OR LOWER(sub_path) LIKE '%.mov' OR LOWER(sub_path) LIKE '%.webm')"
    elif media_type == "photo":
        sql += " AND (LOWER(sub_path) LIKE '%.jpg' OR LOWER(sub_path) LIKE '%.jpeg' OR LOWER(sub_path) LIKE '%.png' OR LOWER(sub_path) LIKE '%.heic')"
    sql += " ORDER BY indexed_at DESC LIMIT ?"
    args.append(limit)
    con = _conn()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 3: Run tests to verify pass**

```
pytest tests/test_social_blueprint.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```
git add dashboard/social_studio.py tests/test_social_blueprint.py
git commit -m "social: presets CRUD + sources picker endpoint

GET/POST/PUT/DELETE /api/ahb/social/presets and
GET /api/ahb/social/sources (filters: project_id, type, q, days, limit).
Schema-aware JSON re-hydration for platform_targets and source_filter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```


---

## Task 4: Posts CRUD + jobs polling

**Files:**
- Modify: `dashboard/social_studio.py`
- Test: extend `tests/test_social_blueprint.py`

- [ ] **Step 1: Append tests**

Add to `tests/test_social_blueprint.py`:

```python
def test_posts_create_and_list(client):
    r = client.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16",
        "source_media_ids": [1], "caption": "hi"
    })
    assert r.status_code == 200
    pid = r.get_json()["id"]
    items = client.get("/api/ahb/social/posts").get_json()["items"]
    assert any(p["id"] == pid and p["caption"] == "hi" for p in items)


def test_posts_patch_status(client):
    pid = client.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16", "source_media_ids": [1]
    }).get_json()["id"]
    r = client.patch(f"/api/ahb/social/posts/{pid}", json={"status": "approved"})
    assert r.status_code == 200
    items = client.get("/api/ahb/social/posts?status=approved").get_json()["items"]
    assert any(p["id"] == pid for p in items)


def test_posts_filter_invalid_status(client):
    r = client.patch("/api/ahb/social/posts/9999", json={"status": "bogus"})
    assert r.status_code == 400


def test_jobs_get_404(client):
    r = client.get("/api/ahb/social/jobs/9999")
    assert r.status_code == 404
```

Run: FAIL.

- [ ] **Step 2: Append routes to `dashboard/social_studio.py`**

```python
POST_WRITABLE = {
    "preset_id", "project_id", "source_media_ids", "platform", "variant",
    "asset_path", "cover_path", "caption", "hashtags", "first_comment",
    "status", "score", "ai_meta", "render_params", "scheduled_at",
    "posted_at", "posted_url",
}

ALLOWED_STATUSES = {
    "draft", "pending_review", "approved", "scheduled", "posted",
    "rejected", "failed",
}

ALLOWED_PLATFORMS = {
    "tiktok", "ig_reel", "ig_feed_square", "ig_feed_portrait", "ig_story",
}


def _row_to_post(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("source_media_ids", "ai_meta", "render_params"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else ([] if k == "source_media_ids" else {})
        except Exception:
            d[k] = [] if k == "source_media_ids" else {}
    return d


@social_bp.route("/api/ahb/social/posts", methods=["GET"])
def social_posts_list():
    status = request.args.get("status")
    platform = request.args.get("platform")
    project_id = request.args.get("project_id", type=int)
    q = (request.args.get("q") or "").strip().lower()
    limit = min(request.args.get("limit", default=100, type=int), 500)
    offset = request.args.get("offset", default=0, type=int)
    sql = "SELECT * FROM ahb_social_posts WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"; args.append(status)
    if platform:
        sql += " AND platform=?"; args.append(platform)
    if project_id is not None:
        sql += " AND project_id=?"; args.append(project_id)
    if q:
        sql += " AND (LOWER(caption) LIKE ? OR LOWER(hashtags) LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    con = _conn()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return jsonify({"items": [_row_to_post(r) for r in rows]})


@social_bp.route("/api/ahb/social/posts", methods=["POST"])
def social_posts_create():
    data = request.get_json(silent=True) or {}
    if data.get("platform") and data["platform"] not in ALLOWED_PLATFORMS:
        return jsonify({"error": f"invalid platform"}), 400
    cols, vals = [], []
    for k, v in data.items():
        if k not in POST_WRITABLE:
            continue
        cols.append(k)
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if "platform" not in cols or "variant" not in cols:
        return jsonify({"error": "platform and variant required"}), 400
    con = _conn()
    cur = con.execute(
        f"INSERT INTO ahb_social_posts ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        vals,
    )
    con.commit()
    pid = cur.lastrowid
    con.close()
    return jsonify({"id": pid})


@social_bp.route("/api/ahb/social/posts/<int:pid>", methods=["PATCH"])
def social_posts_patch(pid: int):
    data = request.get_json(silent=True) or {}
    if "status" in data and data["status"] not in ALLOWED_STATUSES:
        return jsonify({"error": "invalid status"}), 400
    if "platform" in data and data["platform"] not in ALLOWED_PLATFORMS:
        return jsonify({"error": "invalid platform"}), 400
    sets, vals = [], []
    for k, v in data.items():
        if k not in POST_WRITABLE:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    if not sets:
        return jsonify({"error": "no writable fields"}), 400
    sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat(timespec="seconds"))
    vals.append(pid)
    con = _conn()
    con.execute(f"UPDATE ahb_social_posts SET {','.join(sets)} WHERE id=?", vals)
    con.commit(); con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/posts/<int:pid>", methods=["DELETE"])
def social_posts_delete(pid: int):
    con = _conn()
    con.execute("DELETE FROM ahb_social_posts WHERE id=?", (pid,))
    con.commit(); con.close()
    return jsonify({"ok": True})


@social_bp.route("/api/ahb/social/jobs/<int:jid>", methods=["GET"])
def social_jobs_get(jid: int):
    con = _conn()
    r = con.execute("SELECT * FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()
    con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(r))


@social_bp.route("/api/ahb/social/jobs", methods=["GET"])
def social_jobs_list():
    post_id = request.args.get("post_id", type=int)
    status = request.args.get("status")
    sql = "SELECT * FROM ahb_social_jobs WHERE 1=1"
    args = []
    if post_id is not None:
        sql += " AND post_id=?"; args.append(post_id)
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY id DESC LIMIT 200"
    con = _conn()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 3: Run tests**

```
pytest tests/test_social_blueprint.py -v
```

Expected: 7 passed (3 prior + 4 new).

- [ ] **Step 4: Commit**

```
git add dashboard/social_studio.py tests/test_social_blueprint.py
git commit -m "social: posts CRUD + jobs polling endpoints

GET/POST/PATCH/DELETE /api/ahb/social/posts with status, platform,
project_id, q, limit, offset filters. Status/platform whitelists return
400 on invalid input. GET /api/ahb/social/jobs/<id> for async polling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: AI endpoints — caption, hashtags, hooks, score, translate

**Files:**
- Modify: `dashboard/social_studio.py`
- Test: extend `tests/test_social_blueprint.py` with monkeypatched LLM stub

- [ ] **Step 1: Append tests**

```python
import os as _os
import json as _json
from unittest import mock


def _stub_chat(reply: str):
    """Returns a fake Ollama /api/chat response shaped like the real one."""
    return {"message": {"content": reply}}


def test_ai_caption(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: "Built like a tank.\nFraming this week. #ahbco")
    r = client.post("/api/ahb/social/ai/caption", json={
        "source_ids": [1], "platform": "ig_reel", "tone": "pro", "length": "short"
    })
    assert r.status_code == 200
    assert "tank" in r.get_json()["caption"].lower()


def test_ai_hashtags_parses_json_array(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '["#brooklyn", "#renovation", "#ahbco"]')
    r = client.post("/api/ahb/social/ai/hashtags", json={
        "caption": "framing day", "platform": "ig_reel"
    })
    assert r.status_code == 200
    tags = r.get_json()["hashtags"]
    assert "#renovation" in tags


def test_ai_hooks_returns_3(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '["Hook A","Hook B","Hook C"]')
    r = client.post("/api/ahb/social/ai/hooks", json={"source_ids": [1], "n": 3})
    assert r.status_code == 200
    assert len(r.get_json()["hooks"]) == 3


def test_ai_score_returns_score_and_notes(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '{"score": 82, "notes": "Strong hook, weak CTA."}')
    r = client.post("/api/ahb/social/ai/score", json={
        "caption": "x", "hashtags": "#x", "platform": "ig_reel"
    })
    assert r.status_code == 200
    j = r.get_json()
    assert j["score"] == 82 and "CTA" in j["notes"]
```

Run: FAIL.

- [ ] **Step 2: Implement the helper + routes**

Append to `dashboard/social_studio.py`:

```python
import re
import urllib.request

OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _call_ollama_chat(model: str, system: str, user: str,
                      temperature: float = 0.7, timeout: int = 60) -> str:
    """Minimal /api/chat call. Returns the assistant text content."""
    body = json.dumps({
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("message") or {}).get("content", "")


def _pick_copy_model() -> str:
    s = _settings.load_settings()
    return s.get("default_copy_model") or "gpt-oss:20b"


def _extract_json_array(text: str) -> list:
    """Pull the first JSON array out of model output, tolerating code fences."""
    m = re.search(r"\[\s*(?:.|\n)*?\s*\]", text)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _extract_json_obj(text: str) -> dict:
    m = re.search(r"\{\s*(?:.|\n)*?\s*\}", text)
    if not m:
        return {}
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _sources_summary(source_ids: list) -> str:
    """Build a 1-paragraph fact base from image_captions rows for the model."""
    if not source_ids:
        return ""
    placeholders = ",".join("?" * len(source_ids))
    con = _conn()
    rows = con.execute(
        f"SELECT id, sub_path, caption, tags FROM image_captions WHERE id IN ({placeholders})",
        source_ids,
    ).fetchall()
    con.close()
    parts = []
    for r in rows:
        cap = (r["caption"] or "").strip()
        tags = (r["tags"] or "").strip()
        parts.append(f"- {r['sub_path']}: {cap} [{tags}]")
    return "\n".join(parts) if parts else "(no captions available)"


@social_bp.route("/api/ahb/social/ai/caption", methods=["POST"])
def ai_caption():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("caption_system")
    user = (
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Tone: {data.get('tone', 'pro')}\n"
        f"Length: {data.get('length', 'medium')}\n"
        f"Style: {data.get('style', 'trade')}\n"
        f"Source media:\n{_sources_summary(data.get('source_ids') or [])}\n"
    )
    model = data.get("model") or _pick_copy_model()
    text = _call_ollama_chat(model, sys_prompt, user, temperature=0.7).strip()
    return jsonify({"caption": text, "model": model})


@social_bp.route("/api/ahb/social/ai/hashtags", methods=["POST"])
def ai_hashtags():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("hashtag_system")
    brand = _settings.load_brand_kit()
    floor = brand.get("hashtag_floor") or []
    user = (
        f"Caption: {data.get('caption', '')}\n"
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Branded floor (must include): {floor}\n"
        f"Target count: {data.get('count', 18)}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.4)
    tags = _extract_json_array(raw)
    # Ensure brand floor present
    for f in floor:
        if f not in tags:
            tags.append(f)
    return jsonify({"hashtags": tags, "model": model})


@social_bp.route("/api/ahb/social/ai/hooks", methods=["POST"])
def ai_hooks():
    data = request.get_json(silent=True) or {}
    n = int(data.get("n") or 3)
    sys_prompt = _settings.load_prompt("hooks_system")
    user = (
        f"N: {n}\n"
        f"Source media:\n{_sources_summary(data.get('source_ids') or [])}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.9)
    hooks = _extract_json_array(raw)[:n]
    return jsonify({"hooks": hooks, "model": model})


@social_bp.route("/api/ahb/social/ai/score", methods=["POST"])
def ai_score():
    data = request.get_json(silent=True) or {}
    sys_prompt = _settings.load_prompt("score_system")
    user = (
        f"Platform: {data.get('platform', 'ig_reel')}\n"
        f"Caption:\n{data.get('caption', '')}\n"
        f"Hashtags: {data.get('hashtags', '')}\n"
    )
    model = data.get("model") or _pick_copy_model()
    raw = _call_ollama_chat(model, sys_prompt, user, temperature=0.2)
    obj = _extract_json_obj(raw)
    return jsonify({
        "score": int(obj.get("score") or 0),
        "notes": str(obj.get("notes") or ""),
        "model": model,
    })


@social_bp.route("/api/ahb/social/ai/translate", methods=["POST"])
def ai_translate():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    target = data.get("target_lang", "es")
    sys_prompt = (
        f"You are a translator. Translate the user's text into {target}. "
        f"Output only the translation. Preserve hashtags and emoji."
    )
    model = data.get("model") or _pick_copy_model()
    out = _call_ollama_chat(model, sys_prompt, text, temperature=0.2)
    return jsonify({"text": out.strip(), "model": model})
```

- [ ] **Step 3: Run tests**

```
pytest tests/test_social_blueprint.py -v
```

Expected: 11 passed.

- [ ] **Step 4: Commit**

```
git add dashboard/social_studio.py tests/test_social_blueprint.py
git commit -m "social: AI endpoints — caption, hashtags, hooks, score, translate

Local Ollama /api/chat with the 5 tunable system prompts. JSON-array and
JSON-object extractors are tolerant of code fences and stray prose.
Hashtag endpoint forces brand-floor tags. Model defaults to settings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Render pipeline (stills + video) + cover-pick

**Files:**
- Create: `dashboard/social_render.py`
- Modify: `dashboard/social_studio.py` (mount render routes)
- Test: `tests/test_social_render.py`

- [ ] **Step 1: Write failing tests**

`tests/test_social_render.py`:

```python
import os
import sys
import tempfile
import subprocess

import pytest


def _have_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.fixture()
def render_mod():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    if "social_render" in sys.modules:
        del sys.modules["social_render"]
    import social_render
    return social_render


def test_target_dims_for_platforms(render_mod):
    assert render_mod.target_dims("tiktok") == (1080, 1920)
    assert render_mod.target_dims("ig_reel") == (1080, 1920)
    assert render_mod.target_dims("ig_feed_square") == (1080, 1080)
    assert render_mod.target_dims("ig_feed_portrait") == (1080, 1350)
    assert render_mod.target_dims("ig_story") == (1080, 1920)


def test_filter_graph_includes_aspect_crop(render_mod):
    g = render_mod.build_filter_graph(
        in_w=1920, in_h=1080, platform="tiktok",
        fill_mode="blurred", hook_text=None, brand_corner=False,
    )
    assert "scale=" in g
    assert "1080:1920" in g.replace(" ", "")


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required for render integration")
def test_render_still_creates_jpg(render_mod, tmp_path):
    src = tmp_path / "src.jpg"
    # 1px JPEG via PIL
    from PIL import Image
    Image.new("RGB", (1920, 1080), (10, 200, 100)).save(src)
    out = tmp_path / "out.jpg"
    render_mod.render_still(
        src=str(src), out=str(out), platform="ig_feed_square",
        hook_text=None, brand_corner=False,
    )
    assert out.exists() and out.stat().st_size > 100
```

Run: FAIL.

- [ ] **Step 2: Implement `dashboard/social_render.py`**

```python
"""Render pipeline for social_studio. Pure functions, ffmpeg + PIL."""
from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD = os.path.join(HERE, "static", "fonts", "Inter-Bold.ttf")
FONT_REG = os.path.join(HERE, "static", "fonts", "Inter-Regular.ttf")

DIMS = {
    "tiktok": (1080, 1920),
    "ig_reel": (1080, 1920),
    "ig_story": (1080, 1920),
    "ig_feed_square": (1080, 1080),
    "ig_feed_portrait": (1080, 1350),
}


def target_dims(platform: str) -> Tuple[int, int]:
    if platform not in DIMS:
        raise ValueError(f"unknown platform: {platform}")
    return DIMS[platform]


def build_filter_graph(in_w: int, in_h: int, platform: str,
                       fill_mode: str = "blurred",
                       hook_text: Optional[str] = None,
                       brand_corner: bool = False) -> str:
    out_w, out_h = target_dims(platform)
    src_aspect = in_w / max(in_h, 1)
    tgt_aspect = out_w / out_h
    parts = []
    if src_aspect > tgt_aspect:
        # Wider than target → either crop or pad
        if fill_mode == "blurred":
            # Two-stream graph: blurred bg + cropped fg via split
            parts.append(
                f"split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},gblur=sigma=24[bgb];"
                f"[fg]scale={out_w}:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
            )
        else:  # letterbox / brand color / crop
            parts.append(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
    else:
        # Taller or same → simple cover crop
        parts.append(
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}"
        )
    if hook_text:
        safe = hook_text.replace("'", r"\\'")
        parts.append(
            f"drawtext=fontfile={FONT_BOLD}:text='{safe}':"
            f"fontcolor=white:fontsize=72:line_spacing=10:"
            f"box=1:boxcolor=black@0.45:boxborderw=18:"
            f"x=(w-text_w)/2:y=h*0.10"
        )
    return ",".join(parts)


def _ffprobe(path: str) -> Tuple[int, int]:
    """Return (width, height) of the first video stream / image."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split("x")
    return int(w), int(h)


def render_still(src: str, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred") -> str:
    w, h = _ffprobe(src)
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", g, "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def render_video(srcs: List[str], out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60) -> str:
    """Concat sources, re-encode to target dims, optional hook overlay."""
    if not srcs:
        raise ValueError("no sources")
    # Probe first source for dims; assume rest are similar (filter per-clip would be ideal but YAGNI here)
    w, h = _ffprobe(srcs[0])
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    tmpdir = os.path.dirname(out) or "."
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w") as f:
        for s in srcs:
            f.write(f"file {shlex.quote(os.path.abspath(s))}\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-vf", g,
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-t", str(max_seconds),
        out,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def extract_cover(src: str, out: str, t_seconds: float = 0.5) -> str:
    cmd = ["ffmpeg", "-y", "-ss", str(t_seconds), "-i", src,
           "-frames:v", "1", "-q:v", "3", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
```

- [ ] **Step 3: Add render endpoint to `social_studio.py`**

```python
try:
    from dashboard import social_render as _render
except ImportError:
    import social_render as _render


def _resolve_media_paths(source_media_ids: list) -> list:
    """Map image_captions.id → absolute file path. Joins sub_path under the
    baza cloud root if not absolute."""
    if not source_media_ids:
        return []
    placeholders = ",".join("?" * len(source_media_ids))
    con = _conn()
    rows = con.execute(
        f"SELECT id, sub_path FROM image_captions WHERE id IN ({placeholders})",
        source_media_ids,
    ).fetchall()
    con.close()
    cloud_root = os.environ.get(
        "BAZA_CLOUD_ROOT",
        "/home/switchhacker/baza-cloud",
    )
    paths = []
    for r in rows:
        p = r["sub_path"]
        if not os.path.isabs(p):
            p = os.path.join(cloud_root, p)
        if os.path.exists(p):
            paths.append(p)
    return paths


@social_bp.route("/api/ahb/social/posts/<int:pid>/render", methods=["POST"])
def social_render_post(pid: int):
    body = request.get_json(silent=True) or {}
    con = _conn()
    row = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        return jsonify({"error": "post not found"}), 404
    post = _row_to_post(row)
    paths = _resolve_media_paths(post["source_media_ids"])
    if not paths:
        con.close()
        return jsonify({"error": "no resolvable source media"}), 400
    out_dir = os.path.join(
        DASHBOARD_DIR, "artifacts", "social",
        datetime.utcnow().strftime("%Y-%m-%d"), str(pid),
    )
    os.makedirs(out_dir, exist_ok=True)
    is_video = any(
        p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")) for p in paths
    )
    platform = post["platform"]
    ext = ".mp4" if is_video else ".jpg"
    out_path = os.path.join(out_dir, f"{platform}{ext}")
    hook = body.get("hook_text")
    fill = body.get("fill_mode", "blurred")
    try:
        if is_video:
            _render.render_video(paths, out_path, platform, hook_text=hook, fill_mode=fill)
            cover_path = os.path.join(out_dir, "cover.jpg")
            _render.extract_cover(out_path, cover_path)
        else:
            _render.render_still(paths[0], out_path, platform, hook_text=hook, fill_mode=fill)
            cover_path = out_path
    except subprocess.CalledProcessError as e:
        con.execute("UPDATE ahb_social_posts SET status='failed' WHERE id=?", (pid,))
        con.commit(); con.close()
        return jsonify({"error": "render failed", "detail": e.stderr.decode(errors='ignore')[-500:]}), 500
    con.execute(
        "UPDATE ahb_social_posts SET asset_path=?, cover_path=?, updated_at=? WHERE id=?",
        (out_path, cover_path, datetime.utcnow().isoformat(timespec="seconds"), pid),
    )
    con.commit(); con.close()
    return jsonify({"ok": True, "asset_path": out_path, "cover_path": cover_path})
```

- [ ] **Step 4: Run render tests**

```
pytest tests/test_social_render.py -v
```

Expected: 3 passed (still test runs only if ffmpeg present).

- [ ] **Step 5: Commit**

```
git add dashboard/social_render.py dashboard/social_studio.py tests/test_social_render.py
git commit -m "social: ffmpeg render pipeline + endpoint

social_render.py builds platform-specific filter graphs (smart-crop or
blurred-background fill), encodes H.264 + AAC + faststart, extracts a
cover frame. POST /api/ahb/social/posts/<id>/render kicks the encode and
stores asset_path + cover_path on the post.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```


---

## Task 7: ahb123.html — sub-tab nav + tab pane scaffold

**Files:**
- Modify: `dashboard/templates/ahb123.html`

This task adds only the empty shell. Composer, Library, etc. are filled in subsequent tasks. Manual smoke only — no unit tests for HTML.

- [ ] **Step 1: Read the current sub-nav region**

```
grep -n "data-tab=\"reviews\"" dashboard/templates/ahb123.html
```

Expect a hit near line 706. The new `<div class="sub-tab" ...>` goes immediately before that line.

- [ ] **Step 2: Add the sub-tab nav entry**

In `dashboard/templates/ahb123.html`, find:

```html
  <div class="sub-tab" data-tab="reviews" onclick="switchTab('reviews')"><span class="sub-tab-icon">⭐</span> Reviews</div>
```

Insert before it:

```html
  <div class="sub-tab" data-tab="social" onclick="switchTab('social')"><span class="sub-tab-icon">📣</span> Social</div>
```

- [ ] **Step 3: Add the tab pane**

Find the closing of `tab-photos` (search `id="tab-photos"` then locate the matching `</div>` that closes the tab-pane). After that closing `</div>` and before the next `<div class="tab-pane"...>`, insert:

```html
<div class="tab-pane" id="tab-social">
  <div class="page-header">
    <div>
      <div class="page-title">📣 Social Media Studio</div>
      <div class="page-sub">Make TikTok + Instagram content from your project media. Local AI. Presets. Auto-pilot.</div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn-secondary ss-subnav" data-sub="composer">Composer</button>
      <button class="btn-secondary ss-subnav" data-sub="library">Library</button>
      <button class="btn-secondary ss-subnav" data-sub="scheduler">Scheduler</button>
      <button class="btn-secondary ss-subnav" data-sub="presets">Presets</button>
      <button class="btn-secondary ss-subnav" data-sub="autopilot">Auto-Pilot</button>
      <button class="btn-secondary" onclick="SocialStudio.openSettings()" title="Settings">⚙️</button>
    </div>
  </div>
  <div id="ss-sub-composer" class="ss-sub"></div>
  <div id="ss-sub-library" class="ss-sub" style="display:none"></div>
  <div id="ss-sub-scheduler" class="ss-sub" style="display:none"></div>
  <div id="ss-sub-presets" class="ss-sub" style="display:none"></div>
  <div id="ss-sub-autopilot" class="ss-sub" style="display:none"></div>
</div>
```

- [ ] **Step 4: Add body-level modal containers**

Find the very last `</div>` of the body content (or just before `</body>` / `{% endblock %}`). Insert (these are empty placeholders the sub-modules will inject into):

```html
<!-- Social Studio body-level modals (per ahb123 modal-ancestor rule) -->
<div id="socialPresetEditor" class="modal-bg" style="display:none"></div>
<div id="socialBrandKit"     class="modal-bg" style="display:none"></div>
<div id="socialImageGen"     class="modal-bg" style="display:none"></div>
<div id="socialVoiceover"    class="modal-bg" style="display:none"></div>
<div id="socialSettings"     class="modal-bg" style="display:none"></div>
<div id="socialPostDetail"   class="modal-bg" style="display:none"></div>
```

- [ ] **Step 5: Add the base `<style>` and `<script>` blocks**

Before the closing `</body>` (or at the bottom of the existing trailing `<script>` block), append:

```html
<style>
  .ss-sub { padding-top: 12px; }
  .ss-subnav { font-weight: 600; }
  .ss-subnav.active { background:#10b981; color:#0a0a18; border-color:#10b981; }
  .ss-grid { display:grid; grid-template-columns: 320px 1fr 340px; gap: 12px; }
  @media (max-width: 1200px) { .ss-grid { grid-template-columns: 1fr; } }
  .ss-card { background:#0e0e1e; border:1px solid #1a1a2e; border-radius:12px; padding:12px; }
  .ss-thumb { width:100%; aspect-ratio:1/1; object-fit:cover; border-radius:8px; cursor:pointer; }
  .ss-thumb.selected { outline:3px solid #10b981; }
  .ss-platform-tabs { display:flex; gap:6px; margin-bottom:8px; }
  .ss-platform-tab { padding:6px 10px; border:1px solid #1a1a2e; border-radius:6px; font-size:11px; cursor:pointer; color:#aaa; background:#070712; }
  .ss-platform-tab.active { background:#10b981; color:#0a0a18; border-color:#10b981; }
  .ss-preview-shell { background:#000; border-radius:18px; margin:0 auto; max-width:380px; position:relative; overflow:hidden; }
  .ss-preview-shell.tiktok, .ss-preview-shell.ig_reel, .ss-preview-shell.ig_story { aspect-ratio: 9/16; }
  .ss-preview-shell.ig_feed_square { aspect-ratio: 1/1; }
  .ss-preview-shell.ig_feed_portrait { aspect-ratio: 4/5; }
  .ss-preview-shell canvas, .ss-preview-shell video, .ss-preview-shell img { width:100%; height:100%; object-fit:cover; display:block; }
  .ss-status-pill { padding:2px 8px; border-radius:999px; font-size:10px; font-weight:700; text-transform:uppercase; }
  .ss-pill-draft { background:#1f2937; color:#9ca3af; }
  .ss-pill-pending_review { background:#7c2d12; color:#fdba74; }
  .ss-pill-approved { background:#064e3b; color:#10b981; }
  .ss-pill-scheduled { background:#1e3a8a; color:#60a5fa; }
  .ss-pill-posted { background:#064e3b; color:#a7f3d0; }
  .ss-pill-rejected { background:#7f1d1d; color:#fca5a5; }
  .ss-pill-failed { background:#7f1d1d; color:#fca5a5; }
</style>

<script>
window.SocialStudio = window.SocialStudio || {
  state: {
    activeSub: 'composer',
    activePlatform: 'tiktok',
    sources: [],
    shotList: [],
    platforms: { tiktok:true, ig_reel:true, ig_feed_square:true, ig_feed_portrait:false, ig_story:false },
    variants: {},
    settings: null,
    brandKit: null,
  },
  modules: {},
  init() {
    if (this._inited) return;
    this._inited = true;
    document.querySelectorAll('#tab-social .ss-subnav').forEach(b => {
      b.addEventListener('click', () => this.switchSub(b.dataset.sub));
    });
    this.switchSub('composer');
  },
  switchSub(name) {
    this.state.activeSub = name;
    document.querySelectorAll('#tab-social .ss-subnav').forEach(b => {
      b.classList.toggle('active', b.dataset.sub === name);
    });
    document.querySelectorAll('#tab-social .ss-sub').forEach(d => {
      d.style.display = d.id === ('ss-sub-' + name) ? '' : 'none';
    });
    const mod = this.modules[name];
    if (mod && typeof mod.render === 'function') mod.render();
  },
  openSettings() { if (this.modules.settings) this.modules.settings.open(); },
};

// Hook into existing switchTab() so SocialStudio.init() runs when tab opens
(function(){
  const orig = window.switchTab;
  window.switchTab = function(name) {
    const r = orig ? orig.apply(this, arguments) : undefined;
    if (name === 'social') SocialStudio.init();
    return r;
  };
})();
</script>
```

- [ ] **Step 6: Restart dashboard, smoke**

```
sudo systemctl restart baza-dashboard
sleep 2
curl -s http://127.0.0.1:8888/ahb123 -o /tmp/ahb.html
grep -c 'data-tab="social"' /tmp/ahb.html
```

Expected: at least `1`. Then open `http://127.0.0.1:8888/ahb123` in a browser, confirm the new "📣 Social" sub-tab appears and clicking it shows the page header + 5 sub-buttons.

- [ ] **Step 7: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social: tab scaffold + sub-sub-nav + body-level modal slots

Adds the 📣 Social sub-tab between Media and Reviews, a 5-button
sub-sub-nav, empty placeholder divs for each sub-section, and body-level
modal slots (modals must NOT be nested in tab panes — display:none
ancestor rule). window.SocialStudio is the JS namespace; modules
register themselves under SocialStudio.modules.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Composer module (UI) — sources, preview, variants

**Files:**
- Modify: `dashboard/templates/ahb123.html`

- [ ] **Step 1: Append the Composer module script**

Inside the existing `<script>` block from Task 7 (after the `window.SocialStudio = …` object), append:

```javascript
SocialStudio.modules.composer = (function(){
  const root = () => document.getElementById('ss-sub-composer');
  const state = SocialStudio.state;

  async function loadSources(projectId, type, q) {
    const url = new URL('/api/ahb/social/sources', location.origin);
    if (projectId) url.searchParams.set('project_id', projectId);
    if (type) url.searchParams.set('type', type);
    if (q) url.searchParams.set('q', q);
    const r = await fetch(url);
    state.sources = (await r.json()).items || [];
    renderSourceGrid();
  }

  function renderSourceGrid() {
    const grid = document.getElementById('ss-source-grid');
    if (!grid) return;
    grid.innerHTML = state.sources.map(s => `
      <div class="ss-source-item" data-id="${s.id}">
        <img class="ss-thumb${state.shotList.includes(s.id) ? ' selected' : ''}"
             src="/api/ahb/media-thumb?path=${encodeURIComponent(s.sub_path)}"
             loading="lazy" onclick="SocialStudio.modules.composer.toggle(${s.id})">
        <div style="font-size:10px;color:#666;margin-top:2px">${(s.caption||'').slice(0,40)}</div>
      </div>
    `).join('');
  }

  function toggle(id) {
    const i = state.shotList.indexOf(id);
    if (i === -1) state.shotList.push(id); else state.shotList.splice(i, 1);
    renderSourceGrid();
    renderPreview();
  }

  function renderPreview() {
    const shell = document.getElementById('ss-preview-shell');
    if (!shell) return;
    shell.className = 'ss-preview-shell ' + state.activePlatform;
    const firstId = state.shotList[0];
    const src = state.sources.find(s => s.id === firstId);
    if (!src) {
      shell.innerHTML = '<div style="color:#444;padding:80px 12px;text-align:center;font-size:12px">Pick media at left</div>';
      return;
    }
    const ext = (src.sub_path || '').toLowerCase().split('.').pop();
    if (['mp4','mov','webm','mkv'].includes(ext)) {
      shell.innerHTML = `<video src="/api/ahb/media-serve?path=${encodeURIComponent(src.sub_path)}" muted autoplay playsinline loop></video>`;
    } else {
      shell.innerHTML = `<img src="/api/ahb/media-serve?path=${encodeURIComponent(src.sub_path)}">`;
    }
    const hook = (document.getElementById('ss-hook-input') || {}).value;
    if (hook) {
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:absolute;top:8%;left:8%;right:8%;text-align:center;font-weight:800;color:white;text-shadow:0 2px 6px black;font-size:24px;line-height:1.1';
      overlay.textContent = hook;
      shell.appendChild(overlay);
    }
  }

  function setPlatform(p) {
    state.activePlatform = p;
    document.querySelectorAll('#ss-platform-tabs .ss-platform-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.platform === p);
    });
    renderPreview();
  }

  async function aiCaption() {
    const r = await fetch('/api/ahb/social/ai/caption', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        source_ids: state.shotList, platform: state.activePlatform,
        tone: document.getElementById('ss-tone').value,
        length: document.getElementById('ss-length').value,
        style: document.getElementById('ss-style').value,
      }),
    });
    const j = await r.json();
    document.getElementById('ss-caption-' + state.activePlatform).value = j.caption || '';
  }

  async function aiHashtags() {
    const r = await fetch('/api/ahb/social/ai/hashtags', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        caption: document.getElementById('ss-caption-' + state.activePlatform).value,
        platform: state.activePlatform,
        count: 18,
      }),
    });
    const j = await r.json();
    document.getElementById('ss-hashtags-' + state.activePlatform).value = (j.hashtags || []).join(' ');
  }

  async function aiHooks() {
    const r = await fetch('/api/ahb/social/ai/hooks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ source_ids: state.shotList, n: 3 }),
    });
    const j = await r.json();
    const pick = window.prompt('Pick a hook:\n\n' + (j.hooks || []).map((h, i) => `${i+1}. ${h}`).join('\n'), '1');
    const idx = (parseInt(pick, 10) || 1) - 1;
    if (j.hooks && j.hooks[idx]) {
      document.getElementById('ss-hook-input').value = j.hooks[idx];
      renderPreview();
    }
  }

  async function aiScore() {
    const r = await fetch('/api/ahb/social/ai/score', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        caption: document.getElementById('ss-caption-' + state.activePlatform).value,
        hashtags: document.getElementById('ss-hashtags-' + state.activePlatform).value,
        platform: state.activePlatform,
      }),
    });
    const j = await r.json();
    alert(`Score: ${j.score}/100\n\n${j.notes}`);
  }

  async function renderPackage() {
    if (!state.shotList.length) { alert('Pick at least one source.'); return; }
    const post = await fetch('/api/ahb/social/posts', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        platform: state.activePlatform, variant: state.activePlatform,
        source_media_ids: state.shotList,
        caption: document.getElementById('ss-caption-' + state.activePlatform).value,
        hashtags: document.getElementById('ss-hashtags-' + state.activePlatform).value,
      }),
    }).then(r => r.json());
    const ren = await fetch(`/api/ahb/social/posts/${post.id}/render`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ hook_text: document.getElementById('ss-hook-input').value || null }),
    }).then(r => r.json());
    if (ren.error) { alert('Render failed: ' + ren.error); return; }
    alert('Rendered: ' + ren.asset_path);
    SocialStudio.switchSub('library');
  }

  function render() {
    root().innerHTML = `
      <div class="ss-grid">
        <div class="ss-card">
          <div style="font-weight:700;font-size:13px;margin-bottom:8px">Sources</div>
          <select id="ss-source-project" class="filter-select" style="width:100%;margin-bottom:6px" onchange="SocialStudio.modules.composer._reload()">
            <option value="">All projects</option>
          </select>
          <input id="ss-source-q" class="search-box" placeholder="Search caption/tags" style="width:100%;margin-bottom:6px" oninput="if(this._t)clearTimeout(this._t);this._t=setTimeout(()=>SocialStudio.modules.composer._reload(),300)">
          <div id="ss-source-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;max-height:520px;overflow-y:auto"></div>
        </div>
        <div class="ss-card">
          <div id="ss-platform-tabs" class="ss-platform-tabs">
            <div class="ss-platform-tab active" data-platform="tiktok"   onclick="SocialStudio.modules.composer.setPlatform('tiktok')">TikTok 9:16</div>
            <div class="ss-platform-tab" data-platform="ig_reel"          onclick="SocialStudio.modules.composer.setPlatform('ig_reel')">IG Reel 9:16</div>
            <div class="ss-platform-tab" data-platform="ig_feed_square"   onclick="SocialStudio.modules.composer.setPlatform('ig_feed_square')">IG Feed 1:1</div>
            <div class="ss-platform-tab" data-platform="ig_feed_portrait" onclick="SocialStudio.modules.composer.setPlatform('ig_feed_portrait')">IG Feed 4:5</div>
            <div class="ss-platform-tab" data-platform="ig_story"          onclick="SocialStudio.modules.composer.setPlatform('ig_story')">IG Story 9:16</div>
          </div>
          <div id="ss-preview-shell" class="ss-preview-shell tiktok"></div>
          <input id="ss-hook-input" placeholder="Hook overlay text (≤ 60 chars)" maxlength="60" style="width:100%;margin-top:8px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff" oninput="SocialStudio.modules.composer.renderPreview()">
        </div>
        <div class="ss-card" id="ss-variant-panel">
          <div style="font-weight:700;font-size:13px;margin-bottom:8px">Copy</div>
          <select id="ss-tone" class="filter-select" style="width:100%;margin-bottom:4px">
            <option value="pro">Pro</option><option value="hype">Hype</option>
            <option value="casual">Casual</option><option value="educational">Educational</option>
            <option value="trade">Trade</option><option value="funny">Funny</option>
          </select>
          <select id="ss-length" class="filter-select" style="width:100%;margin-bottom:4px">
            <option value="short">Short</option><option value="medium" selected>Medium</option><option value="long">Long</option>
          </select>
          <select id="ss-style" class="filter-select" style="width:100%;margin-bottom:8px">
            <option value="trade">Trade</option><option value="lifestyle">Lifestyle</option>
            <option value="behind">Behind-the-scenes</option><option value="tutorial">Tutorial</option>
            <option value="showcase">Showcase</option>
          </select>
          ${['tiktok','ig_reel','ig_feed_square','ig_feed_portrait','ig_story'].map(p => `
            <div data-platform-block="${p}" style="${p==='tiktok'?'':'display:none'};margin-bottom:6px">
              <textarea id="ss-caption-${p}" placeholder="${p} caption" style="width:100%;height:80px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff;font-size:12px"></textarea>
              <input id="ss-hashtags-${p}" placeholder="#hashtag #pool" style="width:100%;margin-top:4px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff;font-size:12px">
            </div>
          `).join('')}
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px">
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiCaption()">✨ Caption</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHashtags()"># Tags</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHooks()">🪝 Hooks</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiScore()">🎯 Score</button>
          </div>
          <button class="btn-primary" style="width:100%;margin-top:10px" onclick="SocialStudio.modules.composer.renderPackage()">▶️ Render package</button>
        </div>
      </div>
    `;
    loadSources();
  }

  function _reload() {
    const p = document.getElementById('ss-source-project').value || null;
    const q = document.getElementById('ss-source-q').value || null;
    loadSources(p, null, q);
  }

  return { render, toggle, setPlatform, aiCaption, aiHashtags, aiHooks, aiScore, renderPackage, renderPreview, _reload };
})();
```

When switching platform, also show only that platform's caption block:

In `setPlatform()` immediately before `renderPreview();`, add:

```javascript
document.querySelectorAll('[data-platform-block]').forEach(d => {
  d.style.display = d.dataset.platformBlock === p ? '' : 'none';
});
```

- [ ] **Step 2: Restart dashboard**

```
sudo systemctl restart baza-dashboard
```

- [ ] **Step 3: Manual smoke**

1. Open `/ahb123`, click **📣 Social**, land on Composer
2. Type a search query, watch grid update
3. Click a thumbnail — see it outlined green AND appear in the phone preview
4. Switch platform tabs — preview aspect changes
5. Click ✨ Caption — text fills (or LLM error toast if Ollama down)
6. Click # Tags — hashtag row fills
7. Click 🪝 Hooks — picker prompt appears
8. Click 🎯 Score — alert shows score + notes
9. Click ▶️ Render package — alert shows asset_path; lands on Library

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social: Composer UI — sources, preview, variants

Three-column composer. Reuses /api/ahb/media-thumb + /media-serve for
thumbnails and the live preview. Per-platform caption + hashtag blocks
toggle visibility on platform switch. AI buttons wire to /ai/* routes.
Render package POSTs a draft post + kicks the render endpoint.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Library + Post detail modal

**Files:**
- Modify: `dashboard/templates/ahb123.html`

- [ ] **Step 1: Append Library + Post detail modules**

Append inside the same `<script>` block:

```javascript
SocialStudio.modules.library = (function(){
  const root = () => document.getElementById('ss-sub-library');
  let items = [];
  let f = { status: '', platform: '', q: '' };

  async function load() {
    const url = new URL('/api/ahb/social/posts', location.origin);
    if (f.status) url.searchParams.set('status', f.status);
    if (f.platform) url.searchParams.set('platform', f.platform);
    if (f.q) url.searchParams.set('q', f.q);
    const r = await fetch(url);
    items = (await r.json()).items || [];
    paint();
  }

  function paint() {
    const grid = items.map(p => `
      <div class="ss-card" style="display:flex;gap:10px;align-items:center;margin-bottom:8px">
        <img src="${p.cover_path ? '/api/ahb/social/posts/' + p.id + '/cover' : '/static/social/brand/placeholder.png'}"
             style="width:90px;aspect-ratio:9/16;object-fit:cover;border-radius:6px;background:#070712;cursor:pointer"
             onclick="SocialStudio.modules.postdetail.open(${p.id})">
        <div style="flex:1;min-width:0">
          <div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">
            <span class="ss-status-pill ss-pill-${p.status}">${p.status}</span>
            <span style="color:#888;font-size:11px">${p.platform}</span>
            ${p.score != null ? `<span style="color:#10b981;font-size:11px">${p.score}/100</span>` : ''}
          </div>
          <div style="color:#ddd;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(p.caption||'').slice(0,160)}</div>
          <div style="color:#666;font-size:11px;margin-top:2px">${(p.hashtags||'').slice(0,120)}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px">
          ${p.status === 'pending_review' ? `
            <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.library.setStatus(${p.id},'approved')">✅</button>
            <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.library.setStatus(${p.id},'rejected')">❌</button>
          ` : ''}
          <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.library.telegram(${p.id})">📲</button>
        </div>
      </div>
    `).join('');
    root().innerHTML = `
      <div style="display:flex;gap:6px;margin-bottom:12px">
        <select onchange="SocialStudio.modules.library.setFilter('status', this.value)">
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="pending_review">Pending</option>
          <option value="approved">Approved</option>
          <option value="scheduled">Scheduled</option>
          <option value="posted">Posted</option>
          <option value="rejected">Rejected</option>
          <option value="failed">Failed</option>
        </select>
        <select onchange="SocialStudio.modules.library.setFilter('platform', this.value)">
          <option value="">All platforms</option>
          <option value="tiktok">TikTok</option>
          <option value="ig_reel">IG Reel</option>
          <option value="ig_feed_square">IG Feed 1:1</option>
          <option value="ig_feed_portrait">IG Feed 4:5</option>
          <option value="ig_story">IG Story</option>
        </select>
        <input placeholder="Search caption/hashtags" oninput="SocialStudio.modules.library.setFilter('q', this.value)" style="flex:1;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
      </div>
      <div>${grid || '<div style="color:#444;padding:40px;text-align:center">No posts yet.</div>'}</div>
    `;
  }

  function setFilter(k, v) {
    f[k] = v;
    if (this._t) clearTimeout(this._t);
    this._t = setTimeout(load, 250);
  }

  async function setStatus(id, status) {
    await fetch('/api/ahb/social/posts/' + id, {
      method: 'PATCH', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ status }),
    });
    load();
  }

  async function telegram(id) {
    await fetch('/api/ahb/social/posts/' + id + '/telegram', { method: 'POST' });
    alert('Sent to Telegram');
  }

  return { render: load, setFilter, setStatus, telegram };
})();

SocialStudio.modules.postdetail = (function(){
  async function open(id) {
    const r = await fetch('/api/ahb/social/posts/' + id);
    if (!r.ok) { alert('Not found'); return; }
    // For brevity, reuse list endpoint with filter — small N, OK.
    const list = await fetch('/api/ahb/social/posts').then(x => x.json());
    const p = (list.items || []).find(x => x.id === id);
    if (!p) return;
    const m = document.getElementById('socialPostDetail');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:700px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">${p.platform} · #${p.id}</div>
          <button onclick="document.getElementById('socialPostDetail').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">×</button>
        </div>
        <textarea id="pd-caption" style="width:100%;height:120px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff">${p.caption || ''}</textarea>
        <input id="pd-hashtags" style="width:100%;margin-top:6px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff" value="${p.hashtags || ''}">
        <textarea id="pd-firstcomment" style="width:100%;height:60px;margin-top:6px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff" placeholder="First comment (IG convention)">${p.first_comment || ''}</textarea>
        <div style="display:flex;gap:6px;margin-top:10px;justify-content:flex-end">
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.save(${p.id})">Save</button>
          <button class="btn-primary" onclick="SocialStudio.modules.postdetail.bundle(${p.id})">📥 Bundle</button>
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.telegram(${p.id})">📲 Phone</button>
        </div>
      </div>
    `;
  }
  async function save(id) {
    await fetch('/api/ahb/social/posts/' + id, {
      method: 'PATCH', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        caption: document.getElementById('pd-caption').value,
        hashtags: document.getElementById('pd-hashtags').value,
        first_comment: document.getElementById('pd-firstcomment').value,
      }),
    });
    document.getElementById('socialPostDetail').style.display = 'none';
    SocialStudio.modules.library.render();
  }
  function bundle(id) { window.open('/api/ahb/social/posts/' + id + '/bundle', '_blank'); }
  async function telegram(id) {
    await fetch('/api/ahb/social/posts/' + id + '/telegram', { method: 'POST' });
    alert('Sent.');
  }
  return { open, save, bundle, telegram };
})();
```

- [ ] **Step 2: Add cover + bundle endpoints to `social_studio.py`**

```python
from flask import send_file, send_from_directory


@social_bp.route("/api/ahb/social/posts/<int:pid>/cover", methods=["GET"])
def social_post_cover(pid: int):
    con = _conn()
    r = con.execute("SELECT cover_path FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    if not r or not r["cover_path"] or not os.path.exists(r["cover_path"]):
        return jsonify({"error": "no cover"}), 404
    return send_file(r["cover_path"])


@social_bp.route("/api/ahb/social/posts/<int:pid>/bundle", methods=["GET"])
def social_post_bundle(pid: int):
    import io, zipfile
    con = _conn()
    r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    post = _row_to_post(r)
    if not post.get("asset_path") or not os.path.exists(post["asset_path"]):
        return jsonify({"error": "no rendered asset"}), 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(post["asset_path"], arcname=os.path.basename(post["asset_path"]))
        if post.get("cover_path") and os.path.exists(post["cover_path"]):
            z.write(post["cover_path"], arcname="cover.jpg")
        caption_block = (post.get("caption") or "") + "\n\n" + (post.get("hashtags") or "")
        if post.get("first_comment"):
            caption_block += "\n\n---\n" + post["first_comment"]
        z.writestr(f"caption_{post['platform']}.txt", caption_block)
        z.writestr("manifest.json", json.dumps(post, default=str, indent=2))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=f"social_{pid}.zip")
```

- [ ] **Step 3: Restart dashboard, smoke**

```
sudo systemctl restart baza-dashboard
```

In browser: render a package from Composer → switch to Library → see the row → click cover thumbnail → modal opens → edit caption → Save → row updates. Click 📥 → zip downloads.

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html dashboard/social_studio.py
git commit -m "social: Library list + Post detail modal + bundle zip

Library lists posts with status/platform/q filters, inline status
buttons, and per-row Telegram drop. Post-detail modal edits caption /
hashtags / first comment. Bundle endpoint returns a zip containing the
rendered asset, cover, caption.txt, and manifest.json.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```


---

## Task 10: Presets UI (CRUD + seed install) + Settings drawer

**Files:**
- Modify: `dashboard/social_studio.py` — add seed-install endpoint
- Modify: `dashboard/templates/ahb123.html` — add Presets + Settings modules

- [ ] **Step 1: Add seed presets endpoint to `social_studio.py`**

```python
SEED_PRESETS = [
    {"name": "Project Showcase", "tone": "pro", "length": "medium", "style": "showcase",
     "platform_targets": ["ig_feed_square", "ig_reel"], "is_seed": 1,
     "description": "6-10 best photos from one project as carousel + Reel."},
    {"name": "Before / After Reel", "tone": "hype", "length": "short", "style": "showcase",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Split-screen first vs final phase, 15s, hype tone."},
    {"name": "Heavy Equipment Spotlight", "tone": "educational", "length": "medium", "style": "showcase",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Single video, slow-mo intro, gear specs overlay."},
    {"name": "Process Explainer", "tone": "educational", "length": "medium", "style": "tutorial",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "30-60s how-we-do-it w/ voiceover."},
    {"name": "Customer Testimonial", "tone": "pro", "length": "medium", "style": "showcase",
     "platform_targets": ["ig_reel", "ig_feed_square"], "is_seed": 1,
     "description": "Quote pulled from Reviews + branded card."},
    {"name": "Day-in-the-Life", "tone": "casual", "length": "medium", "style": "behind",
     "platform_targets": ["tiktok", "ig_reel"], "is_seed": 1,
     "description": "Montage from one day's media, music-led."},
    {"name": "Quick Tip", "tone": "educational", "length": "short", "style": "tutorial",
     "platform_targets": ["tiktok", "ig_story"], "is_seed": 1,
     "description": "Single still + bold text overlay, 5-10 word hook."},
    {"name": "Sub / Trade Shout-out", "tone": "casual", "length": "short", "style": "behind",
     "platform_targets": ["ig_feed_square", "ig_story"], "is_seed": 1,
     "description": "Tag a sub w/ photo of their work."},
]


@social_bp.route("/api/ahb/social/presets/install-seeds", methods=["POST"])
def social_presets_install_seeds():
    con = _conn()
    existing = {r[0] for r in con.execute(
        "SELECT name FROM ahb_social_presets WHERE is_seed=1")}
    inserted = []
    for sp in SEED_PRESETS:
        if sp["name"] in existing:
            continue
        cols = list(sp.keys())
        vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in sp.values()]
        cur = con.execute(
            f"INSERT INTO ahb_social_presets ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals,
        )
        inserted.append(cur.lastrowid)
    con.commit(); con.close()
    return jsonify({"installed": inserted})
```

- [ ] **Step 2: Presets + Settings UI modules**

Append inside the `<script>` block:

```javascript
SocialStudio.modules.presets = (function(){
  const root = () => document.getElementById('ss-sub-presets');
  let items = [];

  async function load() {
    items = (await fetch('/api/ahb/social/presets').then(r => r.json())).items || [];
    paint();
  }

  function paint() {
    const list = items.map(p => `
      <div class="ss-card" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-weight:700">${p.name} ${p.is_seed ? '<span style="color:#666;font-size:10px;">(seed)</span>' : ''}</div>
            <div style="color:#888;font-size:11px">${p.description || ''}</div>
            <div style="color:#666;font-size:10px;margin-top:2px">cadence: ${p.cadence} · max/day: ${p.max_per_day} · ${p.active ? 'ACTIVE' : 'paused'}</div>
          </div>
          <div style="display:flex;gap:4px">
            <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.presets.edit(${p.id})">✏️</button>
            <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.presets.runOnce(${p.id})">▶️ Test</button>
            <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="SocialStudio.modules.presets.toggle(${p.id}, ${p.active})">${p.active ? '⏸' : '▶'}</button>
          </div>
        </div>
      </div>
    `).join('');
    root().innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-size:13px;color:#aaa">${items.length} presets</div>
        <div style="display:flex;gap:6px">
          <button class="btn-secondary" onclick="SocialStudio.modules.presets.installSeeds()">Install seed presets</button>
          <button class="btn-primary" onclick="SocialStudio.modules.presets.edit(null)">+ New</button>
        </div>
      </div>
      <div>${list || '<div style="color:#444;padding:40px;text-align:center">No presets. Click "Install seed presets".</div>'}</div>
    `;
  }

  async function installSeeds() {
    const r = await fetch('/api/ahb/social/presets/install-seeds', { method: 'POST' });
    const j = await r.json();
    alert('Installed ' + (j.installed || []).length + ' preset(s).');
    load();
  }

  function edit(id) {
    const p = id ? items.find(x => x.id === id) : { name: '', description: '', tone: 'pro',
        length: 'medium', style: 'trade', cadence: 'off', max_per_day: 1,
        auto_approve: 0, score_threshold: 75, active: 1 };
    const m = document.getElementById('socialPresetEditor');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:560px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">${id ? 'Edit' : 'New'} preset</div>
          <button onclick="document.getElementById('socialPresetEditor').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">×</button>
        </div>
        <input id="pe-name" placeholder="Name" value="${p.name || ''}" style="width:100%;margin-bottom:6px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff">
        <textarea id="pe-desc" placeholder="Description" style="width:100%;height:60px;margin-bottom:6px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff">${p.description || ''}</textarea>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:6px">
          <select id="pe-tone"><option value="pro">Pro</option><option value="hype">Hype</option><option value="casual">Casual</option><option value="educational">Educational</option><option value="trade">Trade</option><option value="funny">Funny</option></select>
          <select id="pe-length"><option value="short">Short</option><option value="medium">Medium</option><option value="long">Long</option></select>
          <select id="pe-cadence"><option value="off">Off</option><option value="daily">Daily</option><option value="n_per_week">N/week</option><option value="on_trigger">On trigger</option></select>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px">
          <label style="font-size:12px;color:#aaa">Max/day <input id="pe-max" type="number" min="0" value="${p.max_per_day || 1}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff"></label>
          <label style="font-size:12px;color:#aaa">Score ≥ <input id="pe-threshold" type="number" min="0" max="100" value="${p.score_threshold || 75}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff"></label>
        </div>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:6px">
          <input id="pe-autoapprove" type="checkbox" ${p.auto_approve ? 'checked' : ''}> Auto-approve when score ≥ threshold
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:10px">
          <input id="pe-active" type="checkbox" ${p.active ? 'checked' : ''}> Active
        </label>
        <div style="display:flex;justify-content:flex-end;gap:6px">
          <button class="btn-primary" onclick="SocialStudio.modules.presets.save(${id || 'null'})">Save</button>
        </div>
      </div>
    `;
    // Pre-select dropdowns
    setTimeout(() => {
      document.getElementById('pe-tone').value = p.tone || 'pro';
      document.getElementById('pe-length').value = p.length || 'medium';
      document.getElementById('pe-cadence').value = p.cadence || 'off';
    }, 0);
  }

  async function save(id) {
    const body = {
      name: document.getElementById('pe-name').value,
      description: document.getElementById('pe-desc').value,
      tone: document.getElementById('pe-tone').value,
      length: document.getElementById('pe-length').value,
      cadence: document.getElementById('pe-cadence').value,
      max_per_day: parseInt(document.getElementById('pe-max').value, 10) || 1,
      score_threshold: parseInt(document.getElementById('pe-threshold').value, 10) || 75,
      auto_approve: document.getElementById('pe-autoapprove').checked ? 1 : 0,
      active: document.getElementById('pe-active').checked ? 1 : 0,
    };
    const r = id
      ? await fetch('/api/ahb/social/presets/' + id, {
          method: 'PUT', headers: {'Content-Type':'application/json'},
          body: JSON.stringify(body),
        })
      : await fetch('/api/ahb/social/presets', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify(body),
        });
    if (!r.ok) { alert('Save failed'); return; }
    document.getElementById('socialPresetEditor').style.display = 'none';
    load();
  }

  async function runOnce(id) {
    const r = await fetch('/api/ahb/social/presets/' + id + '/run', { method: 'POST' });
    const j = await r.json();
    alert('Test run: ' + (j.post_id ? '#' + j.post_id : j.error || 'see logs'));
  }

  async function toggle(id, currentActive) {
    await fetch('/api/ahb/social/presets/' + id, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ active: currentActive ? 0 : 1 }),
    });
    load();
  }

  return { render: load, edit, save, runOnce, toggle, installSeeds };
})();

SocialStudio.modules.settings = (function(){
  async function open() {
    const s = await fetch('/api/ahb/social/settings').then(r => r.json());
    const m = document.getElementById('socialSettings');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:520px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">⚙️ Social Studio Settings</div>
          <button onclick="document.getElementById('socialSettings').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">×</button>
        </div>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Default copy model
          <input id="ss-set-model" value="${s.default_copy_model || ''}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Daily post cap
          <input id="ss-set-cap" type="number" min="0" value="${s.daily_post_cap || 4}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Cool-down days (don't reuse media within N days)
          <input id="ss-set-cooldown" type="number" min="0" value="${s.cool_down_days || 14}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">
          <input id="ss-set-cloud" type="checkbox" ${s.cloud_models_enabled ? 'checked' : ''}> Enable cloud models (off by default; HARD RULE)
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">
          <input id="ss-set-autopilot" type="checkbox" ${s.autopilot_master ? 'checked' : ''}> Auto-Pilot master ON
        </label>
        <div style="display:flex;justify-content:flex-end;gap:6px;margin-top:10px">
          <button class="btn-primary" onclick="SocialStudio.modules.settings.save()">Save</button>
        </div>
      </div>
    `;
  }
  async function save() {
    const body = {
      default_copy_model: document.getElementById('ss-set-model').value,
      daily_post_cap: parseInt(document.getElementById('ss-set-cap').value, 10) || 4,
      cool_down_days: parseInt(document.getElementById('ss-set-cooldown').value, 10) || 14,
      cloud_models_enabled: document.getElementById('ss-set-cloud').checked,
      autopilot_master: document.getElementById('ss-set-autopilot').checked,
    };
    await fetch('/api/ahb/social/settings', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    document.getElementById('socialSettings').style.display = 'none';
  }
  return { open, save };
})();
```

- [ ] **Step 3: Add settings GET/PUT routes to `social_studio.py`**

```python
@social_bp.route("/api/ahb/social/settings", methods=["GET"])
def social_settings_get():
    return jsonify(_settings.load_settings())


@social_bp.route("/api/ahb/social/settings", methods=["PUT"])
def social_settings_put():
    data = request.get_json(silent=True) or {}
    s = _settings.load_settings()
    s.update({k: v for k, v in data.items() if k in s})
    _settings.save_settings(s)
    return jsonify({"ok": True, "settings": s})


@social_bp.route("/api/ahb/social/brand-kit", methods=["GET"])
def social_brand_get():
    return jsonify(_settings.load_brand_kit())


@social_bp.route("/api/ahb/social/brand-kit", methods=["PUT"])
def social_brand_put():
    data = request.get_json(silent=True) or {}
    b = _settings.load_brand_kit()
    b.update({k: v for k, v in data.items() if k in b})
    _settings.save_brand_kit(b)
    return jsonify({"ok": True, "brand_kit": b})
```

- [ ] **Step 4: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Presets → "Install seed presets" → see 8 rows. Click ✏️ on one → modal opens → change cadence → Save → list refreshes. Click ⚙️ → Settings modal opens → toggle Auto-Pilot master → Save.

- [ ] **Step 5: Commit**

```
git add dashboard/social_studio.py dashboard/templates/ahb123.html
git commit -m "social: Presets UI + seed installer + Settings drawer

8 seed presets one click away. Editor modal covers tone/length/cadence
/max-per-day/score-threshold/auto-approve/active. Settings drawer
exposes model picker, daily cap, cool-down window, cloud-models-enabled
toggle (HARD RULE: off by default), Auto-Pilot master kill switch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Brand Kit modal + sq_bundle bootstrap

**Files:**
- Modify: `dashboard/social_settings.py` — add `bootstrap_brand_from_sq_bundle()`
- Modify: `dashboard/social_studio.py` — call bootstrap at first read
- Modify: `dashboard/templates/ahb123.html` — Brand Kit modal in Settings drawer

- [ ] **Step 1: Bootstrap function with test**

Append to `tests/test_social_settings.py`:

```python
def test_bootstrap_brand_reads_sq_bundle(tmp_settings, tmp_path):
    import social_settings
    # Simulate sq_bundle index page with HIC# and year
    sq_dir = tmp_path / "sq_bundle"
    sq_dir.mkdir()
    (sq_dir / "index.html").write_text(
        '<html><body>HIC# 1234567-DCA · Est. 2014 · All Home Building Co</body></html>'
    )
    b = social_settings.bootstrap_brand_from_sq_bundle(str(sq_dir))
    assert b["hic_number"] == "1234567-DCA"
    assert b["founded_year"] == "2014"
```

Append to `dashboard/social_settings.py`:

```python
import re


def bootstrap_brand_from_sq_bundle(sq_dir: str) -> Dict[str, Any]:
    """Read sq_bundle/index.html (or any .html) for HIC# + founding year.
    Updates the brand kit on disk and returns it. Idempotent."""
    b = load_brand_kit()
    if not os.path.isdir(sq_dir):
        return b
    text = ""
    for fn in os.listdir(sq_dir):
        if fn.endswith(".html"):
            try:
                with open(os.path.join(sq_dir, fn)) as f:
                    text += "\n" + f.read()
            except Exception:
                continue
    m_hic = re.search(r"HIC#?\s*([0-9A-Z-]+)", text, re.IGNORECASE)
    m_year = re.search(r"(?:Est\.?|Founded|since)\s*(20\d{2}|19\d{2})", text, re.IGNORECASE)
    if m_hic and not b.get("hic_number"):
        b["hic_number"] = m_hic.group(1)
    if m_year and not b.get("founded_year"):
        b["founded_year"] = m_year.group(1)
    save_brand_kit(b)
    return b
```

- [ ] **Step 2: Wire bootstrap at startup**

In `dashboard/social_studio.py`, after the blueprint definition:

```python
# Best-effort brand-kit bootstrap (idempotent — does nothing on re-runs once filled)
try:
    _sq = os.environ.get(
        "BAZA_SQ_BUNDLE",
        "/home/switchhacker/baza-empire/agent-framework-v3/proj-ahb123/sq_bundle",
    )
    if os.path.isdir(_sq):
        _settings.bootstrap_brand_from_sq_bundle(_sq)
except Exception as _e:
    print(f"[social] brand bootstrap skipped: {_e}", flush=True)
```

- [ ] **Step 3: Brand Kit modal**

Append inside the same `<script>` block:

```javascript
SocialStudio.modules.brandkit = (function(){
  async function open() {
    const b = await fetch('/api/ahb/social/brand-kit').then(r => r.json());
    const m = document.getElementById('socialBrandKit');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:520px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">🎨 Brand Kit</div>
          <button onclick="document.getElementById('socialBrandKit').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">×</button>
        </div>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Primary color
          <input id="bk-primary" value="${b.primary_color || ''}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Secondary color
          <input id="bk-secondary" value="${b.secondary_color || ''}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Hashtag floor (space-separated)
          <input id="bk-floor" value="${(b.hashtag_floor||[]).join(' ')}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">First-comment floor
          <textarea id="bk-comment" style="width:100%;height:60px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">${b.first_comment_floor || ''}</textarea>
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">HIC #
          <input id="bk-hic" value="${b.hic_number || ''}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <label style="display:block;font-size:12px;color:#aaa;margin-bottom:8px">Founded year
          <input id="bk-year" value="${b.founded_year || ''}" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
        </label>
        <div style="display:flex;justify-content:flex-end;gap:6px;margin-top:10px">
          <button class="btn-primary" onclick="SocialStudio.modules.brandkit.save()">Save</button>
        </div>
      </div>
    `;
  }
  async function save() {
    const body = {
      primary_color: document.getElementById('bk-primary').value,
      secondary_color: document.getElementById('bk-secondary').value,
      hashtag_floor: document.getElementById('bk-floor').value.split(/\s+/).filter(Boolean),
      first_comment_floor: document.getElementById('bk-comment').value,
      hic_number: document.getElementById('bk-hic').value,
      founded_year: document.getElementById('bk-year').value,
    };
    await fetch('/api/ahb/social/brand-kit', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    document.getElementById('socialBrandKit').style.display = 'none';
  }
  return { open, save };
})();
```

In the Settings drawer HTML (Task 10 Step 2), add a button "Edit Brand Kit" that calls `SocialStudio.modules.brandkit.open()`.

- [ ] **Step 4: Run tests + restart + smoke**

```
pytest tests/test_social_settings.py -v
sudo systemctl restart baza-dashboard
```

Open Settings → click Edit Brand Kit → modal populated (HIC# auto-filled if `sq_bundle/index.html` had it).

- [ ] **Step 5: Commit**

```
git add dashboard/social_settings.py dashboard/social_studio.py dashboard/templates/ahb123.html tests/test_social_settings.py
git commit -m "social: Brand Kit modal + sq_bundle bootstrap

Reads HIC# and founding year out of proj-ahb123/sq_bundle/*.html at
boot when the brand kit is still empty. Idempotent — once filled, the
bootstrap is a no-op so user edits aren't clobbered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Scheduler view + Auto-Pilot telemetry

**Files:**
- Modify: `dashboard/templates/ahb123.html` — Scheduler + Auto-Pilot modules

- [ ] **Step 1: Append modules to `<script>`**

```javascript
SocialStudio.modules.scheduler = (function(){
  const root = () => document.getElementById('ss-sub-scheduler');
  async function load() {
    const items = (await fetch('/api/ahb/social/posts?status=scheduled')
      .then(r => r.json())).items || [];
    // Group by scheduled_at date
    const days = {};
    items.forEach(p => {
      const d = (p.scheduled_at || '').slice(0, 10);
      (days[d] = days[d] || []).push(p);
    });
    const sortedKeys = Object.keys(days).sort();
    const html = sortedKeys.map(d => `
      <div class="ss-card" style="margin-bottom:8px">
        <div style="font-weight:700;color:#10b981;margin-bottom:6px">${d || 'unscheduled'}</div>
        ${days[d].map(p => `
          <div style="display:flex;gap:8px;padding:4px 0;border-top:1px solid #1a1a2e">
            <div style="color:#aaa;font-size:12px">${p.platform}</div>
            <div style="flex:1;color:#ddd;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(p.caption||'').slice(0,140)}</div>
          </div>
        `).join('')}
      </div>
    `).join('');
    root().innerHTML = html || '<div style="color:#444;padding:40px;text-align:center">No scheduled posts.</div>';
  }
  return { render: load };
})();

SocialStudio.modules.autopilot = (function(){
  const root = () => document.getElementById('ss-sub-autopilot');
  async function load() {
    const [status, presets] = await Promise.all([
      fetch('/api/ahb/social/autopilot/status').then(r => r.json()),
      fetch('/api/ahb/social/presets').then(r => r.json()),
    ]);
    const presetRows = (presets.items || []).filter(p => p.active && p.cadence !== 'off').map(p => `
      <tr><td>${p.name}</td><td>${p.cadence}</td><td>${p.last_run_at || '—'}</td><td>${p.next_run_at || '—'}</td></tr>
    `).join('');
    root().innerHTML = `
      <div style="display:flex;gap:12px;margin-bottom:12px">
        <div class="ss-card" style="flex:1">
          <div style="color:#aaa;font-size:11px;text-transform:uppercase">Master switch</div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:6px">
            <label class="switch">
              <input id="ap-master" type="checkbox" ${status.master ? 'checked' : ''} onchange="SocialStudio.modules.autopilot.toggleMaster(this.checked)">
              <span style="margin-left:8px">${status.master ? 'ON' : 'OFF'}</span>
            </label>
          </div>
        </div>
        <div class="ss-card" style="flex:1">
          <div style="color:#aaa;font-size:11px;text-transform:uppercase">Drafts today</div>
          <div style="font-size:24px;font-weight:800;color:#10b981">${status.drafts_today || 0}</div>
        </div>
        <div class="ss-card" style="flex:1">
          <div style="color:#aaa;font-size:11px;text-transform:uppercase">Daily cap</div>
          <div style="font-size:24px;font-weight:800">${status.daily_cap || '—'}</div>
        </div>
      </div>
      <div class="ss-card">
        <div style="font-weight:700;margin-bottom:6px">Active scheduled presets</div>
        <table class="data-table" style="width:100%">
          <thead><tr><th>Name</th><th>Cadence</th><th>Last run</th><th>Next run</th></tr></thead>
          <tbody>${presetRows || '<tr><td colspan="4" style="color:#666;text-align:center">No active scheduled presets.</td></tr>'}</tbody>
        </table>
        <div style="margin-top:8px"><button class="btn-secondary" onclick="SocialStudio.modules.autopilot.runTickNow()">Run tick now</button></div>
      </div>
    `;
  }
  async function toggleMaster(on) {
    await fetch('/api/ahb/social/autopilot/toggle', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ on: !!on }),
    });
    load();
  }
  async function runTickNow() {
    const r = await fetch('/api/ahb/social/autopilot/tick', { method: 'POST' });
    const j = await r.json();
    alert('Tick: ' + JSON.stringify(j));
    load();
  }
  return { render: load, toggleMaster, runTickNow };
})();
```

- [ ] **Step 2: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Click **Scheduler** sub → shows "No scheduled posts" (expected — we haven't added scheduled posts yet). Click **Auto-Pilot** sub → shows master switch, drafts-today, daily cap, and active-presets table. Master switch toggle persists across reload.

- [ ] **Step 3: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social: Scheduler view + Auto-Pilot telemetry UI

Scheduler groups status=scheduled posts by day. Auto-Pilot tab shows
master kill switch, drafts-today counter, daily cap, and the table of
active scheduled presets. \"Run tick now\" hits the autopilot endpoint
for manual exercising.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```


---

## Task 13: Auto-Pilot tick logic + Telegram drop + preset run endpoint

**Files:**
- Modify: `dashboard/social_studio.py` — autopilot tick + `/posts/<id>/telegram` + `/presets/<id>/run`
- Create: `baza-social-autopilot.service`
- Create: `baza-social-autopilot.timer`
- Test: `tests/test_social_autopilot.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_social_autopilot.py`:

```python
import os
import sqlite3
import sys
import tempfile
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="ap_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE image_captions (
        id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
        caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
    )""")
    con.execute("INSERT INTO image_captions VALUES (1,42,'a.jpg','wall','work','ok',?)",
                (datetime.utcnow().isoformat(),))
    con.commit(); con.close()
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    return app.test_client(), social_studio


def test_autopilot_tick_master_off_is_noop(client):
    c, ss = client
    # Settings default has autopilot_master = False
    r = c.post("/api/ahb/social/autopilot/tick")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ran"] == 0


def test_autopilot_tick_with_master_on_and_due_preset(client, monkeypatch):
    c, ss = client
    # Turn master on
    c.put("/api/ahb/social/settings", json={"autopilot_master": True})
    # Insert a due preset
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    con.execute("""INSERT INTO ahb_social_presets
        (name, cadence, active, max_per_day, next_run_at, platform_targets, source_filter)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("T", "daily", 1, 5, (datetime.utcnow() - timedelta(hours=1)).isoformat(),
         json.dumps(["ig_feed_square"]), json.dumps({"project_ids": [42]})))
    con.commit(); con.close()
    # Stub the AI calls + render so we don't actually invoke ffmpeg/LLM
    monkeypatch.setattr(ss, "_call_ollama_chat", lambda *a, **kw: "test caption")
    monkeypatch.setattr(ss, "_resolve_media_paths", lambda ids: [])
    r = c.post("/api/ahb/social/autopilot/tick")
    j = r.get_json()
    assert j["ran"] >= 1
    posts = c.get("/api/ahb/social/posts").get_json()["items"]
    assert len(posts) >= 1
    assert posts[0]["status"] == "pending_review"


def test_autopilot_toggle_persists(client):
    c, ss = client
    c.post("/api/ahb/social/autopilot/toggle", json={"on": True})
    r = c.get("/api/ahb/social/autopilot/status").get_json()
    assert r["master"] is True
```

Run: FAIL.

- [ ] **Step 2: Implement autopilot routes + Telegram drop + preset run**

Append to `dashboard/social_studio.py`:

```python
import time
from datetime import timedelta


def _today_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _count_posts_today(con, preset_id=None) -> int:
    if preset_id is not None:
        return con.execute(
            "SELECT COUNT(*) FROM ahb_social_posts WHERE date(created_at)=? AND preset_id=?",
            (_today_iso(), preset_id),
        ).fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM ahb_social_posts WHERE date(created_at)=?",
        (_today_iso(),),
    ).fetchone()[0]


def _next_run_from_cadence(cadence: str, n_per_week: int) -> str:
    now = datetime.utcnow()
    if cadence == "daily":
        return (now + timedelta(days=1)).isoformat(timespec="seconds")
    if cadence == "n_per_week" and n_per_week > 0:
        gap_hours = max(1, int(7 * 24 / n_per_week))
        return (now + timedelta(hours=gap_hours)).isoformat(timespec="seconds")
    if cadence == "on_trigger":
        return ""
    return ""


def _pick_sources_for_preset(con, source_filter: dict, cool_down_days: int) -> list:
    """Return a list of image_captions.id rows matching the preset's filter,
    excluding any used by a post within cool_down_days."""
    args = []
    sql = "SELECT id FROM image_captions WHERE 1=1"
    pids = source_filter.get("project_ids") or []
    if pids:
        placeholders = ",".join("?" * len(pids))
        sql += f" AND project_id IN ({placeholders})"
        args.extend(pids)
    sql += " ORDER BY indexed_at DESC LIMIT 12"
    candidates = [r[0] for r in con.execute(sql, args).fetchall()]
    # Exclude recent uses
    if candidates:
        used_rows = con.execute(
            f"SELECT source_media_ids FROM ahb_social_posts "
            f"WHERE created_at >= datetime('now', ?)",
            (f"-{int(cool_down_days)} days",),
        ).fetchall()
        used = set()
        for r in used_rows:
            try:
                used.update(json.loads(r[0] or "[]"))
            except Exception:
                pass
        candidates = [c for c in candidates if c not in used]
    return candidates


@social_bp.route("/api/ahb/social/autopilot/status", methods=["GET"])
def autopilot_status():
    s = _settings.load_settings()
    con = _conn()
    drafts_today = _count_posts_today(con)
    con.close()
    return jsonify({
        "master": bool(s.get("autopilot_master")),
        "drafts_today": drafts_today,
        "daily_cap": s.get("daily_post_cap"),
    })


@social_bp.route("/api/ahb/social/autopilot/toggle", methods=["POST"])
def autopilot_toggle():
    on = bool((request.get_json(silent=True) or {}).get("on"))
    s = _settings.load_settings()
    s["autopilot_master"] = on
    _settings.save_settings(s)
    return jsonify({"ok": True, "master": on})


def _generate_one_post_from_preset(preset: dict, source_ids: list) -> Optional[int]:
    """Run the same chain a manual user would: caption → hashtags → score →
    insert with status=pending_review (or approved if auto_approve and score)."""
    platform = (preset.get("platform_targets") or ["ig_feed_square"])[0]
    sys_prompt = _settings.load_prompt("caption_system")
    summary = _sources_summary(source_ids)
    user = (
        f"Platform: {platform}\nTone: {preset.get('tone','pro')}\n"
        f"Length: {preset.get('length','medium')}\nStyle: {preset.get('style','trade')}\n"
        f"Source media:\n{summary}\n"
    )
    model = _pick_copy_model()
    caption = _call_ollama_chat(model, sys_prompt, user).strip()
    # Hashtags
    brand = _settings.load_brand_kit()
    raw = _call_ollama_chat(
        model, _settings.load_prompt("hashtag_system"),
        f"Caption: {caption}\nPlatform: {platform}\nFloor: {brand.get('hashtag_floor') or []}\n",
        temperature=0.4,
    )
    tags = _extract_json_array(raw)
    for f in (brand.get("hashtag_floor") or []):
        if f not in tags:
            tags.append(f)
    # Score
    raw_s = _call_ollama_chat(
        model, _settings.load_prompt("score_system"),
        f"Platform: {platform}\nCaption:\n{caption}\nHashtags: {' '.join(tags)}\n",
        temperature=0.2,
    )
    score_obj = _extract_json_obj(raw_s)
    score = int(score_obj.get("score") or 0)
    status = "pending_review"
    if preset.get("auto_approve") and score >= int(preset.get("score_threshold") or 75):
        status = "approved"
    con = _conn()
    cur = con.execute(
        """INSERT INTO ahb_social_posts
        (preset_id, source_media_ids, platform, variant, caption, hashtags,
         first_comment, status, score, ai_meta)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (preset["id"], json.dumps(source_ids), platform, platform,
         caption, " ".join(tags),
         brand.get("first_comment_floor") or "",
         status, score,
         json.dumps({"model": model, "notes": score_obj.get("notes", "")})),
    )
    con.commit()
    pid = cur.lastrowid
    con.close()
    return pid


@social_bp.route("/api/ahb/social/autopilot/tick", methods=["POST"])
def autopilot_tick():
    s = _settings.load_settings()
    if not s.get("autopilot_master"):
        return jsonify({"ran": 0, "reason": "master off"})
    daily_cap = int(s.get("daily_post_cap") or 4)
    cool_days = int(s.get("cool_down_days") or 14)
    con = _conn()
    drafts_today = _count_posts_today(con)
    if drafts_today >= daily_cap:
        con.close()
        return jsonify({"ran": 0, "reason": "daily cap"})
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    due = con.execute(
        "SELECT * FROM ahb_social_presets WHERE active=1 AND cadence != 'off' "
        "AND (next_run_at IS NULL OR next_run_at <= ?) ORDER BY next_run_at",
        (now_iso,),
    ).fetchall()
    ran = []
    for r in due:
        if drafts_today >= daily_cap:
            break
        preset = _row_to_preset(r)
        if _count_posts_today(con, preset_id=preset["id"]) >= int(preset.get("max_per_day") or 1):
            continue
        try:
            source_filter = preset.get("source_filter") or {}
            if isinstance(source_filter, str):
                source_filter = json.loads(source_filter or "{}")
        except Exception:
            source_filter = {}
        sources = _pick_sources_for_preset(con, source_filter, cool_days)
        if not sources:
            continue
        try:
            pid = _generate_one_post_from_preset(preset, sources)
            ran.append(pid)
            drafts_today += 1
            con.execute(
                "UPDATE ahb_social_presets SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?",
                (now_iso,
                 _next_run_from_cadence(preset["cadence"], int(preset.get("n_per_week") or 0)),
                 now_iso, preset["id"]),
            )
            con.commit()
        except Exception as e:
            print(f"[autopilot] preset {preset['id']} failed: {e}", flush=True)
    con.close()
    return jsonify({"ran": len(ran), "post_ids": ran})


@social_bp.route("/api/ahb/social/presets/<int:pid>/run", methods=["POST"])
def social_preset_run(pid: int):
    con = _conn()
    r = con.execute("SELECT * FROM ahb_social_presets WHERE id=?", (pid,)).fetchone()
    if not r:
        con.close()
        return jsonify({"error": "not found"}), 404
    preset = _row_to_preset(r)
    cool_days = int(_settings.load_settings().get("cool_down_days") or 14)
    try:
        source_filter = preset.get("source_filter") or {}
        if isinstance(source_filter, str):
            source_filter = json.loads(source_filter or "{}")
    except Exception:
        source_filter = {}
    sources = _pick_sources_for_preset(con, source_filter, cool_days)
    con.close()
    if not sources:
        return jsonify({"error": "no eligible sources"}), 400
    new_pid = _generate_one_post_from_preset(preset, sources)
    return jsonify({"post_id": new_pid})


@social_bp.route("/api/ahb/social/posts/<int:pid>/telegram", methods=["POST"])
def social_post_telegram(pid: int):
    """Drop the bundle (or just the caption + cover) to Serge's Telegram via
    the existing Specter bridge `/notify` endpoint."""
    con = _conn()
    r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    post = _row_to_post(r)
    payload = {
        "kind": "social_draft",
        "post_id": pid,
        "platform": post["platform"],
        "caption": post.get("caption") or "",
        "hashtags": post.get("hashtags") or "",
        "cover_path": post.get("cover_path"),
        "asset_path": post.get("asset_path"),
        "score": post.get("score"),
        "status": post.get("status"),
    }
    bridge = os.environ.get("BAZA_SPECTER_BRIDGE", "http://127.0.0.1:8765")
    try:
        req = urllib.request.Request(
            f"{bridge}/notify", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            ok = resp.status == 200
    except Exception as e:
        return jsonify({"error": f"bridge unavailable: {e}"}), 502
    return jsonify({"ok": ok})
```

- [ ] **Step 3: Add systemd user units**

`baza-social-autopilot.service` (repo root):

```ini
[Unit]
Description=Baza Social Auto-Pilot tick (hourly)
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/curl -fsS -X POST http://127.0.0.1:8888/api/ahb/social/autopilot/tick
StandardOutput=journal
StandardError=journal
```

`baza-social-autopilot.timer` (repo root):

```ini
[Unit]
Description=Hourly tick for Baza Social Auto-Pilot

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

Install instructions (in commit message; user runs these once):

```
mkdir -p ~/.config/systemd/user/
cp baza-social-autopilot.service baza-social-autopilot.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now baza-social-autopilot.timer
systemctl --user list-timers | grep social-autopilot
```

- [ ] **Step 4: Run tests + restart**

```
pytest tests/test_social_autopilot.py -v
sudo systemctl restart baza-dashboard
```

Expected: 3 passed.

- [ ] **Step 5: Manual smoke**

1. Enable Auto-Pilot master (Settings drawer)
2. Create a preset with cadence=daily and a project filter that matches existing media
3. Auto-Pilot tab → "Run tick now" → see Drafts today increment
4. Library → see new row with status=pending_review
5. Click 📲 on the row → confirm Specter bridge call (200) — or 502 if bridge isn't running, which is fine for first smoke

- [ ] **Step 6: Commit**

```
git add dashboard/social_studio.py baza-social-autopilot.service baza-social-autopilot.timer tests/test_social_autopilot.py
git commit -m "social: Auto-Pilot tick + preset run + Telegram drop + systemd units

Hourly cron via baza-social-autopilot.timer walks due active presets,
respects per-preset max_per_day + global daily_post_cap + master kill
switch + cool_down_days media reuse window. Score >= score_threshold +
auto_approve flips status to approved; otherwise pending_review.
/posts/<id>/telegram POSTs to the existing Specter bridge /notify.

Install once: cp baza-social-autopilot.{service,timer} ~/.config/systemd/user/
             && systemctl --user enable --now baza-social-autopilot.timer

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: End-to-end smoke + verification + session-log entry

This task does NOT add functional code. It verifies and documents.

**Files touched (verification only):**
- Read: `dashboard/social_studio.py`, `dashboard/social_render.py`, `dashboard/templates/ahb123.html`
- Append: `~/Desktop/baza-session-log.md` (existing session continuity log)

- [ ] **Step 1: Full pytest run**

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pytest tests/test_social_*.py -v
```

Expected: all tests in `test_social_db.py`, `test_social_settings.py`, `test_social_blueprint.py`, `test_social_render.py`, `test_social_autopilot.py` pass.

- [ ] **Step 2: Run the spec's §19 acceptance checklist**

For each of the 15 items in `docs/superpowers/specs/2026-05-22-ahb123-social-media-design.md` §19 ("Acceptance test plan"), execute and check ✅. If any item fails, file a new task and pause this one until fixed.

Specifically these are the highest-signal checks — do them in order, and bail to a bug-fix task if anything trips:

1. Restart dashboard, navigate to `/ahb123`, confirm 📣 Social tab present
2. Pick 4 photos + 1 video from a real project, see them in source grid + first one in preview
3. Toggle the four default platform variants — preview aspect updates each time
4. Click ✨ Caption × 4 platforms; each completes in < 10s with distinct copy
5. Click # Tags × 4 platforms; brand-floor tags present in each
6. Click 🪝 Hooks; pick one; overlay text appears in preview
7. Click 🎯 Score; receive 0–100 + paragraph
8. Click ▶️ Render package; within 90s the artifacts dir contains the .mp4 + .jpg + manifest.json
9. Switch to Library; new row present in `draft` status
10. PATCH to `approved`; 📲 Send to phone; Telegram delivery
11. Create preset (cadence=daily, auto_approve=off); manually `/autopilot/tick`; new draft in `pending_review`; Telegram card arrives
12. Flip Auto-Pilot master OFF; `/autopilot/tick` → `ran: 0`
13. `sudo systemctl restart baza-dashboard`; settings, presets, posts all persist
14. Open Brand Kit modal from any sub-sub-tab (e.g., from Auto-Pilot tab) — modal opens (proves body-level mounting)
15. SD off path: click ➕ AI image → graceful "SD offline" banner; ▶️ Render package still succeeds for non-SD sources

- [ ] **Step 3: Append session log**

Append to `~/Desktop/baza-session-log.md`:

```
### <YYYY-MM-DD HH:MM> | Social Media Studio — shipped

Shipped the new 📣 Social sub-tab in ahb123:
- Composer + Library + Scheduler + Presets + Auto-Pilot
- 3 new tables (ahb_social_presets/posts/jobs)
- /api/ahb/social/* Blueprint at dashboard/social_studio.py
- Render pipeline at dashboard/social_render.py (ffmpeg, target dims per platform, smart-crop or blurred-bg fill, cover-frame extract)
- 8 seed presets, hourly autopilot timer (baza-social-autopilot.timer), Telegram drop via Specter bridge
- HARD RULES honored: body-level modals, local-first AI, dashboard restart after template edits
- Phase 2 deferred: direct TikTok / IG Graph API publishing
```

Get timestamp via:

```
date '+%Y-%m-%d %H:%M'
```

- [ ] **Step 4: Final commit**

```
git add docs/superpowers/specs/2026-05-22-ahb123-social-media-design.md docs/superpowers/plans/2026-05-22-ahb123-social-media-plan.md
git commit --allow-empty -m "social: smoke verified — Social Studio Phase 1 complete

15-item acceptance checklist from spec §19 passed. Auto-Pilot tick
verified with both master-off and master-on paths. Telegram bridge
deliveries confirmed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Plan self-review checklist (for the writer, completed inline)

**1. Spec coverage:**
- §3 Composer → Task 8
- §4 AI matrix → Task 5 (caption/hashtags/hooks/score/translate); cover-pick + voiceover deferred to Phase 1.5 (cover-pick is implicit in Task 6 via `extract_cover` at t=0.5s — vision-driven cover is a Phase 1.5 polish item, noted here so it isn't lost: TODO move into Task 6 when qwen3-vl per-frame eval is wanted)
- §5 Presets → Task 10
- §6 Auto-Pilot → Task 13
- §7 Bundle output → Task 9 (bundle endpoint) + Task 13 (telegram drop)
- §8 Data model → Task 1
- §9 API surface → Tasks 3, 4, 5, 6, 9, 10, 11, 13
- §10 Render pipeline → Task 6 (subtitle burn-in, music bed, voiceover mixing are Phase 1.5 polish — flagged below)
- §11 Frontend modules → Tasks 7, 8, 9, 10, 11, 12
- §12 Brand kit → Task 11
- §13 Settings → Task 10
- §14 Risks → addressed by tests (autopilot toggle off path), graceful 502 on bridge missing
- §15 Prompts → Task 2
- §16 Telegram → Task 13
- §17 Build order → mirrored as Tasks 1–14
- §18 Out of scope → not implemented (correct)
- §19 Acceptance tests → Task 14

**Coverage gaps explicitly carried forward as Phase 1.5 polish (NOT in this plan):**
- Vision-driven cover-pick (use qwen3-vl to evaluate N candidate frames). Currently picks frame at t=0.5s.
- Subtitle burn-in via whisper.cpp (currently no subtitles step).
- Music bed mixing with sidechain ducking (currently no music in render).
- Voiceover via piper (currently no voiceover in render).
- A/B caption variation button (composer has button slot left for future).
- Translate UI button (endpoint exists; composer doesn't expose it yet).

These are acceptable carve-outs because:
- Spec §1 success criteria require platform-correct render + captions; we ship that.
- §14 explicitly lists "gracefully skip until installed" for whisper / piper.
- The render pipeline is structured so these slot in cleanly (concat → filter_graph → encode); adding music/subs/voice = adding to the filter graph + audio mux.

If the user wants these in Phase 1 instead of 1.5, add a follow-up plan or extend Task 6.

**2. Placeholder scan:** Searched for "TBD", "TODO", "implement later", "fill in" — only "TODO" reference is the explicit Phase 1.5 marker in the self-review section above, intentional.

**3. Type consistency:**
- `ALLOWED_PLATFORMS` (Task 4) matches `DIMS` keys (Task 6).
- `ALLOWED_STATUSES` (Task 4) matches CSS `.ss-pill-<status>` classes (Task 9).
- `_call_ollama_chat` signature is consistent across Task 5, 6, 13.
- `_resolve_media_paths` defined Task 6, used Task 6, 13.
- `_row_to_preset` / `_row_to_post` defined and reused consistently.
- `_settings` module alias consistent across all tasks.

No issues found.

---

## Execution

**Plan complete and saved to `docs/superpowers/plans/2026-05-22-ahb123-social-media-plan.md`.**

This plan spans 14 tasks across DB → backend → render → UI → automation. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration on issues. Best for a 14-task plan where bugs in early tasks shouldn't bleed into later ones.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints. Faster if you can babysit the run.

