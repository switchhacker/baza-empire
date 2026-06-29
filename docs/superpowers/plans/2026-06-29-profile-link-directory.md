# Profile Link Directory (Track C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An editable directory of AHB123's public profile URLs — managed in a Social-tab "Links" section and rendered as a clickable "Find us on" block in the dashboard and on the public `/review` page.

**Architecture:** A new `dashboard/profile_links.py` module owns a Flask blueprint (`profile_bp`), one new table `ahb_profile_links`, and CRUD + a public read endpoint. Pure local CRUD — no LLM, no cloud, no network. Frontend adds a `links` sub-tab to the Social tab and a "Find us on" block to `templates/review_public.html`.

**Tech Stack:** Python 3 / Flask, SQLite (`baza_projects.db`), pytest. Vanilla JS in two templates.

---

## Conventions for this plan
- **Commits:** Repo auto-commits hourly via `claw-auto-git` (CLAUDE.md). **Do NOT `git commit` manually.** Checkpoint = green test run.
- **Run from repo root** `/home/switchhacker/baza-empire/agent-framework-v3` with `venv/bin/python -m pytest …`.
- **Test isolation:** build a Flask app, register `profile_bp`, point `BAZA_DASHBOARD_DB` at a tmp file (mirror `tests/test_social_connect.py`'s `env` fixture). No network.
- **Dashboard restart:** after editing `templates/ahb123.html` or `templates/review_public.html`, `sudo systemctl restart baza-dashboard` (Jinja cache) — Task 7.

## File structure
| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `dashboard/profile_links.py` | Create | `ahb_profile_links` table, `profile_bp` CRUD + `/public` |
| `dashboard/app.py` | Modify | Register `profile_bp` |
| `dashboard/templates/ahb123.html` | Modify | Social-tab "Links" sub-tab + `SocialStudio.modules.links` |
| `dashboard/templates/review_public.html` | Modify | Public "Find us on" block |
| `tests/test_profile_links.py` | Create | CRUD + public-filter TDD |

---

### Task 1: Module skeleton — table + GET list

**Files:** Create `dashboard/profile_links.py`; Test `tests/test_profile_links.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_links.py`:
```python
"""Tests for the AHB123 public profile-link directory. No network."""
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    sys.modules.pop("profile_links", None)
    import profile_links
    profile_links._ensure_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(profile_links.profile_bp)
    yield app.test_client(), profile_links, db
    sys.modules.pop("profile_links", None)


def test_list_empty(env):
    c, pl, _ = env
    r = c.get("/api/ahb/profile-links")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_table_exists(env):
    c, pl, db = env
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "ahb_profile_links" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -v`
Expected: FAIL (`No module named 'profile_links'`).

- [ ] **Step 3: Implement the module skeleton**

Create `dashboard/profile_links.py`:
```python
"""AHB123 public profile-link directory ("Find us on …").

Pure local CRUD: an editable list of public profile URLs (LinkedIn, Thumbtack,
HomeAdvisor/Angi, Google Business, socials, website). No LLM, no cloud, no
network. Distinct from social_connections (OAuth publishing credentials).
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
profile_bp = Blueprint("profile_links", __name__)


def _db_path() -> str:
    return os.environ.get(
        "BAZA_DASHBOARD_DB", os.path.join(DASHBOARD_DIR, "baza_projects.db"))


def _db():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _ensure_tables(db_path=None) -> None:
    con = None
    try:
        con = sqlite3.connect(db_path or _db_path(), timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS ahb_profile_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                label TEXT,
                url TEXT NOT NULL,
                icon TEXT,
                display_order INTEGER DEFAULT 100,
                visible INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        con.commit()
    finally:
        if con is not None:
            con.close()


def _normalize_url(raw: str) -> str:
    """Return a safe http(s) URL or raise ValueError (XSS guard for public links)."""
    u = (raw or "").strip()
    if not u:
        raise ValueError("url required")
    if "://" in u:
        if urlparse(u).scheme.lower() not in ("http", "https"):
            raise ValueError("only http(s) URLs allowed")
        return u
    return "https://" + u


@profile_bp.route("/api/ahb/profile-links", methods=["GET"])
def links_list():
    con = _db()
    try:
        rows = con.execute(
            "SELECT * FROM ahb_profile_links ORDER BY display_order, id").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: 2 passed.

---

### Task 2: Create link + URL normalization/validation

**Files:** Modify `dashboard/profile_links.py`; Test `tests/test_profile_links.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_create_normalizes_scheme(env):
    c, pl, db = env
    r = c.post("/api/ahb/profile-links",
               json={"platform": "thumbtack", "label": "Thumbtack",
                     "url": "thumbtack.com/ahb", "icon": "🛠️"})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()
    assert row["url"] == "https://thumbtack.com/ahb"
    assert row["platform"] == "thumbtack" and row["visible"] == 1


def test_create_rejects_non_http_scheme(env):
    c, pl, db = env
    r = c.post("/api/ahb/profile-links",
               json={"platform": "x", "url": "javascript:alert(1)"})
    assert r.status_code == 400
    assert "http" in r.get_json()["error"].lower()


def test_create_requires_platform_and_url(env):
    c, pl, db = env
    assert c.post("/api/ahb/profile-links", json={"url": "x.com"}).status_code == 400
    assert c.post("/api/ahb/profile-links", json={"platform": "x"}).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "create" -v`
Expected: FAIL (405/404 — POST route missing).

- [ ] **Step 3: Implement the create route**

Append to `dashboard/profile_links.py`:
```python
@profile_bp.route("/api/ahb/profile-links", methods=["POST"])
def links_create():
    d = request.get_json(silent=True) or {}
    platform = (d.get("platform") or "").strip()
    if not platform:
        return jsonify({"error": "platform required"}), 400
    try:
        url = _normalize_url(d.get("url"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    con = _db()
    try:
        cur = con.execute(
            "INSERT INTO ahb_profile_links (platform, label, url, icon, "
            "display_order, visible) VALUES (?,?,?,?,?,?)",
            (platform, d.get("label") or platform, url, d.get("icon"),
             int(d.get("display_order", 100) or 100),
             1 if d.get("visible", True) else 0))
        con.commit()
        row = con.execute("SELECT * FROM ahb_profile_links WHERE id=?",
                          (cur.lastrowid,)).fetchone()
    finally:
        con.close()
    return jsonify(dict(row))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "create" -v`
Expected: PASS (all three).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: all pass.

---

### Task 3: Update + Delete

**Files:** Modify `dashboard/profile_links.py`; Test `tests/test_profile_links.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def _seed(c, **over):
    body = {"platform": over.get("platform", "linkedin"),
            "label": over.get("label", "LinkedIn"),
            "url": over.get("url", "https://linkedin.com/company/ahb"),
            "icon": over.get("icon", "💼")}
    return c.post("/api/ahb/profile-links", json=body).get_json()["id"]


def test_update_fields(env):
    c, pl, db = env
    lid = _seed(c)
    r = c.put(f"/api/ahb/profile-links/{lid}",
              json={"label": "AHB LinkedIn", "visible": False, "url": "x.com/new"})
    assert r.status_code == 200
    con = sqlite3.connect(db)
    row = con.execute("SELECT label, visible, url FROM ahb_profile_links WHERE id=?",
                      (lid,)).fetchone()
    con.close()
    assert row[0] == "AHB LinkedIn" and row[1] == 0
    assert row[2] == "https://x.com/new"


def test_update_unknown_is_404(env):
    c, pl, db = env
    assert c.put("/api/ahb/profile-links/9999", json={"label": "z"}).status_code == 404


def test_update_rejects_bad_url(env):
    c, pl, db = env
    lid = _seed(c)
    assert c.put(f"/api/ahb/profile-links/{lid}",
                 json={"url": "ftp://x"}).status_code == 400


def test_delete(env):
    c, pl, db = env
    lid = _seed(c)
    assert c.delete(f"/api/ahb/profile-links/{lid}").status_code == 200
    assert c.delete(f"/api/ahb/profile-links/{lid}").status_code == 404
    assert c.get("/api/ahb/profile-links").get_json()["items"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "update or delete" -v`
Expected: FAIL (405/404 — routes missing).

- [ ] **Step 3: Implement update + delete**

Append to `dashboard/profile_links.py`:
```python
_LINK_FIELDS = {"platform", "label", "url", "icon", "display_order", "visible"}


@profile_bp.route("/api/ahb/profile-links/<int:lid>", methods=["PUT"])
def links_update(lid):
    d = request.get_json(silent=True) or {}
    fields = {k: v for k, v in d.items() if k in _LINK_FIELDS}
    if not fields:
        return jsonify({"error": "no updatable fields"}), 400
    if "url" in fields:
        try:
            fields["url"] = _normalize_url(fields["url"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if "visible" in fields:
        fields["visible"] = 1 if fields["visible"] else 0
    sets = ", ".join(f"{k}=?" for k in fields)
    con = _db()
    try:
        cur = con.execute(
            f"UPDATE ahb_profile_links SET {sets}, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (*fields.values(), lid))
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    finally:
        con.close()
    return jsonify({"ok": True})


@profile_bp.route("/api/ahb/profile-links/<int:lid>", methods=["DELETE"])
def links_delete(lid):
    con = _db()
    try:
        cur = con.execute("DELETE FROM ahb_profile_links WHERE id=?", (lid,))
        con.commit()
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    finally:
        con.close()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "update or delete" -v`
Expected: PASS (all four).

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: all pass.

---

### Task 4: Public endpoint (visible-only, minimal fields)

**Files:** Modify `dashboard/profile_links.py`; Test `tests/test_profile_links.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_public_only_visible_ordered_minimal(env):
    c, pl, db = env
    a = _seed(c, platform="linkedin", url="https://lnkd.in/ahb")
    b = _seed(c, platform="thumbtack", url="https://thumbtack.com/ahb")
    # hide b, order a after via display_order
    c.put(f"/api/ahb/profile-links/{b}", json={"visible": False})
    c.put(f"/api/ahb/profile-links/{a}", json={"display_order": 5})
    r = c.get("/api/ahb/profile-links/public")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 1                       # b hidden
    assert items[0]["platform"] == "linkedin"
    assert set(items[0].keys()) == {"platform", "label", "url", "icon"}  # no id/visible
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "public" -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement the public route**

Append to `dashboard/profile_links.py`:
```python
@profile_bp.route("/api/ahb/profile-links/public", methods=["GET"])
def links_public():
    con = _db()
    try:
        rows = con.execute(
            "SELECT platform, label, url, icon FROM ahb_profile_links "
            "WHERE visible=1 ORDER BY display_order, id").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return jsonify({"items": [dict(r) for r in rows]})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "public" -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint — full module suite**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: all pass (~10 tests).

---

### Task 5: Register the blueprint

**Files:** Modify `dashboard/app.py` (next to `_lead_bp`, ~line 15915); Test `tests/test_profile_links.py`

- [ ] **Step 1: Write the failing test**

Append:
```python
def test_app_registers_profile_bp():
    import importlib
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    app_mod = importlib.import_module("app")
    rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
    assert "/api/ahb/profile-links" in rules
    assert "/api/ahb/profile-links/public" in rules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "registers_profile_bp" -v`
Expected: FAIL (route not registered).

- [ ] **Step 3: Register the blueprint**

In `dashboard/app.py`, immediately after `app.register_blueprint(_lead_bp)` (~line 15915), add:
```python
try:
    from dashboard.profile_links import profile_bp as _profile_bp, _ensure_tables as _ensure_profile_tables
except ImportError:
    from profile_links import profile_bp as _profile_bp, _ensure_tables as _ensure_profile_tables
_ensure_profile_tables()
app.register_blueprint(_profile_bp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -k "registers_profile_bp" -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: all pass (~11 tests).

---

### Task 6: Frontend — Social "Links" sub-tab + public block

**Files:** Modify `dashboard/templates/ahb123.html` and `dashboard/templates/review_public.html`. Verify: manual (Task 7).

Locate insertion points by anchor text.

- [ ] **Step 1: Add the "Links" sub-tab nav button**

In `ahb123.html`, find the subnav row ending with:
`<button class="btn-secondary ss-subnav" data-sub="connect">🔗 Connect</button>`
Immediately AFTER it, add:
```html
      <button class="btn-secondary ss-subnav" data-sub="links">🌐 Links</button>
```

- [ ] **Step 2: Add the sub-pane**

Find `<div id="ss-sub-connect" class="ss-sub" style="display:none"></div>`. Immediately AFTER it, add:
```html
  <div id="ss-sub-links" class="ss-sub" style="display:none"></div>
```
(`SocialStudio.switchSub('links')` already toggles panes by id and calls `this.modules['links'].render()` — no dispatch wiring needed.)

- [ ] **Step 3: Add the `SocialStudio.modules.links` module**

Find the end of the connect module — the line `return { render, setAppCreds, connectOAuth, connectMeta, connectTikTok, connectLinkedIn, disconnect, browseFeed, publishPicker, manualExport };` followed by `})();`. Immediately AFTER that `})();`, add:
```javascript
SocialStudio.modules.links = (function(){
  const S = SocialStudio;
  const PRESETS = [
    {platform:'linkedin', label:'LinkedIn', icon:'💼'},
    {platform:'youtube', label:'YouTube', icon:'▶️'},
    {platform:'instagram', label:'Instagram', icon:'📸'},
    {platform:'facebook', label:'Facebook', icon:'👥'},
    {platform:'tiktok', label:'TikTok', icon:'🎵'},
    {platform:'thumbtack', label:'Thumbtack', icon:'🛠️'},
    {platform:'homeadvisor', label:'HomeAdvisor / Angi', icon:'🏠'},
    {platform:'google', label:'Google Business', icon:'⭐'},
    {platform:'website', label:'Website', icon:'🌐'},
  ];
  function _esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  async function render(){
    const root = document.getElementById('ss-sub-links'); if (!root) return;
    root.innerHTML = '<div style="color:#666;padding:16px">Loading…</div>';
    let items = [];
    try { items = (await (await fetch('/api/ahb/profile-links')).json()).items || []; }
    catch(e){ root.innerHTML = '<div style="color:#a55;padding:16px">Could not load links.</div>'; return; }
    const presetOpts = PRESETS.map(p => `<option value="${p.platform}" data-icon="${p.icon}" data-label="${_esc(p.label)}">${p.icon} ${_esc(p.label)}</option>`).join('');
    root.innerHTML = `
      <div style="font-size:13px;color:#888;margin-bottom:10px">Public profile links — shown on your <a href="/review" target="_blank" style="color:#60a5fa">customer review page</a> and usable on ahb123.com.</div>
      <div style="display:flex;flex-direction:column;gap:8px">${
        items.length ? items.map(l => `
        <div style="display:flex;align-items:center;gap:8px;background:#070712;border:1px solid #1a1a2e;border-radius:6px;padding:8px">
          <span style="font-size:16px">${_esc(l.icon||'🔗')}</span>
          <div style="flex:1;min-width:0"><div style="font-size:12px;color:#ddd">${_esc(l.label||l.platform)}</div>
            <div style="font-size:10px;color:#777;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(l.url)}</div></div>
          <label style="font-size:10px;color:#999"><input type="checkbox" ${l.visible?'checked':''} onchange="SocialStudio.modules.links.toggle(${l.id}, this.checked)"> shown</label>
          <a href="${_esc(l.url)}" target="_blank" rel="noopener" class="btn-secondary" style="font-size:10px;padding:3px 8px">Open</a>
          <button class="btn-secondary" style="font-size:10px;padding:3px 8px;color:#f87171" onclick="SocialStudio.modules.links.remove(${l.id})">Remove</button>
        </div>`).join('') : '<div style="color:#555;font-size:12px">No links yet.</div>'
      }</div>
      <div style="margin-top:14px;background:#0b0b16;border:1px solid #1a1a2e;border-radius:8px;padding:10px">
        <div style="font-size:12px;font-weight:700;color:#ccc;margin-bottom:6px">Add a link</div>
        <select id="pl-preset" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#ddd;font-size:12px;margin-bottom:6px">${presetOpts}</select>
        <input id="pl-url" placeholder="https://… profile URL" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff;font-size:12px;margin-bottom:6px">
        <button class="btn-primary" style="width:100%" onclick="SocialStudio.modules.links.add()">＋ Add link</button>
      </div>`;
  }
  async function add(){
    const sel = document.getElementById('pl-preset');
    const opt = sel.options[sel.selectedIndex];
    const url = document.getElementById('pl-url').value.trim();
    if (!url){ S.modules.toast.error('Enter a URL'); return; }
    const r = await fetch('/api/ahb/profile-links', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({platform: sel.value, label: opt.dataset.label, icon: opt.dataset.icon, url})});
    const j = await r.json();
    if (!r.ok){ S.modules.toast.error(j.error||'Add failed'); return; }
    S.modules.toast.success('Link added'); render();
  }
  async function toggle(id, visible){
    await fetch('/api/ahb/profile-links/'+id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({visible})});
    render();
  }
  async function remove(id){
    if (!confirm('Remove this link?')) return;
    await fetch('/api/ahb/profile-links/'+id, {method:'DELETE'});
    render();
  }
  return { render, add, toggle, remove };
})();
```

- [ ] **Step 4: Add the public "Find us on" block to `review_public.html`**

In `dashboard/templates/review_public.html`, find the closing of the form/success area — the line `</div>` that closes `<div class="wrap">` (the last `</div>` before `<script>`, around line 160). Immediately BEFORE that closing `</div>`, insert:
```html
  <div id="find-us-on" style="display:none;margin-top:18px;text-align:center">
    <div style="font-size:12px;color:#888;margin-bottom:8px">Find &amp; review us on</div>
    <div id="find-us-links" style="display:flex;flex-wrap:wrap;gap:10px;justify-content:center"></div>
  </div>
```

- [ ] **Step 5: Add the fetch to `review_public.html`'s script**

In the same file, inside the `<script>` block (before `</script>` at the end), add:
```javascript
(async function(){
  try {
    const items = (await (await fetch('/api/ahb/profile-links/public')).json()).items || [];
    if (!items.length) return;
    const esc = s => String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    document.getElementById('find-us-links').innerHTML = items.map(l =>
      `<a href="${esc(l.url)}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid #e2e2e2;border-radius:8px;padding:8px 12px;text-decoration:none;color:#333;font-size:13px;font-weight:600">${esc(l.icon||'🔗')} ${esc(l.label||l.platform)}</a>`).join('');
    document.getElementById('find-us-on').style.display = '';
  } catch(e){ /* best-effort; never blocks the review form */ }
})();
```

- [ ] **Step 6: Restart the dashboard**

Run: `sudo systemctl restart baza-dashboard`
Expected: returns 0.

---

### Task 7: Live smoke + session log

**Files:** none (verification + session log).

- [ ] **Step 1: Full module suite**

Run: `venv/bin/python -m pytest tests/test_profile_links.py -q`
Expected: all pass (~11 tests).

- [ ] **Step 2: Restart + live route smoke**

Run:
```bash
sudo systemctl restart baza-dashboard && sleep 2 && systemctl is-active baza-dashboard
curl -s localhost:8888/api/ahb/profile-links | python3 -c "import sys,json;print('items' in json.load(sys.stdin))"
curl -s localhost:8888/api/ahb/profile-links/public | python3 -c "import sys,json;print('items' in json.load(sys.stdin))"
```
Expected: `active`, `True`, `True`.

- [ ] **Step 3: Round-trip smoke (create → public shows → delete)**

Run:
```bash
ID=$(curl -s -X POST localhost:8888/api/ahb/profile-links -H 'Content-Type: application/json' -d '{"platform":"thumbtack","label":"Thumbtack","icon":"🛠️","url":"thumbtack.com/ahb"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s localhost:8888/api/ahb/profile-links/public | python3 -c "import sys,json;print('thumbtack in public:', any(i['platform']=='thumbtack' for i in json.load(sys.stdin)['items']))"
curl -s -X DELETE localhost:8888/api/ahb/profile-links/$ID >/dev/null && echo "cleanup ok"
```
Expected: `thumbtack in public: True`, `cleanup ok`. (Leaves the table as it was.)

- [ ] **Step 4: Visual check**

Open the dashboard → Social tab → **🌐 Links** sub-tab: confirm the editor renders with the preset picker + Add form. Open `/review` in a browser: with no visible links the "Find us on" block is hidden; after adding one in the editor it appears.

- [ ] **Step 5: Append session-log entry**

Run (timestamp from `date`):
```bash
printf '\n### %s | Profile Link Directory (Track C) shipped\n- dashboard/profile_links.py: ahb_profile_links table + profile_bp CRUD (list/create/update/delete) + /public (visible-only, minimal fields, https-normalized, non-http(s) rejected). Registered in app.py. Social tab 🌐 Links sub-tab (SocialStudio.modules.links: preset picker, add/toggle-visible/open/remove) + Find-us-on block on /review (review_public.html, best-effort, auto-hides empty). tests/test_profile_links.py all green; dashboard restarted; live round-trip OK. Track C COMPLETE — A/B/C all shipped.\n' "$(date '+%Y-%m-%d %H:%M')" >> ~/Desktop/baza-session-log.md
```

---

## Self-review notes (author)
- **Spec coverage:** §3.1 table → T1; §3.2 routes (list T1, create T2, update/delete T3, public T4, registration T5) → covered; §3.3 frontend (Social sub-tab T6 s1-3, public block T6 s4-5) → covered; §4 error handling (400 bad/missing, 404, reject non-http(s)) → T2/T3 tests; §5 tests → T1-T5. ✓
- **No placeholders:** every code/test step complete and runnable.
- **Type/name consistency:** `_normalize_url`, `_ensure_tables`, `_db`, `profile_bp`, `_LINK_FIELDS` defined T1-T3 and used consistently; routes `/api/ahb/profile-links[/<id>][/public]` consistent across backend, tests, and both templates' fetches. `SocialStudio.modules.links` exposes `render/add/toggle/remove` used by the rendered onclick handlers.
- **Known follow-ups (out of v1):** click analytics, auto-discovering URLs, automated Squarespace/ahb123.com embed (the `/public` endpoint makes a manual embed trivial). Editor reordering is via the numeric `display_order` field through PUT (no drag-and-drop UI in v1 — `display_order` is settable via the API; a drag UI is a follow-up).
