# Mobile PWA = Full Desktop Baza UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installable `/mobile` PWA serve the entire desktop Baza dashboard (every nav page), rendered as the true desktop layout scaled-to-fit on phones with native pinch-zoom, instead of the stripped-down `mobile.html` app.

**Architecture:** Two changes. (1) Backend routes in `app.py`: `/mobile` 302-redirects to `/`; the old `mobile.html` is preserved at a new `/mobile-classic` route; the manifest `start_url` becomes `/`. (2) One shared template, `templates/_nav.html` (included by all 22 nav-bearing desktop pages), gets a `<script>` that injects the PWA head tags + registers the service worker on every page, and — on small screens only — rewrites the viewport to a fixed `width=1280` scaled to fit so the phone shows the real desktop, pinch-zoomable. Desktop browser viewing is unchanged.

**Tech Stack:** Python 3 / Flask (Jinja templates, no base template), vanilla JS, existing service worker at `/sw.js`. Tests: pytest via Flask `test_client()`.

## Global Constraints

- Working dir: `/home/switchhacker/baza-empire/agent-framework-v3/dashboard`.
- Run tests with the project venv: `../venv/bin/pytest` (i.e. `/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/pytest`) from the `dashboard/` dir.
- `baza-dashboard.service` runs `debug=False` → Jinja caches templates. After editing any `templates/*.html`, you MUST `sudo systemctl restart baza-dashboard` for changes to show. (Tests import the app directly and are unaffected.)
- DO NOT manually `git commit`/`git push` this framework — the `claw-auto-git` user timer commits hourly. The "Commit" steps below are written per the skill, but in THIS repo replace each `git commit` with **staging the change and letting the auto-git timer commit** (or commit only if Serge says it's time-sensitive). Stage with `git add` is fine; skip `git commit`.
- `redirect` is already imported in `app.py:8`. No new imports needed.
- Nothing is deleted: `mobile.html`, `/mobile/manifest.json`, `/mobile/sw.js`, `/sw.js` all stay.
- PWA identity copied verbatim from existing `mobile.html`: manifest href `/mobile/manifest.json`, theme-color `#07070f`, apple-touch-icon `/static/img/ahb_logo.jpeg`, app title `Baza`, status-bar `black-translucent`.

---

### Task 1: Repoint `/mobile` to the desktop, preserve old app at `/mobile-classic`, fix manifest

**Files:**
- Modify: `app.py:5417-5423` (`mobile_page`) and `app.py:5425-5441` (`mobile_manifest`)
- Create (new route, place adjacent to `mobile_page`): `/mobile-classic` in `app.py`
- Test: `tests/test_mobile_pwa.py`

**Interfaces:**
- Consumes: Flask `redirect`, `render_template`, `make_response`, `jsonify` (all already imported in `app.py`).
- Produces:
  - `GET /mobile` → `302`, `Location` header path == `/`.
  - `GET /mobile-classic` → `200`, renders `mobile.html` (body contains `tab-bar-item`).
  - `GET /mobile/manifest.json` → JSON with `start_url == "/"`, `scope == "/"`, `display == "standalone"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mobile_pwa.py`:

```python
"""Tests for the mobile PWA repointing onto the full desktop dashboard.

Mirrors the client fixture pattern in tests/test_invoice_terms.py: import the
real app.py and use its Flask test client (the shared conftest `app` fixture
only wires the email blueprint, so it can't see these routes).
"""
import os
import sys

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import app as appmod


@pytest.fixture
def client():
    with appmod.app.test_client() as c:
        yield c


def test_mobile_redirects_to_desktop_root(client):
    res = client.get("/mobile")
    assert res.status_code == 302
    # Location may be absolute or relative; the path must be the desktop root.
    loc = res.headers["Location"]
    assert loc.rstrip("/").endswith("") and loc.endswith("/"), loc
    assert loc in ("/", "http://localhost/")


def test_mobile_classic_still_serves_old_app(client):
    res = client.get("/mobile-classic")
    assert res.status_code == 200
    assert b"tab-bar-item" in res.data  # marker unique to mobile.html's bottom tab bar


def test_manifest_start_url_is_desktop_root(client):
    res = client.get("/mobile/manifest.json")
    assert res.status_code == 200
    data = res.get_json()
    assert data["start_url"] == "/"
    assert data["scope"] == "/"
    assert data["display"] == "standalone"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../venv/bin/pytest tests/test_mobile_pwa.py -v`
Expected: FAIL — `test_mobile_redirects_to_desktop_root` gets 200 (renders mobile.html) not 302; `test_mobile_classic_still_serves_old_app` gets 404 (route doesn't exist); `test_manifest_start_url_is_desktop_root` fails on `start_url == "/mobile"`.

- [ ] **Step 3: Edit `mobile_page` and add `/mobile-classic`**

Replace the existing `app.py:5417-5423` block:

```python
@app.route('/mobile')
def mobile_page():
    resp = make_response(render_template('mobile.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp
```

with:

```python
@app.route('/mobile')
def mobile_page():
    # The mobile PWA now IS the full desktop dashboard. Send installs/links to
    # the desktop root; _nav.html scales it to fit on phones and keeps it
    # installable/standalone. The old curated mobile app lives at /mobile-classic.
    return redirect('/')


@app.route('/mobile-classic')
def mobile_classic_page():
    resp = make_response(render_template('mobile.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp
```

- [ ] **Step 4: Edit the manifest `start_url`**

In `app.py:5425-5441` (`mobile_manifest`), change the one line:

```python
        "start_url": "/mobile",
```

to:

```python
        "start_url": "/",
```

Leave `scope`, `display`, colors, and `icons` exactly as they are.

- [ ] **Step 5: Run tests to verify they pass**

Run: `../venv/bin/pytest tests/test_mobile_pwa.py -v`
Expected: 3 passed.

- [ ] **Step 6: Stage (auto-git commits)**

```bash
git add tests/test_mobile_pwa.py app.py
# Do NOT `git commit` — claw-auto-git timer owns framework commits (see Global Constraints).
```

---

### Task 2: Inject PWA head tags, service worker, and mobile desktop-scaling into `_nav.html`

**Files:**
- Modify: `templates/_nav.html` (append a new `<script>` block at the very end of the file, after the existing closing `</script>`)
- Test: `tests/test_mobile_pwa.py` (add cases)

**Interfaces:**
- Consumes: the routes from Task 1 (`/mobile/manifest.json`, `/sw.js`) and the existing `/sw.js` service worker.
- Produces: every page that includes `_nav.html` ships, in its HTML, an inline script whose source text contains the literals `'/mobile/manifest.json'`, `serviceWorker.register('/sw.js'`, and `width=1280` — verifiable server-side. At runtime the script injects the manifest link + apple/theme metas into `document.head`, registers the SW, and on screens narrower than 1024px rewrites the viewport meta to `width=1280, initial-scale=<fit>, user-scalable=yes`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mobile_pwa.py`:

```python
def test_nav_injects_pwa_and_desktop_scaling(client):
    # `/datahub` is a plain GET page that includes _nav.html and renders without
    # form state — a stable place to assert the shared nav injection ships.
    res = client.get("/datahub")
    assert res.status_code == 200, res.status_code
    html = res.data.decode("utf-8", "replace")
    # PWA install plumbing delivered on every nav page:
    assert "/mobile/manifest.json" in html
    assert "serviceWorker.register('/sw.js'" in html
    # Mobile-only true-desktop scaling:
    assert "width=1280" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../venv/bin/pytest tests/test_mobile_pwa.py::test_nav_injects_pwa_and_desktop_scaling -v`
Expected: FAIL — none of those literals are in `_nav.html` yet (assertion error on `/mobile/manifest.json`).

> If `/datahub` returns a redirect/non-200 in the test environment (auth), switch the path in this test to `/` and re-run; `index.html` also includes `_nav.html`. Keep whichever returns 200.

- [ ] **Step 3: Append the injection script to `_nav.html`**

At the very end of `templates/_nav.html` (after the final `</script>`), add:

```html
<script>
/* PWA-everywhere + true-desktop-on-mobile.
   _nav.html is included by every desktop dashboard page, so this single block
   makes the whole dashboard installable/standalone and, on phones, renders the
   real desktop layout scaled to fit (pinch-zoomable). Desktop browsers (wide
   viewport) are left exactly as they were. */
(function(){
  var head = document.head;
  function el(tag, attrs){ var e = document.createElement(tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
  function ensure(sel, make){ if (!head.querySelector(sel)) head.appendChild(make()); }

  // --- Installable PWA head tags (idempotent; copied from the old mobile.html) ---
  ensure('link[rel="manifest"]', function(){ return el('link', {rel:'manifest', href:'/mobile/manifest.json'}); });
  ensure('meta[name="apple-mobile-web-app-capable"]', function(){ return el('meta', {name:'apple-mobile-web-app-capable', content:'yes'}); });
  ensure('meta[name="mobile-web-app-capable"]', function(){ return el('meta', {name:'mobile-web-app-capable', content:'yes'}); });
  ensure('meta[name="apple-mobile-web-app-status-bar-style"]', function(){ return el('meta', {name:'apple-mobile-web-app-status-bar-style', content:'black-translucent'}); });
  ensure('meta[name="apple-mobile-web-app-title"]', function(){ return el('meta', {name:'apple-mobile-web-app-title', content:'Baza'}); });
  ensure('meta[name="theme-color"]', function(){ return el('meta', {name:'theme-color', content:'#07070f'}); });
  ensure('link[rel="apple-touch-icon"]', function(){ return el('link', {rel:'apple-touch-icon', href:'/static/img/ahb_logo.jpeg'}); });

  // --- Mobile only: render the full desktop at a fixed 1280px width, scaled to fit ---
  if (window.innerWidth && window.innerWidth < 1024) {
    var dw = window.innerWidth;                       // device CSS width before we change the viewport
    var scale = Math.max(0.1, dw / 1280);
    var vp = head.querySelector('meta[name="viewport"]');
    if (!vp) { vp = el('meta', {name:'viewport'}); head.appendChild(vp); }
    vp.setAttribute('content', 'width=1280, initial-scale=' + scale.toFixed(4) + ', user-scalable=yes, viewport-fit=cover');
  }

  // --- Service worker: satisfies install criteria + speeds cold loads ---
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function(){
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function(){});
    });
  }
})();
</script>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../venv/bin/pytest tests/test_mobile_pwa.py -v`
Expected: 4 passed (3 from Task 1 + this one).

- [ ] **Step 5: Restart the dashboard and smoke-check live**

```bash
sudo systemctl restart baza-dashboard.service
sleep 2
curl -sI http://localhost:8888/mobile | grep -iE '^HTTP|^location'        # expect 302 + Location: .../
curl -s  http://localhost:8888/mobile/manifest.json | grep -o '"start_url": "/"'   # expect a match
curl -s  http://localhost:8888/ | grep -c "serviceWorker.register('/sw.js'"        # expect >= 1
curl -s  http://localhost:8888/mobile-classic | grep -c "tab-bar-item"             # expect >= 1
```

Expected: 302 with `Location` ending in `/`; manifest `start_url` matches; nav injection present on `/`; classic app still served.

- [ ] **Step 6: Stage (auto-git commits)**

```bash
git add templates/_nav.html tests/test_mobile_pwa.py
# Do NOT `git commit` — claw-auto-git timer owns framework commits.
```

---

## Manual device verification (after both tasks)

Not automatable here — do on a phone once code is in:

1. Open `http://<baza-host>:8888/mobile` on the phone → should land on `/` (full desktop dashboard).
2. Page renders as the **desktop layout shrunk to fit** the screen; pinch-zoom in/out works.
3. "Add to Home Screen" → icon installs; opening it launches **standalone** (no browser chrome) into the dashboard.
4. Tap nav items (AHB123, Data Hub, Edge, …) → each loads, stays standalone, stays desktop-scaled + zoomable.
5. On a desktop browser, `/` and all pages look **exactly as before** (viewport untouched at wide widths).

## Self-Review

- **Spec coverage:** (1) `/mobile`→`/` + manifest `start_url`→`/` + `/mobile-classic` preserve → Task 1. (2) `_nav.html` injects manifest link + apple metas + SW register across all 22 pages → Task 2. (3) Native pinch-zoom + true-desktop-scaled-down viewport (mobile only, desktop untouched) → Task 2 viewport rewrite. (4) Nothing deleted → Task 1 keeps `mobile.html` + routes. (5) Stragglers (`cloud/shell/agent.html`) explicitly out of scope → noted in spec, not in this plan. All spec sections covered.
- **Placeholder scan:** No TBD/TODO; every code/edit step shows exact content; test bodies are complete.
- **Type/name consistency:** `mobile_page`, `mobile_classic_page`, `mobile_manifest` route names consistent; test literals (`tab-bar-item`, `serviceWorker.register('/sw.js'`, `width=1280`, `/mobile/manifest.json`) match exactly what the implementation emits.
