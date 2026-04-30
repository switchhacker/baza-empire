# Vision UI — Image Catalogue Engine v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the structured-attribute Vision UI catalogue engine v1 (reference retrieval) on Baza, plus a dashboard light/dark theme toggle, in 8 sequentially-shippable PRs.

**Architecture:** New `dashboard/vision/` Python package + new `dashboard/vision.db` SQLite database, bolted onto the existing private-image capture path. `image_indexer.py` (public corpus) untouched. New systemd timer for vision indexing. Specter's seeding work runs as standalone scripts fired by systemd timers (cleaner than retrofitting `main.py specter`).

**Tech Stack:** Python 3.11+, Flask, SQLite + FTS5, qwen3-vl on Ollama @ 11434, SD WebUI Forge @ 11435, InsightFace, systemd, pytest.

**Spec:** `docs/superpowers/specs/2026-04-30-vision-ui-image-catalogue-design.md` (commit `fe20aa0`)

**Project root (everywhere `<root>` appears):** `/home/switchhacker/baza-empire/agent-framework-v3`

---

## Conventions (read once)

- **All shell commands run from `<root>`** unless explicitly noted otherwise.
- **All `git commit` commands** end with the standard co-author trailer used elsewhere in this repo:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- **Commit prefixes:** `feat:`, `fix:`, `test:`, `chore:`, `docs:` matching existing repo style.
- **Test runner:** `venv/bin/python -m pytest` (added in Phase 0).
- **Python interpreter:** `venv/bin/python` (the existing project venv).
- **Don't run `image_indexer.py` while building this** — it's a long process; nothing in this plan touches it but be aware it runs every 30 min via its existing timer.
- **Privacy invariant:** every file written under `dashboard/artifacts/.vision-*/` is implicitly private (`.private*` directory rule from `private_inbound.py`). Never put vision artifacts outside that prefix.

---

## Phase 0 — Test scaffolding (5 minutes, prerequisite for everything else)

### Task 0.1: Add pytest to the project

**Files:**
- Modify: `<root>/requirements.txt`
- Create: `<root>/tests/__init__.py` (empty)
- Create: `<root>/tests/vision/__init__.py` (empty)
- Create: `<root>/tests/vision/conftest.py`
- Create: `<root>/pytest.ini`

- [ ] **Step 1: Append pytest to requirements**

Append these two lines to `requirements.txt` (preserve existing lines):

```
pytest>=8.0.0
pytest-flask>=1.3.0
```

- [ ] **Step 2: Install**

Run: `venv/bin/pip install pytest pytest-flask`
Expected: Successfully installed pytest pytest-flask.

- [ ] **Step 3: Create pytest.ini**

Create `<root>/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --strict-markers
markers =
    slow: marks tests that take >2s (deselect with '-m "not slow"')
    integration: marks tests requiring network/DB/services
```

- [ ] **Step 4: Create empty package files**

Create empty files:
- `<root>/tests/__init__.py`
- `<root>/tests/vision/__init__.py`

- [ ] **Step 5: Create vision test fixture**

Create `<root>/tests/vision/conftest.py`:

```python
"""Shared fixtures for vision tests."""
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def tmp_vision_db():
    """A throwaway vision.db on an isolated path. Schema is applied by importing
    dashboard.vision.engine.init_db (added in Phase 1). Tests that need only a
    bare connection can use this fixture before the schema work lands."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def fixture_image(tmp_path):
    """A 1x1 black JPEG on disk; small but valid for downscale + sha256 paths."""
    from PIL import Image
    p = tmp_path / "fixture.jpg"
    Image.new("RGB", (8, 8), color=(0, 0, 0)).save(p, "JPEG")
    return str(p)
```

- [ ] **Step 6: Sanity-check pytest runs**

Run: `venv/bin/python -m pytest --collect-only`
Expected: `collected 0 items` and exit code 5 (no tests yet — that's fine).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/vision/__init__.py tests/vision/conftest.py
git commit -m "$(cat <<'EOF'
chore: add pytest + vision test scaffolding

Prereq for the Vision UI implementation plan. Adds pytest, a tests/
package, and shared fixtures. No behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 — Theme toggle (PR 1)

Goal: ship a dashboard-wide light/dark toggle in the header. Persist via cookie + session. Apply to `index.html` for proof; other pages adopt it as touched.

### Task 1.1: Theme route + session/cookie persistence

**Files:**
- Modify: `<root>/dashboard/app.py` (add route, near other settings routes)
- Test: `<root>/tests/vision/test_theme_route.py`

- [ ] **Step 1: Write the failing test**

Create `<root>/tests/vision/test_theme_route.py`:

```python
"""Theme toggle route — sets session + cookie."""
import importlib
import sys


def _client():
    # Re-import dashboard.app fresh per test for isolated session config.
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    sys.path.insert(0, ".")
    mod = importlib.import_module("dashboard.app")
    mod.app.config["TESTING"] = True
    mod.app.config["SECRET_KEY"] = "test"
    return mod.app.test_client()


def test_theme_route_accepts_dark():
    c = _client()
    r = c.post("/settings/theme", json={"value": "dark"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "theme": "dark"}
    # Cookie set with theme=dark
    cookies = r.headers.getlist("Set-Cookie")
    assert any("theme=dark" in c for c in cookies), cookies


def test_theme_route_accepts_light():
    c = _client()
    r = c.post("/settings/theme", json={"value": "light"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "theme": "light"}


def test_theme_route_rejects_invalid():
    c = _client()
    r = c.post("/settings/theme", json={"value": "rainbow"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run the test, watch it fail**

Run: `venv/bin/python -m pytest tests/vision/test_theme_route.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 3: Add the route**

In `<root>/dashboard/app.py`, find an existing simple route (e.g. near the index route) and add this block immediately after it. Place it BEFORE any blueprint registration:

```python
# ── Theme toggle ─────────────────────────────────────────────────────────────
# Stores user theme in session + a 1-year cookie. Templates read session.theme
# (or the cookie via `data-theme="{{ request.cookies.get('theme','dark') }}"`).
@app.route('/settings/theme', methods=['POST'])
def settings_theme():
    body = request.get_json(silent=True) or {}
    val = (body.get('value') or '').strip().lower()
    if val not in ('dark', 'light'):
        return jsonify({'ok': False, 'error': 'theme must be dark or light'}), 400
    session['theme'] = val
    resp = jsonify({'ok': True, 'theme': val})
    # 1y cookie so it survives session expiry. No HttpOnly: theme.js reads it.
    resp.set_cookie('theme', val, max_age=60 * 60 * 24 * 365, samesite='Lax')
    return resp
```

- [ ] **Step 4: Verify the test passes**

Run: `venv/bin/python -m pytest tests/vision/test_theme_route.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py tests/vision/test_theme_route.py
git commit -m "$(cat <<'EOF'
feat: theme toggle route /settings/theme

Stores user theme in session + 1y cookie. Validates value to {dark,light}.
Foundation for the dashboard light/dark mode toggle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.2: Theme stylesheet (custom-property overlay)

**Files:**
- Create: `<root>/dashboard/static/css/theme.css`

- [ ] **Step 1: Create the stylesheet**

Create `<root>/dashboard/static/css/theme.css`:

```css
/* Theme overlay for the Baza Dashboard.
 * The dashboard's existing per-page <style> blocks are the dark default.
 * This file ONLY overrides colors when [data-theme="light"] is set on <html>.
 *
 * Applied page-by-page: link to this file in each template's <head> after
 * the inline <style> block, then add data-theme="..." to the <html> tag.
 */

/* Custom-property baseline (dark theme — matches existing inline styles). */
:root,
:root[data-theme="dark"] {
  --bg:        #07070f;
  --bg-elev:   #0d0d1e;
  --bg-card:   #0e0e1e;
  --fg:        #e0e0e0;
  --fg-dim:    #666;
  --fg-mute:   #aaa;
  --accent:    #e94560;
  --accent-2:  #7c3aed;
  --border:    #1a1a3a;
  --border-2:  #2a2a4a;
  --ok:        #00d084;
  --warn:      #f59e0b;
  --danger:    #ff6666;
}

:root[data-theme="light"] {
  --bg:        #f6f7fb;
  --bg-elev:   #ffffff;
  --bg-card:   #ffffff;
  --fg:        #111122;
  --fg-dim:    #888;
  --fg-mute:   #555;
  --accent:    #c52a45;
  --accent-2:  #5a23c4;
  --border:    #d8d8e8;
  --border-2:  #c0c0d4;
  --ok:        #009b62;
  --warn:      #b06700;
  --danger:    #b73030;
}

/* Light-mode overrides for the most common selectors used across templates.
 * This is intentionally narrow — it doesn't try to remap every shade of grey,
 * just the body, nav, cards, page header, and primary buttons so the toggle
 * is visibly working immediately. Each page picks up additional fixes as it
 * gets touched (see plan Task 1.4). */
:root[data-theme="light"] body {
  background: var(--bg) !important;
  color: var(--fg) !important;
}
:root[data-theme="light"] .nav,
:root[data-theme="light"] header,
:root[data-theme="light"] .topbar {
  background: var(--bg-elev) !important;
  border-color: var(--border) !important;
  color: var(--fg) !important;
}
:root[data-theme="light"] .nav-link {
  color: var(--fg-dim) !important;
}
:root[data-theme="light"] .nav-link:hover,
:root[data-theme="light"] .nav-link.active {
  color: var(--fg) !important;
}
:root[data-theme="light"] .btn-secondary {
  background: var(--bg-elev) !important;
  color: var(--fg-mute) !important;
  border-color: var(--border-2) !important;
}
:root[data-theme="light"] .card,
:root[data-theme="light"] .panel,
:root[data-theme="light"] .container > * {
  background: transparent;
  color: var(--fg);
}
:root[data-theme="light"] .page-title {
  color: var(--fg) !important;
}

/* Theme toggle button itself (rendered in headers via theme.js). */
.theme-toggle {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--fg-mute);
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}
.theme-toggle:hover {
  color: var(--fg);
  border-color: var(--accent);
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/css/theme.css
git commit -m "$(cat <<'EOF'
feat: theme.css custom-property overlay for dashboard light mode

Defines --bg/--fg/--accent/etc tokens. Dark = current values, light = inverted.
Narrow !important overrides for nav/body/buttons so the toggle is visibly
working from PR 1; deeper page-by-page application follows as templates are
touched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.3: Theme toggle script

**Files:**
- Create: `<root>/dashboard/static/js/theme.js`

- [ ] **Step 1: Create the script**

Create `<root>/dashboard/static/js/theme.js`:

```javascript
/* Theme toggle. Reads theme cookie at load, sets <html data-theme="...">,
 * renders a button into [data-theme-mount] (or the body's first <header>/.nav
 * if no mount is specified), and POSTs /settings/theme on click. */
(function () {
  function getCookie(name) {
    return document.cookie.split('; ').reduce(function (acc, c) {
      var parts = c.split('=');
      return parts[0] === name ? decodeURIComponent(parts.slice(1).join('=')) : acc;
    }, null);
  }
  function setCookie(name, value, days) {
    var max = days * 24 * 60 * 60;
    document.cookie = name + '=' + encodeURIComponent(value) + '; path=/; max-age=' + max + '; SameSite=Lax';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function postTheme(theme) {
    return fetch('/settings/theme', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: theme}),
      credentials: 'same-origin',
    });
  }

  function toggle() {
    var current = document.documentElement.getAttribute('data-theme') || 'dark';
    var next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setCookie('theme', next, 365);
    postTheme(next).catch(function () { /* silent — cookie still wins */ });
    var btn = document.querySelector('[data-theme-button]');
    if (btn) btn.textContent = next === 'dark' ? '☀' : '☾';
  }

  function mount() {
    var initial = getCookie('theme') || document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(initial);

    var host = document.querySelector('[data-theme-mount]');
    if (!host) {
      host = document.querySelector('header') || document.querySelector('.nav') || document.querySelector('.topbar');
    }
    if (!host) return;

    var btn = document.createElement('button');
    btn.className = 'theme-toggle';
    btn.setAttribute('data-theme-button', '');
    btn.title = 'Toggle theme';
    btn.textContent = initial === 'dark' ? '☀' : '☾';
    btn.addEventListener('click', toggle);
    host.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/theme.js
git commit -m "$(cat <<'EOF'
feat: theme.js — auto-mounting theme toggle

Reads theme cookie, sets <html data-theme="...">, mounts a sun/moon button
into [data-theme-mount] or the first nav/header on the page, posts to
/settings/theme. Optimistic local update so flicker is zero.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.4: Wire theme into `index.html`

**Files:**
- Modify: `<root>/dashboard/templates/index.html`

- [ ] **Step 1: Add `data-theme` to the `<html>` tag**

Find the line `<html lang="en">` near the top of `index.html`. Replace with:

```html
<html lang="en" data-theme="{{ session.get('theme') or request.cookies.get('theme', 'dark') }}">
```

- [ ] **Step 2: Link `theme.css` and `theme.js`**

Inside the `<head>` of `index.html`, AFTER the existing inline `<style>...</style>` block, add:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/theme.css') }}">
```

Just before `</body>` add:

```html
<script src="{{ url_for('static', filename='js/theme.js') }}"></script>
```

- [ ] **Step 3: Smoke-test in a browser**

Restart the dashboard service:

```bash
sudo systemctl restart baza-dashboard.service
```

Open the dashboard root URL. A sun (☀) button should appear in the nav. Click it: page should re-tint with the light-mode overlay; button becomes a moon (☾). Reload — preference persists. Check DevTools: `<html data-theme="light">` and `theme=light` cookie present.

If no button appears, check that `theme.js` is loading (Network tab) and that the page has either a `header`, `.nav`, or `.topbar` element for it to mount into. If your nav has none of those classes, add `data-theme-mount` to the element you want it in.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/index.html
git commit -m "$(cat <<'EOF'
feat: wire theme toggle into dashboard index page

index.html now reads session/cookie theme into <html data-theme>, links
theme.css overlay, mounts theme.js. Other pages adopt the same three-line
pattern as they get touched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 1.5: Wire theme into `private.html` (will become `vision.html` in Phase 5)

Apply the exact same three changes as Task 1.4 to `<root>/dashboard/templates/private.html`:

- [ ] **Step 1: Update `<html>` tag** with `data-theme="{{ session.get('theme') or request.cookies.get('theme', 'dark') }}"`.
- [ ] **Step 2: Add `theme.css` link in `<head>` after the inline `<style>` block.**
- [ ] **Step 3: Add `theme.js` script tag before `</body>`.**
- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/private.html
git commit -m "$(cat <<'EOF'
feat: wire theme toggle into private gallery page

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 1 done.** Theme toggle ships and works on `/` and `/datahub/private`. Other pages remain dark-only until touched.

---

## Phase 2 — Vision DB schema + backfill (PR 2)

Goal: create `vision.db`, schema, the `dashboard/vision/` package skeleton, and a one-shot script that walks `.private-inbound/` and creates `assets` rows in `status='pending'` for everything already on disk. No classification yet.

### Task 2.1: Vision package skeleton

**Files:**
- Create: `<root>/dashboard/vision/__init__.py`
- Create: `<root>/dashboard/vision/db.py`

- [ ] **Step 1: Create empty package init**

Create `<root>/dashboard/vision/__init__.py` with:

```python
"""Baza Vision — image catalogue engine.

Sub-modules:
  db          — SQLite connection + schema bootstrapping
  taxonomy    — virtual folder tree definitions
  classifier  — qwen3-vl structured-attribute extraction
  cropper     — InsightFace + qwen-bbox crop pipeline
  search      — FTS5 + attribute filter composer
  ingest      — observing new files into the catalogue
  seed_scan   — Specter mode 1 (gap detector)
  seed_fulfill — Specter mode 2 (worker: scrape + generate)
"""
```

- [ ] **Step 2: Create the DB module with schema**

Create `<root>/dashboard/vision/db.py`:

```python
"""SQLite connection + schema bootstrap for vision.db."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(DASHBOARD_DIR, "vision.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id            INTEGER PRIMARY KEY,
  abs_path      TEXT NOT NULL UNIQUE,
  source        TEXT NOT NULL,                       -- 'inbound'|'scraped'|'generated'|'crop'
  origin_agent  TEXT,
  origin_url    TEXT,
  parent_id     INTEGER REFERENCES assets(id),
  width         INTEGER,
  height        INTEGER,
  bytes         INTEGER,
  sha256        TEXT,
  mtime         REAL,
  created_at    REAL,
  classified_at REAL,
  status        TEXT NOT NULL DEFAULT 'pending',     -- 'pending'|'ok'|'failed'|'rejected'
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source);
CREATE INDEX IF NOT EXISTS idx_assets_sha    ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_parent ON assets(parent_id);

CREATE TABLE IF NOT EXISTS attributes (
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  source     TEXT NOT NULL DEFAULT 'qwen3-vl',
  PRIMARY KEY (asset_id, key)
);
CREATE INDEX IF NOT EXISTS idx_attrs_kv ON attributes(key, value);

CREATE TABLE IF NOT EXISTS captions (
  asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  caption  TEXT,
  tags     TEXT,
  model    TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  caption, tags, attrs_blob,
  content='', tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS crops (
  asset_id  INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  part      TEXT NOT NULL,
  bbox_x    INTEGER, bbox_y INTEGER,
  bbox_w    INTEGER, bbox_h INTEGER,
  detector  TEXT
);
CREATE INDEX IF NOT EXISTS idx_crops_part ON crops(part);

CREATE TABLE IF NOT EXISTS seed_demand (
  id            INTEGER PRIMARY KEY,
  taxonomy_path TEXT NOT NULL,
  needed        INTEGER NOT NULL DEFAULT 6,
  reason        TEXT,
  requested_at  REAL,
  fulfilled_at  REAL,
  fulfilled_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_seed_open ON seed_demand(fulfilled_at, requested_at);

CREATE TABLE IF NOT EXISTS gpu_lease (
  gpu         TEXT PRIMARY KEY,
  holder      TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at  REAL NOT NULL,
  purpose     TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
  id          INTEGER PRIMARY KEY,
  asset_id    INTEGER REFERENCES assets(id),
  step        TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  duration_ms INTEGER,
  detail      TEXT,
  ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON ingest_log(ts);
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection with foreign keys ON and a sensible busy timeout."""
    p = path or DEFAULT_DB_PATH
    con = sqlite3.connect(p, timeout=30, isolation_level=None)  # autocommit
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    con.row_factory = sqlite3.Row
    return con


def init_db(path: Optional[str] = None) -> sqlite3.Connection:
    """Create the schema if missing. Idempotent."""
    con = connect(path)
    con.executescript(SCHEMA)
    return con
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/vision/__init__.py dashboard/vision/db.py
git commit -m "$(cat <<'EOF'
feat: vision package + db.connect/init_db

Empty package skeleton + the SQLite schema (assets, attributes, captions,
assets_fts, crops, seed_demand, gpu_lease, ingest_log). FK on, WAL on,
idempotent init.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2.2: Test the schema initializes cleanly

**Files:**
- Create: `<root>/tests/vision/test_db.py`

- [ ] **Step 1: Write the test**

Create `<root>/tests/vision/test_db.py`:

```python
"""Schema bootstrap is idempotent and the expected tables exist."""
import sqlite3

from dashboard.vision.db import init_db


EXPECTED_TABLES = {
    "assets", "attributes", "captions", "crops",
    "seed_demand", "gpu_lease", "ingest_log",
    "assets_fts",
}


def _table_set(con):
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_init_creates_all_tables(tmp_vision_db):
    con = init_db(tmp_vision_db)
    have = _table_set(con)
    missing = EXPECTED_TABLES - have
    assert not missing, f"missing tables: {missing}"


def test_init_is_idempotent(tmp_vision_db):
    init_db(tmp_vision_db).close()
    init_db(tmp_vision_db).close()
    con = sqlite3.connect(tmp_vision_db)
    assert _table_set(con) >= EXPECTED_TABLES


def test_foreign_keys_on(tmp_vision_db):
    con = init_db(tmp_vision_db)
    [(fk,)] = con.execute("PRAGMA foreign_keys").fetchall()
    assert fk == 1
```

- [ ] **Step 2: Run the test**

Run: `venv/bin/python -m pytest tests/vision/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/vision/test_db.py
git commit -m "$(cat <<'EOF'
test: vision schema initializes idempotently with FK on

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2.3: Asset ingest helper (core insert)

**Files:**
- Create: `<root>/dashboard/vision/ingest.py`
- Create: `<root>/tests/vision/test_ingest.py`

- [ ] **Step 1: Write the failing test**

Create `<root>/tests/vision/test_ingest.py`:

```python
"""ingest.observe() — insert (or skip dup) an asset row by abs_path + sha256."""
import os

from dashboard.vision.db import init_db
from dashboard.vision.ingest import observe


def test_observe_inserts_pending_row(tmp_vision_db, fixture_image):
    init_db(tmp_vision_db)
    asset_id = observe(fixture_image, source="inbound", db_path=tmp_vision_db,
                       origin_agent="test")
    assert asset_id > 0


def test_observe_dedupes_by_abs_path(tmp_vision_db, fixture_image):
    init_db(tmp_vision_db)
    a = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    b = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    assert a == b


def test_observe_records_sha256_and_dimensions(tmp_vision_db, fixture_image):
    from dashboard.vision.db import connect
    init_db(tmp_vision_db)
    asset_id = observe(fixture_image, source="inbound", db_path=tmp_vision_db)
    row = connect(tmp_vision_db).execute(
        "SELECT sha256, width, height, status FROM assets WHERE id=?", (asset_id,),
    ).fetchone()
    assert row["sha256"] and len(row["sha256"]) == 64
    assert row["width"] == 8 and row["height"] == 8
    assert row["status"] == "pending"
```

- [ ] **Step 2: Watch it fail**

Run: `venv/bin/python -m pytest tests/vision/test_ingest.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Write the implementation**

Create `<root>/dashboard/vision/ingest.py`:

```python
"""Observe a file on disk into the vision catalogue as a pending asset row."""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from PIL import Image

from dashboard.vision.db import connect


def _sha256(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _dimensions(path: str) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def observe(
    abs_path: str,
    source: str,
    *,
    db_path: Optional[str] = None,
    origin_agent: Optional[str] = None,
    origin_url: Optional[str] = None,
    parent_id: Optional[int] = None,
) -> int:
    """Insert (or fetch existing) the asset row for abs_path. Returns id."""
    abs_path = os.path.abspath(abs_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(abs_path)
    if source not in ("inbound", "scraped", "generated", "crop"):
        raise ValueError(f"bad source: {source}")

    con = connect(db_path)
    try:
        existing = con.execute(
            "SELECT id FROM assets WHERE abs_path = ?", (abs_path,),
        ).fetchone()
        if existing:
            return existing["id"]

        st = os.stat(abs_path)
        w, h = _dimensions(abs_path)
        cur = con.execute(
            """INSERT INTO assets
                (abs_path, source, origin_agent, origin_url, parent_id,
                 width, height, bytes, sha256, mtime, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (abs_path, source, origin_agent, origin_url, parent_id,
             w, h, st.st_size, _sha256(abs_path), st.st_mtime, time.time()),
        )
        asset_id = cur.lastrowid
        con.execute(
            "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'ingest', 1, ?, ?)",
            (asset_id, time.time(), source),
        )
        return asset_id
    finally:
        con.close()
```

- [ ] **Step 4: Verify tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_ingest.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/ingest.py tests/vision/test_ingest.py
git commit -m "$(cat <<'EOF'
feat(vision): ingest.observe() inserts pending asset row

Computes sha256, captures dimensions via Pillow, dedupes by abs_path.
Logs every ingest in ingest_log. No classification yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2.4: Backfill script for existing private images

**Files:**
- Create: `<root>/dashboard/vision/migrate_existing.py`

- [ ] **Step 1: Write the migration script**

Create `<root>/dashboard/vision/migrate_existing.py`:

```python
#!/usr/bin/env python3
"""One-shot backfill: walk `dashboard/artifacts/.private-inbound/` and create
pending `assets` rows for everything already on disk. Idempotent — re-runs
skip rows already present.

Usage:
    venv/bin/python -m dashboard.vision.migrate_existing
    venv/bin/python -m dashboard.vision.migrate_existing --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

from dashboard.vision.db import init_db, DEFAULT_DB_PATH
from dashboard.vision.ingest import observe

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
PRIVATE_INBOUND_DIR = os.path.join(ARTIFACTS_DIR, ".private-inbound")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def _agent_from_path(path: str) -> str | None:
    rel = os.path.relpath(path, PRIVATE_INBOUND_DIR)
    parts = rel.split(os.sep)
    return parts[0] if parts else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    init_db(args.db)
    if not os.path.isdir(PRIVATE_INBOUND_DIR):
        print(f"[migrate] {PRIVATE_INBOUND_DIR} missing — nothing to do.")
        return 0

    seen = added = skipped = 0
    for root, _dirs, files in os.walk(PRIVATE_INBOUND_DIR):
        for fn in files:
            if fn.endswith(".meta"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in IMG_EXTS:
                continue
            path = os.path.join(root, fn)
            seen += 1
            if args.dry_run:
                print(f"[would-add] {path}")
                continue
            try:
                aid = observe(path, source="inbound", db_path=args.db,
                              origin_agent=_agent_from_path(path))
                if aid:
                    added += 1
            except Exception as e:
                skipped += 1
                print(f"[skip] {path}: {e}", file=sys.stderr)

    print(f"[migrate] seen={seen} added={added} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Dry-run on the live system to confirm targeting**

Run: `venv/bin/python -m dashboard.vision.migrate_existing --dry-run | head -20`
Expected: a few `[would-add] /path/to/.private-inbound/<agent>/<file>.jpg` lines (or `[migrate] /...private-inbound missing` if no telegram inbound exists yet — that's fine).

- [ ] **Step 3: Execute the backfill**

Run: `venv/bin/python -m dashboard.vision.migrate_existing`
Expected: `[migrate] seen=N added=N skipped=0` (or seen=0 if no inbound yet).

Verify: `sqlite3 dashboard/vision.db "SELECT COUNT(*) FROM assets WHERE status='pending';"`
Expected: count matches `added` from the prior run.

- [ ] **Step 4: Add `vision.db` and `.vision-*` to `.gitignore`**

Append to `<root>/.gitignore`:

```
# Vision UI runtime artifacts
dashboard/vision.db
dashboard/vision.db-shm
dashboard/vision.db-wal
dashboard/artifacts/.vision-generated/
dashboard/artifacts/.vision-scraped/
dashboard/artifacts/.vision-crops/
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/migrate_existing.py .gitignore
git commit -m "$(cat <<'EOF'
feat(vision): migrate_existing — backfill pending rows for private inbound

One-shot walk of dashboard/artifacts/.private-inbound/ that registers
every existing image as a pending asset. Idempotent, dry-run support.
Adds vision.db + .vision-* artifact subdirs to .gitignore.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 2 done.** `vision.db` exists, schema is in place, every existing private image has a pending row. No classification, no UI yet.

---

## Phase 3 — Classifier + indexer (PR 3)

Goal: add `vision_indexer.py` (mirrors `image_indexer.py`), the structured-attribute classifier (one qwen3-vl call → JSON), and a systemd timer. After this phase ships, the backlog from Phase 2 is consumed at ~5-30s/image, low-priority on the AMD GPU.

### Task 3.1: Controlled vocabulary

**Files:**
- Create: `<root>/dashboard/vision/vocab.py`
- Create: `<root>/tests/vision/test_vocab.py`

- [ ] **Step 1: Write the vocab tests**

Create `<root>/tests/vision/test_vocab.py`:

```python
from dashboard.vision.vocab import VOCAB, normalize, REQUIRED_KEYS


def test_required_keys_subset_of_vocab():
    for k in REQUIRED_KEYS:
        assert k in VOCAB, k


def test_normalize_lowercases_and_validates():
    assert normalize("gender", "Female") == "female"
    assert normalize("gender", "MALE") == "male"


def test_normalize_unknown_value_returns_unknown():
    # We never raise on an unexpected value — we coerce to "unknown" so a
    # chatty model doesn't crash the classifier loop.
    assert normalize("gender", "non-binary-ish") == "unknown"


def test_normalize_unknown_key_passthrough_ok():
    # Keys outside the vocab are passed through (e.g. classifier emitted
    # extra keys) — no crash, just lowercase trim.
    assert normalize("custom_key", "  Some Value  ") == "some value"


def test_parts_visible_normalized_to_csv_lowercase():
    assert normalize("parts_visible", ["Face", "Eyes", "Hands"]) == "face,eyes,hands"
    assert normalize("parts_visible", "face, eyes ,hands") == "face,eyes,hands"
```

- [ ] **Step 2: Watch tests fail**

Run: `venv/bin/python -m pytest tests/vision/test_vocab.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `vocab.py`**

Create `<root>/dashboard/vision/vocab.py`:

```python
"""Controlled vocabulary for image attributes.

The classifier prompt asks qwen3-vl to populate this exact key set with a
value from the given list. `normalize()` coerces the model's output into a
known value or "unknown" — never raises so a single bad image doesn't
break the indexer loop.
"""
from __future__ import annotations

from typing import Iterable

VOCAB: dict[str, set[str]] = {
    "image_type":     {"person", "object", "scene", "mixed", "text", "meme", "unknown"},
    "person_count":   {"0", "1", "2", "3+", "unknown"},
    "gender":         {"female", "male", "androgynous", "unknown"},
    "age_band":       {"child", "teen", "young-adult", "adult", "senior", "unknown"},
    "hair_color":     {"blonde", "brown", "black", "red", "gray", "dyed-other", "unknown"},
    "hair_style":     {"long", "short", "medium", "up", "bald", "covered", "unknown"},
    "build":          {"slim", "athletic", "average", "curvy", "heavy", "unknown"},
    "pose":           {"standing", "sitting", "lying", "crouching", "walking",
                       "dancing", "action", "unknown"},
    "viewpoint":      {"front", "back", "left-profile", "right-profile",
                       "three-quarter", "top", "unknown"},
    "mood":           {"neutral", "smiling", "serious", "surprised",
                       "pensive", "playful", "unknown"},
    "clothing_style": {"casual", "formal", "swimwear", "sportswear",
                       "lingerie", "costume", "none", "unknown"},
    "setting":        {"indoor", "outdoor-urban", "outdoor-nature",
                       "beach", "studio", "vehicle", "unknown"},
    "nsfw":           {"safe", "suggestive", "explicit", "unknown"},
}

# Body parts the cropper might extract; not constrained by VOCAB because we
# may add parts later (toes, ears) without breaking inference.
PART_VOCAB: set[str] = {
    "face", "eye", "eyes", "lips", "nose", "ear",
    "torso", "arm", "hand", "fingers",
    "leg", "thigh", "knee", "calf", "foot", "feet", "toes",
    "hair",
}

REQUIRED_KEYS: tuple[str, ...] = (
    "image_type", "person_count", "gender", "pose", "mood",
    "setting", "parts_visible", "nsfw",
)


def normalize(key: str, value) -> str:
    """Coerce a value to its canonical lowercase form. Unknown values for
    a known key collapse to 'unknown'. Unknown keys pass through trimmed."""
    if key == "parts_visible":
        if isinstance(value, (list, tuple)):
            items = [str(v).strip().lower() for v in value]
        else:
            items = [s.strip().lower() for s in str(value).split(",")]
        items = [s for s in items if s]
        return ",".join(items)

    s = "" if value is None else str(value).strip().lower()
    allowed = VOCAB.get(key)
    if allowed is None:
        return s  # unknown key — pass through
    if s in allowed:
        return s
    return "unknown"
```

- [ ] **Step 4: Verify tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_vocab.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/vocab.py tests/vision/test_vocab.py
git commit -m "$(cat <<'EOF'
feat(vision): controlled vocabulary + normalize()

Defines the 13 attribute keys + their allowed values, plus a normalize()
helper that coerces noisy classifier output to canonical lowercase or
"unknown". Never raises — single bad image must not break the loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.2: Classifier (qwen3-vl JSON parsing)

**Files:**
- Create: `<root>/dashboard/vision/classifier.py`
- Create: `<root>/tests/vision/test_classifier.py`

- [ ] **Step 1: Write the parser tests**

Create `<root>/tests/vision/test_classifier.py`:

```python
"""Classifier JSON parsing — robust to extra text, code fences, missing keys."""
import pytest

from dashboard.vision.classifier import parse_classifier_response


def test_parses_clean_json():
    raw = '{"image_type":"person","gender":"female","mood":"smiling",' \
          '"pose":"standing","setting":"beach","parts_visible":["face","eyes"],' \
          '"nsfw":"safe","person_count":"1","caption":"a woman smiling at the beach",' \
          '"tags":"woman,smile,beach"}'
    out = parse_classifier_response(raw)
    assert out["image_type"] == "person"
    assert out["gender"] == "female"
    assert out["parts_visible"] == "face,eyes"
    assert out["caption"].startswith("a woman")


def test_strips_code_fences_and_preamble():
    raw = "Here is the JSON:\n```json\n" \
          '{"image_type":"object","person_count":"0","gender":"unknown",' \
          '"pose":"unknown","mood":"neutral","setting":"indoor",' \
          '"parts_visible":[],"nsfw":"safe","caption":"a chair","tags":"chair"}' \
          "\n```\nDone."
    out = parse_classifier_response(raw)
    assert out["image_type"] == "object"
    assert out["parts_visible"] == ""


def test_missing_required_key_raises():
    raw = '{"image_type":"person"}'
    with pytest.raises(ValueError):
        parse_classifier_response(raw)


def test_invalid_value_coerces_to_unknown():
    raw = '{"image_type":"person","person_count":"1","gender":"alien",' \
          '"pose":"floating","mood":"neutral","setting":"indoor",' \
          '"parts_visible":["face"],"nsfw":"safe","caption":"x","tags":"y"}'
    out = parse_classifier_response(raw)
    assert out["gender"] == "unknown"     # 'alien' not in VOCAB
    assert out["pose"] == "unknown"        # 'floating' not in VOCAB


def test_garbage_input_raises_value_error():
    with pytest.raises(ValueError):
        parse_classifier_response("not json at all")
```

- [ ] **Step 2: Watch tests fail**

Run: `venv/bin/python -m pytest tests/vision/test_classifier.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement classifier**

Create `<root>/dashboard/vision/classifier.py`:

```python
"""Structured-attribute classifier on top of qwen3-vl via Ollama.

Produces ONE inference per image; parses the model's JSON response strictly
but defensively (strips code fences, finds the first `{...}` block) and
normalizes every value through dashboard.vision.vocab.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
from typing import Optional

from PIL import Image

from dashboard.vision.vocab import REQUIRED_KEYS, VOCAB, normalize

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3-vl:latest"
FALLBACK_MODEL = "llava:13b"
DOWNSCALE_PX = 384
PER_IMAGE_TIMEOUT = 90  # seconds

PROMPT = """You are an image cataloguer. Respond with ONLY a single JSON object, no prose, no thinking, no code fences.

Required keys (use exactly these names; values must come from the listed options):

image_type:     person | object | scene | mixed | text | meme | unknown
person_count:   0 | 1 | 2 | 3+ | unknown
gender:         female | male | androgynous | unknown
age_band:       child | teen | young-adult | adult | senior | unknown
hair_color:     blonde | brown | black | red | gray | dyed-other | unknown
hair_style:     long | short | medium | up | bald | covered | unknown
build:          slim | athletic | average | curvy | heavy | unknown
pose:           standing | sitting | lying | crouching | walking | dancing | action | unknown
viewpoint:      front | back | left-profile | right-profile | three-quarter | top | unknown
mood:           neutral | smiling | serious | surprised | pensive | playful | unknown
clothing_style: casual | formal | swimwear | sportswear | lingerie | costume | none | unknown
setting:        indoor | outdoor-urban | outdoor-nature | beach | studio | vehicle | unknown
nsfw:           safe | suggestive | explicit | unknown

parts_visible: array of strings from {face, eyes, lips, hair, torso, arm, hand, leg, foot}.
caption:       one natural-language sentence.
tags:          12 comma-separated keywords.

If no person is present, set all person attributes (gender, age_band, hair_color,
hair_style, build, pose, viewpoint, mood, clothing_style) to "unknown".
"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class ClassifierError(RuntimeError):
    pass


class GPUContention(ClassifierError):
    pass


def _downscale_to_b64(path: str, max_px: int = DOWNSCALE_PX) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def parse_classifier_response(raw: str) -> dict:
    """Pull the first JSON object out of the model output, normalize every
    value, ensure all REQUIRED_KEYS are present. Raises ValueError on any
    irrecoverable shape problem."""
    if not raw:
        raise ValueError("empty response")
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        raise ValueError("no JSON object found in response")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("response is not an object")

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"missing required keys: {missing}")

    normalized: dict[str, str] = {}
    for k, v in data.items():
        if k in ("caption", "tags"):
            normalized[k] = ("" if v is None else str(v)).strip()
        else:
            normalized[k] = normalize(k, v)
    return normalized


def _post_ollama(b64: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_predict": 1500, "temperature": 0.2, "num_ctx": 3072},
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PER_IMAGE_TIMEOUT) as resp:
            data = json.loads(resp.read())
            return (data.get("message") or {}).get("content", "") or ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 500 and ("resource" in body.lower() or "failed to load" in body.lower()):
            raise GPUContention(body) from e
        raise ClassifierError(f"HTTP {e.code}: {body}") from e


def classify(path: str) -> tuple[dict, str]:
    """Run one classifier pass against `path`. Returns (normalized_attrs, model_id).
    Raises GPUContention to let the caller back off, or ClassifierError on hard errors."""
    b64 = _downscale_to_b64(path)
    last_err: Optional[Exception] = None
    for model in (MODEL, FALLBACK_MODEL):
        try:
            raw = _post_ollama(b64, model)
            return parse_classifier_response(raw), model
        except GPUContention:
            raise
        except (ValueError, ClassifierError) as e:
            last_err = e
    raise ClassifierError(f"both models failed: {last_err}")
```

- [ ] **Step 4: Verify parser tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_classifier.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/classifier.py tests/vision/test_classifier.py
git commit -m "$(cat <<'EOF'
feat(vision): classifier — qwen3-vl structured JSON attributes

One inference per image. Tolerates code fences and preamble in the
model's response, validates required keys, normalizes values through
the controlled vocabulary or coerces to "unknown". Raises GPUContention
on Ollama 500-resource so the indexer can back off.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.3: Indexer loop

**Files:**
- Create: `<root>/dashboard/vision/indexer.py`
- Create: `<root>/dashboard/vision_indexer.py` (CLI entrypoint)

- [ ] **Step 1: Implement the indexer module**

Create `<root>/dashboard/vision/indexer.py`:

```python
"""Vision indexer — consume pending assets, classify, persist attributes.

Mirrors image_indexer.py's behavior: low-priority, resumable, retries failed
rows after a cooldown, never pins the GPU.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from dashboard.vision.classifier import (
    ClassifierError, GPUContention, classify,
)
from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db

RETRY_FAILED_AFTER = 6 * 3600
INTER_IMAGE_SLEEP = 0.5
BACKOFF_ON_500 = 20

_SHUTDOWN = False


def _sigterm(_sig, _frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    print("[vision-indexer] SIGTERM — finishing current image and exiting", flush=True)


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


def _attrs_blob(attrs: dict) -> str:
    """Compose the FTS5 attrs_blob: `key:value key:value ...`."""
    skip = {"caption", "tags"}
    return " ".join(f"{k}:{v}" for k, v in attrs.items() if k not in skip and v)


def _persist(con, asset_id: int, attrs: dict, model: str) -> None:
    """Write attributes + caption rows + sync FTS5. Single transaction."""
    caption = attrs.get("caption", "")
    tags = attrs.get("tags", "")
    blob = _attrs_blob(attrs)

    con.execute("BEGIN")
    try:
        for k, v in attrs.items():
            if k in ("caption", "tags") or v == "" or v is None:
                continue
            con.execute(
                """INSERT INTO attributes (asset_id, key, value, confidence, source)
                   VALUES (?, ?, ?, 1.0, ?)
                   ON CONFLICT (asset_id, key) DO UPDATE SET
                       value=excluded.value,
                       confidence=excluded.confidence,
                       source=excluded.source""",
                (asset_id, k, v, model),
            )
        con.execute(
            """INSERT INTO captions (asset_id, caption, tags, model)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(asset_id) DO UPDATE SET
                   caption=excluded.caption, tags=excluded.tags, model=excluded.model""",
            (asset_id, caption, tags, model),
        )
        # Re-sync FTS5: delete old rowid then insert.
        con.execute("DELETE FROM assets_fts WHERE rowid = ?", (asset_id,))
        con.execute(
            "INSERT INTO assets_fts (rowid, caption, tags, attrs_blob) VALUES (?, ?, ?, ?)",
            (asset_id, caption, tags, blob),
        )
        con.execute(
            "UPDATE assets SET status='ok', classified_at=?, error=NULL WHERE id=?",
            (time.time(), asset_id),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def run(db_path: Optional[str] = None, *, force: bool = False,
        retry_failed: bool = False, limit: int = 0, verbose: bool = False) -> int:
    init_db(db_path)
    con = connect(db_path)
    t0 = time.time()
    processed = failed = 0

    cur = con.execute(
        """SELECT id, abs_path, status, classified_at FROM assets
           WHERE status = 'pending'
              OR (status='failed' AND (? OR (?-COALESCE(classified_at,0)) > ?))
              OR (? AND status='ok')
           ORDER BY id ASC""",
        (1 if retry_failed else 0, time.time(), RETRY_FAILED_AFTER, 1 if force else 0),
    )
    rows = cur.fetchall()

    for row in rows:
        if _SHUTDOWN:
            break
        if limit and processed >= limit:
            break

        asset_id = row["id"]
        path = row["abs_path"]
        t_img = time.time()
        try:
            attrs, model = classify(path)
        except GPUContention as e:
            print(f"[gpu-busy] sleeping {BACKOFF_ON_500}s — {str(e)[:80]}", flush=True)
            time.sleep(BACKOFF_ON_500)
            continue
        except (ClassifierError, ValueError, OSError) as e:
            con.execute(
                "UPDATE assets SET status='failed', classified_at=?, error=? WHERE id=?",
                (time.time(), str(e)[:300], asset_id),
            )
            con.execute(
                "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'classify', 0, ?, ?)",
                (asset_id, time.time(), str(e)[:300]),
            )
            failed += 1
            print(f"[fail] {path} — {e}", flush=True)
            continue

        try:
            _persist(con, asset_id, attrs, model)
            processed += 1
            elapsed = time.time() - t_img
            if verbose:
                print(f"[ok {elapsed:5.1f}s] {path}\n          {attrs.get('caption','')[:140]}", flush=True)
            else:
                print(f"[ok {elapsed:5.1f}s] {path} — {attrs.get('caption','')[:80]}", flush=True)
        except Exception as e:
            failed += 1
            print(f"[persist-fail] {path}: {e}", flush=True)

        time.sleep(INTER_IMAGE_SLEEP)

    print(f"\n[vision-indexer] processed={processed} failed={failed} elapsed={time.time()-t0:.1f}s", flush=True)
    return 0
```

- [ ] **Step 2: Implement the CLI entrypoint**

Create `<root>/dashboard/vision_indexer.py`:

```python
#!/usr/bin/env python3
"""Vision indexer — CLI entrypoint mirroring image_indexer.py shape."""
import argparse
import sys

from dashboard.vision.db import DEFAULT_DB_PATH
from dashboard.vision.indexer import run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-classify ok rows")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry rows with status='failed' regardless of cooldown")
    ap.add_argument("--limit", type=int, default=0, help="stop after N images")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    return run(args.db, force=args.force, retry_failed=args.retry_failed,
               limit=args.limit, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Smoke test on real data (limit=2)**

If Ollama and qwen3-vl are running and Phase 2 ingested any images:

```bash
venv/bin/python dashboard/vision_indexer.py --limit 2 --verbose
```

Expected output: two `[ok N.Ns] /path... — <caption>` lines, no Python tracebacks. Then verify the DB:

```bash
sqlite3 dashboard/vision.db "SELECT a.id, a.status, c.caption FROM assets a LEFT JOIN captions c ON c.asset_id=a.id WHERE a.status='ok' LIMIT 2;"
```

Expected: 2 rows with non-null captions.

If no images exist or Ollama is down, this just prints a summary and exits 0 — that's fine.

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision/indexer.py dashboard/vision_indexer.py
git commit -m "$(cat <<'EOF'
feat(vision): indexer + CLI

Consumes status='pending' rows, classifies via qwen3-vl, writes attribute
rows + captions + FTS5 in one transaction, marks ok/failed. Retry cooldown
matches image_indexer.py (6h on failed, 0.5s inter-image).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3.4: Systemd unit + timer

**Files:**
- Create: `<root>/baza-vision-indexer.service`
- Create: `<root>/baza-vision-indexer.timer`

- [ ] **Step 1: Create the service unit**

Create `<root>/baza-vision-indexer.service`:

```ini
[Unit]
Description=Baza Empire — Vision UI Indexer (qwen3-vl structured attributes)
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python /home/switchhacker/baza-empire/agent-framework-v3/dashboard/vision_indexer.py
Nice=15
IOSchedulingClass=best-effort
IOSchedulingPriority=7
TimeoutStartSec=3h
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the timer**

Create `<root>/baza-vision-indexer.timer`:

```ini
[Unit]
Description=Baza Empire — Vision Indexer schedule (every 30 min)

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
Persistent=true
Unit=baza-vision-indexer.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Install + enable**

```bash
sudo cp baza-vision-indexer.service /etc/systemd/system/
sudo cp baza-vision-indexer.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baza-vision-indexer.timer
systemctl status baza-vision-indexer.timer --no-pager
```

Expected: timer active. First run in ~3 minutes.

- [ ] **Step 4: Watch the first run**

```bash
journalctl -u baza-vision-indexer.service -f --no-pager
```

Wait up to 3 minutes (or trigger immediately with `sudo systemctl start baza-vision-indexer.service`). Expected: `[ok N.Ns] ...` lines, exit summary.

- [ ] **Step 5: Commit**

```bash
git add baza-vision-indexer.service baza-vision-indexer.timer
git commit -m "$(cat <<'EOF'
feat(vision): systemd unit + timer for vision indexer

Mirrors baza-image-indexer.* — Nice=15, best-effort IO, 30-min cadence.
Runs only when ollama.service is available.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 3 done.** Backlog from Phase 2 starts draining; every new private inbound image gets caught on the next 30-min tick (once Phase 5 wires `ingest.observe()` into the upload flow).

---

## Phase 4 — Cropper (PR 4)

Goal: detect faces (+ eye/lip sub-crops) with InsightFace, save crop JPEGs, register them as `source='crop', parent_id=<frame>` assets, denormalize inheritable parent attributes onto crops. Non-face parts (hands/feet/torso) handled in Phase 4.5 via a second qwen3-vl bbox prompt. v1 ships face crops only; bodies are a stretch.

### Task 4.1: Install InsightFace

- [ ] **Step 1: Add to requirements**

Append to `<root>/requirements.txt`:

```
insightface>=0.7.3
onnxruntime>=1.17.0
```

- [ ] **Step 2: Install**

Run: `venv/bin/pip install insightface onnxruntime`
Expected: success. First import will download ~250 MB of model files into `~/.insightface/`. Acceptable.

- [ ] **Step 3: Smoke test**

Run:
```bash
venv/bin/python -c "from insightface.app import FaceAnalysis; a=FaceAnalysis(providers=['CPUExecutionProvider']); a.prepare(ctx_id=-1); print('OK')"
```
Expected: a few "find model:" prints + `OK`. (CPU is fine for Baza — face count per image is single-digit.)

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "$(cat <<'EOF'
chore: add insightface + onnxruntime for vision face cropping

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 4.2: Cropper module + bbox math tests

**Files:**
- Create: `<root>/dashboard/vision/cropper.py`
- Create: `<root>/tests/vision/test_cropper.py`

- [ ] **Step 1: Write bbox math tests**

Create `<root>/tests/vision/test_cropper.py`:

```python
"""Cropper bbox helpers — clamp + expand math, no model required."""
from dashboard.vision.cropper import clamp_bbox, expand_bbox


def test_clamp_bbox_keeps_inside_image():
    # bbox sticks out left and top; should clamp.
    assert clamp_bbox(-10, -5, 50, 50, img_w=100, img_h=100) == (0, 0, 40, 45)


def test_clamp_bbox_keeps_inside_right_bottom():
    assert clamp_bbox(80, 80, 50, 50, img_w=100, img_h=100) == (80, 80, 20, 20)


def test_expand_bbox_pads_proportionally():
    # 100x100 bbox in a 200x200 image, expand 0.2 → +20 each side, but clamped.
    assert expand_bbox(50, 50, 100, 100, 0.2, img_w=200, img_h=200) == (30, 30, 140, 140)


def test_expand_bbox_clamps_at_edges():
    assert expand_bbox(0, 0, 100, 100, 0.2, img_w=100, img_h=100) == (0, 0, 100, 100)
```

- [ ] **Step 2: Watch tests fail**

Run: `venv/bin/python -m pytest tests/vision/test_cropper.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement cropper**

Create `<root>/dashboard/vision/cropper.py`:

```python
"""Face + eye/lip crop pipeline using InsightFace SCRFD.

For each detected face:
  · save the face crop as <source-id>_face_<n>.jpg
  · save eye crop (combined eyes region) as ...eye_<n>.jpg
  · save lips crop as ...lips_<n>.jpg
  · register each as a child asset (source='crop', parent_id=<frame>)

Each crop inherits intrinsic parent attributes (gender, hair_color, age_band,
build, mood, nsfw) so /Catalogue/Faces/Female filters work without a 3-table
join. Done in Python, not a SQL trigger — see spec §5.3.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from PIL import Image

from dashboard.vision.db import DEFAULT_DB_PATH, connect

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROPS_DIR = os.path.join(DASHBOARD_DIR, "artifacts", ".vision-crops")

INHERITABLE_KEYS = ("gender", "age_band", "hair_color", "hair_style",
                    "build", "mood", "nsfw", "ethnicity")

PADDING = 0.12   # 12% bbox expansion before crop


def clamp_bbox(x: int, y: int, w: int, h: int, *, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Clip a bbox to image bounds. Returns (x, y, w, h) — possibly shrunk."""
    x2, y2 = x + w, y + h
    nx = max(0, x); ny = max(0, y)
    nx2 = min(img_w, x2); ny2 = min(img_h, y2)
    return nx, ny, max(0, nx2 - nx), max(0, ny2 - ny)


def expand_bbox(x: int, y: int, w: int, h: int, pct: float, *, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Expand bbox by `pct` on each side, then clamp to image bounds."""
    dx = int(w * pct); dy = int(h * pct)
    return clamp_bbox(x - dx, y - dy, w + 2 * dx, h + 2 * dy, img_w=img_w, img_h=img_h)


def _save_crop(img: Image.Image, bbox, out_path: str) -> None:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.crop((x, y, x + w, y + h)).save(out_path, "JPEG", quality=88)


def _eye_bbox_from_landmarks(face) -> Optional[tuple[int, int, int, int]]:
    """SCRFD landmark 5 = [le, re, nose, lm, rm]. Compose a bbox covering both eyes."""
    if face.kps is None or len(face.kps) < 5:
        return None
    lx, ly = face.kps[0]; rx, ry = face.kps[1]
    cx = int((lx + rx) / 2); cy = int((ly + ry) / 2)
    eye_w = int(abs(rx - lx) * 1.6)
    eye_h = int(eye_w * 0.45)
    return (cx - eye_w // 2, cy - eye_h // 2, eye_w, eye_h)


def _lips_bbox_from_landmarks(face) -> Optional[tuple[int, int, int, int]]:
    if face.kps is None or len(face.kps) < 5:
        return None
    lx, ly = face.kps[3]; rx, ry = face.kps[4]
    cx = int((lx + rx) / 2); cy = int((ly + ry) / 2)
    lip_w = int(abs(rx - lx) * 1.4)
    lip_h = int(lip_w * 0.5)
    return (cx - lip_w // 2, cy - lip_h // 2, lip_w, lip_h)


_FACE_APP = None


def _face_app():
    global _FACE_APP
    if _FACE_APP is None:
        from insightface.app import FaceAnalysis
        _FACE_APP = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _FACE_APP.prepare(ctx_id=-1, det_size=(640, 640))
    return _FACE_APP


def _inheritable_attrs(con, parent_id: int) -> dict[str, tuple[str, float]]:
    rows = con.execute(
        "SELECT key, value, confidence FROM attributes WHERE asset_id=? AND key IN ({})".format(
            ",".join(["?"] * len(INHERITABLE_KEYS))
        ),
        (parent_id, *INHERITABLE_KEYS),
    ).fetchall()
    return {r["key"]: (r["value"], r["confidence"]) for r in rows}


def _register_crop(con, *, abs_path: str, parent_id: int, part: str,
                   bbox: tuple[int, int, int, int], detector: str) -> int:
    """Insert child asset row, crops row, and inherited attribute rows."""
    from dashboard.vision.ingest import observe
    asset_id = observe(abs_path, source="crop", db_path=None, parent_id=parent_id)
    # ingest.observe uses DEFAULT_DB_PATH; ensure same con can see it (we're WAL).
    # Insert crop row.
    x, y, w, h = bbox
    con.execute(
        """INSERT INTO crops (asset_id, part, bbox_x, bbox_y, bbox_w, bbox_h, detector)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(asset_id) DO UPDATE SET
               part=excluded.part, bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y,
               bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, detector=excluded.detector""",
        (asset_id, part, x, y, w, h, detector),
    )
    # Denormalize inheritable parent attrs onto child.
    for k, (v, conf) in _inheritable_attrs(con, parent_id).items():
        con.execute(
            """INSERT INTO attributes (asset_id, key, value, confidence, source)
               VALUES (?, ?, ?, ?, 'inherited')
               ON CONFLICT(asset_id, key) DO NOTHING""",
            (asset_id, k, v, conf),
        )
    return asset_id


def crop_one(parent_path: str, parent_id: int, db_path: Optional[str] = None) -> int:
    """Detect faces in `parent_path`, save crops, register children. Returns
    the count of new crop assets created."""
    img = Image.open(parent_path).convert("RGB")
    img_w, img_h = img.size

    import numpy as np
    faces = _face_app().get(np.array(img))
    if not faces:
        return 0

    crops_root = os.path.join(CROPS_DIR, str(parent_id))
    os.makedirs(crops_root, exist_ok=True)

    con = connect(db_path)
    created = 0
    for n, f in enumerate(faces):
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        face_bbox = expand_bbox(x1, y1, x2 - x1, y2 - y1, PADDING, img_w=img_w, img_h=img_h)
        face_path = os.path.join(crops_root, f"face_{n}.jpg")
        _save_crop(img, face_bbox, face_path)
        _register_crop(con, abs_path=face_path, parent_id=parent_id,
                       part="face", bbox=face_bbox, detector="insightface-scrfd")
        created += 1

        eye = _eye_bbox_from_landmarks(f)
        if eye:
            eye = clamp_bbox(*eye, img_w=img_w, img_h=img_h)
            eye_path = os.path.join(crops_root, f"eye_{n}.jpg")
            _save_crop(img, eye, eye_path)
            _register_crop(con, abs_path=eye_path, parent_id=parent_id,
                           part="eye", bbox=eye, detector="insightface-landmarks")
            created += 1

        lips = _lips_bbox_from_landmarks(f)
        if lips:
            lips = clamp_bbox(*lips, img_w=img_w, img_h=img_h)
            lips_path = os.path.join(crops_root, f"lips_{n}.jpg")
            _save_crop(img, lips, lips_path)
            _register_crop(con, abs_path=lips_path, parent_id=parent_id,
                           part="lips", bbox=lips, detector="insightface-landmarks")
            created += 1

    con.execute(
        "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'crop', 1, ?, ?)",
        (parent_id, time.time(), f"created={created}"),
    )
    return created
```

- [ ] **Step 4: Verify bbox tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_cropper.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/cropper.py tests/vision/test_cropper.py
git commit -m "$(cat <<'EOF'
feat(vision): cropper — face + eye + lips via InsightFace

For each detected face: save padded face crop, eye and lip crops derived
from SCRFD landmarks, register each as a child asset with crops row and
inherited intrinsic attributes (gender/age_band/hair_color/etc).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 4.3: Wire cropper into the indexer

**Files:**
- Modify: `<root>/dashboard/vision/indexer.py`

- [ ] **Step 1: Call cropper after successful classify**

In `<root>/dashboard/vision/indexer.py`, find the `try: _persist(con, asset_id, attrs, model)` block. Immediately AFTER the successful persist (inside the same try, before `processed += 1`), add:

```python
            # Crop pass — only for person-class images with face visible.
            if attrs.get("image_type") == "person" and "face" in (attrs.get("parts_visible") or ""):
                try:
                    from dashboard.vision.cropper import crop_one
                    n = crop_one(path, asset_id, db_path=db_path)
                    if verbose and n:
                        print(f"          + {n} crop(s)", flush=True)
                except Exception as ce:
                    print(f"[crop-fail] {path}: {ce}", flush=True)
                    con.execute(
                        "INSERT INTO ingest_log (asset_id, step, ok, ts, detail) VALUES (?, 'crop', 0, ?, ?)",
                        (asset_id, time.time(), str(ce)[:300]),
                    )
```

- [ ] **Step 2: Smoke test**

If you have an image with a face in `.private-inbound/`:

```bash
sudo systemctl start baza-vision-indexer.service
journalctl -u baza-vision-indexer.service -n 50 --no-pager
```

Expected: at least one `+ N crop(s)` line if `--verbose` (or run manually: `venv/bin/python dashboard/vision_indexer.py --limit 3 --verbose`).

Verify in DB:

```bash
sqlite3 dashboard/vision.db "SELECT COUNT(*) FROM crops; SELECT part, COUNT(*) FROM crops GROUP BY part;"
```

Expected: non-zero face counts (or zero if no face-class images yet — fine).

- [ ] **Step 3: Commit**

```bash
git add dashboard/vision/indexer.py
git commit -m "$(cat <<'EOF'
feat(vision): indexer triggers cropper after classify on person images

When classify says image_type=person and parts_visible includes face,
the indexer runs InsightFace and registers face/eye/lips child assets.
Crop failures are isolated — never block the parent asset's ok status.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 4 done.** Faces from inbound images are extracted as first-class crop assets that inherit gender/age/hair/etc. from their parent. Body-part crops (hand/foot/torso) deferred — when needed, add a second qwen3-vl bbox prompt or a YOLO model. v1 ships face crops only.

---

## Phase 5 — Vision UI page + JSON API (PR 5)

Goal: ship the `/vision` page (replaces `/datahub/private`) and the six JSON endpoints. Reuse existing passphrase gate + private file-serving routes. Wire `ingest.observe()` into the inbound capture path so new Telegram images become pending assets immediately.

### Task 5.1: Taxonomy module + query composer

**Files:**
- Create: `<root>/dashboard/vision/taxonomy.py`
- Create: `<root>/dashboard/vision/search.py`
- Create: `<root>/tests/vision/test_taxonomy.py`
- Create: `<root>/tests/vision/test_search.py`

- [ ] **Step 1: Write taxonomy structure tests**

Create `<root>/tests/vision/test_taxonomy.py`:

```python
from dashboard.vision.taxonomy import TAXONOMY, find_node, ancestor_filters


def test_root_paths_exist():
    paths = {n.path for n in TAXONOMY}
    assert "/Inbound" in paths
    assert "/Generated" in paths
    assert "/Scraped" in paths
    assert "/Catalogue" in paths


def test_find_node_returns_correct_node():
    n = find_node("/Catalogue/People/Female")
    assert n is not None
    assert n.path == "/Catalogue/People/Female"


def test_find_node_returns_none_for_unknown():
    assert find_node("/NoSuch/Folder") is None


def test_ancestor_filters_combines_query_dicts():
    # /Catalogue/People/Female should AND its query with parent /Catalogue/People.
    fl = ancestor_filters("/Catalogue/People/Female")
    # 'image_type':'person' from People + 'gender':'female' from Female.
    assert fl.get("image_type") == "person"
    assert fl.get("gender") == "female"
```

- [ ] **Step 2: Watch them fail**

Run: `venv/bin/python -m pytest tests/vision/test_taxonomy.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement taxonomy**

Create `<root>/dashboard/vision/taxonomy.py`:

```python
"""Virtual folder taxonomy. Each Node has a path and optional `q` filter
dict; child nodes inherit their parent's filters via AND."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Node:
    path: str
    label: str = ""
    q: dict = field(default_factory=dict)         # attribute key→value filters
    target: int = 6                                # gap-fill target count
    children: list["Node"] = field(default_factory=list)

    def __post_init__(self):
        if not self.label:
            self.label = self.path.rsplit("/", 1)[-1] or self.path


def _people_color(color: str) -> Node:
    return Node(f"/Catalogue/People/Female/{color}", q={"hair_color": _color_value(color)})


def _color_value(label: str) -> str:
    return {"Blonde": "blonde", "Brunette": "brown", "Black": "black",
            "Red": "red", "Gray": "gray"}[label]


TAXONOMY: list[Node] = [
    Node("/Inbound",   q={"source": "inbound"}),
    Node("/Generated", q={"source": "generated"}),
    Node("/Scraped",   q={"source": "scraped"}),

    Node("/Catalogue", children=[
        Node("/Catalogue/People", q={"image_type": "person"}, children=[
            Node("/Catalogue/People/Female", q={"gender": "female"}, children=[
                _people_color("Blonde"),
                _people_color("Brunette"),
                _people_color("Black"),
                _people_color("Red"),
            ]),
            Node("/Catalogue/People/Male", q={"gender": "male"}),
        ]),
        Node("/Catalogue/Faces", q={"source": "crop"}, children=[
            Node("/Catalogue/Faces/Female", q={"gender": "female"}, children=[
                Node("/Catalogue/Faces/Female/Eyes", q={"crops.part": "eye"}),
                Node("/Catalogue/Faces/Female/Lips", q={"crops.part": "lips"}),
                Node("/Catalogue/Faces/Female/Face", q={"crops.part": "face"}),
            ]),
            Node("/Catalogue/Faces/Male", q={"gender": "male"}, children=[
                Node("/Catalogue/Faces/Male/Eyes", q={"crops.part": "eye"}),
                Node("/Catalogue/Faces/Male/Lips", q={"crops.part": "lips"}),
                Node("/Catalogue/Faces/Male/Face", q={"crops.part": "face"}),
            ]),
        ]),
        Node("/Catalogue/Body", q={"source": "crop"}, children=[
            Node("/Catalogue/Body/Hands", q={"crops.part": "hand"}),
            Node("/Catalogue/Body/Feet",  q={"crops.part": "foot"}),
            Node("/Catalogue/Body/Torso", q={"crops.part": "torso"}),
            Node("/Catalogue/Body/Legs",  q={"crops.part": "leg"}),
        ]),
        Node("/Catalogue/Style", children=[
            Node("/Catalogue/Style/Swimwear",   q={"clothing_style": "swimwear"}),
            Node("/Catalogue/Style/Formal",     q={"clothing_style": "formal"}),
            Node("/Catalogue/Style/Sportswear", q={"clothing_style": "sportswear"}),
            Node("/Catalogue/Style/Casual",     q={"clothing_style": "casual"}),
        ]),
        Node("/Catalogue/Scenes", children=[
            Node("/Catalogue/Scenes/Beach",   q={"setting": "beach"}),
            Node("/Catalogue/Scenes/Studio",  q={"setting": "studio"}),
            Node("/Catalogue/Scenes/Outdoor", q={"setting": "outdoor-nature"}),
            Node("/Catalogue/Scenes/Urban",   q={"setting": "outdoor-urban"}),
            Node("/Catalogue/Scenes/Indoor",  q={"setting": "indoor"}),
        ]),
        Node("/Catalogue/Mood", children=[
            Node("/Catalogue/Mood/Smiling", q={"mood": "smiling"}),
            Node("/Catalogue/Mood/Pensive", q={"mood": "pensive"}),
            Node("/Catalogue/Mood/Serious", q={"mood": "serious"}),
            Node("/Catalogue/Mood/Playful", q={"mood": "playful"}),
        ]),
    ]),
]


def _walk(nodes: list[Node]):
    for n in nodes:
        yield n
        yield from _walk(n.children)


def all_nodes() -> list[Node]:
    return list(_walk(TAXONOMY))


def find_node(path: str) -> Optional[Node]:
    for n in all_nodes():
        if n.path == path:
            return n
    return None


def ancestor_filters(path: str) -> dict:
    """Compose all filter dicts from /Catalogue down to `path` (inclusive)."""
    parts = path.split("/")
    out: dict = {}
    for i in range(2, len(parts) + 1):
        sub = "/".join(parts[:i])
        n = find_node(sub)
        if n:
            for k, v in n.q.items():
                out[k] = v
    return out
```

- [ ] **Step 4: Verify taxonomy tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_taxonomy.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write search composer tests**

Create `<root>/tests/vision/test_search.py`:

```python
import sqlite3

from dashboard.vision.db import init_db
from dashboard.vision.search import browse_query, count_for_node


def _seed(con):
    con.executescript("""
        INSERT INTO assets (id, abs_path, source, status) VALUES
            (1, '/a/1.jpg', 'inbound', 'ok'),
            (2, '/a/2.jpg', 'inbound', 'ok'),
            (3, '/a/3.jpg', 'crop',    'ok');
        INSERT INTO attributes (asset_id, key, value) VALUES
            (1, 'image_type', 'person'),
            (1, 'gender', 'female'),
            (1, 'hair_color', 'blonde'),
            (2, 'image_type', 'person'),
            (2, 'gender', 'male'),
            (3, 'gender', 'female');
        INSERT INTO crops (asset_id, part, bbox_x, bbox_y, bbox_w, bbox_h, detector) VALUES
            (3, 'eye', 0, 0, 10, 10, 'test');
    """)


def test_browse_query_filters_by_attributes(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    sql, params = browse_query({"image_type": "person", "gender": "female"})
    rows = con.execute(sql, params).fetchall()
    assert [r["id"] for r in rows] == [1]


def test_browse_query_handles_crop_part(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    sql, params = browse_query({"source": "crop", "crops.part": "eye"})
    rows = con.execute(sql, params).fetchall()
    assert [r["id"] for r in rows] == [3]


def test_count_for_node(tmp_vision_db):
    init_db(tmp_vision_db)
    con = sqlite3.connect(tmp_vision_db); con.row_factory = sqlite3.Row
    _seed(con)
    assert count_for_node(con, {"image_type": "person"}) == 2
    assert count_for_node(con, {"gender": "female"}) == 2
    assert count_for_node(con, {"source": "crop"}) == 1
```

- [ ] **Step 6: Implement `search.py`**

Create `<root>/dashboard/vision/search.py`:

```python
"""SQL composers for browse + search.

The browse query AND-joins `assets` with one `attributes` row per filter key.
Crops use a special `crops.<col>` namespace (only `crops.part` for v1) which
joins the `crops` table directly.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


def browse_query(filters: dict, *, page: int = 1, limit: int = 60) -> tuple[str, list]:
    """Return (sql, params) selecting matching assets ordered by id desc."""
    base = ["SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at FROM assets a"]
    where = ["a.status = 'ok'"]
    params: list = []

    attr_n = 0
    for k, v in (filters or {}).items():
        if k == "source":
            where.append("a.source = ?")
            params.append(v)
        elif k == "crops.part":
            base.append("JOIN crops c ON c.asset_id = a.id")
            where.append("c.part = ?")
            params.append(v)
        else:
            attr_n += 1
            alias = f"at{attr_n}"
            base.append(f"JOIN attributes {alias} ON {alias}.asset_id = a.id")
            where.append(f"{alias}.key = ? AND {alias}.value = ?")
            params.extend([k, v])

    sql = " ".join(base) + " WHERE " + " AND ".join(where) + " ORDER BY a.id DESC"
    if limit:
        offset = max(0, (page - 1) * limit)
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return sql, params


def count_for_node(con: sqlite3.Connection, filters: dict) -> int:
    """COUNT(*) of matching ok assets — used by /api/vision/tree."""
    sql, params = browse_query(filters, page=1, limit=0)
    sql = sql.replace(
        "SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at",
        "SELECT COUNT(DISTINCT a.id)",
        1,
    )
    sql = sql.split(" ORDER BY ")[0]
    return con.execute(sql, params).fetchone()[0]


def fts_search(con: sqlite3.Connection, q: str, limit: int = 60) -> list[sqlite3.Row]:
    """FTS5 query on caption/tags/attrs_blob."""
    rows = con.execute(
        """SELECT a.id, a.abs_path, a.source, a.width, a.height, a.classified_at,
                  fts.rank
             FROM assets_fts fts
             JOIN assets a ON a.id = fts.rowid
            WHERE assets_fts MATCH ?
              AND a.status = 'ok'
            ORDER BY fts.rank
            LIMIT ?""",
        (q, int(limit)),
    ).fetchall()
    return list(rows)
```

- [ ] **Step 7: Verify search tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_search.py -v`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add dashboard/vision/taxonomy.py dashboard/vision/search.py tests/vision/test_taxonomy.py tests/vision/test_search.py
git commit -m "$(cat <<'EOF'
feat(vision): taxonomy + browse_query + fts_search

Taxonomy is a Python module — Node defs + ancestor_filters() composes
all filters from /Catalogue down to a leaf. browse_query() AND-joins
assets with one attribute join per filter key; crops.part joins crops.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5.2: Vision Flask blueprint with all 6 endpoints

**Files:**
- Create: `<root>/dashboard/vision_routes.py`
- Modify: `<root>/dashboard/app.py` (register blueprint, redirect old route, rename nav)

- [ ] **Step 1: Implement the blueprint**

Create `<root>/dashboard/vision_routes.py`:

```python
"""Vision UI Flask blueprint.

All routes gated by the existing _is_private_unlocked() session check
(re-imported from dashboard.app to keep the gate single-source-of-truth).
"""
from __future__ import annotations

import functools
import os
import time
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, session, abort, redirect, url_for

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.search import browse_query, count_for_node, fts_search
from dashboard.vision.taxonomy import TAXONOMY, all_nodes, ancestor_filters, find_node

bp = Blueprint("vision", __name__)


def _require_unlocked(fn):
    @functools.wraps(fn)
    def wrap(*a, **kw):
        if not session.get("private_unlocked"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "locked"}), 401
            return redirect("/datahub/private")  # existing unlock UI handles this
        return fn(*a, **kw)
    return wrap


def _node_to_dict(node, con) -> dict:
    filters = ancestor_filters(node.path)
    return {
        "path": node.path,
        "label": node.label,
        "count": count_for_node(con, filters) if filters else None,
        "target": node.target,
        "children": [_node_to_dict(c, con) for c in node.children],
    }


@bp.route("/vision")
@_require_unlocked
def vision_page():
    return render_template("vision.html")


@bp.route("/api/vision/tree")
@_require_unlocked
def api_tree():
    init_db()
    con = connect()
    try:
        tree = [_node_to_dict(n, con) for n in TAXONOMY]
        return jsonify({"ok": True, "tree": tree})
    finally:
        con.close()


@bp.route("/api/vision/browse")
@_require_unlocked
def api_browse():
    init_db()
    path = request.args.get("path", "/Catalogue")
    page = max(1, int(request.args.get("page", 1)))
    limit = max(1, min(200, int(request.args.get("limit", 60))))
    node = find_node(path)
    if not node:
        return jsonify({"ok": False, "error": f"no such path: {path}"}), 404

    filters = ancestor_filters(path)
    con = connect()
    try:
        sql, params = browse_query(filters, page=page, limit=limit)
        assets = [dict(r) for r in con.execute(sql, params).fetchall()]
        total = count_for_node(con, filters) if filters else 0
        pages = (total + limit - 1) // limit if limit else 1
        return jsonify({
            "ok": True,
            "node": {"path": node.path, "label": node.label, "target": node.target},
            "assets": assets,
            "total": total,
            "page": page,
            "pages": pages,
        })
    finally:
        con.close()


@bp.route("/api/vision/search")
@_require_unlocked
def api_search():
    init_db()
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(200, int(request.args.get("limit", 60))))
    if not q:
        return jsonify({"ok": True, "assets": [], "total": 0})
    con = connect()
    try:
        rows = fts_search(con, q, limit=limit)
        assets = [dict(r) for r in rows]
        return jsonify({"ok": True, "assets": assets, "total": len(assets)})
    finally:
        con.close()


@bp.route("/api/vision/asset/<int:asset_id>")
@_require_unlocked
def api_asset(asset_id: int):
    init_db()
    con = connect()
    try:
        a = con.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            return jsonify({"ok": False, "error": "no such asset"}), 404
        attrs = {r["key"]: {"value": r["value"], "confidence": r["confidence"], "source": r["source"]}
                 for r in con.execute(
                     "SELECT key, value, confidence, source FROM attributes WHERE asset_id=?",
                     (asset_id,)
                 ).fetchall()}
        cap = con.execute("SELECT caption, tags, model FROM captions WHERE asset_id=?", (asset_id,)).fetchone()
        crops = [dict(r) for r in con.execute(
            """SELECT a.id, a.abs_path, c.part, c.bbox_x, c.bbox_y, c.bbox_w, c.bbox_h
                 FROM assets a JOIN crops c ON c.asset_id=a.id
                WHERE a.parent_id=?""",
            (asset_id,),
        ).fetchall()]
        parent = None
        if a["parent_id"]:
            p = con.execute("SELECT id, abs_path, source FROM assets WHERE id=?", (a["parent_id"],)).fetchone()
            parent = dict(p) if p else None
        return jsonify({
            "ok": True,
            "asset": dict(a),
            "attributes": attrs,
            "caption": dict(cap) if cap else None,
            "crops": crops,
            "parent": parent,
        })
    finally:
        con.close()


@bp.route("/api/vision/asset/<int:asset_id>/attributes", methods=["POST"])
@_require_unlocked
def api_asset_attributes(asset_id: int):
    body = request.get_json(silent=True) or {}
    updates = body.get("attributes") or {}
    if not isinstance(updates, dict) or not updates:
        return jsonify({"ok": False, "error": "attributes dict required"}), 400
    init_db()
    con = connect()
    try:
        a = con.execute("SELECT id FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            return jsonify({"ok": False, "error": "no such asset"}), 404
        for k, v in updates.items():
            if v is None:
                con.execute("DELETE FROM attributes WHERE asset_id=? AND key=?", (asset_id, k))
            else:
                con.execute(
                    """INSERT INTO attributes (asset_id, key, value, confidence, source)
                       VALUES (?, ?, ?, 1.0, 'manual')
                       ON CONFLICT(asset_id, key) DO UPDATE SET
                           value=excluded.value, confidence=1.0, source='manual'""",
                    (asset_id, k, str(v).strip().lower()),
                )
        return jsonify({"ok": True, "asset_id": asset_id})
    finally:
        con.close()


@bp.route("/api/vision/specter/seed", methods=["POST"])
@_require_unlocked
def api_specter_seed():
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path or not find_node(path):
        return jsonify({"ok": False, "error": "valid taxonomy path required"}), 400
    init_db()
    con = connect()
    try:
        cur = con.execute(
            """INSERT INTO seed_demand (taxonomy_path, needed, reason, requested_at)
               VALUES (?, 6, 'agent-request', ?)""",
            (path, time.time()),
        )
        return jsonify({"ok": True, "demand_id": cur.lastrowid, "eta_seconds": 600})
    finally:
        con.close()
```

- [ ] **Step 2: Register blueprint in app.py**

Open `<root>/dashboard/app.py`. Find a stable location near the end of the file (before `if __name__ == '__main__':`) and add:

```python
# ── Vision UI ──────────────────────────────────────────────────────────────
from dashboard.vision_routes import bp as vision_bp  # noqa: E402
app.register_blueprint(vision_bp)


@app.route("/datahub/private")
def datahub_private_redirect():
    """Old URL — keep redirecting old bookmarks to /vision."""
    return redirect("/vision", code=302)
```

⚠️ This REPLACES the existing `/datahub/private` route at app.py:1318. Find:

```python
@app.route('/datahub/private')
def datahub_private_page():
    return render_template('private.html', ...)
```

Delete that handler (the redirect above is the new owner of the URL). The unlock POST routes that the existing `private.html` uses — keep those; we're going to render the same passphrase form from `vision.html` until session.private_unlocked is set.

- [ ] **Step 3: Update left-nav in templates**

Find the existing nav fragment (likely repeated in `index.html`, `agent.html`, etc — the dashboard doesn't use a layout). For each template that has a `<a href="/datahub/private"...>Private</a>` link, change it to:

```html
<a href="/vision" class="nav-link">Vision</a>
```

Templates to grep for: `<root>/dashboard/templates/*.html`.

```bash
grep -l "datahub/private" dashboard/templates/*.html
```

Update each (their nav blocks are similar — replace the `Private` label with `Vision`).

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision_routes.py dashboard/app.py dashboard/templates/
git commit -m "$(cat <<'EOF'
feat(vision): blueprint + 6 JSON endpoints + nav rename

/vision page (placeholder template arrives next task), 6 endpoints behind
the existing private session gate, /datahub/private 302 → /vision.
Left-nav renamed Private → Vision across templates.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5.3: Vision UI page template

**Files:**
- Create: `<root>/dashboard/templates/vision.html`

- [ ] **Step 1: Create the template**

Create `<root>/dashboard/templates/vision.html`. It's long but self-contained — three-pane layout with vanilla JS:

```html
<!DOCTYPE html>
<html lang="en" data-theme="{{ session.get('theme') or request.cookies.get('theme', 'dark') }}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Baza Empire — Vision</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#07070f;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
    a{color:inherit;text-decoration:none}
    .nav{background:#0d0d1e;border-bottom:1px solid #1a1a3a;padding:0 32px;display:flex;align-items:center;position:sticky;top:0;z-index:200}
    .nav-brand{display:flex;align-items:center;gap:10px;padding:18px 24px 18px 0;border-right:1px solid #1a1a3a;margin-right:8px}
    .nav-brand h1{font-size:18px;font-weight:800;color:#e94560;letter-spacing:2px}
    .nav-link{padding:20px 18px;font-size:13px;font-weight:600;color:#666;border-bottom:3px solid transparent}
    .nav-link.active{color:#e0e0e0;border-bottom-color:#e94560}
    .nav-spacer{margin-left:auto}
    .clock{padding:0 18px;font-size:12px;color:#666;font-variant-numeric:tabular-nums}
    .layout{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 60px)}
    .tree{border-right:1px solid #1a1a3a;background:#09091a;padding:16px;overflow-y:auto;max-height:calc(100vh - 60px);position:sticky;top:60px}
    .tree-node{padding:6px 8px;font-size:13px;cursor:pointer;border-radius:5px;display:flex;align-items:center;gap:6px}
    .tree-node:hover{background:#13132a}
    .tree-node.active{background:#1a1a3a;color:#e94560}
    .tree-children{margin-left:14px}
    .tree-count{margin-left:auto;font-size:11px;color:#666}
    .main{padding:24px 28px}
    .breadcrumb{font-size:12px;color:#666;margin-bottom:18px}
    .toolbar{display:flex;gap:10px;margin-bottom:16px;align-items:center}
    .search{flex:1;background:#0e0e1e;border:1px solid #2a2a4a;color:#e0e0e0;padding:8px 12px;border-radius:6px;font-size:13px}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
    .card{aspect-ratio:1;background:#0e0e1e;border:1px solid #1a1a3a;border-radius:6px;overflow:hidden;cursor:pointer;position:relative}
    .card img{width:100%;height:100%;object-fit:cover}
    .card .badge{position:absolute;top:6px;left:6px;background:rgba(0,0,0,0.7);color:#aaa;font-size:10px;padding:2px 6px;border-radius:3px}
    .empty{padding:40px;text-align:center;color:#666;border:1px dashed #2a2a4a;border-radius:8px}
    .seed-cta{margin-top:16px;padding:14px 18px;background:#1a0a1e;border:1px solid #4a1a3a;border-radius:8px;display:flex;justify-content:space-between;align-items:center}
    .btn{padding:8px 14px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;border:none;background:linear-gradient(135deg,#e94560,#7c3aed);color:#fff}
    .btn:hover{opacity:.85}
    .pager{margin-top:18px;display:flex;gap:8px;justify-content:center;align-items:center;color:#666;font-size:12px}
    .pager button{background:#0e0e1e;border:1px solid #2a2a4a;color:#aaa;padding:6px 12px;border-radius:5px;cursor:pointer}
    .pager button:disabled{opacity:.4;cursor:not-allowed}
    .modal{position:fixed;inset:0;background:rgba(0,0,0,0.85);display:none;align-items:center;justify-content:center;z-index:300}
    .modal.open{display:flex}
    .modal-card{background:#0d0d1e;border:1px solid #2a2a4a;border-radius:10px;max-width:90vw;max-height:90vh;display:grid;grid-template-columns:1fr 320px;overflow:hidden}
    .modal-img{background:#000;display:flex;align-items:center;justify-content:center;min-height:300px}
    .modal-img img{max-width:100%;max-height:80vh;object-fit:contain}
    .modal-meta{padding:18px;overflow-y:auto;font-size:12px;line-height:1.6}
    .modal-meta h3{font-size:14px;color:#e94560;margin-bottom:10px}
    .meta-row{display:flex;gap:8px;border-bottom:1px solid #1a1a3a;padding:5px 0}
    .meta-key{color:#666;width:110px;font-size:11px;text-transform:uppercase;letter-spacing:1px}
    .meta-val{color:#e0e0e0}
    .nsfw-blur img{filter:blur(20px)}
  </style>
  <link rel="stylesheet" href="{{ url_for('static', filename='css/theme.css') }}">
</head>
<body>
  <nav class="nav">
    <div class="nav-brand"><h1>BAZA</h1></div>
    <a class="nav-link" href="/">Home</a>
    <a class="nav-link" href="/agents">Agents</a>
    <a class="nav-link" href="/datahub">Data Hub</a>
    <a class="nav-link active" href="/vision">Vision</a>
    <div class="nav-spacer"></div>
    <span class="clock" id="clock"></span>
    <span data-theme-mount></span>
  </nav>

  <div class="layout">
    <aside class="tree" id="tree">Loading…</aside>
    <main class="main">
      <div class="breadcrumb" id="breadcrumb">Vision</div>
      <div class="toolbar">
        <input class="search" id="search" placeholder="Search captions, tags, attributes (e.g. blonde bikini beach)">
      </div>
      <div id="content"></div>
      <div class="pager" id="pager"></div>
    </main>
  </div>

  <div class="modal" id="modal">
    <div class="modal-card">
      <div class="modal-img"><img id="modalImg" src=""></div>
      <div class="modal-meta" id="modalMeta">…</div>
    </div>
  </div>

  <script src="{{ url_for('static', filename='js/theme.js') }}"></script>
  <script src="{{ url_for('static', filename='js/vision.js') }}"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/templates/vision.html
git commit -m "$(cat <<'EOF'
feat(vision): vision.html — three-pane page template

Tree on the left, grid in the center, modal for asset detail. Inline
styles + theme.css overlay; no SPA framework. JS lives in vision.js.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5.4: Vision UI JavaScript

**Files:**
- Create: `<root>/dashboard/static/js/vision.js`

- [ ] **Step 1: Create the script**

Create `<root>/dashboard/static/js/vision.js`:

```javascript
/* Vision UI client — fetches /api/vision/* endpoints, renders tree/grid/modal. */
(function () {
  var state = { path: '/Catalogue', page: 1, limit: 60 };

  function el(id) { return document.getElementById(id); }
  function setBreadcrumb(path) { el('breadcrumb').textContent = 'Vision ▸ ' + path.replace(/^\//,'').replace(/\//g, ' ▸ '); }

  // ── Tree ────────────────────────────────────────────────────────────────
  function renderTreeNode(n, depth) {
    var div = document.createElement('div');
    div.className = 'tree-node' + (n.path === state.path ? ' active' : '');
    div.style.paddingLeft = (8 + depth * 12) + 'px';
    var label = n.label;
    var count = (n.count == null) ? '' : n.count;
    div.innerHTML = '<span>' + label + '</span><span class="tree-count">' + count + '</span>';
    div.addEventListener('click', function (e) { e.stopPropagation(); navigate(n.path); });
    var wrap = document.createElement('div');
    wrap.appendChild(div);
    if (n.children && n.children.length) {
      var c = document.createElement('div'); c.className = 'tree-children';
      n.children.forEach(function (cc) { c.appendChild(renderTreeNode(cc, depth + 1)); });
      wrap.appendChild(c);
    }
    return wrap;
  }
  function refreshTree() {
    return fetch('/api/vision/tree').then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) return;
      var root = el('tree'); root.innerHTML = '';
      j.tree.forEach(function (n) { root.appendChild(renderTreeNode(n, 0)); });
    });
  }

  // ── Grid + pager ────────────────────────────────────────────────────────
  function renderAssets(assets) {
    var c = el('content');
    c.innerHTML = '';
    if (!assets.length) {
      c.innerHTML = '<div class="empty">No assets in this folder yet. ' +
        '<button class="btn" id="seedBtn" style="margin-left:12px">Specter: fill this folder</button></div>';
      var sb = el('seedBtn'); if (sb) sb.addEventListener('click', requestSeed);
      return;
    }
    var grid = document.createElement('div'); grid.className = 'grid';
    assets.forEach(function (a) {
      var div = document.createElement('div'); div.className = 'card';
      var img = document.createElement('img');
      img.src = '/api/vision/asset/' + a.id + '/thumb';
      img.alt = '';
      img.loading = 'lazy';
      div.appendChild(img);
      var b = document.createElement('span'); b.className = 'badge'; b.textContent = a.source;
      div.appendChild(b);
      div.addEventListener('click', function () { openAsset(a.id); });
      grid.appendChild(div);
    });
    c.appendChild(grid);
  }

  function renderPager(total, page, pages) {
    var p = el('pager'); p.innerHTML = '';
    if (pages <= 1) { p.textContent = total + ' items'; return; }
    var prev = document.createElement('button'); prev.textContent = '⟵ prev'; prev.disabled = page <= 1;
    prev.addEventListener('click', function () { state.page = page - 1; loadBrowse(); });
    var info = document.createElement('span'); info.textContent = 'page ' + page + ' / ' + pages + ' — ' + total + ' items';
    var next = document.createElement('button'); next.textContent = 'next ⟶'; next.disabled = page >= pages;
    next.addEventListener('click', function () { state.page = page + 1; loadBrowse(); });
    p.appendChild(prev); p.appendChild(info); p.appendChild(next);
  }

  function loadBrowse() {
    var q = '?path=' + encodeURIComponent(state.path) + '&page=' + state.page + '&limit=' + state.limit;
    return fetch('/api/vision/browse' + q).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { el('content').innerHTML = '<div class="empty">' + (j.error || 'error') + '</div>'; return; }
      setBreadcrumb(state.path);
      renderAssets(j.assets);
      renderPager(j.total, j.page, j.pages);
      // Seed CTA if thin.
      var c = el('content');
      if (j.assets.length > 0 && j.total < (j.node.target || 6)) {
        var cta = document.createElement('div'); cta.className = 'seed-cta';
        cta.innerHTML = '<span>This folder is thin (' + j.total + ' / ' + (j.node.target || 6) + ').</span>' +
          '<button class="btn" id="seedBtn">Specter: fill this folder now</button>';
        c.appendChild(cta);
        el('seedBtn').addEventListener('click', requestSeed);
      }
    });
  }

  function navigate(path) { state.path = path; state.page = 1; refreshTree(); loadBrowse(); }

  // ── Modal ───────────────────────────────────────────────────────────────
  function openAsset(id) {
    fetch('/api/vision/asset/' + id).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) return;
      el('modalImg').src = '/api/vision/asset/' + id + '/thumb?full=1';
      var meta = el('modalMeta');
      var rows = ['<h3>Asset #' + j.asset.id + '</h3>'];
      rows.push('<div class="meta-row"><div class="meta-key">source</div><div class="meta-val">' + j.asset.source + '</div></div>');
      if (j.caption && j.caption.caption) rows.push('<div class="meta-row"><div class="meta-key">caption</div><div class="meta-val">' + j.caption.caption + '</div></div>');
      Object.keys(j.attributes).sort().forEach(function (k) {
        rows.push('<div class="meta-row"><div class="meta-key">' + k + '</div><div class="meta-val">' + j.attributes[k].value + '</div></div>');
      });
      meta.innerHTML = rows.join('');
      el('modal').classList.add('open');
    });
  }
  el('modal').addEventListener('click', function (e) {
    if (e.target.id === 'modal') el('modal').classList.remove('open');
  });

  // ── Seed CTA ────────────────────────────────────────────────────────────
  function requestSeed() {
    fetch('/api/vision/specter/seed', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: state.path}),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) alert('Specter queued. ETA ~' + (j.eta_seconds / 60) + ' min.');
      else alert('Error: ' + (j.error || 'unknown'));
    });
  }

  // ── Search ──────────────────────────────────────────────────────────────
  var searchTimer = null;
  el('search').addEventListener('input', function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    searchTimer = setTimeout(function () {
      if (!q) return loadBrowse();
      fetch('/api/vision/search?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (j) {
        if (!j.ok) return;
        setBreadcrumb('Search: ' + q);
        renderAssets(j.assets);
        renderPager(j.assets.length, 1, 1);
      });
    }, 280);
  });

  // ── Clock ───────────────────────────────────────────────────────────────
  function tickClock() {
    var d = new Date();
    el('clock').textContent = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  }
  setInterval(tickClock, 30 * 1000); tickClock();

  // ── Boot ────────────────────────────────────────────────────────────────
  refreshTree().then(loadBrowse);
})();
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/js/vision.js
git commit -m "$(cat <<'EOF'
feat(vision): vision.js client — tree/grid/modal/search/seed

Vanilla JS, no framework. Calls /api/vision/* endpoints. Search debounced
280ms. Modal renders all attributes + caption. Seed-this-folder button
appears when total < node.target.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5.5: Asset thumbnail/serving endpoint

**Files:**
- Modify: `<root>/dashboard/vision_routes.py` (add `/api/vision/asset/<id>/thumb`)

- [ ] **Step 1: Add the thumb route**

Append to `<root>/dashboard/vision_routes.py`:

```python
@bp.route("/api/vision/asset/<int:asset_id>/thumb")
@_require_unlocked
def api_asset_thumb(asset_id: int):
    init_db()
    full = request.args.get("full") == "1"
    con = connect()
    try:
        a = con.execute("SELECT abs_path FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not a:
            abort(404)
        path = a["abs_path"]
        if not os.path.isfile(path):
            abort(404)

        # Re-use the existing private serve gate — defense in depth: only
        # serve files under .private-inbound/ or .vision-* dirs.
        allowed_prefixes = (
            os.path.join(os.path.dirname(__file__), "artifacts", ".private-inbound"),
            os.path.join(os.path.dirname(__file__), "artifacts", ".vision-generated"),
            os.path.join(os.path.dirname(__file__), "artifacts", ".vision-scraped"),
            os.path.join(os.path.dirname(__file__), "artifacts", ".vision-crops"),
        )
        if not any(os.path.abspath(path).startswith(p) for p in allowed_prefixes):
            abort(403)

        if full:
            return _send_file(path)

        # Generate a 256px thumbnail on the fly. Cheap with Pillow + JPEG
        # quality 78 — typical thumb < 30 KB. No on-disk thumb cache for v1.
        from io import BytesIO
        from PIL import Image
        from flask import send_file
        img = Image.open(path).convert("RGB")
        img.thumbnail((256, 256), Image.LANCZOS)
        buf = BytesIO(); img.save(buf, "JPEG", quality=78); buf.seek(0)
        return send_file(buf, mimetype="image/jpeg",
                         download_name=f"thumb_{asset_id}.jpg")
    finally:
        con.close()


def _send_file(path: str):
    from flask import send_file
    return send_file(path, mimetype=None, conditional=True)
```

- [ ] **Step 2: Smoke test the endpoint**

Restart dashboard, unlock `/datahub/private`, then visit `/vision`.

```bash
sudo systemctl restart baza-dashboard.service
```

Open browser → `/vision`. Expect: tree on left renders, grid populates if Phase 3 has classified anything ok. Click a thumbnail → modal opens with attributes.

If grid is empty, that just means Phase 3's indexer hasn't run yet (or no inbound images exist) — that's fine.

- [ ] **Step 3: Commit**

```bash
git add dashboard/vision_routes.py
git commit -m "$(cat <<'EOF'
feat(vision): asset thumb/serve endpoint with prefix safety check

Generates 256px thumbnails on the fly (?full=1 streams the original).
Defense-in-depth: only serves files under .private-inbound/ or .vision-*
directories, abort(403) otherwise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5.6: Wire ingest.observe() into Telegram capture

**Files:**
- Modify: `<root>/dashboard/private_inbound.py` (add a hook)

- [ ] **Step 1: Add observation hook**

In `<root>/dashboard/private_inbound.py`, find the `mark_private` function. Append after it:

```python
def observe_into_vision(fpath: str, *, agent_id: Optional[str] = None) -> None:
    """Best-effort: register the file with the Vision catalogue. Failures are
    swallowed — never break the upload flow because vision indexing is down."""
    try:
        from dashboard.vision.ingest import observe
        observe(fpath, source="inbound", origin_agent=agent_id)
    except Exception:
        pass
```

- [ ] **Step 2: Find and update existing `mark_private` callers**

```bash
grep -rn "mark_private(" --include="*.py" .
```

For each location that calls `mark_private(fpath, ...)` for an image upload (telegram-bound media), add `observe_into_vision(fpath, agent_id=...)` immediately after. Skip non-image callers (any `mark_private` calls for documents/json/audio).

The most likely callers: agent files under `<root>/agents/*/agent.py` that handle Telegram media. Each has a clear "image saved to path X, mark_private(X)" sequence — that's where the hook goes.

- [ ] **Step 3: Smoke test**

If you can manually drop an image into a `.private-inbound/<agent>/` directory:

```bash
cp /some/test/image.jpg dashboard/artifacts/.private-inbound/scout_reeves/test_$(date +%s).jpg
venv/bin/python -c "from dashboard.private_inbound import observe_into_vision; observe_into_vision('dashboard/artifacts/.private-inbound/scout_reeves/test_xxx.jpg')"
sqlite3 dashboard/vision.db "SELECT COUNT(*) FROM assets WHERE abs_path LIKE '%test_%';"
```

Expected: count 1.

- [ ] **Step 4: Commit**

```bash
git add dashboard/private_inbound.py agents/
git commit -m "$(cat <<'EOF'
feat(vision): wire inbound capture into vision.observe()

private_inbound.observe_into_vision() registers any newly-marked-private
image with vision.db so it's queued for the next indexer tick. Failures
swallowed — never breaks the upload flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 5 done.** `/vision` is live, browseable, search works, Telegram inbound flows in. Specter integrations come next.

---

## Phase 6 — Specter mode 1 + 3: gap-scan + UI fill button (PR 6)

Goal: detect thin/empty bins; let the user trigger a fill from the UI. Generation/scrape come in Phases 7-8 — for now, the demand ledger logs intent.

### Task 6.1: Seed-scan script

**Files:**
- Create: `<root>/dashboard/vision/seed_scan.py`
- Create: `<root>/baza-vision-seed-scan.service`
- Create: `<root>/baza-vision-seed-scan.timer`

- [ ] **Step 1: Implement seed_scan**

Create `<root>/dashboard/vision/seed_scan.py`:

```python
#!/usr/bin/env python3
"""Specter mode 1 — gap detector.

Walks the taxonomy, counts ok assets per leaf node, inserts seed_demand
rows for empty/thin bins (subject to a 24h dedup window so we don't pile
up duplicates each run).
"""
from __future__ import annotations

import argparse
import sys
import time

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.search import count_for_node
from dashboard.vision.taxonomy import all_nodes, ancestor_filters

DEDUP_WINDOW = 24 * 3600   # don't re-request a thin bin within 24h
NEVER_SEED_PREFIXES = ("/Inbound", "/Generated", "/Scraped")


def is_leaf(node) -> bool:
    return not node.children


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    init_db(args.db)
    con = connect(args.db)
    now = time.time()
    requested = 0

    for node in all_nodes():
        if not is_leaf(node):
            continue
        if any(node.path.startswith(p) for p in NEVER_SEED_PREFIXES):
            continue
        filters = ancestor_filters(node.path)
        if not filters:
            continue

        count = count_for_node(con, filters)
        if count >= node.target:
            continue

        recent = con.execute(
            """SELECT id FROM seed_demand
                WHERE taxonomy_path = ?
                  AND fulfilled_at IS NULL
                  AND requested_at > ?""",
            (node.path, now - DEDUP_WINDOW),
        ).fetchone()
        if recent:
            if args.verbose:
                print(f"[skip-dup] {node.path} (count={count}/{node.target})")
            continue

        reason = "empty" if count == 0 else "thin"
        con.execute(
            """INSERT INTO seed_demand (taxonomy_path, needed, reason, requested_at)
               VALUES (?, ?, ?, ?)""",
            (node.path, node.target, reason, now),
        )
        requested += 1
        if args.verbose:
            print(f"[demand] {node.path} count={count} target={node.target} reason={reason}")

    print(f"[seed-scan] queued {requested} demands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Systemd service + timer**

Create `<root>/baza-vision-seed-scan.service`:

```ini
[Unit]
Description=Baza Empire — Vision seed-scan (Specter mode 1, gap detector)
After=baza-vision-indexer.service

[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python -m dashboard.vision.seed_scan
StandardOutput=journal
StandardError=journal
```

Create `<root>/baza-vision-seed-scan.timer`:

```ini
[Unit]
Description=Baza Empire — Vision seed-scan schedule (every 6h)

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
Persistent=true
Unit=baza-vision-seed-scan.service

[Install]
WantedBy=timers.target
```

Install:

```bash
sudo cp baza-vision-seed-scan.service /etc/systemd/system/
sudo cp baza-vision-seed-scan.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baza-vision-seed-scan.timer
sudo systemctl start baza-vision-seed-scan.service   # run once now
journalctl -u baza-vision-seed-scan.service -n 30 --no-pager
```

Expected: `[seed-scan] queued N demands` where N is roughly the number of empty leaf bins (probably most of them on first run).

- [ ] **Step 3: Commit**

```bash
git add dashboard/vision/seed_scan.py baza-vision-seed-scan.service baza-vision-seed-scan.timer
git commit -m "$(cat <<'EOF'
feat(vision): seed_scan — Specter mode 1 (gap detector)

Walks taxonomy leaves, queues seed_demand rows for any bin where
count < target. 24h dedup window prevents duplicate demands.
Systemd timer runs every 6h.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 6.2: Inbox / pending counter (UI)

**Files:**
- Modify: `<root>/dashboard/vision_routes.py` (extend `/api/vision/tree` to include pending count)
- Modify: `<root>/dashboard/static/js/vision.js` (show inbox warn when pending > 0)

- [ ] **Step 1: Extend tree endpoint with pending counts**

In `<root>/dashboard/vision_routes.py`, find `def api_tree():` and add to the response:

```python
        pending = con.execute(
            "SELECT COUNT(*) FROM assets WHERE status='pending'"
        ).fetchone()[0]
        failed = con.execute(
            "SELECT COUNT(*) FROM assets WHERE status='failed'"
        ).fetchone()[0]
        open_demand = con.execute(
            "SELECT COUNT(*) FROM seed_demand WHERE fulfilled_at IS NULL"
        ).fetchone()[0]
        return jsonify({
            "ok": True, "tree": tree,
            "stats": {"pending": pending, "failed": failed, "open_demand": open_demand},
        })
```

- [ ] **Step 2: Render the stats in the UI**

In `<root>/dashboard/static/js/vision.js`, update `refreshTree()`:

```javascript
  function refreshTree() {
    return fetch('/api/vision/tree').then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) return;
      var root = el('tree'); root.innerHTML = '';
      if (j.stats) {
        var s = j.stats;
        var bar = document.createElement('div');
        bar.style.cssText = 'font-size:11px;color:#666;margin-bottom:10px;border-bottom:1px solid #1a1a3a;padding-bottom:8px';
        bar.innerHTML = 'pending: ' + s.pending + ' &middot; failed: ' + s.failed + ' &middot; demand: ' + s.open_demand;
        root.appendChild(bar);
      }
      j.tree.forEach(function (n) { root.appendChild(renderTreeNode(n, 0)); });
    });
  }
```

- [ ] **Step 3: Smoke test**

Restart dashboard, visit `/vision`. Top of tree shows `pending: N · failed: M · demand: P` line.

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision_routes.py dashboard/static/js/vision.js
git commit -m "$(cat <<'EOF'
feat(vision): inbox/pending/demand stats in tree sidebar

/api/vision/tree now returns {pending, failed, open_demand} counts;
sidebar header renders them so you can see the indexer's queue depth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 6 done.** Specter's gap detection runs every 6h; UI shows queue depth + open demands. The "fill this folder now" button writes a high-priority demand row but no fulfillment exists yet — that's Phase 7.

---

## Phase 7 — Specter generate mode (PR 7)

Goal: Specter can fulfill `seed_demand` rows for `/Catalogue/People`, `/Catalogue/Faces`, `/Catalogue/Body` by calling SD WebUI Forge with prompts derived from the taxonomy path. GPU-leased to avoid colliding with cron jobs.

### Task 7.1: GPU lease helper

**Files:**
- Create: `<root>/dashboard/vision/gpu_lease.py`
- Create: `<root>/tests/vision/test_gpu_lease.py`

- [ ] **Step 1: Test acquire/release**

Create `<root>/tests/vision/test_gpu_lease.py`:

```python
import time

from dashboard.vision.db import init_db
from dashboard.vision.gpu_lease import acquire, release, holder


def test_acquire_succeeds_when_unheld(tmp_vision_db):
    init_db(tmp_vision_db)
    ok = acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    assert ok is True
    assert holder("rtx3070", db_path=tmp_vision_db) == "specter"


def test_acquire_fails_when_held(tmp_vision_db):
    init_db(tmp_vision_db)
    acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    ok = acquire("rtx3070", "other", ttl=60, db_path=tmp_vision_db)
    assert ok is False


def test_release_clears_lease(tmp_vision_db):
    init_db(tmp_vision_db)
    acquire("rtx3070", "specter", ttl=60, db_path=tmp_vision_db)
    release("rtx3070", "specter", db_path=tmp_vision_db)
    ok = acquire("rtx3070", "another", ttl=60, db_path=tmp_vision_db)
    assert ok is True


def test_expired_lease_can_be_retaken(tmp_vision_db):
    init_db(tmp_vision_db)
    # Acquire with a 0-second TTL — already expired.
    acquire("rtx3070", "specter", ttl=0, db_path=tmp_vision_db)
    time.sleep(0.01)
    assert acquire("rtx3070", "another", ttl=60, db_path=tmp_vision_db) is True
```

- [ ] **Step 2: Implement gpu_lease**

Create `<root>/dashboard/vision/gpu_lease.py`:

```python
"""GPU lease — coordinates SD WebUI use across Specter and other cron jobs.

Single row per GPU. Acquire = INSERT-or-replace IF the existing row's expires_at
is in the past. Release = DELETE if you are the holder. Other cron jobs that
use the GPU should call acquire() at start and release() at end (best-effort).
"""
from __future__ import annotations

import time
from typing import Optional

from dashboard.vision.db import connect


def acquire(gpu: str, holder_name: str, ttl: int, *, db_path: Optional[str] = None,
            purpose: Optional[str] = None) -> bool:
    """Try to take the lease. Returns True on success, False if held by other."""
    now = time.time()
    expires = now + ttl
    con = connect(db_path)
    try:
        # If existing row is unexpired and not us, refuse.
        existing = con.execute("SELECT holder, expires_at FROM gpu_lease WHERE gpu=?", (gpu,)).fetchone()
        if existing and existing["expires_at"] > now and existing["holder"] != holder_name:
            return False
        con.execute(
            """INSERT INTO gpu_lease (gpu, holder, acquired_at, expires_at, purpose)
                VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(gpu) DO UPDATE SET
                   holder=excluded.holder, acquired_at=excluded.acquired_at,
                   expires_at=excluded.expires_at, purpose=excluded.purpose""",
            (gpu, holder_name, now, expires, purpose),
        )
        return True
    finally:
        con.close()


def release(gpu: str, holder_name: str, *, db_path: Optional[str] = None) -> None:
    con = connect(db_path)
    try:
        con.execute("DELETE FROM gpu_lease WHERE gpu=? AND holder=?", (gpu, holder_name))
    finally:
        con.close()


def holder(gpu: str, *, db_path: Optional[str] = None) -> Optional[str]:
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT holder, expires_at FROM gpu_lease WHERE gpu=?", (gpu,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            return None
        return row["holder"]
    finally:
        con.close()
```

- [ ] **Step 3: Verify tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_gpu_lease.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision/gpu_lease.py tests/vision/test_gpu_lease.py
git commit -m "$(cat <<'EOF'
feat(vision): gpu_lease helper — acquire/release/holder

Single-row-per-GPU coordination so Specter's SD generations don't
collide with scheduled cron jobs on the RTX 3070.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 7.2: Prompt mapping from taxonomy path

**Files:**
- Create: `<root>/dashboard/vision/prompt_map.py`
- Create: `<root>/tests/vision/test_prompt_map.py`

- [ ] **Step 1: Test prompt mapping**

Create `<root>/tests/vision/test_prompt_map.py`:

```python
from dashboard.vision.prompt_map import prompt_for_path


def test_blonde_female():
    p = prompt_for_path("/Catalogue/People/Female/Blonde")
    assert "blonde" in p["prompt"]
    assert "woman" in p["prompt"]
    assert "photorealistic" in p["prompt"]
    assert p["negative"]


def test_face_eye_crop():
    p = prompt_for_path("/Catalogue/Faces/Female/Eyes")
    assert "eye" in p["prompt"]
    assert "close-up" in p["prompt"] or "macro" in p["prompt"]


def test_scene_beach():
    p = prompt_for_path("/Catalogue/Scenes/Beach")
    assert "beach" in p["prompt"]
    assert "no people" in p["prompt"] or "empty" in p["prompt"]
```

- [ ] **Step 2: Implement prompt_map**

Create `<root>/dashboard/vision/prompt_map.py`:

```python
"""Map a taxonomy path to an SD Forge txt2img prompt + negative prompt.

Heuristic-only — Specter does not yet learn from the DB. Each prompt is
phrased to produce a clean, well-lit, photorealistic reference image; the
negative filters watermarks, weird anatomy, and low quality.
"""
from __future__ import annotations

NEGATIVE = (
    "watermark, text, signature, logo, low quality, blurry, cropped, "
    "extra fingers, fused fingers, bad anatomy, bad hands, deformed, "
    "mutation, extra limbs, asymmetric eyes"
)

GENDER_NOUN = {"female": "woman", "male": "man", "androgynous": "person"}


def _person_prompt(parts: list[str]) -> dict:
    # parts: e.g. ['Catalogue', 'People', 'Female', 'Blonde']
    gender = parts[2].lower() if len(parts) > 2 else "female"
    noun = GENDER_NOUN.get(gender, "person")
    extras = []
    if len(parts) > 3:
        attr = parts[3].lower()
        if attr in ("blonde", "brunette", "black", "red"):
            color = {"brunette": "brown"}.get(attr, attr)
            extras.append(f"{color} hair")
        elif attr == "gray":
            extras.append("gray hair")
    extras_s = ", " + ", ".join(extras) if extras else ""
    return {
        "prompt": f"professional photo of a {noun}{extras_s}, "
                  f"neutral expression, studio lighting, photorealistic, sharp focus",
        "negative": NEGATIVE,
    }


def _face_crop_prompt(parts: list[str]) -> dict:
    # /Catalogue/Faces/Female/Eyes
    gender = parts[2].lower() if len(parts) > 2 else "female"
    part = parts[3].lower() if len(parts) > 3 else "face"
    noun = GENDER_NOUN.get(gender, "person")
    if part == "eyes":
        return {"prompt": f"close-up macro photograph of {noun}'s eye, sharp focus, "
                          f"natural lighting, photorealistic", "negative": NEGATIVE}
    if part == "lips":
        return {"prompt": f"close-up macro photograph of {noun}'s lips, sharp focus, "
                          f"natural lighting, photorealistic", "negative": NEGATIVE}
    return {"prompt": f"close-up portrait of a {noun}'s face, photorealistic, "
                      f"neutral background, soft lighting", "negative": NEGATIVE}


def _body_part_prompt(parts: list[str]) -> dict:
    part = parts[2].lower() if len(parts) > 2 else "torso"
    return {"prompt": f"close-up macro photograph of human {part}, photorealistic, "
                      f"clean background, studio lighting", "negative": NEGATIVE}


def _style_prompt(parts: list[str]) -> dict:
    style = parts[2].lower() if len(parts) > 2 else "casual"
    return {"prompt": f"flat-lay photograph of {style} clothing on a neutral "
                      f"background, photorealistic, well-lit, catalog style",
            "negative": NEGATIVE}


def _scene_prompt(parts: list[str]) -> dict:
    setting = parts[2].lower() if len(parts) > 2 else "outdoor"
    return {"prompt": f"photograph of an empty {setting} scene with no people, "
                      f"photorealistic, natural lighting, sharp focus",
            "negative": NEGATIVE + ", person, people, crowd, human"}


def _mood_prompt(parts: list[str]) -> dict:
    mood = parts[2].lower() if len(parts) > 2 else "neutral"
    return {"prompt": f"portrait photograph of a person with a {mood} expression, "
                      f"photorealistic, soft lighting", "negative": NEGATIVE}


def prompt_for_path(path: str) -> dict:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "Catalogue":
        raise ValueError(f"unsupported path for generation: {path}")
    section = parts[1]
    if section == "People":
        return _person_prompt(parts)
    if section == "Faces":
        return _face_crop_prompt(parts)
    if section == "Body":
        return _body_part_prompt(parts)
    if section == "Style":
        return _style_prompt(parts)
    if section == "Scenes":
        return _scene_prompt(parts)
    if section == "Mood":
        return _mood_prompt(parts)
    raise ValueError(f"no prompt template for {path}")
```

- [ ] **Step 3: Verify tests pass**

Run: `venv/bin/python -m pytest tests/vision/test_prompt_map.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision/prompt_map.py tests/vision/test_prompt_map.py
git commit -m "$(cat <<'EOF'
feat(vision): prompt_map — taxonomy path → SD Forge txt2img prompt

Heuristic templates per /Catalogue subsection (People, Faces, Body,
Style, Scenes, Mood). Hardcoded negative prompt filters watermarks +
common anatomy issues. Gen prompts always include 'photorealistic'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 7.3: Generate via SD Forge

**Files:**
- Create: `<root>/dashboard/vision/sd_forge.py`

- [ ] **Step 1: Implement the SD client**

Create `<root>/dashboard/vision/sd_forge.py`:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/vision/sd_forge.py
git commit -m "$(cat <<'EOF'
feat(vision): sd_forge.txt2img + save_generated

Minimal SD WebUI Forge client. Returns PNG bytes for the first image.
save_generated() writes to artifacts/.vision-generated/<bin-slug>/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 7.4: Seed-fulfill (generate path)

**Files:**
- Create: `<root>/dashboard/vision/seed_fulfill.py`
- Create: `<root>/baza-vision-seed-fulfill.service`
- Create: `<root>/baza-vision-seed-fulfill.timer`

- [ ] **Step 1: Implement seed_fulfill**

Create `<root>/dashboard/vision/seed_fulfill.py`:

```python
#!/usr/bin/env python3
"""Specter mode 2 — fulfill open seed_demand rows.

For people/faces/body paths: GENERATE via SD Forge.
For scrape paths (style, scenes): scrape route hands off to seed_fulfill_scrape (Phase 8).

Picks the oldest unfulfilled demand. Acquires GPU lease before generating.
On lease contention, requeues by leaving fulfilled_at NULL — picked up next tick.
"""
from __future__ import annotations

import argparse
import sys
import time

from dashboard.vision.db import DEFAULT_DB_PATH, connect, init_db
from dashboard.vision.gpu_lease import acquire as lease_acquire, release as lease_release
from dashboard.vision.ingest import observe
from dashboard.vision.prompt_map import prompt_for_path
from dashboard.vision.sd_forge import save_generated, txt2img

GENERATE_PREFIXES = (
    "/Catalogue/People",
    "/Catalogue/Faces",
    "/Catalogue/Body",
    "/Catalogue/Mood",
)
SCRAPE_FIRST_PREFIXES = (
    "/Catalogue/Scenes",
    "/Catalogue/Style",
)


def _strategy(path: str) -> str:
    if any(path.startswith(p) for p in GENERATE_PREFIXES):
        return "generate"
    if any(path.startswith(p) for p in SCRAPE_FIRST_PREFIXES):
        return "scrape"
    return "skip"


def fulfill_one_generate(con, demand_row) -> bool:
    path = demand_row["taxonomy_path"]
    needed = demand_row["needed"]
    try:
        prompt = prompt_for_path(path)
    except ValueError as e:
        con.execute("UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='unsupported' WHERE id=?",
                    (time.time(), demand_row["id"]))
        print(f"[skip] {path}: {e}")
        return False

    if not lease_acquire("rtx3070", "specter", ttl=600,
                         db_path=None, purpose=f"seed:{path}"):
        print(f"[lease-busy] {path} requeued")
        return False
    try:
        for n in range(needed):
            print(f"[gen {n+1}/{needed}] {path}")
            try:
                png = txt2img(prompt["prompt"], prompt["negative"])
                abs_path = save_generated(png, path)
                observe(abs_path, source="generated", origin_agent="specter")
            except Exception as e:
                print(f"[gen-fail] {path}: {e}")
                # Continue — partial fulfillment is fine.
        con.execute(
            "UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='generate' WHERE id=?",
            (time.time(), demand_row["id"]),
        )
        return True
    finally:
        lease_release("rtx3070", "specter")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--limit", type=int, default=2, help="max demands per run")
    args = ap.parse_args()

    init_db(args.db)
    con = connect(args.db)
    rows = con.execute(
        """SELECT id, taxonomy_path, needed, reason FROM seed_demand
            WHERE fulfilled_at IS NULL
            ORDER BY (reason='agent-request') DESC, requested_at ASC
            LIMIT ?""",
        (args.limit,),
    ).fetchall()

    fulfilled = 0
    for row in rows:
        strategy = _strategy(row["taxonomy_path"])
        if strategy == "skip":
            con.execute("UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='skip' WHERE id=?",
                        (time.time(), row["id"]))
            continue
        if strategy == "scrape":
            print(f"[defer-scrape] {row['taxonomy_path']} (Phase 8 will handle)")
            continue
        if fulfill_one_generate(con, row):
            fulfilled += 1

    print(f"[seed-fulfill] fulfilled={fulfilled} of {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Systemd unit + timer**

Create `<root>/baza-vision-seed-fulfill.service`:

```ini
[Unit]
Description=Baza Empire — Vision seed-fulfill (Specter mode 2, generator)
After=baza-vision-seed-scan.service

[Service]
Type=oneshot
User=switchhacker
WorkingDirectory=/home/switchhacker/baza-empire/agent-framework-v3
ExecStart=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python -m dashboard.vision.seed_fulfill --limit 2
Nice=10
StandardOutput=journal
StandardError=journal
TimeoutStartSec=2h
```

Create `<root>/baza-vision-seed-fulfill.timer`:

```ini
[Unit]
Description=Baza Empire — Vision seed-fulfill schedule (every 1h)

[Timer]
OnBootSec=15min
OnUnitActiveSec=1h
Persistent=true
Unit=baza-vision-seed-fulfill.service

[Install]
WantedBy=timers.target
```

Install:

```bash
sudo cp baza-vision-seed-fulfill.service /etc/systemd/system/
sudo cp baza-vision-seed-fulfill.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baza-vision-seed-fulfill.timer
```

- [ ] **Step 3: Smoke test (one-off, against SD)**

If SD Forge is up on :11435:

```bash
sudo systemctl start baza-vision-seed-fulfill.service
journalctl -u baza-vision-seed-fulfill.service -n 50 --no-pager
```

Expected: a `[gen 1/6] /Catalogue/...` line, then a generated PNG appears under `dashboard/artifacts/.vision-generated/<slug>/gen_*.png`. Subsequent run of the indexer classifies it.

- [ ] **Step 4: Commit**

```bash
git add dashboard/vision/seed_fulfill.py baza-vision-seed-fulfill.service baza-vision-seed-fulfill.timer
git commit -m "$(cat <<'EOF'
feat(vision): seed_fulfill — Specter mode 2 (generate via SD Forge)

Picks oldest open seed_demand. Generate strategy for /Catalogue/People,
Faces, Body, Mood. GPU-leased; on contention, requeues. Generated assets
land in .vision-generated/<slug>/ and enter the classify pipeline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 7 done.** Specter generates fills for under-represented person/face/body bins. Scrape paths (Scenes, Style) deferred to Phase 8.

---

## Phase 8 — Specter scrape mode (PR 8)

Goal: fulfill open `seed_demand` rows for `/Catalogue/Scenes/*` and `/Catalogue/Style/*` by hitting the curated CC0/CC-BY image APIs (Unsplash, Pexels, Pixabay, Wikimedia). No HTML scraping. Per-source rate limit. NSFW classified before insert.

### Task 8.1: Source allow-list

**Files:**
- Create: `<root>/dashboard/vision/scrape_sources.yaml`

- [ ] **Step 1: Create the config**

Create `<root>/dashboard/vision/scrape_sources.yaml`:

```yaml
# Curated CC0/CC-BY image search APIs. Specter only scrapes from this list.
# Add new sources only after vetting their license + ToS.

sources:
  - id: unsplash
    enabled: true
    base_url: "https://api.unsplash.com/search/photos"
    auth_header: "Authorization"
    auth_value_env: "UNSPLASH_ACCESS_KEY"   # set in .env.nuc; missing = disabled
    auth_prefix: "Client-ID "
    rate_limit_seconds: 2
    license: "Unsplash License (CC0-like, attribution preferred)"
    query_param: query
    page_param: page
    per_page_param: per_page
    per_page_default: 6
    response_path:                            # how to walk the JSON to get URLs
      list: results
      url:  urls.regular
      author: user.name

  - id: pexels
    enabled: true
    base_url: "https://api.pexels.com/v1/search"
    auth_header: "Authorization"
    auth_value_env: "PEXELS_API_KEY"
    auth_prefix: ""
    rate_limit_seconds: 2
    license: "Pexels License (CC0-like)"
    query_param: query
    page_param: page
    per_page_param: per_page
    per_page_default: 6
    response_path:
      list: photos
      url:  src.large
      author: photographer

  - id: pixabay
    enabled: true
    base_url: "https://pixabay.com/api/"
    auth_header: ""                           # uses query-string key=
    auth_value_env: "PIXABAY_API_KEY"
    auth_prefix: ""
    rate_limit_seconds: 2
    license: "Pixabay License (CC0)"
    query_param: q
    page_param: page
    per_page_param: per_page
    per_page_default: 6
    extra_params:
      key_env: PIXABAY_API_KEY                # appended as ?key=...
    response_path:
      list: hits
      url: largeImageURL
      author: user

  - id: wikimedia
    enabled: true
    base_url: "https://commons.wikimedia.org/w/api.php"
    auth_header: ""
    auth_value_env: ""                        # public, no key
    rate_limit_seconds: 2
    license: "Wikimedia Commons (mostly CC-BY-SA)"
    query_param: srsearch
    page_param: sroffset
    per_page_param: srlimit
    per_page_default: 6
    extra_params:
      static:
        action: query
        list: search
        srnamespace: 6
        format: json
    response_path:
      list: query.search
      url: __wikimedia_title__               # special: derive image URL from title
      author: ""

# Path → query string mapping (terminal segment of the taxonomy path).
queries:
  "/Catalogue/Scenes/Beach":   "empty beach landscape no people"
  "/Catalogue/Scenes/Studio":  "empty photography studio"
  "/Catalogue/Scenes/Outdoor": "outdoor nature scene no people"
  "/Catalogue/Scenes/Urban":   "empty urban street architecture"
  "/Catalogue/Scenes/Indoor":  "empty interior room"
  "/Catalogue/Style/Swimwear":   "swimwear clothing flat lay"
  "/Catalogue/Style/Formal":     "formal clothing flat lay"
  "/Catalogue/Style/Sportswear": "sportswear clothing flat lay"
  "/Catalogue/Style/Casual":     "casual clothing flat lay"
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/vision/scrape_sources.yaml
git commit -m "$(cat <<'EOF'
feat(vision): scrape source allow-list (CC0/CC-BY APIs only)

Unsplash, Pexels, Pixabay, Wikimedia. Per-source rate limit, env-var keys.
Terminal-path → query string mapping for /Catalogue/Scenes/* and /Style/*.
No HTML scraping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 8.2: Scraper + integrate into seed_fulfill

**Files:**
- Create: `<root>/dashboard/vision/scraper.py`
- Modify: `<root>/dashboard/vision/seed_fulfill.py`

- [ ] **Step 1: Implement the scraper**

Create `<root>/dashboard/vision/scraper.py`:

```python
"""Curated CC0/CC-BY image scraper.

Reads scrape_sources.yaml, picks an enabled source whose API key is set,
runs the search, downloads N images to artifacts/.vision-scraped/<source>/<date>/,
returns the local paths. Rate-limited per source.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

import yaml

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPE_DIR = os.path.join(DASHBOARD_DIR, "artifacts", ".vision-scraped")
SOURCES_PATH = os.path.join(os.path.dirname(__file__), "scrape_sources.yaml")
LAST_REQUEST_AT: dict[str, float] = {}


def _load_sources() -> dict:
    with open(SOURCES_PATH) as fh:
        return yaml.safe_load(fh)


def _walk_path(obj, dotted: str):
    """Walk obj following dotted keys; return None on miss."""
    cur = obj
    for k in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
        if cur is None:
            return None
    return cur


def _rate_limit(source_id: str, seconds: float):
    last = LAST_REQUEST_AT.get(source_id, 0)
    delta = time.time() - last
    if delta < seconds:
        time.sleep(seconds - delta)
    LAST_REQUEST_AT[source_id] = time.time()


def _enabled_sources(cfg: dict) -> list[dict]:
    out = []
    for src in cfg.get("sources", []):
        if not src.get("enabled"):
            continue
        env = src.get("auth_value_env") or ""
        if env and not os.getenv(env):
            continue   # api key missing — skip
        out.append(src)
    return out


def _query_for_path(cfg: dict, path: str) -> Optional[str]:
    return cfg.get("queries", {}).get(path)


def _http_get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        with open(dest, "wb") as fh:
            fh.write(resp.read())


def _safe_filename(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)[:100]


def scrape_for_path(taxonomy_path: str, *, count: int = 6) -> list[tuple[str, str]]:
    """Returns list of (abs_path, origin_url) for downloaded images."""
    cfg = _load_sources()
    query = _query_for_path(cfg, taxonomy_path)
    if not query:
        raise ValueError(f"no scrape query mapped for {taxonomy_path}")

    enabled = _enabled_sources(cfg)
    if not enabled:
        raise RuntimeError("no enabled scrape sources have API keys configured")

    src = enabled[0]   # pick the first; round-robin can wait for v2
    _rate_limit(src["id"], src.get("rate_limit_seconds", 2))

    # Compose URL
    params = {
        src["query_param"]: query,
        src["per_page_param"]: min(count, src.get("per_page_default", 6)),
    }
    extra = src.get("extra_params") or {}
    for k, v in (extra.get("static") or {}).items():
        params[k] = v
    if extra.get("key_env"):
        params["key"] = os.getenv(extra["key_env"], "")

    url = src["base_url"] + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "BazaSpecter/1.0 (+vision-seeder)"}
    if src.get("auth_header"):
        env = src.get("auth_value_env") or ""
        prefix = src.get("auth_prefix") or ""
        headers[src["auth_header"]] = prefix + os.getenv(env, "")

    data = _http_get_json(url, headers)
    items = _walk_path(data, src["response_path"]["list"]) or []

    today = time.strftime("%Y-%m-%d")
    out_dir = os.path.join(SCRAPE_DIR, src["id"], today)
    results: list[tuple[str, str]] = []
    for n, item in enumerate(items[:count]):
        url_path = src["response_path"]["url"]
        if url_path == "__wikimedia_title__":
            # Wikimedia returns titles; need a second API hop to get the actual URL.
            # Skipped for v1 — Wikimedia stays disabled in practice unless the user
            # adds a per-title resolution pass.
            continue
        img_url = _walk_path(item, url_path)
        if not img_url:
            continue
        ext = ".jpg"
        if ".png" in img_url.lower():
            ext = ".png"
        dest = os.path.join(out_dir, _safe_filename(f"{src['id']}_{n}{ext}"))
        try:
            _download(img_url, dest)
            results.append((dest, img_url))
            _rate_limit(src["id"], src.get("rate_limit_seconds", 2))
        except Exception as e:
            print(f"[scrape-fail] {img_url}: {e}")
    return results
```

- [ ] **Step 2: Add `pyyaml` import note**

`pyyaml` is already in `requirements.txt` (line: `pyyaml==6.0.1`). No change needed.

- [ ] **Step 3: Wire scraper into seed_fulfill**

In `<root>/dashboard/vision/seed_fulfill.py`, find `def main():`. Find the `if strategy == "scrape":` branch and replace with:

```python
        if strategy == "scrape":
            try:
                from dashboard.vision.scraper import scrape_for_path
                pairs = scrape_for_path(row["taxonomy_path"], count=row["needed"])
                for abs_path, origin_url in pairs:
                    observe(abs_path, source="scraped", origin_agent="specter",
                            origin_url=origin_url)
                con.execute(
                    "UPDATE seed_demand SET fulfilled_at=?, fulfilled_by='scrape' WHERE id=?",
                    (time.time(), row["id"]),
                )
                fulfilled += 1
            except Exception as e:
                print(f"[scrape-fail] {row['taxonomy_path']}: {e}")
            continue
```

- [ ] **Step 4: Smoke test**

Set at least one API key (e.g. Pixabay's free key — register at pixabay.com/api/docs):

```bash
echo "PIXABAY_API_KEY=your_key_here" | sudo tee -a /home/switchhacker/baza-empire/.env.nuc
```

Then trigger fulfill:

```bash
venv/bin/python -m dashboard.vision.seed_fulfill --limit 1
```

Expected: a beach image lands in `dashboard/artifacts/.vision-scraped/pixabay/<date>/`. Run the indexer to classify:

```bash
venv/bin/python dashboard/vision_indexer.py --limit 5 --verbose
```

Expected: classification ok, image appears in `/Catalogue/Scenes/Beach` after browser refresh.

- [ ] **Step 5: Commit**

```bash
git add dashboard/vision/scraper.py dashboard/vision/seed_fulfill.py
git commit -m "$(cat <<'EOF'
feat(vision): scrape mode — CC0/CC-BY API integration

scraper.scrape_for_path() honors the allow-list, rate-limits per source,
downloads to .vision-scraped/<source>/<date>/. seed_fulfill picks scrape
strategy for /Catalogue/Scenes/* and /Style/*. Wikimedia disabled in
practice for v1 (needs second-hop title→URL resolver).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Phase 8 done.** Full v1 catalogue engine ships: ingest → classify → crop → browse → search → seed-scan → seed-fulfill (generate or scrape).

---

## Final smoke run

After all 8 phases land:

- [ ] **Step 1: Run the full test suite**

```bash
venv/bin/python -m pytest tests/vision -v
```

Expected: all green (≈ 25 tests).

- [ ] **Step 2: End-to-end browser test**

1. Restart dashboard: `sudo systemctl restart baza-dashboard.service`
2. Visit `/datahub/private` — passphrase prompt (or auto-redirect to /vision if already unlocked).
3. Unlock — should land on `/vision`.
4. Confirm tree renders, sidebar stats show.
5. Click `/Catalogue/People/Female/Blonde` — empty grid + "Specter: fill this folder now" CTA.
6. Click the CTA — toast: "Specter queued. ETA ~10 min."
7. Manually fire fulfill: `sudo systemctl start baza-vision-seed-fulfill.service`
8. Wait, refresh — generated images appear, classified, browseable.
9. Search `blonde` — matches return.
10. Click any image — modal shows attributes.
11. Toggle theme via the sun/moon button — dashboard re-tints.

- [ ] **Step 3: Final commit if anything tweaked, then push the branch**

```bash
git status
# review
git push origin <branch-name>
```

---

## Rollback

If anything in Phase 3+ misbehaves:

```bash
sudo systemctl disable --now baza-vision-indexer.timer baza-vision-seed-scan.timer baza-vision-seed-fulfill.timer
sudo systemctl daemon-reload
git revert <last-N-commits>           # or revert specific PRs
rm -f dashboard/vision.db dashboard/vision.db-wal dashboard/vision.db-shm
rm -rf dashboard/artifacts/.vision-generated dashboard/artifacts/.vision-scraped dashboard/artifacts/.vision-crops
sudo systemctl restart baza-dashboard.service
```

`.private-inbound/` and `image_indexer.py`'s `image_captions.db` are untouched by this rollback.

---

## Out of scope (explicit, not a TODO)

- IP-Adapter / ControlNet conditioning (v2 roadmap)
- LoRA training corpus (v3 roadmap)
- Body-part detection beyond face (hand/foot/torso) — easy to add by extending `cropper.py` with a YOLO model or qwen-bbox prompt
- Wikimedia title→URL resolution
- Mobile-responsive Vision UI
- Telegram bot commands to query Vision from chat
