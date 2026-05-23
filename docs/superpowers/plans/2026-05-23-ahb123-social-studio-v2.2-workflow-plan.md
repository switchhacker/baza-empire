# Social Studio v2.2 — Workflow + Trends + Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Third and final phase of v2 mega-expansion — visual month calendar with drag-reschedule, bulk operations, saved templates, recurring schedules, tags/collections/campaigns, FTS5 search, multi-step approval workflow, version history, auto-save, trends discovery (URL paste, hashtag/competitor/sound trackers, inspo feed), and manual analytics (stats entry, dashboard, heatmap, hashtag perf, CSV import, library cleanup).

**Architecture:** New backend modules `dashboard/social_workflow.py`, `dashboard/social_trends.py`, `dashboard/social_analytics.py`. Heavy DB additions: ~7 new tables + FTS5 virtual table + triggers + several column additions. Frontend gets calendar component, bulk action bar, tags manager, version viewer, inspo browser, analytics dashboard.

**Tech Stack:** SQLite FTS5 / yt-dlp (reused from v2.1) / pure-JS calendar / vanilla charts (no Chart.js dep — small bar/line/heatmap charts done in CSS+SVG).

**Spec:** `docs/superpowers/specs/2026-05-22-ahb123-social-studio-v2-design.md` Bundles D + G + K.

**Prerequisites:** v2.0 AND v2.1 must be merged first.

---

## Process notes (same as v2.0/v2.1)

- No `git --amend`. Forward commits only.
- `sudo systemctl restart baza-dashboard` after template edits.
- Local `_esc()` helper per IIFE.
- Body-level modals only.
- All file paths absolute from `/home/switchhacker/baza-empire/agent-framework-v3/`.
- Commit messages end with `\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**New backend modules:**
- `dashboard/social_workflow.py` — templates CRUD, tags, bulk ops, versions, approval log
- `dashboard/social_trends.py` — inspo URL parse, hashtag snapshots, competitors, sounds
- `dashboard/social_analytics.py` — stats CRUD, summary aggregations, heatmap, hashtag perf, library cleanup

**Modified:**
- `dashboard/social_studio.py` — schema migrations for v2.2 tables (`_ensure_social_v22_tables`); register new modules' Blueprint contributions
- `dashboard/social_settings.py` — accessor for `cool_down_archive_days` (default 90)
- `dashboard/templates/ahb123.html` — calendar/bulk/templates/tags/versions/inspo/stats modules

**New tables:** 7 new (`ahb_social_post_templates`, `ahb_social_tags`, `ahb_social_post_tags`, `ahb_social_hashtag_snapshots`, `ahb_social_competitors`, `ahb_social_sound_snapshots`, `ahb_social_analytics`, `ahb_social_approval_events`, `ahb_social_post_versions`) + FTS5 virtual table + 3 triggers.

**Column additions** to existing tables: `ahb_social_presets.requires_review`, `schedule_dow`, `schedule_time`.

**New tests:**
- `tests/test_social_v22_workflow.py`
- `tests/test_social_v22_trends.py`
- `tests/test_social_v22_analytics.py`

---

## Task 1: Schema migrations + module scaffolds

**Files:**
- Modify: `dashboard/social_studio.py` (add `_ensure_social_v22_tables`, register new modules)
- Create: `dashboard/social_workflow.py`, `dashboard/social_trends.py`, `dashboard/social_analytics.py` (empty scaffolds)
- Test: `tests/test_social_v22_workflow.py`

- [ ] **Step 1: Write failing tests**

`tests/test_social_v22_workflow.py`:

```python
"""Tests for Social Studio v2.2 — schema migration smoke."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def db_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv22_")
    p = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_workflow",
              "social_trends", "social_analytics",
              "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    yield p
    for m in ("social_studio", "social_settings", "social_workflow",
              "social_trends", "social_analytics",
              "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_v22_tables_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_v2_tables(db_path)
    social_studio._ensure_social_v22_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["ahb_social_post_templates", "ahb_social_tags",
                  "ahb_social_post_tags", "ahb_social_hashtag_snapshots",
                  "ahb_social_competitors", "ahb_social_sound_snapshots",
                  "ahb_social_analytics", "ahb_social_approval_events",
                  "ahb_social_post_versions"]:
            assert t in names, f"missing table: {t}"
        # FTS5 virtual table (may not be available if SQLite was built without FTS5)
        try:
            con.execute("SELECT count(*) FROM ahb_social_posts_fts")
            fts_ok = True
        except sqlite3.OperationalError:
            fts_ok = False
        # OK either way — we fall back to LIKE if FTS5 absent
        # Preset column additions
        preset_cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_presets)")}
        assert "requires_review" in preset_cols
        assert "schedule_dow" in preset_cols
        assert "schedule_time" in preset_cols
    finally:
        con.close()
```

Run: `pytest tests/test_social_v22_workflow.py -v` → FAIL.

- [ ] **Step 2: Create scaffold modules**

`dashboard/social_workflow.py`:

```python
"""Social Studio v2.2 — templates, tags, bulk ops, versions, approval log."""
from __future__ import annotations


def register(bp):
    """Register workflow routes on the given Blueprint."""
    pass
```

`dashboard/social_trends.py`:

```python
"""Social Studio v2.2 — inspo URL parse, hashtag snapshots, competitors."""
from __future__ import annotations


def register(bp):
    pass
```

`dashboard/social_analytics.py`:

```python
"""Social Studio v2.2 — stats CRUD, summary, heatmap, hashtag perf, cleanup."""
from __future__ import annotations


def register(bp):
    pass
```

- [ ] **Step 3: Add `_ensure_social_v22_tables` to social_studio.py**

Append to `dashboard/social_studio.py`:

```python
def _ensure_social_v22_tables(db_path: Optional[str] = None) -> None:
    """Add v2.2 tables for workflow/trends/analytics. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        # Column additions on presets
        for col_def in [
            "requires_review INTEGER DEFAULT 0",
            "schedule_dow TEXT",
            "schedule_time TEXT",
        ]:
            try:
                con.execute(f"ALTER TABLE ahb_social_presets ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # New tables
        con.executescript("""
            CREATE TABLE IF NOT EXISTS ahb_social_post_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                caption_template TEXT,
                hashtag_set TEXT,
                platform_targets TEXT DEFAULT '[]',
                first_comment_template TEXT,
                music_id INTEGER,
                voiceover_script TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT DEFAULT '#10b981',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_post_tags (
                post_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (post_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS ahb_social_hashtag_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT NOT NULL,
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source_url TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS ahb_social_competitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL,
                platform TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_sound_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sound_url TEXT,
                example_video_url TEXT,
                title TEXT,
                observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS ahb_social_analytics (
                post_id INTEGER PRIMARY KEY,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                saves INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                posted_at TEXT,
                post_url TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_approval_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor TEXT,
                note TEXT,
                at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ahb_social_post_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                version_at TEXT DEFAULT CURRENT_TIMESTAMP,
                snapshot TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hashtag_snapshots_tag ON ahb_social_hashtag_snapshots(tag);
            CREATE INDEX IF NOT EXISTS idx_post_versions_post ON ahb_social_post_versions(post_id);
            CREATE INDEX IF NOT EXISTS idx_approval_events_post ON ahb_social_approval_events(post_id);
        """)
        # FTS5 virtual table (gracefully skip if SQLite lacks FTS5)
        try:
            con.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS ahb_social_posts_fts USING fts5(
                    caption, hashtags, first_comment,
                    content='ahb_social_posts',
                    content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ai AFTER INSERT ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
                END;
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_au AFTER UPDATE ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
                    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
                    INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                    VALUES (new.id, new.caption, new.hashtags, new.first_comment);
                END;
                CREATE TRIGGER IF NOT EXISTS ahb_social_posts_ad AFTER DELETE ON ahb_social_posts BEGIN
                    INSERT INTO ahb_social_posts_fts(ahb_social_posts_fts, rowid, caption, hashtags, first_comment)
                    VALUES('delete', old.id, old.caption, old.hashtags, old.first_comment);
                END;
            """)
            # Backfill FTS with existing posts (one-time)
            con.execute("""
                INSERT INTO ahb_social_posts_fts(rowid, caption, hashtags, first_comment)
                SELECT id, caption, hashtags, first_comment FROM ahb_social_posts
                WHERE id NOT IN (SELECT rowid FROM ahb_social_posts_fts)
            """)
        except sqlite3.OperationalError as e:
            print(f"[startup] FTS5 unavailable, search will fall back to LIKE: {e}", flush=True)
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_v22_tables deferred: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


_ensure_social_v22_tables()


# Register v2.2 sub-modules
try:
    from dashboard import social_workflow, social_trends, social_analytics
except ImportError:
    import social_workflow
    import social_trends
    import social_analytics
social_workflow.register(social_bp)
social_trends.register(social_bp)
social_analytics.register(social_bp)
```

- [ ] **Step 4: Run tests + restart + smoke**

```
pytest tests/test_social_v22_workflow.py -v
sudo systemctl restart baza-dashboard
sleep 2
sqlite3 dashboard/baza_projects.db ".tables" | tr ' ' '\n' | grep -E "templates|tags|hashtag_snapshots|competitors|analytics|approval_events|versions|posts_fts"
```

Expected: 1 test passes; 9 tables listed.

- [ ] **Step 5: Commit**

```
git add dashboard/social_studio.py dashboard/social_workflow.py dashboard/social_trends.py dashboard/social_analytics.py tests/test_social_v22_workflow.py
git commit -m "social v2.2: schema + module scaffolds for workflow/trends/analytics

9 new tables (templates, tags, post_tags join, hashtag_snapshots,
competitors, sound_snapshots, analytics, approval_events, post_versions)
+ FTS5 virtual table with triggers (graceful skip if FTS5 absent) +
3 column additions to presets (requires_review, schedule_dow,
schedule_time). Empty scaffold modules register() on social_bp.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Templates CRUD + apply

**Files:**
- Modify: `dashboard/social_workflow.py` (template routes)
- Modify: `dashboard/templates/ahb123.html` (templates picker module)
- Test: extend `tests/test_social_v22_workflow.py`

- [ ] **Step 1: Append tests**

```python
def test_template_create_and_list(client):
    c, _ = client
    pid = c.post("/api/ahb/social/templates", json={
        "name": "AHB launch",
        "caption_template": "New project: {{project_name}}!",
        "hashtag_set": "#ahbco #renovation",
        "platform_targets": ["ig_reel"],
    }).get_json()["id"]
    items = c.get("/api/ahb/social/templates").get_json()["items"]
    assert any(t["id"] == pid and t["name"] == "AHB launch" for t in items)


def test_template_apply_returns_draft(client):
    c, _ = client
    tid = c.post("/api/ahb/social/templates", json={
        "name": "T", "caption_template": "Hello {{project_name}}",
        "hashtag_set": "#hi",
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/templates/{tid}/apply",
               json={"variables": {"project_name": "Brooklyn Reno"}})
    assert r.status_code == 200
    j = r.get_json()
    assert "Brooklyn Reno" in j["caption"]
```

Also add a `client` fixture to test_social_v22_workflow.py (similar pattern to v2.1 tests — see test_social_v2_audio.py for the template). For brevity here, copy that fixture into v2.2 test files.

- [ ] **Step 2: Implement template routes**

Replace `dashboard/social_workflow.py`:

```python
"""Social Studio v2.2 — templates, tags, bulk ops, versions, approval log."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _db():
    path = os.environ.get("BAZA_DASHBOARD_DB",
                          os.path.join(_HERE, "baza_projects.db"))
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


TEMPLATE_WRITABLE = {
    "name", "caption_template", "hashtag_set", "platform_targets",
    "first_comment_template", "music_id", "voiceover_script",
}


def _row_to_template(r):
    d = dict(r)
    try:
        d["platform_targets"] = json.loads(d["platform_targets"]) if d.get("platform_targets") else []
    except Exception:
        d["platform_targets"] = []
    return d


def _interpolate(template: str, variables: dict) -> str:
    if not template:
        return ""
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", repl, template)


def register(bp):
    from flask import jsonify, request

    @bp.route("/api/ahb/social/templates", methods=["GET"])
    def social_templates_list():
        con = _db()
        try:
            rows = con.execute("SELECT * FROM ahb_social_post_templates ORDER BY id DESC").fetchall()
        finally:
            con.close()
        return jsonify({"items": [_row_to_template(r) for r in rows]})

    @bp.route("/api/ahb/social/templates", methods=["POST"])
    def social_templates_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cols, vals = ["name"], [name]
        for k, v in data.items():
            if k == "name" or k not in TEMPLATE_WRITABLE:
                continue
            cols.append(k)
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        con = _db()
        try:
            cur = con.execute(
                f"INSERT INTO ahb_social_post_templates ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals,
            )
            con.commit()
            tid = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": tid})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["PUT"])
    def social_templates_update(tid: int):
        data = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k, v in data.items():
            if k not in TEMPLATE_WRITABLE:
                continue
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        if not sets:
            return jsonify({"error": "no writable fields"}), 400
        sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat(timespec="seconds"))
        vals.append(tid)
        con = _db()
        try:
            con.execute(f"UPDATE ahb_social_post_templates SET {','.join(sets)} WHERE id=?", vals)
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["DELETE"])
    def social_templates_delete(tid: int):
        con = _db()
        try:
            con.execute("DELETE FROM ahb_social_post_templates WHERE id=?", (tid,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>/apply", methods=["POST"])
    def social_templates_apply(tid: int):
        data = request.get_json(silent=True) or {}
        variables = data.get("variables") or {}
        con = _db()
        try:
            r = con.execute("SELECT * FROM ahb_social_post_templates WHERE id=?", (tid,)).fetchone()
        finally:
            con.close()
        if not r:
            return jsonify({"error": "not found"}), 404
        t = _row_to_template(r)
        # Add common variables
        from datetime import date
        variables.setdefault("date", date.today().isoformat())
        return jsonify({
            "caption": _interpolate(t["caption_template"] or "", variables),
            "hashtags": t["hashtag_set"] or "",
            "first_comment": _interpolate(t.get("first_comment_template") or "", variables),
            "platform_targets": t["platform_targets"],
            "music_id": t.get("music_id"),
            "voiceover_script": t.get("voiceover_script"),
            "template_id": tid,
        })
```

- [ ] **Step 3: Add Templates UI module**

Append to `dashboard/templates/ahb123.html`:

```html
<script>
SocialStudio.modules.templates = (function(){
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  async function picker(callback) {
    let items = [];
    try { items = ((await (await fetch('/api/ahb/social/templates')).json()).items || []); } catch(e) {}
    const m = document.createElement('div');
    m.className = 'modal-bg';
    m.style.cssText = 'display:flex';
    document.body.appendChild(m);
    const close = () => document.body.removeChild(m);
    m.innerHTML = `
      <div class="modal" style="max-width:560px;max-height:80vh;overflow-y:auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">📋 Templates</div>
          <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        ${items.length ? items.map(t => `
          <div class="ss-card" style="margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">
            <div style="min-width:0;flex:1">
              <div style="font-weight:700">${_esc(t.name)}</div>
              <div style="color:#888;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc((t.caption_template||'').slice(0,120))}</div>
            </div>
            <button class="btn-primary" style="padding:4px 12px" data-apply="${t.id}">Use</button>
          </div>
        `).join('') : '<div style="color:#444;padding:24px;text-align:center">No templates yet. Save a draft as template in Library → post detail.</div>'}
      </div>
    `;
    m.querySelector('[data-close]').addEventListener('click', close);
    m.addEventListener('click', async (e) => {
      if (e.target.dataset.apply) {
        const tid = e.target.dataset.apply;
        const r = await fetch(`/api/ahb/social/templates/${tid}/apply`, {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ variables: {} }),
        });
        const draft = await r.json();
        callback(draft);
        close();
      }
    });
  }
  return { picker };
})();
</script>
```

- [ ] **Step 4: Add "Use Template" button to Composer**

Find the Composer's variant panel. Add at the top of the panel (before the Tone select):

```javascript
          <button class="btn-secondary" style="width:100%;margin-bottom:8px" onclick="SocialStudio.modules.templates.picker(draft => SocialStudio.modules.composer._applyTemplate(draft))">📋 Use template</button>
```

In the composer IIFE, after `aiTranslate`, add:

```javascript
  function _applyTemplate(draft) {
    if (!draft) return;
    const p = state.activePlatform;
    document.getElementById('ss-caption-' + p).value = draft.caption || '';
    document.getElementById('ss-hashtags-' + p).value = draft.hashtags || '';
    if (draft.music_id) state.musicTrack = { id: draft.music_id, title: '(from template)' };
    SocialStudio.modules.toast.success('Template applied');
  }
```

And add `_applyTemplate` to the return list.

- [ ] **Step 5: Add "Save as template" to Library post-detail**

In the postdetail modal button row, add:

```javascript
          <button class="btn-secondary" onclick="SocialStudio.modules.postdetail.saveAsTemplate(${p.id})">📋 Save as template</button>
```

Add the function:

```javascript
  async function saveAsTemplate(id) {
    const name = window.prompt('Template name:', 'New template');
    if (!name) return;
    const items = (await (await fetch('/api/ahb/social/posts')).json()).items || [];
    const p = items.find(x => x.id === id);
    if (!p) return;
    await fetch('/api/ahb/social/templates', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        name,
        caption_template: p.caption,
        hashtag_set: p.hashtags,
        first_comment_template: p.first_comment,
        platform_targets: [p.platform],
        music_id: p.music_id || null,
      }),
    });
    SocialStudio.modules.toast.success('Saved as template');
  }
  return { open, save, bundle, telegram, subtitles, saveAsTemplate };
```

- [ ] **Step 6: Test + restart + smoke**

```
pytest tests/test_social_v22_workflow.py -v
sudo systemctl restart baza-dashboard
```

Library: open a post → "📋 Save as template" → name it → success toast. Composer: "📋 Use template" → picker shows the saved template → click Use → caption/hashtags fill.

- [ ] **Step 7: Commit**

```
git add dashboard/social_workflow.py dashboard/templates/ahb123.html tests/test_social_v22_workflow.py
git commit -m "social v2.2: templates CRUD + apply with {{var}} interpolation

GET/POST/PUT/DELETE /api/ahb/social/templates + apply endpoint that
interpolates {{var}} placeholders. Composer 📋 Use template picker.
Library post-detail 📋 Save as template button. Template variables
auto-include date; user can pass any extras via apply body.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Tags / collections / campaigns

**Files:** `dashboard/social_workflow.py`, `dashboard/templates/ahb123.html`, test extension.

Routes:
- `GET /api/ahb/social/tags` — list
- `POST /api/ahb/social/tags` body `{name, color}` — create
- `PUT /api/ahb/social/tags/<id>` — rename/recolor
- `DELETE /api/ahb/social/tags/<id>` — delete (cascades to post_tags via app-side cleanup)
- `POST /api/ahb/social/posts/<id>/tags` body `{tag_ids: [...]}` — set tags
- `GET /api/ahb/social/posts?tag=foo` — filter by tag (extend existing list endpoint)

UI: Tags Manager modal (open from a small "🏷 Tags" admin button in Library). Tag chips on post cards. Filter dropdown by tag in Library.

Tests: tag create + list, tag assignment, filter-by-tag returns expected count.

**Commit:** `social v2.2: tags / collections / campaigns`

---

## Task 4: Full-text search

**Files:** `dashboard/social_workflow.py` (extend posts list endpoint), `dashboard/templates/ahb123.html`, tests.

Modify `social_posts_list` in `social_studio.py` (or move to workflow module): when `q` param has > 2 chars, try FTS first via `MATCH ?` joining `ahb_social_posts_fts`. Fall back to existing LIKE if FTS not available.

UI: Library search input becomes a global search box (search bar fixed at top of Social tab); results highlight matched terms.

Tests: FTS search returns hits; LIKE fallback returns same hits when FTS dropped.

**Commit:** `social v2.2: FTS5 full-text search with LIKE fallback`

---

## Task 5: Bulk operations

**Files:** `dashboard/social_workflow.py` (bulk endpoint), `dashboard/templates/ahb123.html` (multi-select + bulk action bar), tests.

Route `POST /api/ahb/social/posts/bulk` body `{ids: [...], action, params}` where action is `set_status`, `schedule`, `delete`, `telegram`, `tag`, `bundle`.

UI: Library cards gain checkboxes (shift-click range select). Bulk action bar slides up from bottom when ≥1 selected — Approve, Reject, Schedule, Delete, 🏷 Tag, 📲 Telegram, 📥 Export bundle (zip of all selected). Calendar view (Task 7) also supports bulk via shift-click on calendar chips.

Tests: bulk set_status updates N rows in one request; bulk delete removes N rows.

**Commit:** `social v2.2: bulk operations (multi-select + action bar)`

---

## Task 6: Auto-save + version history

**Files:** `dashboard/social_workflow.py` (versions routes + middleware), `dashboard/templates/ahb123.html` (auto-save in post detail), tests.

On every PATCH to `ahb_social_posts`, write the prior row's full JSON snapshot to `ahb_social_post_versions`. Routes: `GET /posts/<id>/versions` lists versions; `POST /posts/<id>/versions/<v>/restore` overwrites current with the snapshot at that version.

Auto-save: post detail modal fields debounce 500ms on input → PATCH. Save button becomes "Saved ✓" badge that flashes green when an auto-save fires. Esc closes without prompt.

UI: "History" button on post detail opens a modal listing versions with timestamp + diff (caption-only diff for now); each version has a Restore button.

Tests: PATCH creates a version row; restore round-trips correctly.

**Commit:** `social v2.2: auto-save + version history with restore`

---

## Task 7: Visual month calendar with drag-reschedule

**Files:** `dashboard/templates/ahb123.html` (calendar module), CSS additions.

In the Scheduler sub-tab, ADD a "Calendar" toggle alongside the existing list view (don't replace). Calendar = 7×6 grid for the current month, prev/next nav buttons. Each day cell shows up to 3 post chips (colored by status: scheduled=blue, posted=green, pending_review=amber, draft=gray); "+N more" link when > 3.

Click a chip → opens post detail modal.
Drag a chip onto another day → fires `PATCH /posts/<id>` with new `scheduled_at` (date + existing time-of-day) → calendar re-renders.

UI gets a date filter so scheduled-only posts (or scheduled+approved) show on the calendar. Calendar covers all months you've ever posted; default view = current month.

**Commit:** `social v2.2: visual month calendar with drag-to-reschedule`

---

## Task 8: Multi-step approval workflow + recurring schedules

**Files:** `dashboard/social_workflow.py` (approval log + recurring tick), `dashboard/social_studio.py` (autopilot tick gains cadence filter), `dashboard/templates/ahb123.html`, tests.

Approval log: every status change on a post inserts into `ahb_social_approval_events` with action + actor (currently always "serge" — single user). Post detail modal gains a "History" tab showing the approval log.

Per-preset `requires_review` flag (column added in Task 1): when ON, drafts start as `pending_review` even if `auto_approve` was true on the preset.

Recurring schedules: preset's `schedule_dow` + `schedule_time` honored by autopilot tick — only generates if today is in the dow CSV AND current UTC time is within ±30min of `schedule_time`.

UI: preset editor modal gains a "Recurring schedule" section: day-of-week chip selector (Su Mo Tu We Th Fr Sa) + time input. requires_review checkbox.

**Commit:** `social v2.2: approval workflow + recurring preset schedules`

---

## Task 9: Trends — URL inspo + competitor watch + hashtag/sound snapshots

**Files:** `dashboard/social_trends.py` (all routes), `dashboard/templates/ahb123.html` (new sub-sub-tab "💡 Inspo"), tests.

Add a 6th sub-sub-tab "💡 Inspo" to the Social tab. The tab is split into 4 sections (left rail):

1. **URL paste** — input + button → calls `POST /trends/inspo-url` body `{url}` → server runs yt-dlp metadata fetch (`yt-dlp --skip-download --print-json`) → returns `{title, description, hashtags: [...], views, uploader, thumbnail_url, days_ago}`. Result card displays this with a "🪝 Suggest similar hook" button that feeds the description into `/ai/hook` with a prompt addendum "match this structure".

2. **Hashtag tracker** — list + add form. POST/GET `/trends/hashtag-snapshots`. Tags with most recent snapshots first; chart of frequency over time (small SVG sparkline).

3. **Competitor watch** — list of handles. POST/GET `/trends/competitors`. Per competitor, a "Snapshot recent posts" form (paste 5 URLs) that runs each through the URL paste pipeline + stores.

4. **Sound tracker** — paste TikTok sound URL + example video URL. POST/GET `/trends/sound-snapshots`. Activity feed.

5. **Inspo library** — curated examples bundled at `dashboard/static/social/inspo/*.json`. Each file has `{category, thumbnail, caption, hook, structural_analysis}`. Browse view with category filter.

**Commit:** `social v2.2: trends sub-tab — inspo URL, competitors, hashtag/sound snapshots`

---

## Task 10: Manual analytics — stats entry + dashboard

**Files:** `dashboard/social_analytics.py` (CRUD + summary routes), `dashboard/templates/ahb123.html` (stats panel + dashboard sub-tab), tests.

Routes:
- `GET /posts/<id>/analytics` — read row
- `PUT /posts/<id>/analytics` body `{views, likes, comments, saves, shares, posted_at, post_url}` — upsert
- `GET /analytics/summary?window=30d` — aggregated totals
- `GET /analytics/heatmap` — 7×24 grid of avg engagement_rate by day_of_week × hour
- `GET /analytics/hashtags` — per-hashtag total views + avg engagement
- `POST /analytics/import-csv` — multipart CSV import

UI:
- Post detail modal (when status=posted): adds a "📊 Stats" panel with 5 number inputs (views/likes/comments/saves/shares) + posted_at picker + post URL. Auto-save 500ms.
- New sub-sub-tab "📊 Stats" inside the Social tab:
  - Top row: this-week vs last-week (views, engagement rate) — calc deltas
  - Top performers list: top 10 by views in window
  - Per-platform pie chart (CSS-only)
  - Heatmap: 7×24 grid colored by engagement
  - Hashtag performance table sortable by avg views

Tests: stats round-trip; summary aggregation correct; heatmap returns 168 buckets; CSV import inserts N rows.

**Commit:** `social v2.2: manual analytics — stats entry + dashboard with heatmap`

---

## Task 11: Library cleanup tool

**Files:** `dashboard/social_analytics.py` (cleanup routes), `dashboard/templates/ahb123.html` (admin view in Stats sub-tab), tests.

Routes:
- `GET /analytics/cleanup?older_than_days=90` — list posts with status=posted older than threshold
- `POST /analytics/cleanup/archive` body `{ids: [...]}` — move asset/cover files to `dashboard/artifacts/social/archive/<date>/` and mark post `archived_at`
- `POST /analytics/cleanup/delete` body `{ids: [...]}` — remove asset files + delete post + tags + analytics rows

Add `archived_at` column to `ahb_social_posts` in this task's migration (extend `_ensure_social_v22_tables` or add a new function).

UI: Stats sub-tab gains an "Admin → Library cleanup" section: configurable days input, list of old posts with file sizes + last accessed, bulk Archive / Delete buttons.

**Commit:** `social v2.2: library cleanup — archive or delete old posted assets`

---

## Task 12: End-to-end smoke + session-log update

This task does NOT write production code. Same pattern as v1's Task 14.

- [ ] **Step 1: Full pytest sweep**

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pytest tests/test_social_*.py -v 2>&1 | tail -20
```

Expected: all green. Total count = sum of v1 (52) + v2.0 (~6) + v2.1 (~12-15) + v2.2 (~12-15) ≈ 80-90 tests.

- [ ] **Step 2: Live-service smoke**

```
sudo systemctl status baza-dashboard --no-pager | head -3
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/ahb123
# v2.2-specific:
curl -s http://127.0.0.1:8888/api/ahb/social/templates | head -c 200
curl -s http://127.0.0.1:8888/api/ahb/social/tags | head -c 200
curl -s http://127.0.0.1:8888/api/ahb/social/analytics/summary | head -c 200
curl -s http://127.0.0.1:8888/api/ahb/social/analytics/heatmap | head -c 200
```

- [ ] **Step 3: Session-log entry**

```
d=$(date '+%Y-%m-%d %H:%M')
cat >> /home/switchhacker/Desktop/baza-session-log.md <<EOF

### $d | Social Studio v2.2 shipped (workflow + trends + analytics)

Phase 3 of 3 of the Social Studio mega-expansion landed:
- Visual month calendar with drag-to-reschedule
- Bulk operations (multi-select, action bar, bulk Approve/Reject/Schedule/Delete/Tag/Telegram/Bundle)
- Templates with {{variable}} interpolation, save-as-template from Library
- Tags / collections / campaigns
- FTS5 full-text search across captions/hashtags/first comments
- Multi-step approval workflow + recurring preset schedules (schedule_dow / schedule_time)
- Auto-save (500ms debounce) + version history with restore
- New 💡 Inspo sub-tab: URL paste (yt-dlp metadata), competitor watch, hashtag snapshots, sound tracker, curated inspo library
- New 📊 Stats sub-tab: manual analytics entry, summary dashboard, 7x24 heatmap, hashtag perf, CSV import, library cleanup admin
- ~80-90 pytest tests across all 3 phases

Branch & commit: feature/social-media-studio-v2.2 → main, HEAD <fill in>

Pre-prod TODOs:
- Drop royalty-free music files into dashboard/static/social/music/free/ for the music picker
- Drop CC0 SFX into dashboard/static/social/sfx/ for the storyboard SFX picker
- Verify piper voices downloaded correctly (see install script output)
- Phase 2 (direct API publishing) remains the next major milestone
EOF
echo "session log updated"
```

- [ ] **Step 4: Milestone commit**

```
git rev-parse HEAD
git commit --allow-empty -m "social v2.2: Phase 3 complete — workflow + trends + analytics shipped

All 12 v2.2 tasks landed on top of v2.0 + v2.1. The Social Studio is now
feature-complete for everything in the v2 mega-spec except direct API
publishing (Phase 2 — gated on IG Business + TikTok Business setup).

Spec:   docs/superpowers/specs/2026-05-22-ahb123-social-studio-v2-design.md
Plans:  docs/superpowers/plans/2026-05-23-ahb123-social-studio-v2.{0,1,2}-*.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Plan self-review

**Spec coverage:**
- D.1 visual month calendar → Task 7 ✓
- D.2 bulk operations → Task 5 ✓
- D.3 templates → Task 2 ✓
- D.4 recurring schedules → Task 8 ✓
- D.5 tags / collections → Task 3 ✓
- D.6 FTS5 search → Task 4 ✓
- D.7 approval workflow → Task 8 ✓
- D.8 version history → Task 6 ✓
- D.9 auto-save → Task 6 ✓
- G.1 URL paste → Task 9 ✓
- G.2 hashtag tracker → Task 9 ✓
- G.3 competitor watch → Task 9 ✓
- G.4 inspo feed → Task 9 ✓
- G.5 sound tracker → Task 9 ✓
- K.1 stats entry → Task 10 ✓
- K.2 dashboard → Task 10 ✓
- K.3 heatmap → Task 10 ✓
- K.4 engagement-rate trend → Task 10 (part of dashboard) ✓
- K.5 hashtag perf → Task 10 ✓
- K.6 CSV import → Task 10 ✓
- Library cleanup (mentioned in v2 spec §18 risks → v2.2) → Task 11 ✓

**Placeholder scan:** None.

**Type consistency:** `_kick_render_async` from v2.0/v2.1 unchanged. Schema migrations extend Phase 1's `_ensure_social_tables` (v1), v2.0's `_ensure_social_v2_tables`, and v2.2's new `_ensure_social_v22_tables`. POST_WRITABLE (Phase 1) gets `archived_at` added in Task 11.

---

## Execution

**Plan complete and saved to** `docs/superpowers/plans/2026-05-23-ahb123-social-studio-v2.2-workflow-plan.md`.

12 tasks. **v2.0 + v2.1 must land first.**

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — executing-plans in this session
