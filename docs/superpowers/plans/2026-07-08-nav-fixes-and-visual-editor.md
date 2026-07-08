# Nav Fixes + Visual Editor Core (Phases A + B-i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix AHB123 subtab drift/order + add Email & Web to the main nav (Phase A), then ship the click-to-edit overrides editor on every Baza dashboard page with a `/web` control page (Phase B-i).

**Architecture:** Phase A creates one shared Jinja macro file (`_ahb_tabs.html`) that renders both the `_nav.html` AHB123 dropdown and the `ahb123.html` sub-tab bar, killing the drift class. Phase B-i adds a new SQLite store (`ui_overrides.db`) + Flask blueprint (`ui_editor.py`) and a vanilla-JS editor (`static/edit.js`) injected via `_nav.html` on every page: toggle Edit Mode, click any element, edit text/image/style/link/visibility; changes persist as overrides re-applied on every load.

**Tech Stack:** Flask blueprints, Jinja2 macros, SQLite (WAL), vanilla JS (no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md` (Phases B-ii, B-iii get separate plans after this lands.)

## Global Constraints

- Repo root: `/home/switchhacker/baza-empire/agent-framework-v3`. All paths below relative to it. Run tests from repo root with `venv/bin/python -m pytest`.
- **Local-first hard rule:** no cloud APIs, no CDN scripts, no new pip/npm deps. Vanilla JS only.
- **Template caching:** `baza-dashboard.service` runs `debug=False` — after any `dashboard/templates/*.html` change, verification requires `sudo systemctl restart baza-dashboard`. Static JS/CSS need no restart but ARE cached by the service worker — always bump the `?v=` query when changing `edit.js`/`edit.css`.
- **Commit per task** (established SDD practice in this repo — active feature work is time-sensitive; do not wait for claw-auto-git).
- New sub-tab priority order (spec A1): **email, projects, receipts(QuickRF), dashboard, clients, treasury, heavyeq, schedule, noted, voice, chatdept, photos, social, reviews, leads, web**.
- Modals/panels injected into pages must be attached to `document.body` (never inside a `#tab-*` pane — display:none ancestors make them invisible).
- Don't touch `dashboard/baza_projects.db` — overrides live in their own `dashboard/ui_overrides.db`.
- Editor endpoints share the dashboard's existing auth posture (LAN/Tailscale/CF Access, single user) — no new auth layer.

---

### Task 1: Shared AHB123 tab list (`_ahb_tabs.html`) — kills the dropdown drift

**Files:**
- Create: `dashboard/templates/_ahb_tabs.html`
- Modify: `dashboard/templates/_nav.html` (lines 50–72, the AHB123 dropdown)
- Modify: `dashboard/templates/ahb123.html` (lines 881–896, the `.sub-nav` block)
- Test: `tests/test_nav_ahb_tabs.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: Jinja macros `dropdown_links()` and `subtab_bar(active)` importable via `{% from '_ahb_tabs.html' import dropdown_links, subtab_bar %}`. Tab keys match existing `tab-<key>` pane ids / `TAB_GROUPS` keys in `ahb123.html`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nav_ahb_tabs.py — AHB123 tabs render from ONE shared list (spec A2)
import os, re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO_ROOT, "dashboard", "templates")

def read(name):
    with open(os.path.join(TPL, name), encoding="utf-8") as f:
        return f.read()

def test_shared_list_defines_priority_order():
    src = read("_ahb_tabs.html")
    keys = re.findall(r"\(\s*'([a-z]+)'", src)
    # Priority items first (spec A1)
    assert keys[:3] == ["email", "projects", "receipts"], keys[:3]
    expected = {"email","projects","receipts","dashboard","clients","treasury",
                "heavyeq","schedule","noted","voice","chatdept","photos",
                "social","reviews","leads","web"}
    assert expected == set(keys)

def test_both_surfaces_import_the_shared_list():
    nav, page = read("_nav.html"), read("ahb123.html")
    assert "_ahb_tabs.html" in nav and "dropdown_links" in nav
    assert "_ahb_tabs.html" in page and "subtab_bar" in page

def test_stale_hand_copied_entries_are_gone():
    nav = read("_nav.html")
    # InvoiceIT/Billing merged into Projects 2026-06-11; dropdown still had them
    for stale in ["InvoiceIT", "/ahb123/invoices", "/ahb123/billing",
                  "/ahb123/estimator", "/ahb123/receipts"]:
        assert stale not in nav, f"stale dropdown entry survived: {stale}"

def test_no_hardcoded_subtab_divs_left_in_ahb123():
    page = read("ahb123.html")
    # The old hand-written bar had ~15 of these; macro renders them now.
    assert page.count('class="sub-tab') <= 1  # only the macro's template string
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_nav_ahb_tabs.py -v`
Expected: FAIL — `FileNotFoundError: _ahb_tabs.html`

- [ ] **Step 3: Create `dashboard/templates/_ahb_tabs.html`**

```jinja
{# Single source of truth for AHB123 top-level tabs (spec 2026-07-08 A2).
   BOTH the _nav.html dropdown and the ahb123.html sub-tab bar render from
   AHB_TABS. Add/rename/reorder tabs HERE — never in the consumers.
   Keys must match a tab-<key> pane or TAB_GROUPS group in ahb123.html.
   Leaf tabs inside groups (Treasury/HeavyEq children) stay in TAB_GROUPS. #}
{% set AHB_TABS = [
  ('email',     '📧', 'Email'),
  ('projects',  '🏗️', 'Projects'),
  ('receipts',  '🧾', 'QuickRF'),
  ('dashboard', '📊', 'Dashboard'),
  ('clients',   '👥', 'Clients'),
  ('treasury',  '🏦', 'Treasury'),
  ('heavyeq',   '🚜', 'Heavy Equipment'),
  ('schedule',  '📅', 'Calendar'),
  ('noted',     '📌', 'Sticky Pad'),
  ('voice',     '🎙️', 'Voice'),
  ('chatdept',  '💬', 'Chat Dept'),
  ('photos',    '🎥', 'Media'),
  ('social',    '📣', 'Social'),
  ('reviews',   '⭐', 'Reviews'),
  ('leads',     '🎯', 'Leads'),
  ('web',       '🌐', 'Web'),
] %}

{% macro dropdown_links() -%}
  {%- for key, icon, label in AHB_TABS %}
  <a href="/ahb123?tab={{ key }}">{{ icon }} {{ label }}</a>
  {%- endfor %}
{%- endmacro %}

{% macro subtab_bar(active='dashboard') -%}
  {%- for key, icon, label in AHB_TABS %}
  <div class="sub-tab{% if key == active %} active{% endif %}" data-tab="{{ key }}" onclick="switchTab('{{ key }}')"><span class="sub-tab-icon">{{ icon }}</span> {{ label }}</div>
  {%- endfor %}
{%- endmacro %}
```

- [ ] **Step 4: Rewire `_nav.html` dropdown**

Replace the entire AHB123 submenu body (`_nav.html` lines 52–71, the `<div class="nav-submenu">…</div>` under the AHB123 link) with:

```jinja
    <div class="nav-submenu">
      {{ dropdown_links() }}
    </div>
```

And at the top of `_nav.html`, directly after the `{% set _act = … %}` line (line 15), add:

```jinja
{% from '_ahb_tabs.html' import dropdown_links %}
```

- [ ] **Step 5: Rewire `ahb123.html` sub-tab bar**

Replace the 15 hardcoded `<div class="sub-tab" …>` lines (881–896, inside the `.sub-nav` container — keep the container element itself) with:

```jinja
  {{ subtab_bar('dashboard') }}
```

At the top of `ahb123.html` (before the sub-nav is rendered, e.g. right after the `{% set nav_active … %}` / first template line), add:

```jinja
{% from '_ahb_tabs.html' import subtab_bar %}
```

- [ ] **Step 6: Run tests**

Run: `venv/bin/python -m pytest tests/test_nav_ahb_tabs.py -v`
Expected: 4 PASS

- [ ] **Step 7: Sanity-render check** — templates with syntax errors 500 the whole dashboard. Quick offline render:

```bash
venv/bin/python - <<'EOF'
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('dashboard/templates'))
t = env.get_template('_ahb_tabs.html')
mod = t.make_module()
out = mod.subtab_bar('dashboard')
assert 'data-tab="email"' in str(out) and 'QuickRF' in str(out)
print("macro renders OK")
EOF
```

Expected: `macro renders OK`

- [ ] **Step 8: Commit**

```bash
git add dashboard/templates/_ahb_tabs.html dashboard/templates/_nav.html dashboard/templates/ahb123.html tests/test_nav_ahb_tabs.py
git commit -m "feat(nav): single-source AHB123 tabs, priority order Email/Projects/QuickRF (fixes dropdown drift)"
```

---

### Task 2: `switchTab` promoted-leaf handling (QuickRF as top-level tab)

**Files:**
- Modify: `dashboard/templates/ahb123.html` (`switchTab()` at ~line 5287, the highlight block at lines 5305–5316)

**Interfaces:**
- Consumes: Task 1's sub-tab bar (top-level `data-tab="receipts"` now exists).
- Produces: `switchTab('receipts')` highlights the top-level QuickRF tab and hides the leaf-nav row; `Treasury → QuickRF` still works and still highlights Treasury with the leaf row visible. No other tab behavior changes.

**Why:** `receipts` is a child of `TAB_GROUPS.treasury`. Old code always highlights the *parent group* tab, so clicking top-level QuickRF would light up Treasury instead. New rule: an element that has its own top-level sub-tab AND whose parent group is a *different* key is "promoted" — highlight it directly, skip leaf-nav. The `parent !== tabName` guard keeps the Projects super-tab (group key == leaf key) on the old path so its Change Orders leaf row keeps working.

- [ ] **Step 1: Apply the change**

In `ahb123.html` `switchTab()`, replace this block (currently ~lines 5305–5316):

```js
  // Highlight the matching super-tab — direct match for leaves, parent group for nested
  const parent = _parentGroupOf(tabName);
  const superKey = parent || tabName;
  const superEl = document.querySelector(`.sub-nav .sub-tab[data-tab="${superKey}"]`);
  if(superEl) superEl.classList.add('active');
  // Render the contextual leaf nav (or hide it for leaf super-tabs)
  if(parent){
    _renderLeafNav(parent, tabName);
    sessionStorage.setItem('ahbLeaf:'+parent, tabName);
  } else {
    _renderLeafNav(null);
  }
```

with:

```js
  // Highlight the matching super-tab. A leaf with its OWN top-level sub-tab
  // (e.g. QuickRF, promoted 2026-07-08) is highlighted directly with no leaf
  // row; parent!==tabName keeps same-named group/leaf pairs (Projects) on the
  // group path so their leaf rows still render.
  const parent = _parentGroupOf(tabName);
  const directEl = document.querySelector(`.sub-nav .sub-tab[data-tab="${tabName}"]`);
  const promoted = !!(directEl && parent && parent !== tabName);
  const superEl = promoted ? directEl
        : document.querySelector(`.sub-nav .sub-tab[data-tab="${parent || tabName}"]`);
  if(superEl) superEl.classList.add('active');
  // Render the contextual leaf nav (or hide it for promoted/plain leaf tabs)
  if(parent && !promoted){
    _renderLeafNav(parent, tabName);
    sessionStorage.setItem('ahbLeaf:'+parent, tabName);
  } else {
    _renderLeafNav(null);
  }
```

- [ ] **Step 2: Verify by rendering logic table (no JS test infra — verify in browser after restart)**

```bash
sudo systemctl restart baza-dashboard
```

Then in a browser on `http://baza:8888/ahb123` check ALL of:
1. Top row order starts 📧 Email, 🏗️ Projects, 🧾 QuickRF.
2. Click **QuickRF** (top-level) → QuickRF pane opens, QuickRF tab highlighted, NO leaf row.
3. Click **Treasury** → leaf row appears (QuickRF/Vendors/Payroll/Uncle Sam/Debt); click its QuickRF → pane opens, **Treasury** stays highlighted, leaf row visible.
4. Click **Projects** → leaf row shows Active Projects/Change Orders (regression check for the `parent!==tabName` guard).
5. Deep link `http://baza:8888/ahb123?tab=receipts` behaves like (2).
6. Top-nav AHB123 dropdown lists all 16 tabs, Email first; clicking Email opens the Email subtab.

- [ ] **Step 3: Run the Task 1 tests again (guard against accidental template damage)**

Run: `venv/bin/python -m pytest tests/test_nav_ahb_tabs.py -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/ahb123.html
git commit -m "feat(ahb123): promoted-leaf tab handling so top-level QuickRF highlights correctly"
```

---

### Task 3: Main-nav Email + Web links, `/web` route + skeleton, banner shrink

**Files:**
- Modify: `dashboard/templates/_nav.html` (main row + style block)
- Modify: `dashboard/app.py` (add `/web` route next to `ahb123_page`, ~line 5885)
- Create: `dashboard/templates/web.html` (skeleton — Task 7 replaces it with the full page)
- Test: `tests/test_nav_main_tabs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `GET /web` renders `web.html` with `nav_active='web'`; main nav contains `📧 Email → /email` and `🌐 Web → /web`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nav_main_tabs.py — Email + Web in the main nav; banner shrunk (spec A3/A4)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_main_nav_has_email_and_web_links():
    nav = read("dashboard", "templates", "_nav.html")
    assert 'href="/email"' in nav
    assert 'href="/web"' in nav
    # active-state wiring for both keys
    assert "_act == 'email'" in nav and "_act == 'web'" in nav

def test_web_route_and_template_exist():
    app_src = read("dashboard", "app.py")
    assert "@app.route('/web')" in app_src
    web = read("dashboard", "templates", "web.html")
    assert "nav_active = 'web'" in web.replace('"', "'")
    assert "_nav.html" in web

def test_banner_shrink_rules_present():
    nav = read("dashboard", "templates", "_nav.html")
    assert ".nav-brand h1{font-size:13px" in nav
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_nav_main_tabs.py -v`
Expected: FAIL (3 failures — links, route, CSS all missing)

- [ ] **Step 3: Add the two main-nav links in `_nav.html`**

Directly after the AHB123 dropdown's closing `</div>` (the one before the `Sticky` link, line 72→73 boundary), insert:

```jinja
  <a class="nav-link {% if _act == 'email' %}active{% endif %}" href="/email" title="Email Studio">&#128231; Email</a>
  <a class="nav-link {% if _act == 'web' %}active{% endif %}" href="/web" title="Web — sites &amp; visual editor">&#127760; Web</a>
```

- [ ] **Step 4: Banner shrink — append to the `<style>` block in `_nav.html` (inside the existing block, after the `.nav-submenu` mobile rules)**

```css
  /* Banner shrink (2026-07-08) — canonical override so every host page gets a
     compact brand, freeing room for the Email/Web main tabs. */
  .nav-brand{gap:6px!important;padding-top:12px!important;padding-bottom:12px!important;padding-right:14px!important;margin-right:6px!important}
  .nav-brand h1{font-size:13px!important;letter-spacing:1px!important;white-space:nowrap}
  .nav-brand>span{font-size:15px!important}
  @media (max-width:1500px){ .nav-brand h1{display:none!important} }
```

Also add `title="BAZA EMPIRE"` to the `.nav-brand` div (line 34) so the name survives the collapsed state:

```html
  <div class="nav-brand" title="BAZA EMPIRE"><span>⚡</span><h1>BAZA EMPIRE</h1></div>
```

- [ ] **Step 5: Add the `/web` route in `dashboard/app.py`**

Directly after the `sticky_page` function (~line 5885):

```python
@app.route('/web')
def web_editor_page():
    """Web command center — site status + the Baza visual editor home (spec 2026-07-08)."""
    return render_template('web.html')
```

- [ ] **Step 6: Create skeleton `dashboard/templates/web.html`** (Task 7 replaces the body; the shell/CSS here is final)

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Baza Empire — Web</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#07070f;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
    a{color:inherit;text-decoration:none}
    .nav{background:#0d0d1e;border-bottom:1px solid #1a1a3a;padding:0 32px;display:flex;align-items:center;position:sticky;top:0;z-index:100;flex-wrap:nowrap}
    .nav-brand{display:flex;align-items:center;gap:10px;padding:18px 24px 18px 0;border-right:1px solid #1a1a3a;margin-right:8px;flex-shrink:0}
    .nav-brand h1{font-size:18px;font-weight:800;color:#e94560;letter-spacing:2px}
    .nav-link{padding:20px 18px;font-size:13px;font-weight:600;color:#666;border-bottom:3px solid transparent;white-space:nowrap}
    .nav-link:hover,.nav-link.active{color:#e0e0e0;border-bottom-color:#e94560}
    .nav-right{margin-left:auto;display:flex;align-items:center;gap:14px}
    .nav-status{font-size:11px;color:#555}
    .container{max-width:1200px;margin:0 auto;padding:28px 32px}
    .page-title{font-size:22px;font-weight:800;color:#fff;margin-bottom:6px;display:flex;align-items:center;gap:12px}
    .page-sub{font-size:12px;color:#555;margin-bottom:22px}
    .card{background:#0e0e1e;border:1px solid #1a1a2e;border-radius:12px;margin-bottom:22px;overflow:hidden}
    .card-head{padding:16px 22px;border-bottom:1px solid #111;display:flex;align-items:center;justify-content:space-between}
    .card-title{font-size:14px;font-weight:700;color:#fff}
    .card-body{padding:22px}
    .btn{background:linear-gradient(135deg,#e94560,#7c3aed);color:#fff;border:none;padding:10px 18px;border-radius:8px;font-size:13px;font-weight:700;cursor:pointer}
    .btn:hover{opacity:.9}
    .btn.ghost{background:#111;color:#aaa;border:1px solid #1a1a2e}
    .btn.danger{background:#2a0d0d;color:#ff6666;border:1px solid #5a1a1a}
    .btn.sm{padding:6px 11px;font-size:12px}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
    .pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700}
    .pill.ok{background:#0a2a1a;color:#00d084}
    .pill.warn{background:#2a230a;color:#d0a000}
    .pill.off{background:#111;color:#666}
  </style>
</head>
<body>
{% set nav_active = 'web' %}
{% include '_nav.html' %}
<div class="container">
  <div class="page-title">🌐 Web</div>
  <div class="page-sub">Sites, status, and the Baza visual editor.</div>
  <div class="card"><div class="card-body" id="web-root">Loading…</div></div>
</div>
</body>
</html>
```

- [ ] **Step 7: Run tests**

Run: `venv/bin/python -m pytest tests/test_nav_main_tabs.py tests/test_nav_ahb_tabs.py -v`
Expected: 7 PASS

- [ ] **Step 8: Restart + manual check**

```bash
sudo systemctl restart baza-dashboard
```

Check: main nav shows compact ⚡BAZA EMPIRE, then Agents / AHB123 / **Email** / **Web** / Sticky / Data Hub / Projects / Cloud / Phantom / Settings on one row at desktop width; `/web` loads; `/email` still loads; below-1500px width shows ⚡ only.

- [ ] **Step 9: Commit**

```bash
git add dashboard/templates/_nav.html dashboard/templates/web.html dashboard/app.py tests/test_nav_main_tabs.py
git commit -m "feat(nav): Email + Web main tabs, /web page, compact banner"
```

---

### Task 4: Overrides store + API (`dashboard/ui_editor.py`)

**Files:**
- Create: `dashboard/ui_editor.py`
- Test: `tests/test_ui_editor.py`

**Interfaces:**
- Consumes: nothing (self-contained blueprint; registered in Task 5).
- Produces (all JSON):
  - `ui_bp` — Flask Blueprint; `init_db()` — creates schema at module global `DB_PATH`.
  - `GET  /api/ui/overrides?page=P` → `{"page": P, "overrides": [{id,page,selector,kind,value,fingerprint,active,created_at,updated_at}]}` (active only, `value` JSON-decoded)
  - `POST /api/ui/overrides` body `{page, selector, kind, value, fingerprint?}` → `{"ok":true,"id":N}`; upserts on active (page,selector,kind)
  - `POST /api/ui/overrides/<id>/revert` → `{"ok":true}` (sets active=0)
  - `POST /api/ui/overrides/reset` body `{page, selector?}` → `{"ok":true,"reverted":N}`
  - `GET  /api/ui/overrides/history?page=P` → `{"page":P,"overrides":[...]}` (active AND inactive, newest first)
  - `GET  /api/ui/overrides/summary` → `{"pages":[{"page":P,"count":N}]}` (active counts)
  - `POST /api/ui/upload` multipart `file` → `{"ok":true,"url":"/static/uploads/<name>"}`
  - Kinds: `text|image|style|hide|link|order|attr` (constant `KINDS`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui_editor.py — overrides store + API (spec B2)
import io, json, os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
import pytest
from flask import Flask
import ui_editor as u


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "DB_PATH", str(tmp_path / "ov.db"))
    monkeypatch.setattr(u, "UPLOAD_DIR", str(tmp_path / "uploads"))
    u.init_db()
    app = Flask("t")
    app.register_blueprint(u.ui_bp)
    return app.test_client()


def _save(client, **kw):
    body = {"page": "/ahb123", "selector": "#x", "kind": "text", "value": "Hi"}
    body.update(kw)
    return client.post("/api/ui/overrides", json=body)


def test_normalize_page_strips_query_hash_and_trailing_slash():
    assert u.normalize_page("/ahb123?tab=email#x") == "/ahb123"
    assert u.normalize_page("ahb123/") == "/ahb123"
    assert u.normalize_page("") == "/"
    assert u.normalize_page("/") == "/"


def test_save_and_list_roundtrip(client):
    r = _save(client, value="New Label",
              fingerprint={"tag": "div", "text": "Old", "cls": "sub-tab"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    got = client.get("/api/ui/overrides?page=/ahb123?tab=email").get_json()
    assert got["page"] == "/ahb123"
    assert len(got["overrides"]) == 1
    ov = got["overrides"][0]
    assert ov["value"] == "New Label" and ov["kind"] == "text"
    assert ov["fingerprint"]["tag"] == "div"


def test_upsert_same_key_updates_not_duplicates(client):
    _save(client, value="one")
    _save(client, value="two")
    ovs = client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"]
    assert len(ovs) == 1 and ovs[0]["value"] == "two"


def test_bad_kind_and_bad_selector_rejected(client):
    assert _save(client, kind="explode").status_code == 422
    assert _save(client, selector="").status_code == 422
    assert _save(client, selector="x" * 1001).status_code == 422


def test_revert_removes_from_active_keeps_in_history(client):
    oid = _save(client).get_json()["id"]
    assert client.post(f"/api/ui/overrides/{oid}/revert").status_code == 200
    assert client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"] == []
    hist = client.get("/api/ui/overrides/history?page=/ahb123").get_json()["overrides"]
    assert len(hist) == 1 and hist[0]["active"] == 0


def test_reset_page_and_reset_selector(client):
    _save(client, selector="#a")
    _save(client, selector="#b")
    _save(client, selector="#b", kind="style", value={"color": "red"})
    r = client.post("/api/ui/overrides/reset", json={"page": "/ahb123", "selector": "#b"})
    assert r.get_json()["reverted"] == 2
    left = client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"]
    assert [o["selector"] for o in left] == ["#a"]
    r = client.post("/api/ui/overrides/reset", json={"page": "/ahb123"})
    assert r.get_json()["reverted"] == 1


def test_summary_counts_active_per_page(client):
    _save(client, page="/ahb123", selector="#a")
    _save(client, page="/datahub", selector="#a")
    _save(client, page="/datahub", selector="#b")
    pages = {p["page"]: p["count"]
             for p in client.get("/api/ui/overrides/summary").get_json()["pages"]}
    assert pages == {"/ahb123": 1, "/datahub": 2}


def test_upload_rejects_bad_extension_and_saves_good(client):
    bad = {"file": (io.BytesIO(b"x"), "evil.py")}
    assert client.post("/api/ui/upload", data=bad,
                       content_type="multipart/form-data").status_code == 422
    good = {"file": (io.BytesIO(b"\x89PNG fake"), "pic.PNG")}
    r = client.post("/api/ui/upload", data=good, content_type="multipart/form-data")
    url = r.get_json()["url"]
    assert url.startswith("/static/uploads/") and url.endswith(".png")
    assert os.path.exists(os.path.join(u.UPLOAD_DIR, os.path.basename(url)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_ui_editor.py -v`
Expected: FAIL — `ModuleNotFoundError: ui_editor`

- [ ] **Step 3: Implement `dashboard/ui_editor.py`**

```python
# dashboard/ui_editor.py — Visual editor overrides store + API.
# Spec: docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md (B2)
# Overrides are cosmetic patches (text/image/style/hide/link/order/attr) applied
# by static/edit.js over live dashboard pages. Separate DB — never touches
# baza_projects.db. Revert = soft-delete (active=0) so history survives.
import json
import os
import sqlite3
import uuid

from flask import Blueprint, jsonify, request

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "ui_overrides.db")
UPLOAD_DIR = os.path.join(_HERE, "static", "uploads")
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
KINDS = {"text", "image", "style", "hide", "link", "order", "attr"}

ui_bp = Blueprint("ui_editor", __name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
  id INTEGER PRIMARY KEY,
  page TEXT NOT NULL,
  selector TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  fingerprint TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_overrides_page ON overrides(page, active);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)


def normalize_page(p):
    """Path key for a page: strip query/hash/trailing slash, ensure leading /."""
    p = (p or "/").split("?", 1)[0].split("#", 1)[0].strip()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def _row(r):
    d = dict(r)
    for field in ("value", "fingerprint"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (TypeError, ValueError):
                pass
    return d


@ui_bp.route("/api/ui/overrides")
def list_overrides():
    page = normalize_page(request.args.get("page"))
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM overrides WHERE page=? AND active=1 ORDER BY id",
            (page,)).fetchall()
    return jsonify({"page": page, "overrides": [_row(r) for r in rows]})


@ui_bp.route("/api/ui/overrides", methods=["POST"])
def save_override():
    b = request.get_json(force=True, silent=True) or {}
    page = normalize_page(b.get("page"))
    selector = (b.get("selector") or "").strip()
    kind = b.get("kind")
    if not selector or len(selector) > 1000:
        return jsonify({"error": "selector required (max 1000 chars)"}), 422
    if kind not in KINDS:
        return jsonify({"error": "kind must be one of %s" % sorted(KINDS)}), 422
    value = json.dumps(b.get("value"))
    fp = json.dumps(b["fingerprint"]) if b.get("fingerprint") else None
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM overrides WHERE page=? AND selector=? AND kind=? AND active=1",
            (page, selector, kind)).fetchone()
        if row:
            c.execute(
                "UPDATE overrides SET value=?, fingerprint=COALESCE(?, fingerprint),"
                " updated_at=datetime('now') WHERE id=?",
                (value, fp, row["id"]))
            oid = row["id"]
        else:
            oid = c.execute(
                "INSERT INTO overrides(page, selector, kind, value, fingerprint)"
                " VALUES(?,?,?,?,?)",
                (page, selector, kind, value, fp)).lastrowid
    return jsonify({"ok": True, "id": oid})


@ui_bp.route("/api/ui/overrides/<int:oid>/revert", methods=["POST"])
def revert_override(oid):
    with _conn() as c:
        n = c.execute(
            "UPDATE overrides SET active=0, updated_at=datetime('now') WHERE id=? AND active=1",
            (oid,)).rowcount
    if not n:
        return jsonify({"error": "no active override %d" % oid}), 404
    return jsonify({"ok": True})


@ui_bp.route("/api/ui/overrides/reset", methods=["POST"])
def reset_overrides():
    b = request.get_json(force=True, silent=True) or {}
    page = normalize_page(b.get("page"))
    selector = (b.get("selector") or "").strip()
    with _conn() as c:
        if selector:
            n = c.execute(
                "UPDATE overrides SET active=0, updated_at=datetime('now')"
                " WHERE page=? AND selector=? AND active=1", (page, selector)).rowcount
        else:
            n = c.execute(
                "UPDATE overrides SET active=0, updated_at=datetime('now')"
                " WHERE page=? AND active=1", (page,)).rowcount
    return jsonify({"ok": True, "reverted": n})


@ui_bp.route("/api/ui/overrides/history")
def override_history():
    page = normalize_page(request.args.get("page"))
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM overrides WHERE page=? ORDER BY updated_at DESC, id DESC",
            (page,)).fetchall()
    return jsonify({"page": page, "overrides": [_row(r) for r in rows]})


@ui_bp.route("/api/ui/overrides/summary")
def override_summary():
    with _conn() as c:
        rows = c.execute(
            "SELECT page, COUNT(*) AS n FROM overrides WHERE active=1"
            " GROUP BY page ORDER BY page").fetchall()
    return jsonify({"pages": [{"page": r["page"], "count": r["n"]} for r in rows]})


@ui_bp.route("/api/ui/upload", methods=["POST"])
def upload_image():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 422
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "extension %s not allowed" % ext}), 422
    blob = f.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        return jsonify({"error": "file too large (max %dMB)" % (MAX_UPLOAD_BYTES // 1048576)}), 422
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = uuid.uuid4().hex[:12] + ext
    with open(os.path.join(UPLOAD_DIR, name), "wb") as out:
        out.write(blob)
    return jsonify({"ok": True, "url": "/static/uploads/" + name})
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_ui_editor.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/ui_editor.py tests/test_ui_editor.py
git commit -m "feat(editor): ui_overrides store + API blueprint (CRUD, revert, history, summary, upload)"
```

---

### Task 5: Register blueprint + editor core (`edit.js` apply engine, edit mode, selection)

**Files:**
- Modify: `dashboard/app.py` (blueprint registration block, after `app.register_blueprint(_ahb_web_bp)` at ~line 16364)
- Modify: `dashboard/templates/_nav.html` (line 13–14 area — add script/css includes)
- Create: `dashboard/static/edit.css`
- Create: `dashboard/static/edit.js`
- Test: `tests/test_editor_wiring.py`

**Interfaces:**
- Consumes: Task 4's API + `ui_bp`/`init_db`.
- Produces: every page including `_nav.html` loads `edit.js?v=1` + `edit.css?v=1`. Global JS namespace `window.BazaEdit = {selectorFor, fingerprintFor, saveOverride, refresh, setEditMode, getSelected}` — Task 6's inspector builds on exactly these names. Overrides auto-apply on load + on DOM mutation. `?edit=1` in any URL auto-enables edit mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_editor_wiring.py — edit.js wired into every page via _nav.html (spec B1)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_nav_includes_editor_assets_with_cache_bust():
    nav = read("dashboard", "templates", "_nav.html")
    assert "/static/edit.js?v=" in nav
    assert "/static/edit.css?v=" in nav

def test_app_registers_ui_blueprint_and_inits_db():
    src = read("dashboard", "app.py")
    assert "ui_editor" in src and "ui_bp" in src
    assert "init_db()" in src.split("ui_editor")[1][:500]

def test_edit_js_core_api_surface():
    js = read("dashboard", "static", "edit.js")
    for name in ["selectorFor", "fingerprintFor", "saveOverride", "refresh",
                 "setEditMode", "getSelected", "window.BazaEdit"]:
        assert name in js, f"missing {name}"
    # mutation-loop guard: text apply must be conditional
    assert "el.textContent !== o.value" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: FAIL (3 failures)

- [ ] **Step 3: Register the blueprint in `dashboard/app.py`**

After the `app.register_blueprint(_ahb_web_bp)` line (~16364), following the same try/except import idiom used by its neighbors:

```python
try:
    from dashboard.ui_editor import ui_bp as _ui_bp, init_db as _ui_init_db
except ImportError:
    from ui_editor import ui_bp as _ui_bp, init_db as _ui_init_db
_ui_init_db()
app.register_blueprint(_ui_bp)
```

- [ ] **Step 4: Add asset includes to `_nav.html`**

After line 14 (`<script defer src="/static/help.js"></script>`):

```html
<link rel="stylesheet" href="/static/edit.css?v=1">
<script defer src="/static/edit.js?v=1"></script>
```

- [ ] **Step 5: Create `dashboard/static/edit.css`**

```css
/* Baza Visual Editor chrome (spec 2026-07-08 B1). ?v= bumped on every change
   — the service worker caches statics. */
#baza-edit-toggle{background:transparent;border:1px solid #2a2a4a;border-radius:8px;color:#888;font-size:14px;padding:4px 9px;cursor:pointer;margin-left:10px;line-height:1}
#baza-edit-toggle.on{background:#e94560;border-color:#e94560;color:#fff;box-shadow:0 0 12px #e9456088}
#baza-edit-toggle.floating{position:fixed;bottom:18px;right:18px;z-index:99998;background:#0d0d1e;font-size:18px;padding:8px 12px}
body.baza-editing .baza-hover{outline:2px dashed #7c3aed !important;outline-offset:1px;cursor:crosshair !important}
body.baza-editing .baza-selected{outline:2px solid #e94560 !important;outline-offset:1px}
#baza-edit-hint{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);background:#e94560;color:#fff;font:700 12px 'Segoe UI',system-ui,sans-serif;padding:7px 16px;border-radius:16px;z-index:99998;box-shadow:0 4px 16px rgba(0,0,0,.5);pointer-events:none}
/* Inspector panel (populated in Task 6) */
#baza-edit-panel{position:fixed;top:64px;right:12px;width:310px;max-height:calc(100vh - 90px);overflow-y:auto;background:#0d0d1e;border:1px solid #2a2a4a;border-radius:12px;z-index:99999;box-shadow:0 12px 40px rgba(0,0,0,.7);font:12px 'Segoe UI',system-ui,sans-serif;color:#ccc}
#baza-edit-panel .bep-head{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #1a1a3a;cursor:move;background:#11112a;border-radius:12px 12px 0 0}
#baza-edit-panel .bep-title{font-weight:800;color:#fff;font-size:12px}
#baza-edit-panel .bep-x{cursor:pointer;color:#666;font-size:15px;padding:0 4px}
#baza-edit-panel .bep-x:hover{color:#fff}
#baza-edit-panel .bep-sec{padding:10px 14px;border-bottom:1px solid #14142a}
#baza-edit-panel .bep-lbl{font-size:10px;font-weight:800;color:#555;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
#baza-edit-panel .bep-sel{font-family:monospace;font-size:10px;color:#7c8aed;word-break:break-all}
#baza-edit-panel textarea,#baza-edit-panel input[type=text],#baza-edit-panel input[type=number],#baza-edit-panel select{width:100%;background:#07070f;border:1px solid #1a1a3a;border-radius:6px;color:#ddd;font-size:12px;padding:6px 8px;margin-bottom:6px}
#baza-edit-panel input[type=color]{width:34px;height:26px;border:1px solid #1a1a3a;border-radius:6px;background:#07070f;padding:1px;cursor:pointer}
#baza-edit-panel .bep-row{display:flex;gap:6px;align-items:center;margin-bottom:6px}
#baza-edit-panel .bep-row label{font-size:11px;color:#888;min-width:70px}
#baza-edit-panel button.bep-btn{background:#1a1a3e;color:#cfcfe8;border:1px solid #2a2a4a;border-radius:6px;padding:6px 10px;font-size:11px;font-weight:700;cursor:pointer}
#baza-edit-panel button.bep-btn:hover{background:#24244e}
#baza-edit-panel button.bep-btn.primary{background:#e94560;border-color:#e94560;color:#fff}
#baza-edit-panel button.bep-btn.danger{background:#2a0d0d;border-color:#5a1a1a;color:#ff6666}
#baza-edit-panel .bep-note{font-size:10px;color:#555;margin-top:4px}
```

- [ ] **Step 6: Create `dashboard/static/edit.js`** (core engine — inspector body arrives in Task 6)

```js
/* Baza Visual Editor — overrides engine + Edit Mode.
   Spec: docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md (B1).
   Loaded on EVERY dashboard page via _nav.html. Two halves:
   1) apply engine — always on: fetch /api/ui/overrides for this page, apply,
      re-apply on DOM mutation (dashboard pages render lots of content via JS).
   2) Edit Mode — ✏️ toggle: hover-highlight, click-select, inspector panel
      (panel body built in buildInspector(), Task 6). */
(function () {
'use strict';

var PAGE = location.pathname.split('?')[0].replace(/\/+$/, '') || '/';
var API = '/api/ui/overrides';
var OVERRIDES = [];
var editMode = false;
var selected = null;
var styleEl = null;
var applyTimer = null;
var hoverTarget = null;

/* ---------- selectors + fingerprints ---------- */
function esc(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(s)
       : String(s).replace(/([^a-zA-Z0-9_-])/g, '\\$1');
}
function selectorFor(el) {
  if (el.id) return '#' + esc(el.id);
  var dt = el.getAttribute && el.getAttribute('data-tab');
  if (dt) {
    var s = el.tagName.toLowerCase() + '[data-tab="' + dt + '"]';
    if (document.querySelectorAll(s).length === 1) return s;
  }
  var parts = [], cur = el;
  while (cur && cur !== document.body && parts.length < 7) {
    if (cur.id) { parts.unshift('#' + esc(cur.id)); break; }
    var part = cur.tagName.toLowerCase();
    var par = cur.parentElement;
    if (par) {
      var same = Array.prototype.filter.call(par.children, function (c) { return c.tagName === cur.tagName; });
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
    }
    parts.unshift(part);
    var cand = parts.join(' > ');
    try { if (document.querySelectorAll(cand).length === 1) return cand; } catch (e) {}
    cur = par;
  }
  return parts.join(' > ');
}
function fingerprintFor(el) {
  return {
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || '').trim().slice(0, 60),
    cls: (el.getAttribute && el.getAttribute('class') || '').slice(0, 120)
  };
}

/* ---------- apply engine ---------- */
function ensureSheet() {
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'baza-ov-css';
    document.head.appendChild(styleEl);
  }
  return styleEl;
}
function cssProps(props) {
  return Object.keys(props || {}).map(function (k) {
    var css = k.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); });
    return css + ':' + props[k] + ' !important';
  }).join(';');
}
function rebuildSheet() {
  var rules = [];
  OVERRIDES.forEach(function (o) {
    try {
      if (o.kind === 'style' && o.value) rules.push(o.selector + '{' + cssProps(o.value) + '}');
      else if (o.kind === 'hide' && o.value !== false) rules.push(o.selector + '{display:none !important}');
    } catch (e) {}
  });
  ensureSheet().textContent = rules.join('\n');
}
function applyDom() {
  OVERRIDES.forEach(function (o) {
    if (o.kind === 'style' || o.kind === 'hide') return; // sheet handles these
    var els;
    try { els = document.querySelectorAll(o.selector); } catch (e) { return; }
    if (!els.length) { o._stale = true; return; }
    o._stale = false;
    Array.prototype.forEach.call(els, function (el) {
      // every mutation is guarded by a value check → no MutationObserver loops
      if (o.kind === 'text') {
        if (el.textContent !== o.value && document.activeElement !== el) el.textContent = o.value;
      } else if (o.kind === 'image') {
        if (el.tagName === 'IMG' && el.getAttribute('src') !== o.value) el.setAttribute('src', o.value);
      } else if (o.kind === 'link') {
        if (el.tagName === 'A' && el.getAttribute('href') !== o.value) el.setAttribute('href', o.value);
      } else if (o.kind === 'attr' && o.value && o.value.name) {
        if (el.getAttribute(o.value.name) !== o.value.value) el.setAttribute(o.value.name, o.value.value);
      } else if (o.kind === 'order' && Array.isArray(o.value)) {
        var kids = o.value.map(function (s) {
          try { return el.querySelector(':scope > ' + s); } catch (e) { return null; }
        }).filter(Boolean);
        var current = Array.prototype.filter.call(el.children, function (c) { return kids.indexOf(c) !== -1; });
        var moved = kids.some(function (k, i) { return k !== current[i]; });
        if (moved) kids.forEach(function (k) { el.appendChild(k); });
      }
    });
  });
}
function scheduleApply() {
  clearTimeout(applyTimer);
  applyTimer = setTimeout(applyDom, 150);
}
function refresh() {
  return fetch(API + '?page=' + encodeURIComponent(PAGE))
    .then(function (r) { return r.json(); })
    .then(function (j) { OVERRIDES = j.overrides || []; rebuildSheet(); applyDom(); })
    .catch(function () {});
}

/* ---------- edit mode ---------- */
function inChrome(t) {
  return !!(t.closest && t.closest('#baza-edit-panel,#baza-edit-toggle,#baza-edit-hint'));
}
function setEditMode(on) {
  editMode = !!on;
  try { sessionStorage.setItem('bazaEdit', on ? '1' : '0'); } catch (e) {}
  document.body.classList.toggle('baza-editing', editMode);
  var btn = document.getElementById('baza-edit-toggle');
  if (btn) btn.classList.toggle('on', editMode);
  var hint = document.getElementById('baza-edit-hint');
  if (editMode && !hint) {
    hint = document.createElement('div');
    hint.id = 'baza-edit-hint';
    hint.textContent = '✏️ Edit Mode — click any element · Esc to exit';
    document.body.appendChild(hint);
  } else if (!editMode && hint) hint.remove();
  if (!editMode) { clearSel(); hidePanel(); }
}
function clearSel() {
  if (hoverTarget) { hoverTarget.classList.remove('baza-hover'); hoverTarget = null; }
  if (selected) { selected.classList.remove('baza-selected'); selected = null; }
}
function select(el) {
  if (selected) selected.classList.remove('baza-selected');
  selected = el;
  selected.classList.add('baza-selected');
  showPanel(); // Task 6 fills the panel; core provides the hook
}
/* Panel shell — buildInspector(panel) is defined in the inspector half (Task 6). */
function showPanel() {
  var p = document.getElementById('baza-edit-panel');
  if (!p) {
    p = document.createElement('div');
    p.id = 'baza-edit-panel';
    document.body.appendChild(p); // body-level: never inside a tab pane
  }
  p.style.display = 'block';
  if (typeof buildInspector === 'function') buildInspector(p);
}
function hidePanel() {
  var p = document.getElementById('baza-edit-panel');
  if (p) p.style.display = 'none';
}

function saveOverride(kind, value) {
  if (!selected) return Promise.resolve();
  return fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      page: PAGE, selector: selectorFor(selected), kind: kind, value: value,
      fingerprint: fingerprintFor(selected)
    })
  }).then(function () { return refresh(); });
}

/* ---------- wiring ---------- */
function initToggle() {
  if (document.getElementById('baza-edit-toggle')) return;
  var btn = document.createElement('button');
  btn.id = 'baza-edit-toggle';
  btn.title = 'Edit Mode — click any element on the page to edit it';
  btn.textContent = '✏️';
  btn.addEventListener('click', function () { setEditMode(!editMode); });
  var host = document.querySelector('.nav-right');
  if (host) host.appendChild(btn);
  else { btn.classList.add('floating'); document.body.appendChild(btn); }
  var qs = new URLSearchParams(location.search);
  if (qs.get('edit') === '1' || sessionStorage.getItem('bazaEdit') === '1') setEditMode(true);
}
document.addEventListener('mouseover', function (e) {
  if (!editMode || inChrome(e.target)) return;
  if (hoverTarget) hoverTarget.classList.remove('baza-hover');
  hoverTarget = e.target;
  hoverTarget.classList.add('baza-hover');
}, true);
document.addEventListener('click', function (e) {
  if (!editMode || inChrome(e.target)) return;
  e.preventDefault();
  e.stopPropagation();
  select(e.target);
}, true);
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && editMode) setEditMode(false);
});

function boot() {
  initToggle();
  refresh().then(function () {
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var t = muts[i].target;
        if (!(t.closest && t.closest('#baza-edit-panel'))) { scheduleApply(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

window.BazaEdit = {
  selectorFor: selectorFor, fingerprintFor: fingerprintFor,
  saveOverride: saveOverride, refresh: refresh, setEditMode: setEditMode,
  getSelected: function () { return selected; },
  _overrides: function () { return OVERRIDES; }
};
})();
```

Note for the implementer: `buildInspector` is intentionally referenced but not defined yet — `typeof buildInspector === 'function'` guards it, so the core is fully functional (select + highlight, no panel body) until Task 6. It must be declared with `function buildInspector(...)` INSIDE this same IIFE in Task 6.

- [ ] **Step 7: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py tests/test_ui_editor.py -v`
Expected: 11 PASS

- [ ] **Step 8: Restart + smoke check**

```bash
sudo systemctl restart baza-dashboard
```

Browser (hard-refresh to bypass sw cache): ✏️ appears in nav-right on `/ahb123` and `/settings`; toggling shows the hint pill; hover outlines elements; click selects (red outline, empty panel appears); Esc exits; normal clicks work when edit mode is off. Seed one override manually and confirm it applies after reload:

```bash
curl -s -X POST http://localhost:8888/api/ui/overrides -H 'Content-Type: application/json' \
  -d '{"page":"/ahb123","selector":"#tab-receipts .page-title","kind":"text","value":"QuickRF ✏️ override test"}'
```

Reload `/ahb123?tab=receipts` → the QuickRF page title reads "QuickRF ✏️ override test". Then clean up:

```bash
curl -s -X POST http://localhost:8888/api/ui/overrides/reset -H 'Content-Type: application/json' -d '{"page":"/ahb123"}'
```

- [ ] **Step 9: Commit**

```bash
git add dashboard/app.py dashboard/templates/_nav.html dashboard/static/edit.css dashboard/static/edit.js tests/test_editor_wiring.py
git commit -m "feat(editor): edit.js apply engine + Edit Mode on every dashboard page"
```

---

### Task 6: Inspector panel (text / image / style / link / hide / reset)

**Files:**
- Modify: `dashboard/static/edit.js` (add `buildInspector` + helpers inside the IIFE, before the `window.BazaEdit` export)
- Modify: `dashboard/templates/_nav.html` (bump both asset includes to `?v=2`)
- Test: `tests/test_editor_wiring.py` (extend)

**Interfaces:**
- Consumes: Task 5's core (`selected`, `saveOverride`, `selectorFor`, `hidePanel`, panel shell, edit.css panel classes) and Task 4's `/api/ui/upload` + `/api/ui/overrides/reset`.
- Produces: full inspector UI. No new JS globals.

- [ ] **Step 1: Extend the wiring test (failing first)**

Append to `tests/test_editor_wiring.py`:

```python
def test_inspector_capabilities_present():
    js = read("dashboard", "static", "edit.js")
    for feature in ["buildInspector", "api/ui/upload", "contenteditable",
                    "fontSize", "borderRadius", "Reset element", "Hide element"]:
        assert feature in js, f"inspector missing {feature}"

def test_asset_version_bumped():
    nav = read("dashboard", "templates", "_nav.html")
    assert "edit.js?v=2" in nav and "edit.css?v=2" in nav
```

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: 2 new FAIL

- [ ] **Step 2: Add the inspector to `edit.js`** — insert this block inside the IIFE, directly above the `/* ---------- wiring ---------- */` comment:

```js
/* ---------- inspector panel (Task 6) ---------- */
var STYLE_FIELDS = [
  // [override style prop, label, input type]
  ['color',           'Text color',   'color'],
  ['backgroundColor', 'Background',   'color'],
  ['fontSize',        'Font size',    'px'],
  ['fontWeight',      'Weight',       'select:normal,600,700,800'],
  ['fontFamily',      'Font',         'text'],
  ['textAlign',       'Align',        'select:left,center,right'],
  ['padding',         'Padding',      'text'],
  ['margin',          'Margin',       'text'],
  ['border',          'Border',       'text'],
  ['borderRadius',    'Radius',       'px'],
  ['width',           'Width',        'text'],
  ['opacity',         'Opacity',      'text']
];
function existingStyle() {
  if (!selected) return {};
  var sel = selectorFor(selected);
  var found = {};
  OVERRIDES.forEach(function (o) {
    if (o.kind === 'style' && o.selector === sel && o.value) found = o.value;
  });
  return found;
}
function el(tag, attrs, text) {
  var e = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
  if (text !== undefined) e.textContent = text;
  return e;
}
function section(panel, label) {
  var s = el('div', { 'class': 'bep-sec' });
  s.appendChild(el('div', { 'class': 'bep-lbl' }, label));
  panel.appendChild(s);
  return s;
}
function btn(label, cls, fn) {
  var b = el('button', { 'class': 'bep-btn' + (cls ? ' ' + cls : '') }, label);
  b.addEventListener('click', fn);
  return b;
}
function toast(msg) {
  var h = document.getElementById('baza-edit-hint');
  if (h) { h.textContent = msg; setTimeout(function () { if (editMode && h) h.textContent = '✏️ Edit Mode — click any element · Esc to exit'; }, 1600); }
}
function buildInspector(panel) {
  if (!selected) return;
  panel.innerHTML = '';
  var sel = selectorFor(selected);

  // head
  var head = el('div', { 'class': 'bep-head' });
  head.appendChild(el('span', { 'class': 'bep-title' }, '✏️ <' + selected.tagName.toLowerCase() + '>'));
  var x = el('span', { 'class': 'bep-x', title: 'Close (element stays selected until Esc)' }, '✕');
  x.addEventListener('click', hidePanel);
  head.appendChild(x);
  panel.appendChild(head);

  // selector info
  var info = section(panel, 'Element');
  info.appendChild(el('div', { 'class': 'bep-sel' }, sel));

  // text — inline contenteditable on the page itself
  if (selected.childElementCount === 0) {
    var st = section(panel, 'Text');
    var ta = el('textarea', { rows: '3' });
    ta.value = (selected.textContent || '').trim();
    st.appendChild(ta);
    st.appendChild(btn('Save text', 'primary', function () {
      saveOverride('text', ta.value).then(function () { toast('✓ text saved'); });
    }));
    st.appendChild(btn('Edit on page', '', function () {
      selected.setAttribute('contenteditable', 'true');
      selected.focus();
      var done = function () {
        selected.removeAttribute('contenteditable');
        selected.removeEventListener('blur', done);
        ta.value = (selected.textContent || '').trim();
        saveOverride('text', ta.value).then(function () { toast('✓ text saved'); });
      };
      selected.addEventListener('blur', done);
    }));
    st.appendChild(el('div', { 'class': 'bep-note' }, 'Edit on page: type directly into the element, click away to save.'));
  }

  // image
  if (selected.tagName === 'IMG') {
    var si = section(panel, 'Image');
    var url = el('input', { type: 'text', placeholder: 'Image URL or /static/... path' });
    url.value = selected.getAttribute('src') || '';
    si.appendChild(url);
    var file = el('input', { type: 'file', accept: 'image/*' });
    si.appendChild(file);
    file.addEventListener('change', function () {
      if (!file.files.length) return;
      var fd = new FormData();
      fd.append('file', file.files[0]);
      fetch('/api/ui/upload', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.url) { url.value = j.url; toast('uploaded — hit Save image'); }
          else toast('✗ ' + (j.error || 'upload failed'));
        });
    });
    si.appendChild(btn('Save image', 'primary', function () {
      saveOverride('image', url.value).then(function () { toast('✓ image saved'); });
    }));
  }

  // link
  if (selected.tagName === 'A') {
    var sl = section(panel, 'Link');
    var href = el('input', { type: 'text' });
    href.value = selected.getAttribute('href') || '';
    sl.appendChild(href);
    sl.appendChild(btn('Save link', 'primary', function () {
      saveOverride('link', href.value).then(function () { toast('✓ link saved'); });
    }));
  }

  // style
  var ss = section(panel, 'Style');
  var cur = existingStyle();
  var inputs = {};
  STYLE_FIELDS.forEach(function (f) {
    var prop = f[0], label = f[1], type = f[2];
    var row = el('div', { 'class': 'bep-row' });
    row.appendChild(el('label', {}, label));
    var inp;
    if (type === 'color') {
      inp = el('input', { type: 'color' });
      if (cur[prop]) inp.value = cur[prop];
    } else if (type === 'px') {
      inp = el('input', { type: 'number', placeholder: 'px' });
      if (cur[prop]) inp.value = parseInt(cur[prop], 10) || '';
    } else if (type.indexOf('select:') === 0) {
      inp = el('select');
      inp.appendChild(el('option', { value: '' }, '—'));
      type.slice(7).split(',').forEach(function (o) {
        var op = el('option', { value: o }, o);
        if (cur[prop] === o) op.setAttribute('selected', '');
        inp.appendChild(op);
      });
    } else {
      inp = el('input', { type: 'text', placeholder: 'e.g. 8px 12px' });
      if (cur[prop]) inp.value = cur[prop];
    }
    inp.dataset.dirty = '';
    inp.addEventListener('input', function () { inp.dataset.dirty = '1'; });
    inp.addEventListener('change', function () { inp.dataset.dirty = '1'; });
    inputs[prop] = { inp: inp, type: type };
    row.appendChild(inp);
    ss.appendChild(row);
  });
  ss.appendChild(btn('Apply style', 'primary', function () {
    var props = existingStyle();
    Object.keys(inputs).forEach(function (prop) {
      var rec = inputs[prop];
      if (!rec.inp.dataset.dirty) return;         // only send touched fields
      var v = rec.inp.value;
      if (v === '' || v === null) { delete props[prop]; return; }
      props[prop] = (rec.type === 'px') ? v + 'px' : v;
    });
    saveOverride('style', props).then(function () { toast('✓ style saved'); });
  }));
  ss.appendChild(el('div', { 'class': 'bep-note' }, 'Only fields you touched are saved. Clear a field to remove that property.'));

  // visibility + reset
  var sv = section(panel, 'Element actions');
  sv.appendChild(btn('🙈 Hide element', '', function () {
    saveOverride('hide', true).then(function () { toast('hidden — restore from /web history'); });
  }));
  sv.appendChild(btn('↺ Reset element', 'danger', function () {
    fetch(API + '/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: PAGE, selector: sel })
    }).then(function () { location.reload(); });
  }));
  sv.appendChild(el('div', { 'class': 'bep-note' }, 'Reset element reverts every override on this selector and reloads. Full page history & revert: 🌐 Web tab.'));
}
```

- [ ] **Step 3: Bump asset versions in `_nav.html`**

Change both includes to `?v=2`:

```html
<link rel="stylesheet" href="/static/edit.css?v=2">
<script defer src="/static/edit.js?v=2"></script>
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: 5 PASS

- [ ] **Step 5: Manual verification (dashboard restart NOT needed for statics, but hard-refresh)**

On `/ahb123`: rename a sub-tab label via Text save → reload → sticks. Swap the AHB logo `<img>` via upload → sticks. Set a card's background + radius via Style → sticks. Hide an element → reload → gone. Reset element → everything on it back to stock. Check `/api/ui/overrides/history?page=/ahb123` shows the trail.

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/edit.js dashboard/templates/_nav.html tests/test_editor_wiring.py
git commit -m "feat(editor): inspector panel — text/image/style/link/hide/reset on any element"
```

---

### Task 7: `/web` page — site cards, page list, override history + revert

**Files:**
- Modify: `dashboard/templates/web.html` (replace the `web-root` card with the full body + JS; keep the shell/CSS from Task 3)
- Test: `tests/test_web_page.py`

**Interfaces:**
- Consumes: `/api/ui/overrides/summary`, `/api/ui/overrides/history`, `/api/ui/overrides/<id>/revert`, `/api/ui/overrides/reset` (Task 4); `/api/ahb/web/status` (pre-existing, from `web_site_routes.py`); `?edit=1` auto-enable (Task 5).
- Produces: the Web command center at `/web`. (Phase B-iii will add the ahb123.com source editor onto this page — its site card links out until then.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_page.py — /web command center (spec B3, phase B-i slice)
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()

def test_web_page_wires_the_override_apis():
    web = read("dashboard", "templates", "web.html")
    for api in ["/api/ui/overrides/summary", "/api/ui/overrides/history",
                "/revert", "/api/ui/overrides/reset", "/api/ahb/web/status"]:
        assert api in web, f"missing {api}"

def test_web_page_lists_dash_pages_with_edit_links():
    web = read("dashboard", "templates", "web.html")
    assert "?edit=1" in web
    for path in ["/ahb123", "/datahub", "/projects", "/cloud", "/settings"]:
        assert f"'{path}'" in web, f"page list missing {path}"
```

Run: `venv/bin/python -m pytest tests/test_web_page.py -v`
Expected: FAIL

- [ ] **Step 2: Replace the placeholder card in `web.html`** — swap `<div class="card"><div class="card-body" id="web-root">Loading…</div></div>` with:

```html
  <!-- Sites -->
  <div class="card">
    <div class="card-head"><div class="card-title">Sites</div></div>
    <div class="card-body">
      <div class="grid" id="site-cards">
        <div class="card" style="margin:0"><div class="card-body">
          <div style="font-weight:800;color:#fff;margin-bottom:6px">🏠 ahb123.com</div>
          <div id="site-ahb" style="font-size:12px;color:#888">Checking…</div>
          <div style="margin-top:10px" class="bep-note">
            <a class="btn ghost sm" href="/ahb123?tab=web">Status &amp; deploy →</a>
          </div>
        </div></div>
        <div class="card" style="margin:0"><div class="card-body">
          <div style="font-weight:800;color:#fff;margin-bottom:6px">⚡ baza.ahb123.com</div>
          <div style="font-size:12px;color:#888">This dashboard, via Cloudflare Tunnel + Access.<br>
            Edit it live: toggle ✏️ on any page. <span class="pill ok">overrides</span></div>
        </div></div>
        <div class="card" style="margin:0"><div class="card-body">
          <div style="font-weight:800;color:#fff;margin-bottom:6px">💬 nova.ahb123.com</div>
          <div style="font-size:12px;color:#888">Nova chat (self-hosted, Caddy). Status only — not editable.</div>
        </div></div>
      </div>
    </div>
  </div>

  <!-- Baza Dash pages -->
  <div class="card">
    <div class="card-head">
      <div class="card-title">Baza Dash pages — visual overrides</div>
      <span style="font-size:11px;color:#555">✏️ opens the page in Edit Mode</span>
    </div>
    <div class="card-body"><div class="grid" id="page-cards">Loading…</div></div>
  </div>

  <!-- History drawer -->
  <div class="card" id="hist-card" style="display:none">
    <div class="card-head">
      <div class="card-title" id="hist-title">History</div>
      <button class="btn danger sm" id="hist-reset" onclick="resetPage()">↺ Reset page</button>
    </div>
    <div class="card-body" id="hist-body"></div>
  </div>

<script>
const DASH_PAGES = [
  ['/',          '🤖 Agents'],
  ['/ahb123',    '🏢 AHB123'],
  ['/email',     '📧 Email Studio'],
  ['/datahub',   '📦 Data Hub'],
  ['/projects',  '🏗️ Projects'],
  ['/sticky',    '📌 Sticky'],
  ['/cloud',     '☁️ Cloud'],
  ['/network',   '🌐 Network'],
  ['/infra',     '🛡️ Infra'],
  ['/edge',      '📡 Edge'],
  ['/crons',     '⏰ Crons'],
  ['/settings',  '⚙️ Settings'],
];
let histPage = null;

function escHtml(s){ return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function loadSiteCard(){
  const box = document.getElementById('site-ahb');
  try{
    const st = await (await fetch('/api/ahb/web/status')).json();
    box.innerHTML =
      `NS: ${st.ns_cloudflare ? '<span class="pill ok">Cloudflare</span>' : '<span class="pill warn">'+escHtml((st.ns||[]).join(', ')||'unresolved')+'</span>'}<br>` +
      `Apex: <span class="pill ${st.apex_source==='pages'?'ok':'warn'}">${escHtml(st.apex_source||'?')}</span> ` +
      `Preview: <span class="pill ${st.preview_ok?'ok':'off'}">${st.preview_ok?'live':'down'}</span><br>` +
      `<span style="color:#555">Visual source editor lands with phase B-iii.</span>`;
  }catch(e){ box.textContent = 'status unavailable'; }
}

async function loadPageCards(){
  let counts = {};
  try{
    const j = await (await fetch('/api/ui/overrides/summary')).json();
    (j.pages||[]).forEach(p => counts[p.page] = p.count);
  }catch(e){}
  document.getElementById('page-cards').innerHTML = DASH_PAGES.map(([path,label]) => {
    const n = counts[path]||0;
    return `<div class="card" style="margin:0"><div class="card-body">
      <div style="font-weight:800;color:#fff;margin-bottom:4px">${label}</div>
      <div style="font-size:11px;color:#555;font-family:monospace">${path}</div>
      <div style="margin:8px 0">${n ? `<span class="pill warn">${n} override${n>1?'s':''}</span>` : '<span class="pill off">stock</span>'}</div>
      <a class="btn sm" href="${path}${path.includes('?')?'&':'?'}edit=1">✏️ Edit</a>
      <button class="btn ghost sm" onclick="showHistory('${path}')">🕘 History</button>
    </div></div>`;
  }).join('');
}

async function showHistory(page){
  histPage = page;
  document.getElementById('hist-card').style.display = 'block';
  document.getElementById('hist-title').textContent = 'History — ' + page;
  const body = document.getElementById('hist-body');
  body.textContent = 'Loading…';
  try{
    const j = await (await fetch('/api/ui/overrides/history?page='+encodeURIComponent(page))).json();
    if(!(j.overrides||[]).length){ body.innerHTML = '<span style="color:#555">No overrides ever recorded for this page.</span>'; return; }
    body.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<tr style="color:#555;text-align:left"><th style="padding:6px">Kind</th><th>Selector</th><th>Value</th><th>Updated</th><th>State</th><th></th></tr>' +
      j.overrides.map(o => `<tr style="border-top:1px solid #14142a${o.active?'':';opacity:.45'}">
        <td style="padding:6px"><span class="pill ${o.active?'ok':'off'}">${escHtml(o.kind)}</span></td>
        <td style="font-family:monospace;font-size:10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(o.selector)}">${escHtml(o.selector)}</td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(JSON.stringify(o.value))}">${escHtml(JSON.stringify(o.value))}</td>
        <td style="color:#555;white-space:nowrap">${escHtml(o.updated_at)}</td>
        <td>${o.active ? '<span class="pill ok">active</span>' : '<span class="pill off">reverted</span>'}</td>
        <td>${o.active ? `<button class="btn ghost sm" onclick="revertOne(${o.id})">↺ Revert</button>` : ''}</td>
      </tr>`).join('') + '</table>';
  }catch(e){ body.textContent = 'failed to load history'; }
}

async function revertOne(id){
  await fetch('/api/ui/overrides/'+id+'/revert', {method:'POST'});
  showHistory(histPage); loadPageCards();
}

async function resetPage(){
  if(!histPage || !confirm('Revert ALL active overrides on '+histPage+'?')) return;
  await fetch('/api/ui/overrides/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({page:histPage})});
  showHistory(histPage); loadPageCards();
}

loadSiteCard(); loadPageCards();
</script>
```

- [ ] **Step 3: Run tests (full editor suite)**

Run: `venv/bin/python -m pytest tests/test_web_page.py tests/test_editor_wiring.py tests/test_ui_editor.py tests/test_nav_main_tabs.py tests/test_nav_ahb_tabs.py -v`
Expected: ALL PASS (16)

- [ ] **Step 4: Restart + end-to-end walkthrough**

```bash
sudo systemctl restart baza-dashboard
```

1. `/web` → three site cards render, ahb123.com card shows live NS/apex/preview pills.
2. Page grid shows override counts (seed one via the ✏️ flow on `/ahb123` first).
3. `✏️ Edit` on the AHB123 card → lands on `/ahb123?edit=1` with Edit Mode already on.
4. Make an edit → back to `/web` → History shows it → Revert → page returns to stock on reload.
5. Reset page works with confirm.

- [ ] **Step 5: Full regression run**

Run: `venv/bin/python -m pytest tests/ -x -q -k "nav or ui_editor or editor_wiring or web_page or ahb_web_tab"`
Expected: PASS (pre-existing `test_ahb_web_tab.py` still green — we didn't touch `web_site_routes.py`)

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/web.html tests/test_web_page.py
git commit -m "feat(editor): /web command center — site cards, page grid, override history + revert"
```

- [ ] **Step 7: Session log entry** (per home-dir CLAUDE.md): append a timestamped entry to `~/Desktop/baza-session-log.md` summarizing what shipped (files, commits, restart done), using `date '+%Y-%m-%d %H:%M'` for the timestamp.

---

## Post-plan follow-ups (NOT in this plan)

- **Phase B-ii plan:** drag-to-reorder UX (`order` kind is already applied by the engine — the drag UI is what's missing), Data Hub image picker in the inspector (inspect `/api/ahb123/media/library` response shape first), stale-override badges in `/web` history, per-change undo toast.
- **Phase B-iii plan:** ahb123.com source editor — `build.py --preview` with `data-edit-id` stamping, `/web/preview/ahb123/<path>` serving, fragment edit endpoints, Draft/Publish with background deploy.
