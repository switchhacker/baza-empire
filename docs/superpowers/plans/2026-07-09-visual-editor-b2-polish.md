# Visual Editor Phase B-ii — Reorder, Data Hub Picker, Stale Badges, Undo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the dashboard visual editor's arrangement features: drag-to-reorder siblings, a Data Hub image picker, stale-override detection surfaced in `/web`, double-click rename, and per-change undo.

**Architecture:** Everything builds on the shipped B-i overrides system: `dashboard/ui_editor.py` (Flask blueprint, SQLite `ui_overrides.db`), `dashboard/static/edit.js` (ES5 IIFE apply-engine + inspector), `dashboard/templates/web.html` (command center). The `order` kind already has an apply engine (edit.js `applyDom`); this phase adds the UI that produces it, plus a fallback child-matching format. Stale detection is client-computed (only the live page knows if a selector matches) and reported to a new server endpoint so `/web` can badge it.

**Tech Stack:** Flask blueprint + sqlite3 (stdlib), ES5 JavaScript (no build step, no libraries), pytest.

## Global Constraints

- **Local-first, zero new dependencies.** stdlib + Flask only on the server; plain ES5 in `edit.js` (no arrow functions, no `let`/`const`, no template literals — the file is a `'use strict'` ES5 IIFE and must stay consistent).
- **Any modal/overlay element must be appended to `document.body`** (never inside a tab pane — `.modal-bg` nested in `#tab-*` is invisible from other tabs).
- **Every task that changes `edit.js` or `edit.css` must bump the `?v=` cache-bust number in `dashboard/templates/_nav.html`** (both lines) and update the `test_asset_version_bumped` assertion in `tests/test_editor_wiring.py` to the new number. Current version: `v=5`.
- **Template (`.html`) changes require `sudo systemctl restart baza-dashboard`** to take effect (Jinja cache, debug=False). Static `.js`/`.css` changes do not need a restart, but the `?v=` bump is what defeats the browser cache.
- Run tests with `venv/bin/python -m pytest` from the repo root `/home/switchhacker/baza-empire/agent-framework-v3`.
- The value stored for an `order` override is a JSON array of "child keys"; each entry is EITHER a CSS selector string resolved via `el.querySelector(':scope > ' + s)` OR an object `{tag, text}` matched by scanning `el.children` for tag + trimmed-text-prefix. (Strings are the B-i format; objects are the new fallback for children with no id/data-tab.)
- API: `POST /api/ui/overrides/stale-report` body `{page, stale_ids: [int], ok_ids: [int]}` → marks `stale=1`/`stale=0` on active overrides of that page. Summary rows become `{page, count, stale}`.
- `saveOverrideFor(el, kind, value)` is the generalized save (any element); `saveOverride(kind, value)` stays as `saveOverrideFor(selected, kind, value)`. Both resolve `{ok: true/false, id: <int|null>, prev: <previous value|null>}`.

---

### Task 1: Server-side stale tracking (`ui_editor.py`)

**Files:**
- Modify: `dashboard/ui_editor.py`
- Test: `tests/test_ui_editor.py` (append)

**Interfaces:**
- Consumes: existing `overrides` table, `_db()`, `normalize_page()`.
- Produces: `stale` INTEGER column (default 0); `POST /api/ui/overrides/stale-report`; `stale` field in list/history rows; `stale` count in `/api/ui/overrides/summary` rows (`{page, count, stale}`). Task 2's client reporter and web.html badges rely on these exact shapes.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ui_editor.py` (it already has a `client` fixture that monkeypatches `DB_PATH` to a tmp file and calls `init_db()` — reuse it):

```python
def test_stale_column_and_report_roundtrip(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/x", "selector": "#gone", "kind": "text", "value": "hi"})
    oid = r.get_json()["id"]
    r2 = client.post("/api/ui/overrides", json={
        "page": "/x", "selector": "#alive", "kind": "text", "value": "yo"})
    oid2 = r2.get_json()["id"]
    # fresh overrides are not stale
    rows = client.get("/api/ui/overrides?page=/x").get_json()["overrides"]
    assert all(o["stale"] == 0 for o in rows)
    # report one stale, one ok
    rep = client.post("/api/ui/overrides/stale-report", json={
        "page": "/x", "stale_ids": [oid], "ok_ids": [oid2]})
    assert rep.status_code == 200
    j = rep.get_json()
    assert j["ok"] and j["marked"] == 1 and j["cleared"] == 1
    by_id = {o["id"]: o for o in
             client.get("/api/ui/overrides/history?page=/x").get_json()["overrides"]}
    assert by_id[oid]["stale"] == 1 and by_id[oid2]["stale"] == 0
    # summary carries a stale count
    pages = client.get("/api/ui/overrides/summary").get_json()["pages"]
    px = [p for p in pages if p["page"] == "/x"][0]
    assert px["count"] == 2 and px["stale"] == 1

def test_stale_report_validation_and_scoping(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/a", "selector": "#s", "kind": "text", "value": "v"})
    oid = r.get_json()["id"]
    # non-int ids rejected
    bad = client.post("/api/ui/overrides/stale-report", json={
        "page": "/a", "stale_ids": ["x"], "ok_ids": []})
    assert bad.status_code == 422
    # wrong page does not mark
    client.post("/api/ui/overrides/stale-report", json={
        "page": "/other", "stale_ids": [oid], "ok_ids": []})
    row = client.get("/api/ui/overrides?page=/a").get_json()["overrides"][0]
    assert row["stale"] == 0

def test_resave_clears_stale(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/y", "selector": "#s", "kind": "text", "value": "v1"})
    oid = r.get_json()["id"]
    client.post("/api/ui/overrides/stale-report", json={
        "page": "/y", "stale_ids": [oid], "ok_ids": []})
    # upsert (same page+selector+kind) resets stale to 0 — the element was just edited live
    client.post("/api/ui/overrides", json={
        "page": "/y", "selector": "#s", "kind": "text", "value": "v2"})
    row = client.get("/api/ui/overrides?page=/y").get_json()["overrides"][0]
    assert row["stale"] == 0 and row["value"] == "v2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_ui_editor.py -k stale -v`
Expected: FAIL — `KeyError: 'stale'` and/or 404 on `/stale-report`.

- [ ] **Step 3: Implement**

In `dashboard/ui_editor.py`:

(a) migrate in `init_db()`:

```python
def init_db():
    with _db() as c:
        c.executescript(_SCHEMA)
        try:
            c.execute("ALTER TABLE overrides ADD COLUMN stale INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
```

Also add `stale INTEGER NOT NULL DEFAULT 0,` to the `_SCHEMA` CREATE TABLE (fresh DBs get it directly; the ALTER covers existing DBs).

(b) in `save_override()`, clear stale on upsert — change the UPDATE to:

```python
            c.execute(
                "UPDATE overrides SET value=?, fingerprint=COALESCE(?, fingerprint),"
                " stale=0, updated_at=datetime('now') WHERE id=?",
                (value, fp, row["id"]))
```

(c) new endpoint after `reset_overrides`:

```python
@ui_bp.route("/api/ui/overrides/stale-report", methods=["POST"])
def stale_report():
    """Client-side apply engine reports which overrides' selectors no longer
    match anything on the live page. Only the browser can know this."""
    b = request.get_json(force=True, silent=True) or {}
    page = normalize_page(b.get("page"))
    stale_ids = b.get("stale_ids") or []
    ok_ids = b.get("ok_ids") or []
    for ids in (stale_ids, ok_ids):
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return jsonify({"error": "stale_ids/ok_ids must be lists of ints"}), 422
    marked = cleared = 0
    with _db() as c:
        if stale_ids:
            q = ",".join("?" * len(stale_ids))
            marked = c.execute(
                "UPDATE overrides SET stale=1 WHERE page=? AND active=1"
                " AND stale=0 AND id IN (%s)" % q, [page] + stale_ids).rowcount
        if ok_ids:
            q = ",".join("?" * len(ok_ids))
            cleared = c.execute(
                "UPDATE overrides SET stale=0 WHERE page=? AND active=1"
                " AND stale=1 AND id IN (%s)" % q, [page] + ok_ids).rowcount
    return jsonify({"ok": True, "marked": marked, "cleared": cleared})
```

(d) summary gains the stale count:

```python
        rows = c.execute(
            "SELECT page, COUNT(*) AS n,"
            " SUM(CASE WHEN stale=1 THEN 1 ELSE 0 END) AS s"
            " FROM overrides WHERE active=1 GROUP BY page ORDER BY page").fetchall()
    return jsonify({"pages": [
        {"page": r["page"], "count": r["n"], "stale": r["s"] or 0} for r in rows]})
```

List/history need no change — they `SELECT *`, so `stale` rides along via `_row()`.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_ui_editor.py -v`
Expected: all pass (16 = 13 existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add dashboard/ui_editor.py tests/test_ui_editor.py
git commit -m "feat(ui-editor): stale-override tracking (column, report endpoint, summary counts)"
```

---

### Task 2: Client stale reporter + `/web` stale badges

**Files:**
- Modify: `dashboard/static/edit.js`
- Modify: `dashboard/templates/web.html`
- Modify: `dashboard/templates/_nav.html` (bump `?v=5` → `?v=6`, both lines)
- Test: `tests/test_editor_wiring.py` (append + update version test), `tests/test_web_page.py` (append)

**Interfaces:**
- Consumes: Task 1's `POST /api/ui/overrides/stale-report` and `{page, count, stale}` summary rows, `stale` field in history rows.
- Produces: nothing downstream depends on this task.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_editor_wiring.py`:

```python
def test_stale_reporter_present():
    js = read("dashboard", "static", "edit.js")
    assert "stale-report" in js
    assert "reportStale" in js
```

Append to `tests/test_web_page.py` (same `read()` helper pattern):

```python
def test_web_page_shows_stale_badges():
    html = read("dashboard", "templates", "web.html")
    assert "stale" in html  # summary badge + history pill wiring
```

Update `test_asset_version_bumped` in `tests/test_editor_wiring.py` to `v=6`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py tests/test_web_page.py -v`
Expected: the two new tests FAIL; `test_asset_version_bumped` FAILS (still v=5).

- [ ] **Step 3: Implement edit.js reporter**

In `dashboard/static/edit.js`, after the `refresh()` function add:

```js
var staleReported = false;
function reportStale() {
  // One shot per page load, delayed so JS-rendered content has appeared.
  if (staleReported || !OVERRIDES.length) return;
  staleReported = true;
  var stale = [], ok = [];
  OVERRIDES.forEach(function (o) {
    var n = 0;
    try { n = document.querySelectorAll(o.selector).length; } catch (e) {}
    (n ? ok : stale).push(o.id);
  });
  fetch(API + '/stale-report', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page: PAGE, stale_ids: stale, ok_ids: ok })
  }).catch(function () {});
}
```

In `boot()`, after the `refresh().then(...)` chain sets up the MutationObserver, add inside the `.then`:

```js
      setTimeout(reportStale, 2500);
```

Expose it for debugging: add `reportStale: reportStale,` to the `window.BazaEdit` object.

- [ ] **Step 4: Implement web.html badges**

In `dashboard/templates/web.html`:

(a) `loadPageCards()` — keep a `staleCounts` map and render a warn pill. Replace the counts block and card template:

```js
  let counts = {}, staleCounts = {};
  try{
    const j = await (await fetch('/api/ui/overrides/summary')).json();
    (j.pages||[]).forEach(p => { counts[p.page] = p.count; staleCounts[p.page] = p.stale||0; });
  }catch(e){}
  document.getElementById('page-cards').innerHTML = DASH_PAGES.map(([path,label]) => {
    const n = counts[path]||0, s = staleCounts[path]||0;
    return `<div class="card" style="margin:0"><div class="card-body">
      <div style="font-weight:800;color:#fff;margin-bottom:4px">${label}</div>
      <div style="font-size:11px;color:#555;font-family:monospace">${path}</div>
      <div style="margin:8px 0">${n ? `<span class="pill warn">${n} override${n>1?'s':''}</span>` : '<span class="pill off">stock</span>'}
        ${s ? `<span class="pill warn" title="selector no longer matches — template changed underneath">⚠ ${s} stale</span>` : ''}</div>
      <a class="btn sm" href="${path}${path.includes('?')?'&':'?'}edit=1">✏️ Edit</a>
      <button class="btn ghost sm" onclick="showHistory('${path}')">🕘 History</button>
    </div></div>`;
  }).join('');
```

(b) `showHistory()` — in the State cell, mark stale actives:

```js
        <td>${o.active ? (o.stale ? '<span class="pill warn" title="selector no longer matches the page">⚠ stale</span>' : '<span class="pill ok">active</span>') : '<span class="pill off">reverted</span>'}</td>
```

- [ ] **Step 5: Bump cache-bust**

In `dashboard/templates/_nav.html` change both `?v=5` to `?v=6`.

- [ ] **Step 6: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py tests/test_web_page.py tests/test_ui_editor.py -v`
Expected: all pass.

- [ ] **Step 7: Restart dashboard + smoke check**

```bash
sudo systemctl restart baza-dashboard
sleep 2 && curl -s localhost:8888/web | grep -c 'edit.js?v=6'
```
Expected: `1`.

- [ ] **Step 8: Commit**

```bash
git add dashboard/static/edit.js dashboard/templates/web.html dashboard/templates/_nav.html tests/test_editor_wiring.py tests/test_web_page.py
git commit -m "feat(editor): client stale reporter + stale badges on /web (v=6)"
```

---

### Task 3: Data Hub image picker in the inspector

**Files:**
- Modify: `dashboard/static/edit.js`
- Modify: `dashboard/static/edit.css`
- Modify: `dashboard/templates/_nav.html` (bump `?v=6` → `?v=7`, both lines)
- Test: `tests/test_editor_wiring.py` (append + version bump)

**Interfaces:**
- Consumes: `GET /api/ahb123/media/library?type=photo&page=&page_size=&q=` → `{items:[{filepath, filename, caption, ...}], total, page, page_size}`; thumbnails `GET /api/cloud/thumb/<filepath>?size=200`.
- Produces: picked image URL written into the inspector's Image URL input as `/api/cloud/thumb/<filepath>?size=1600` (always browser-renderable JPEG — originals can be HEIC). Nothing downstream depends on this task.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_editor_wiring.py`:

```python
def test_datahub_picker_present():
    js = read("dashboard", "static", "edit.js")
    for needle in ["openMediaPicker", "api/ahb123/media/library", "api/cloud/thumb"]:
        assert needle in js, f"missing {needle}"
    # modal must be appended to document.body (hard rule: body-level modals)
    assert "document.body.appendChild(pick" in js

def test_picker_styles_present():
    css = read("dashboard", "static", "edit.css")
    assert "baza-dh-picker" in css
```

Update `test_asset_version_bumped` to `v=7`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement the picker in edit.js**

Add after the `toast()` function (before `buildInspector`):

```js
/* ---------- Data Hub media picker (body-level modal) ---------- */
function openMediaPicker(onPick) {
  var old = document.getElementById('baza-dh-picker');
  if (old) old.remove();
  var pick = document.createElement('div');
  pick.id = 'baza-dh-picker';
  var state = { page: 1, q: '' };
  var head = mkEl('div', { 'class': 'bdp-head' });
  var q = mkEl('input', { type: 'text', placeholder: 'Search filename or caption…' });
  head.appendChild(q);
  var close = mkBtn('✕', '', function () { pick.remove(); });
  head.appendChild(close);
  pick.appendChild(head);
  var grid = mkEl('div', { 'class': 'bdp-grid' });
  pick.appendChild(grid);
  var foot = mkEl('div', { 'class': 'bdp-foot' });
  var prev = mkBtn('← Prev', '', function () { if (state.page > 1) { state.page--; load(); } });
  var info = mkEl('span', { 'class': 'bdp-info' }, '');
  var next = mkBtn('Next →', '', function () { state.page++; load(); });
  foot.appendChild(prev); foot.appendChild(info); foot.appendChild(next);
  pick.appendChild(foot);
  function load() {
    grid.textContent = 'Loading…';
    var url = '/api/ahb123/media/library?type=photo&page_size=60&page=' + state.page +
              (state.q ? '&q=' + encodeURIComponent(state.q) : '');
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      grid.innerHTML = '';
      var items = j.items || [];
      if (!items.length) { grid.textContent = 'No photos found.'; }
      items.forEach(function (it) {
        var cell = mkEl('div', { 'class': 'bdp-cell', title: (it.caption || it.filename || '') });
        var img = mkEl('img', {
          src: '/api/cloud/thumb/' + encodeURI(it.filepath) + '?size=200',
          loading: 'lazy', alt: it.filename || ''
        });
        cell.appendChild(img);
        cell.addEventListener('click', function () {
          onPick('/api/cloud/thumb/' + encodeURI(it.filepath) + '?size=1600');
          pick.remove();
        });
        grid.appendChild(cell);
      });
      var pages = Math.max(1, Math.ceil((j.total || 0) / (j.page_size || 60)));
      info.textContent = 'page ' + state.page + ' / ' + pages + ' · ' + (j.total || 0) + ' photos';
      if (state.page > pages) { state.page = pages; }
    }).catch(function () { grid.textContent = 'Failed to load Data Hub media.'; });
  }
  var qt = null;
  q.addEventListener('input', function () {
    clearTimeout(qt);
    qt = setTimeout(function () { state.q = q.value.trim(); state.page = 1; load(); }, 350);
  });
  document.body.appendChild(pick);
  load();
}
```

In `buildInspector`, inside the `if (selected.tagName === 'IMG')` section, after the upload `file` input handling, add:

```js
    si.appendChild(mkBtn('📦 Pick from Data Hub', '', function () {
      openMediaPicker(function (pickedUrl) {
        url.value = pickedUrl;
        toast('picked — hit Save image');
      });
    }));
```

Add `openMediaPicker: openMediaPicker,` to `window.BazaEdit`.

- [ ] **Step 4: Style the picker in edit.css**

Append to `dashboard/static/edit.css`:

```css
/* Data Hub media picker */
#baza-dh-picker{position:fixed;inset:6vh 8vw;background:#0d0d1e;border:1px solid #2a2a4a;
  border-radius:12px;z-index:100001;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.7)}
#baza-dh-picker .bdp-head{display:flex;gap:8px;padding:12px 14px;border-bottom:1px solid #1a1a3a}
#baza-dh-picker .bdp-head input{flex:1;background:#111;border:1px solid #2a2a4a;color:#eee;
  border-radius:8px;padding:8px 10px;font-size:13px}
#baza-dh-picker .bdp-grid{flex:1;overflow-y:auto;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;padding:12px 14px;color:#888}
#baza-dh-picker .bdp-cell{aspect-ratio:1;overflow:hidden;border-radius:8px;cursor:pointer;
  border:2px solid transparent;background:#111}
#baza-dh-picker .bdp-cell:hover{border-color:#e94560}
#baza-dh-picker .bdp-cell img{width:100%;height:100%;object-fit:cover;display:block;min-width:0}
#baza-dh-picker .bdp-foot{display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;border-top:1px solid #1a1a3a}
#baza-dh-picker .bdp-info{font-size:12px;color:#888}
```

- [ ] **Step 5: Bump cache-bust to v=7** in `_nav.html` (both lines).

- [ ] **Step 6: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/static/edit.js dashboard/static/edit.css dashboard/templates/_nav.html tests/test_editor_wiring.py
git commit -m "feat(editor): Data Hub image picker modal in inspector (v=7)"
```

---

### Task 4: Drag-to-reorder siblings + robust order matching

**Files:**
- Modify: `dashboard/static/edit.js`
- Modify: `dashboard/static/edit.css`
- Modify: `dashboard/templates/_nav.html` (bump `?v=7` → `?v=8`)
- Test: `tests/test_editor_wiring.py` (append + version bump)

**Interfaces:**
- Consumes: existing `applyDom` order branch, `saveOverride`, `selectorFor`, `esc`.
- Produces: `childKeyFor(el)` → string selector or `{tag, text}` object; `saveOverrideFor(el, kind, value)` (generalized save — Task 5's undo depends on its `{ok, id, prev}` resolution shape); extended `applyDom` order matching that accepts both entry formats.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_editor_wiring.py`:

```python
def test_reorder_ui_present():
    js = read("dashboard", "static", "edit.js")
    for needle in ["childKeyFor", "saveOverrideFor", "Move up", "Move down",
                   "Drag siblings", "Save order", "dragstart", "dragover"]:
        assert needle in js, f"missing {needle}"

def test_order_matching_supports_object_entries():
    js = read("dashboard", "static", "edit.js")
    # the {tag,text} fallback matcher must exist in the apply engine
    assert "findChildByKey" in js
```

Update `test_asset_version_bumped` to `v=8`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement child keys + matcher + generalized save**

In `edit.js` after `fingerprintFor` add:

```js
function childKeyFor(el) {
  // Key for one direct child inside an order override. Must stay valid after
  // reordering, so positional selectors are the LAST resort.
  if (el.id) return '#' + esc(el.id);
  var dt = el.getAttribute && el.getAttribute('data-tab');
  if (dt) return el.tagName.toLowerCase() + '[data-tab="' + esc(dt) + '"]';
  var txt = (el.textContent || '').trim().slice(0, 40);
  if (txt) return { tag: el.tagName.toLowerCase(), text: txt };
  var par = el.parentElement;
  var same = par ? Array.prototype.filter.call(par.children, function (c) {
    return c.tagName === el.tagName;
  }) : [el];
  return el.tagName.toLowerCase() + ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
}
function findChildByKey(el, key) {
  if (typeof key === 'string') {
    try { return el.querySelector(':scope > ' + key); } catch (e) { return null; }
  }
  if (key && key.tag && typeof key.text === 'string') {
    for (var i = 0; i < el.children.length; i++) {
      var c = el.children[i];
      if (c.tagName.toLowerCase() === key.tag &&
          (c.textContent || '').trim().slice(0, key.text.length) === key.text) return c;
    }
  }
  return null;
}
```

Rewrite the `order` branch in `applyDom` to use the matcher:

```js
      } else if (o.kind === 'order' && Array.isArray(o.value)) {
        var kids = o.value.map(function (s) { return findChildByKey(el, s); })
          .filter(Boolean).filter(function (k, i, a) { return a.indexOf(k) === i; });
        var current = Array.prototype.filter.call(el.children, function (c) { return kids.indexOf(c) !== -1; });
        var moved = kids.some(function (k, i) { return k !== current[i]; });
        if (moved) kids.forEach(function (k) { el.appendChild(k); });
      }
```

Generalize save — replace `saveOverride` with:

```js
function saveOverrideFor(el, kind, value) {
  if (!el) return Promise.resolve({ ok: false, id: null, prev: null });
  var sel = selectorFor(el);
  var prev = null;
  OVERRIDES.forEach(function (o) {
    if (o.selector === sel && o.kind === kind) prev = o.value;
  });
  return fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      page: PAGE, selector: sel, kind: kind, value: value,
      fingerprint: fingerprintFor(el)
    })
  }).then(function (r) {
    if (!r.ok) throw new Error('save failed: ' + r.status);
    return r.json();
  }).then(function (j) {
    return refresh().then(function () { return { ok: true, id: j.id, prev: prev }; });
  }).catch(function () { return { ok: false, id: null, prev: null }; });
}
function saveOverride(kind, value) { return saveOverrideFor(selected, kind, value); }
```

**Compatibility sweep (required):** every existing `saveOverride(...).then(function (ok) {...})` call in `buildInspector` treats the result as a boolean. Update each to `.then(function (res) { toast(res.ok ? ... : ...); })` — there are 5 call sites (text ×2, image, link, style) plus hide. Keep the toast messages identical.

- [ ] **Step 4: Implement the Reorder section in buildInspector**

Add after the "visibility + reset" section in `buildInspector`:

```js
  // reorder among siblings
  var par = selected.parentElement;
  var sibs = par ? Array.prototype.filter.call(par.children, function (c) {
    return !inChrome(c);
  }) : [];
  if (par && par !== document.body && sibs.length > 1) {
    var so = section(panel, 'Reorder among siblings');
    function persistOrder() {
      var keys = Array.prototype.filter.call(par.children, function (c) { return !inChrome(c); })
        .map(childKeyFor);
      saveOverrideFor(par, 'order', keys).then(function (res) {
        toast(res.ok ? '✓ order saved' : '✗ save failed');
      });
    }
    so.appendChild(mkBtn('⬆ Move up', '', function () {
      var p = selected.previousElementSibling;
      if (p) { par.insertBefore(selected, p); persistOrder(); }
    }));
    so.appendChild(mkBtn('⬇ Move down', '', function () {
      var n = selected.nextElementSibling;
      if (n) { par.insertBefore(n, selected); persistOrder(); }
    }));
    var dragging = false, dragEl = null;
    var dragBtn = mkBtn('↕ Drag siblings', '', function () {
      dragging = !dragging;
      dragBtn.classList.toggle('primary', dragging);
      Array.prototype.forEach.call(par.children, function (c) {
        if (inChrome(c)) return;
        c.draggable = dragging;
        c.classList.toggle('baza-draggable', dragging);
      });
    });
    so.appendChild(dragBtn);
    par.addEventListener('dragstart', function (e) {
      if (!dragging) return;
      dragEl = e.target.closest && e.target.closest(selectorFor(par) + ' > *');
      if (dragEl) e.dataTransfer.effectAllowed = 'move';
    });
    par.addEventListener('dragover', function (e) {
      if (!dragging || !dragEl) return;
      e.preventDefault();
      var over = e.target;
      while (over && over.parentElement !== par) over = over.parentElement;
      if (!over || over === dragEl) return;
      var r = over.getBoundingClientRect();
      var before = (e.clientY - r.top) < r.height / 2;
      par.insertBefore(dragEl, before ? over : over.nextSibling);
    });
    par.addEventListener('drop', function (e) {
      if (!dragging || !dragEl) return;
      e.preventDefault();
      dragEl = null;
      persistOrder();
    });
    so.appendChild(mkBtn('💾 Save order', 'primary', persistOrder));
    so.appendChild(mkEl('div', { 'class': 'bep-note' },
      'Move with ⬆/⬇ (auto-saves), or toggle Drag siblings, drag cards around, then Save order.'));
  }
```

Add `childKeyFor: childKeyFor,` to `window.BazaEdit`.

- [ ] **Step 5: Style the drag affordance in edit.css**

```css
.baza-draggable{cursor:grab;outline:1px dashed #7c3aed;outline-offset:2px}
.baza-draggable:active{cursor:grabbing}
```

- [ ] **Step 6: Bump cache-bust to v=8** in `_nav.html`.

- [ ] **Step 7: Run tests**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py tests/test_ui_editor.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add dashboard/static/edit.js dashboard/static/edit.css dashboard/templates/_nav.html tests/test_editor_wiring.py
git commit -m "feat(editor): drag-to-reorder siblings + {tag,text} order keys (v=8)"
```

---

### Task 5: Double-click rename + per-change undo

**Files:**
- Modify: `dashboard/static/edit.js`
- Modify: `dashboard/static/edit.css`
- Modify: `dashboard/templates/_nav.html` (bump `?v=8` → `?v=9`)
- Test: `tests/test_editor_wiring.py` (append + version bump)

**Interfaces:**
- Consumes: Task 4's `saveOverrideFor` (`{ok, id, prev}` resolution), existing Edit-on-page contenteditable flow.
- Produces: `startInlineEdit(node)` shared helper; undo affordance in the hint bar. Nothing downstream depends on this task.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_editor_wiring.py`:

```python
def test_dblclick_rename_and_undo_present():
    js = read("dashboard", "static", "edit.js")
    for needle in ["startInlineEdit", "dblclick", "showUndo", "↶ Undo"]:
        assert needle in js, f"missing {needle}"
```

Update `test_asset_version_bumped` to `v=9`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py -v`
Expected: new test FAILS.

- [ ] **Step 3: Extract startInlineEdit + wire dblclick**

In `edit.js`, add near `toast()`:

```js
function startInlineEdit(node) {
  // Shared by the panel's "Edit on page" button and dblclick-rename.
  if (!node || node.getAttribute('contenteditable') === 'true') { if (node) node.focus(); return; }
  node.setAttribute('contenteditable', 'true');
  node.focus();
  var done = function () {
    node.removeAttribute('contenteditable');
    node.removeEventListener('blur', done);
    var txt = (node.textContent || '').trim();
    saveOverrideFor(node, 'text', txt).then(function (res) {
      toast(res.ok ? '✓ text saved' : '✗ save failed');
      if (res.ok) showUndo(res);
      // keep the panel textarea in sync if this node is the selected one
      if (selected === node) { var p = document.getElementById('baza-edit-panel');
        if (p && p.style.display !== 'none') buildInspector(p); }
    });
  };
  node.addEventListener('blur', done);
}
```

Replace the body of the panel's "Edit on page" click handler with `startInlineEdit(selected);` (delete the old inline `node`/`done` logic — `startInlineEdit` is its exact replacement; the textarea-sync line moves into the helper as above).

Add the dblclick listener next to the existing click listener:

```js
document.addEventListener('dblclick', function (e) {
  if (!editMode || inChrome(e.target)) return;
  if (e.target.childElementCount !== 0) return; // leaf text elements only
  e.preventDefault();
  e.stopPropagation();
  select(e.target);
  startInlineEdit(e.target);
}, true);
```

- [ ] **Step 4: Implement undo**

Add after `toast()`:

```js
var lastChange = null;
function showUndo(res) {
  // res = {ok, id, prev} plus we stash selector/kind on save below
  lastChange = res;
  var h = document.getElementById('baza-edit-hint');
  if (!h) return;
  var msg = h.textContent || 'saved';   // keep whatever toast() just wrote
  h.innerHTML = '';
  h.appendChild(document.createTextNode(msg + ' '));
  var u = mkBtn('↶ Undo', 'primary', function () {
    var c = lastChange; lastChange = null;
    if (!c) return;
    var done = function (ok) {
      refresh().then(function () { toast(ok ? '✓ undone' : '✗ undo failed'); });
    };
    if (c.prev === null || c.prev === undefined) {
      // the save created this override — undo = revert it entirely
      fetch(API + '/' + c.id + '/revert', { method: 'POST' })
        .then(function (r) { done(r.ok); }).catch(function () { done(false); });
    } else {
      // restore the previous value via upsert
      fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page: PAGE, selector: c.selector, kind: c.kind, value: c.prev })
      }).then(function (r) { done(r.ok); }).catch(function () { done(false); });
    }
  });
  h.appendChild(u);
  setTimeout(function () {
    if (editMode && h && !h.contains(document.activeElement)) {
      h.textContent = '✏️ Edit Mode — click any element · Esc to exit';
    }
  }, 6000);
}
```

In `saveOverrideFor`, extend the success resolution to carry selector/kind so undo can act on them:

```js
    return refresh().then(function () {
      return { ok: true, id: j.id, prev: prev, selector: sel, kind: kind };
    });
```

Then in each inspector save handler that shows a `✓` toast, call `showUndo(res)` when `res.ok` (text, image, link, style, hide — NOT reorder's `persistOrder`, whose repeated auto-saves would make prev-chains confusing; reorder undo = Reset element).

Style the hint button in `edit.css`:

```css
#baza-edit-hint .bep-btn{margin-left:10px;padding:2px 10px;font-size:11px}
```

- [ ] **Step 5: Bump cache-bust to v=9** in `_nav.html`.

- [ ] **Step 6: Run the full editor test set**

Run: `venv/bin/python -m pytest tests/test_editor_wiring.py tests/test_ui_editor.py tests/test_web_page.py tests/test_nav_ahb_tabs.py tests/test_nav_main_tabs.py -v`
Expected: all pass.

- [ ] **Step 7: Restart + smoke**

```bash
sudo systemctl restart baza-dashboard
sleep 2 && curl -s localhost:8888/web | grep -c 'edit.js?v=9'
```
Expected: `1`.

- [ ] **Step 8: Commit**

```bash
git add dashboard/static/edit.js dashboard/static/edit.css dashboard/templates/_nav.html tests/test_editor_wiring.py
git commit -m "feat(editor): dblclick rename + per-change undo toast (v=9)"
```
