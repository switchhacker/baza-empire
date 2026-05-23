# Social Studio v2.0 — Polish + Preview + Mobile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First phase of the Social Studio v2 mega-expansion — polish/UX (toasts, shortcuts, render progress, drag/trim, render-all, A/B, translate), compatibility preview (device frames, platform overlays, safe zones, caption truncation, IG grid), mobile/a11y (responsive, touch, tooltips, tour, ARIA).

**Architecture:** All work appends to `dashboard/templates/ahb123.html` (frontend), plus a small backend addition to `dashboard/social_studio.py` for async render polling. No backend refactor in this phase — that's v2.1. Each new JS feature lands as its own IIFE under `window.SocialStudio.modules.*`.

**Tech Stack:** Vanilla JS / CSS in `ahb123.html`; Flask in `dashboard/social_studio.py`; pytest for backend tests; manual browser smoke for UI.

**Spec:** `docs/superpowers/specs/2026-05-22-ahb123-social-studio-v2-design.md` Bundles A + F + J.

---

## Process notes

- **No git --amend** — the claw-auto-git timer commits hourly; amending races with it. Make forward commits only.
- **Template cache** — after ANY edit to `dashboard/templates/ahb123.html`, run `sudo systemctl restart baza-dashboard`. The dashboard caches Jinja templates with debug=False.
- **HTML escape** — every new IIFE must define its own local `_esc(s)` helper for innerHTML interpolation. Pattern from Phase 1: `s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')`.
- **Body-level modals** — modals MUST be declared at body level (NOT nested in `<div class="tab-pane">`). Phase 1 declared modal slots at `dashboard/templates/ahb123.html` lines 4494-4500.
- **File size** — `dashboard/templates/ahb123.html` is ~18,400 lines after Phase 1. Use grep to find insertion points; don't read the whole file.
- All file paths are absolute from repo root `/home/switchhacker/baza-empire/agent-framework-v3/`.
- Commit messages all end with `\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## File Structure

**Modified:**
- `dashboard/templates/ahb123.html` — append new CSS rules + new `<script>` blocks for ~12 new IIFE modules + new body-level modal slots
- `dashboard/social_studio.py` — async render endpoint + job-cancel route + PID column migration
- `dashboard/social_render.py` — install real Inter fonts via script (no Python change)
- `dashboard/static/fonts/Inter-Bold.ttf` and `Inter-Regular.ttf` — replace placeholder text stubs with real OFL TTFs
- `dashboard/social_install_assets.sh` — new script that downloads fonts (and later, music/LUTs/voices in v2.1)

**Created (new IIFE modules in `ahb123.html`):**
- `SocialStudio.modules.toast` — global notification system
- `SocialStudio.modules.keymap` — keyboard shortcuts
- `SocialStudio.modules.progress` — render job polling UI
- `SocialStudio.modules.shotlist` — drag-reorder + trim handles (extends existing composer)
- `SocialStudio.modules.device` — device frame mockups
- `SocialStudio.modules.overlay` — platform UI overlay
- `SocialStudio.modules.gridpreview` — IG cover grid mockup
- `SocialStudio.modules.tour` — first-time user tour
- `SocialStudio.modules.shortcuts_help` — `?` overlay listing keymap

**Tests:**
- `tests/test_social_v2_polish.py` — new file, covers async render endpoint + job cancel + PID column migration

---

## Task 1: Real Inter fonts + install script

**Files:**
- Create: `dashboard/social_install_assets.sh`
- Modify: `dashboard/static/fonts/Inter-Bold.ttf` and `Inter-Regular.ttf` (overwrite stubs)
- Test: `tests/test_social_v2_polish.py` (new) — minimal smoke that the files exist + are larger than the stubs

- [ ] **Step 1: Write the failing test**

`tests/test_social_v2_polish.py`:

```python
"""Tests for Social Studio v2.0 polish phase."""
import os
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_inter_bold_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Bold.ttf")
    assert os.path.exists(path), f"{path} missing"
    size = os.path.getsize(path)
    # Real Inter-Bold.ttf is ~310KB; placeholder stub was ~65 bytes.
    assert size > 50_000, f"Inter-Bold.ttf too small ({size} bytes) — still a placeholder?"
    with open(path, "rb") as f:
        head = f.read(4)
    # TTF magic: 0x00010000 (TrueType) or 'OTTO' (OpenType) or 'true' (Mac)
    assert head in (b"\x00\x01\x00\x00", b"OTTO", b"true"), f"Not a real TTF: head={head!r}"


def test_inter_regular_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Regular.ttf")
    assert os.path.exists(path)
    assert os.path.getsize(path) > 50_000
    with open(path, "rb") as f:
        assert f.read(4) in (b"\x00\x01\x00\x00", b"OTTO", b"true")
```

- [ ] **Step 2: Verify test fails**

```
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate
pytest tests/test_social_v2_polish.py -v
```

Expected: FAIL with "Inter-Bold.ttf too small (65 bytes) — still a placeholder?"

- [ ] **Step 3: Write the install script**

`dashboard/social_install_assets.sh`:

```bash
#!/usr/bin/env bash
# Social Studio v2 asset installer. Idempotent: safe to re-run.
# Downloads real Inter fonts (v2.0); in v2.1 will also download Piper voices,
# LUTs, music, and SFX.
set -euo pipefail

cd "$(dirname "$0")"
FONTS_DIR="$(pwd)/static/fonts"
mkdir -p "$FONTS_DIR"

INTER_BASE="https://github.com/rsms/inter/raw/master/docs/font-files"
INTER_BOLD="$FONTS_DIR/Inter-Bold.ttf"
INTER_REG="$FONTS_DIR/Inter-Regular.ttf"

# Replace any placeholder stub (< 1KB) with the real font.
fetch_if_stub() {
    local path="$1" url="$2"
    if [[ -f "$path" && $(stat -c%s "$path") -gt 50000 ]]; then
        echo "ok: $path already real ($(stat -c%s "$path") bytes)"
        return
    fi
    echo "fetch: $url -> $path"
    curl -fsSL --retry 3 "$url" -o "$path.tmp"
    mv "$path.tmp" "$path"
    echo "  installed $(stat -c%s "$path") bytes"
}

# Inter is OFL-licensed. Repo: github.com/rsms/inter
# These URLs point at OTF files which ffmpeg's drawtext supports identically to TTF.
fetch_if_stub "$INTER_BOLD" "$INTER_BASE/Inter-Bold.otf"
fetch_if_stub "$INTER_REG"  "$INTER_BASE/Inter-Regular.otf"

echo "Social Studio asset install complete."
```

```
chmod +x dashboard/social_install_assets.sh
```

- [ ] **Step 4: Run the installer**

```
bash dashboard/social_install_assets.sh
```

Expected: prints `fetch: ...` then `installed NNNNNN bytes`. Both files should now be > 100KB.

If `curl` exits non-zero (network/GitHub unavailable): the install script fails fast. In that case, manually download the two OTF files from https://github.com/rsms/inter/releases (latest release → font-files folder), rename to `.ttf`, drop into `dashboard/static/fonts/`. Report this as a BLOCKER if neither approach works.

- [ ] **Step 5: Verify tests pass**

```
pytest tests/test_social_v2_polish.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Smoke test ffmpeg can use the new fonts**

Render a 1-frame test with hook overlay:

```
python -c "
import sys
sys.path.insert(0, 'dashboard')
from social_render import render_still
import os, tempfile
from urllib.request import urlretrieve
src = tempfile.mktemp(suffix='.jpg')
# Tiny 800x600 test image
import subprocess
subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=800x600',
                '-frames:v', '1', src], capture_output=True, check=True)
out = tempfile.mktemp(suffix='.jpg')
render_still(src=src, out=out, platform='ig_feed_square',
             hook_text='Hello World', brand_corner=False)
assert os.path.exists(out) and os.path.getsize(out) > 1000, 'render failed'
print('ok:', os.path.getsize(out), 'bytes')
"
```

Expected: prints `ok: NNNNN bytes` (>1000). No ffmpeg error about missing font.

- [ ] **Step 7: Commit**

```
git add dashboard/social_install_assets.sh dashboard/static/fonts/Inter-Bold.ttf dashboard/static/fonts/Inter-Regular.ttf tests/test_social_v2_polish.py
git commit -m "social v2: real Inter fonts + install script

Replaces Phase 1's placeholder text-file stubs with real OFL Inter
fonts fetched from github.com/rsms/inter. social_install_assets.sh is
idempotent and will gain more downloads in v2.1 (Piper voices, LUTs,
music, SFX).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Async render endpoint + PID column

**Files:**
- Modify: `dashboard/social_studio.py` (add async render route + cancel route + pid migration)
- Test: extend `tests/test_social_v2_polish.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_social_v2_polish.py`:

```python
import sqlite3
import sys
import tempfile
from datetime import datetime


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv2_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)  # NEW — adds pid column
    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE image_captions (
            id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
            caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
        )""")
        con.execute("INSERT INTO image_captions VALUES (1,42,'a.jpg','x','work','ok',?)",
                    (datetime.utcnow().isoformat(),))
        con.commit()
    finally:
        con.close()
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]


def test_jobs_pid_column_exists(client):
    c, ss = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_jobs)")}
        assert "pid" in cols
    finally:
        con.close()


def test_render_async_returns_job_id(client, monkeypatch):
    c, ss = client
    # Stub the actual render so it doesn't run ffmpeg
    monkeypatch.setattr(ss, "_resolve_media_paths", lambda ids: ["/tmp/fake.jpg"])
    # Insert a post we can render
    pid = c.post("/api/ahb/social/posts", json={
        "platform": "ig_feed_square", "variant": "1x1", "source_media_ids": [1],
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/posts/{pid}/render-async", json={})
    assert r.status_code == 200
    j = r.get_json()
    assert "job_id" in j
    # Job should exist in DB with status='queued' or 'running'
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        row = con.execute("SELECT status, kind, post_id FROM ahb_social_jobs WHERE id=?",
                          (j["job_id"],)).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[1] == "render"
    assert row[2] == pid


def test_job_cancel_marks_cancelled(client, monkeypatch):
    c, ss = client
    # Create a job manually
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_jobs (post_id, kind, status, pid) VALUES (?, ?, ?, ?)",
            (1, "render", "running", None),
        )
        con.commit()
        jid = cur.lastrowid
    finally:
        con.close()
    r = c.delete(f"/api/ahb/social/jobs/{jid}")
    assert r.status_code == 200
    # Status should now be 'cancelled'
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        status = con.execute("SELECT status FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()[0]
    finally:
        con.close()
    assert status == "cancelled"


def test_job_cancel_404_for_unknown(client):
    c, _ = client
    r = c.delete("/api/ahb/social/jobs/999999")
    assert r.status_code == 404
```

- [ ] **Step 2: Verify tests fail**

```
pytest tests/test_social_v2_polish.py -v
```

Expected: 2 prior tests pass; 4 new ones fail (function `_ensure_social_v2_tables` missing, async route missing, cancel route missing).

- [ ] **Step 3: Add `_ensure_social_v2_tables` + async + cancel routes**

Append to `dashboard/social_studio.py`:

```python
def _ensure_social_v2_tables(db_path: Optional[str] = None) -> None:
    """Add v2 column additions and tables. Idempotent."""
    path = db_path or _db_path()
    con = None
    try:
        con = sqlite3.connect(path, timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        # Idempotent column additions
        for table, col_def in [
            ("ahb_social_jobs", "pid INTEGER"),
        ]:
            col_name = col_def.split()[0]
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass  # column exists
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[startup] _ensure_social_v2_tables deferred: {e}", flush=True)
    finally:
        if con is not None:
            con.close()


# Run at import time, alongside Phase 1's _ensure_social_tables.
_ensure_social_v2_tables()


import signal
import threading


def _kick_render_async(post_id: int, body: dict) -> int:
    """Insert a job row, spawn a worker thread that calls the existing
    synchronous render endpoint internally. Returns job_id."""
    con = _conn()
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_jobs (post_id, kind, status, input) VALUES (?, ?, ?, ?)",
            (post_id, "render", "queued", json.dumps(body)),
        )
        con.commit()
        job_id = cur.lastrowid
    finally:
        con.close()

    def _worker():
        # Mark running, capture pid (this thread's process pid is the dashboard server's;
        # the actual ffmpeg subprocess pid is set later by render_video/render_still).
        con = _conn()
        try:
            con.execute(
                "UPDATE ahb_social_jobs SET status='running', started_at=?, pid=? WHERE id=?",
                (datetime.utcnow().isoformat(timespec="seconds"), os.getpid(), job_id),
            )
            con.commit()
        finally:
            con.close()
        try:
            # Call the existing synchronous render code path.
            paths = _resolve_media_paths(_get_post_source_ids(post_id))
            if not paths:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
            out_dir = os.path.join(
                DASHBOARD_DIR, "artifacts", "social",
                datetime.utcnow().strftime("%Y-%m-%d"), str(post_id),
            )
            os.makedirs(out_dir, exist_ok=True)
            is_video = any(
                p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")) for p in paths
            )
            post = _get_post(post_id)
            if post is None:
                _job_finish(job_id, "failed", error="post not found")
                return
            platform = post["platform"]
            ext = ".mp4" if is_video else ".jpg"
            out_path = os.path.join(out_dir, f"{platform}{ext}")
            hook = (body or {}).get("hook_text")
            fill = (body or {}).get("fill_mode", "blurred")
            try:
                if is_video:
                    _render.render_video(paths, out_path, platform, hook_text=hook, fill_mode=fill)
                    cover_path = os.path.join(out_dir, "cover.jpg")
                    _render.extract_cover(out_path, cover_path)
                else:
                    _render.render_still(paths[0], out_path, platform, hook_text=hook, fill_mode=fill)
                    cover_path = out_path
            except (subprocess.CalledProcessError, ValueError, OSError) as e:
                _set_post_status(post_id, "failed")
                detail = (e.stderr.decode(errors='ignore')[-500:]
                          if isinstance(e, subprocess.CalledProcessError) and e.stderr
                          else str(e))
                _job_finish(job_id, "failed", error=detail, output_path=None)
                return
            _set_post_render_paths(post_id, out_path, cover_path)
            _job_finish(job_id, "done", output_path=out_path)
        except Exception as e:
            _job_finish(job_id, "failed", error=str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return job_id


def _get_post_source_ids(post_id: int) -> list:
    con = _conn()
    try:
        r = con.execute("SELECT source_media_ids FROM ahb_social_posts WHERE id=?", (post_id,)).fetchone()
    finally:
        con.close()
    if not r:
        return []
    try:
        return json.loads(r["source_media_ids"] or "[]")
    except Exception:
        return []


def _get_post(post_id: int):
    con = _conn()
    try:
        r = con.execute("SELECT * FROM ahb_social_posts WHERE id=?", (post_id,)).fetchone()
    finally:
        con.close()
    return r


def _set_post_status(post_id: int, status: str) -> None:
    con = _conn()
    try:
        con.execute("UPDATE ahb_social_posts SET status=?, updated_at=? WHERE id=?",
                    (status, datetime.utcnow().isoformat(timespec="seconds"), post_id))
        con.commit()
    finally:
        con.close()


def _set_post_render_paths(post_id: int, asset_path: str, cover_path: str) -> None:
    con = _conn()
    try:
        con.execute(
            "UPDATE ahb_social_posts SET asset_path=?, cover_path=?, updated_at=? WHERE id=?",
            (asset_path, cover_path, datetime.utcnow().isoformat(timespec="seconds"), post_id),
        )
        con.commit()
    finally:
        con.close()


def _job_finish(job_id: int, status: str, error: str = None, output_path: str = None) -> None:
    con = _conn()
    try:
        con.execute(
            "UPDATE ahb_social_jobs SET status=?, finished_at=?, error=?, output_path=? WHERE id=?",
            (status, datetime.utcnow().isoformat(timespec="seconds"), error, output_path, job_id),
        )
        con.commit()
    finally:
        con.close()


@social_bp.route("/api/ahb/social/posts/<int:pid>/render-async", methods=["POST"])
def social_render_post_async(pid: int):
    body = request.get_json(silent=True) or {}
    # Verify the post exists
    if _get_post(pid) is None:
        return jsonify({"error": "post not found"}), 404
    job_id = _kick_render_async(pid, body)
    return jsonify({"job_id": job_id})


@social_bp.route("/api/ahb/social/jobs/<int:jid>", methods=["DELETE"])
def social_job_cancel(jid: int):
    con = _conn()
    try:
        row = con.execute("SELECT status, pid FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        # Best-effort: signal the ffmpeg subprocess if we tracked its pid.
        # In threaded mode the dashboard's own pid is stored, which is NOT what we want
        # to kill. Future v2.1 will pass the actual ffmpeg child pid up from
        # social_render. For now, just mark cancelled and let the running thread
        # complete naturally (it may finish before noticing the flag, that's OK).
        con.execute(
            "UPDATE ahb_social_jobs SET status='cancelled', finished_at=? WHERE id=?",
            (datetime.utcnow().isoformat(timespec="seconds"), jid),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Verify tests pass**

```
pytest tests/test_social_v2_polish.py -v
```

Expected: 6 passed (2 fonts + 4 jobs).

- [ ] **Step 5: Restart dashboard + smoke**

```
sudo systemctl restart baza-dashboard
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8888/api/ahb/social/posts/999999/render-async  # 404
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE http://127.0.0.1:8888/api/ahb/social/jobs/999999              # 404
```

- [ ] **Step 6: Commit**

```
git add dashboard/social_studio.py tests/test_social_v2_polish.py
git commit -m "social v2: async render endpoint + job cancel + pid column

POST /posts/<id>/render-async returns {job_id} immediately and runs the
render in a background thread. DELETE /jobs/<id> marks the job cancelled
(graceful — the running thread may complete naturally). New pid column
on ahb_social_jobs reserved for v2.1's true subprocess termination.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Toast notification system

**Files:**
- Modify: `dashboard/templates/ahb123.html` — append CSS for toast stack + IIFE module + body-level container

This task is UI-only — no backend tests. Manual smoke per step.

- [ ] **Step 1: Add body-level toast container**

Find the existing social modal block (search `grep -n 'id="socialPostDetail"' dashboard/templates/ahb123.html` — should be near line 4500). Insert immediately AFTER the existing modal divs:

```html
<!-- Social Studio toast stack (body-level, bottom-right) -->
<div id="socialToastStack" style="position:fixed;bottom:16px;right:16px;z-index:99999;display:flex;flex-direction:column;gap:8px;align-items:flex-end;pointer-events:none"></div>
```

- [ ] **Step 2: Append toast CSS**

Find the closing `</style>` of the existing Social Studio CSS block (search `grep -n '.ss-pill-failed' dashboard/templates/ahb123.html`). In the same `<style>` block (or a new one immediately after), append:

```html
<style>
  .ss-toast { min-width:240px; max-width:380px; padding:10px 14px; border-radius:10px; font-size:13px; color:#fff; box-shadow:0 8px 24px rgba(0,0,0,.5); pointer-events:auto; cursor:pointer; display:flex; align-items:center; gap:8px; animation:ss-toast-in .2s ease-out; }
  .ss-toast-info { background:#1e3a8a; border-left:4px solid #60a5fa; }
  .ss-toast-success { background:#064e3b; border-left:4px solid #10b981; }
  .ss-toast-error { background:#7f1d1d; border-left:4px solid #fca5a5; }
  .ss-toast-progress { background:#1e293b; border-left:4px solid #94a3b8; cursor:default; min-width:300px; }
  .ss-toast-progress-bar { height:4px; background:#94a3b8; border-radius:2px; margin-top:6px; transition:width .3s ease; }
  .ss-toast-icon { font-size:18px; line-height:1; }
  .ss-toast-body { flex:1; }
  .ss-toast-x { background:none; border:none; color:rgba(255,255,255,.6); cursor:pointer; font-size:18px; padding:0 4px; line-height:1; }
  .ss-toast-x:hover { color:#fff; }
  @keyframes ss-toast-in { from { transform:translateX(20px); opacity:0 } to { transform:translateX(0); opacity:1 } }
  @keyframes ss-toast-out { to { transform:translateX(20px); opacity:0 } }
  .ss-toast-leaving { animation:ss-toast-out .2s ease-out forwards; }
</style>
```

- [ ] **Step 3: Append the toast IIFE**

Find the end of `SocialStudio.modules.autopilot` (search `grep -n 'SocialStudio.modules.autopilot' dashboard/templates/ahb123.html`, find its closing `})();`). Insert immediately after:

```html
<script>
SocialStudio.modules.toast = (function(){
  const stack = () => document.getElementById('socialToastStack');
  let counter = 0;
  const live = new Map();  // id -> element

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _make(kind, msg, opts) {
    const id = ++counter;
    const icon = ({ info: 'ℹ️', success: '✓', error: '⚠', progress: '⟳' })[kind] || '';
    const el = document.createElement('div');
    el.className = 'ss-toast ss-toast-' + kind;
    el.dataset.toastId = id;
    el.innerHTML = `
      <span class="ss-toast-icon">${icon}</span>
      <div class="ss-toast-body">${_esc(msg)}${kind === 'progress' ? '<div class="ss-toast-progress-bar" style="width:0%"></div>' : ''}</div>
      ${kind !== 'progress' ? '<button class="ss-toast-x" aria-label="Dismiss">×</button>' : '<button class="ss-toast-x" aria-label="Cancel">×</button>'}
    `;
    el.querySelector('.ss-toast-x').addEventListener('click', (e) => {
      e.stopPropagation();
      if (kind === 'progress' && opts && opts.onCancel) opts.onCancel(id);
      _dismiss(id);
    });
    el.addEventListener('click', () => { if (kind !== 'progress') _dismiss(id); });

    // Cap at 3 visible
    const s = stack();
    while (s.children.length >= 3) {
      const oldId = parseInt(s.firstElementChild.dataset.toastId, 10);
      _dismiss(oldId);
    }
    s.appendChild(el);
    live.set(id, el);

    if (kind !== 'progress') {
      const ms = (opts && opts.ms) || (kind === 'error' ? 8000 : 5000);
      setTimeout(() => _dismiss(id), ms);
    }
    return id;
  }

  function _dismiss(id) {
    const el = live.get(id);
    if (!el) return;
    el.classList.add('ss-toast-leaving');
    setTimeout(() => {
      if (el.parentElement) el.parentElement.removeChild(el);
      live.delete(id);
    }, 200);
  }

  function update(id, opts) {
    const el = live.get(id);
    if (!el) return;
    if (opts.msg) el.querySelector('.ss-toast-body').firstChild.nodeValue = opts.msg;
    if (typeof opts.percent === 'number') {
      const bar = el.querySelector('.ss-toast-progress-bar');
      if (bar) bar.style.width = Math.max(0, Math.min(100, opts.percent)) + '%';
    }
  }

  function resolve(id, kind, msg) {
    _dismiss(id);
    return _make(kind, msg);
  }

  return {
    info: (msg, opts) => _make('info', msg, opts),
    success: (msg, opts) => _make('success', msg, opts),
    error: (msg, opts) => _make('error', msg, opts),
    progress: (msg, opts) => _make('progress', msg, opts),
    update,
    resolve,
    dismiss: _dismiss,
  };
})();
</script>
```

- [ ] **Step 4: Replace existing `alert()` calls with toasts**

Find every `alert(` in the existing SocialStudio modules. Run:

```
grep -n "alert(" dashboard/templates/ahb123.html | grep -E "Studio|composer|library|postdetail|presets|settings|brandkit|scheduler|autopilot"
```

You should see ~15-20 hits. Replace each:

- `alert('Sent to Telegram')` → `SocialStudio.modules.toast.success('Sent to Telegram')`
- `alert('Telegram bridge unavailable')` → `SocialStudio.modules.toast.error('Telegram bridge unavailable')`
- `alert('Render failed: ' + ren.error)` → `SocialStudio.modules.toast.error('Render failed: ' + ren.error)`
- `alert('Rendered: ' + ren.asset_path)` → `SocialStudio.modules.toast.success('Rendered → Library')`
- `alert('Pick at least one source.')` → `SocialStudio.modules.toast.info('Pick at least one source first.')`
- `alert('No hooks returned')` → `SocialStudio.modules.toast.error('No hooks returned')`
- `alert('Save failed')` → `SocialStudio.modules.toast.error('Save failed')`
- `alert('Installed ' + …)` → `SocialStudio.modules.toast.success('Installed ' + (j.installed || []).length + ' preset(s)')`
- `alert('Test run: …')` → `SocialStudio.modules.toast.info('Test run: ' + ...)`
- `alert('Tick: …')` → `SocialStudio.modules.toast.info('Tick: ' + ...)`
- `alert('Auto-Pilot endpoint not ready …')` → `SocialStudio.modules.toast.error('Auto-Pilot endpoint not ready (Task 13)')`
- `alert('Sent.')` → `SocialStudio.modules.toast.success('Sent.')`
- Score popup `alert(\`Score: ${j.score}/100\n\n${j.notes}\`)` → keep as alert for now (multi-line; toast doesn't handle multiline well — addressed in v2.1 with score modal)

Keep the existing `window.prompt(...)` calls for now (those are interactive — toast can't replace them; addressed in v2.1 with proper modals).

- [ ] **Step 5: Restart + smoke**

```
sudo systemctl restart baza-dashboard
sleep 2
```

Open `http://127.0.0.1:8888/ahb123`, click Social tab, then in browser devtools console:

```javascript
SocialStudio.modules.toast.info('Info toast');
SocialStudio.modules.toast.success('Success!');
SocialStudio.modules.toast.error('Error toast');
const pid = SocialStudio.modules.toast.progress('Working…');
setTimeout(() => SocialStudio.modules.toast.update(pid, {percent: 50, msg: '50%'}), 500);
setTimeout(() => SocialStudio.modules.toast.resolve(pid, 'success', 'Done'), 1500);
```

All four toasts should appear bottom-right, fade in, auto-dismiss appropriately. Progress bar should animate.

- [ ] **Step 6: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: toast notification system replaces alert() calls

SocialStudio.modules.toast provides info/success/error/progress APIs.
Bottom-right stack, max 3 visible, auto-dismiss 5s (info/success) or 8s
(error). Progress toasts persist until resolve() with optional cancel
callback. All existing alert() calls migrated to toasts where the
message is single-line; multi-line score popup stays as alert until
v2.1 adds a proper score modal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Keyboard shortcuts + help overlay

**Files:**
- Modify: `dashboard/templates/ahb123.html` — append IIFE module + body-level help overlay

- [ ] **Step 1: Add body-level shortcuts-help modal slot**

In the modal block from Task 3 (near line 4500-4510), insert:

```html
<div id="socialShortcutsHelp" class="modal-bg" style="display:none"></div>
```

- [ ] **Step 2: Append the keymap IIFE**

After the toast IIFE from Task 3, append:

```html
<script>
SocialStudio.modules.keymap = (function(){
  const bindings = [
    { key: '?', desc: 'Show this help', action: () => showHelp() },
    { key: '/', desc: 'Focus search', action: () => focusSearch() },
    { key: 'j', desc: 'Next item', action: () => navigate(1) },
    { key: 'k', desc: 'Previous item', action: () => navigate(-1) },
    { key: 'a', desc: 'Approve current Library item', action: () => approveFocused() },
    { key: 'r', desc: 'Render current composer post', action: () => renderCurrent() },
    { key: 'Escape', desc: 'Close modal / clear focus', action: () => closeAny() },
  ];

  let installed = false;

  function _isEditable(el) {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  function _onKey(e) {
    // Only fire when Social tab is the active tab
    const socialTab = document.getElementById('tab-social');
    if (!socialTab || !socialTab.classList.contains('active')) return;
    if (_isEditable(e.target)) return;
    const b = bindings.find(b => b.key === e.key);
    if (!b) return;
    e.preventDefault();
    b.action();
  }

  function install() {
    if (installed) return;
    installed = true;
    document.addEventListener('keydown', _onKey);
  }

  function showHelp() {
    const m = document.getElementById('socialShortcutsHelp');
    m.style.display = 'flex';
    m.innerHTML = `
      <div class="modal" style="max-width:420px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">⌨ Keyboard shortcuts</div>
          <button onclick="document.getElementById('socialShortcutsHelp').style.display='none'" style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        <table style="width:100%;font-size:13px">
          ${bindings.map(b => `<tr><td style="padding:6px 0"><kbd style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:4px;padding:2px 8px;font-family:monospace">${b.key}</kbd></td><td style="padding:6px 12px;color:#aaa">${b.desc}</td></tr>`).join('')}
        </table>
        <div style="color:#666;font-size:11px;margin-top:14px">Shortcuts active only on the Social tab. Disabled when typing in inputs.</div>
      </div>
    `;
  }

  function focusSearch() {
    // Try the library search first, then library tag chip, then composer search
    const targets = ['#ss-sub-library input[placeholder*="Search"]',
                     '#ss-source-q'];
    for (const sel of targets) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) { el.focus(); el.select && el.select(); return; }
    }
  }

  function navigate(dir) {
    // In Library: move focus across post cards
    const cards = document.querySelectorAll('#ss-sub-library .ss-card');
    if (!cards.length) return;
    let idx = -1;
    cards.forEach((c, i) => { if (c.dataset.focused === '1') idx = i; });
    const next = Math.max(0, Math.min(cards.length - 1, idx + dir));
    cards.forEach(c => { c.dataset.focused = ''; c.style.outline = ''; });
    cards[next].dataset.focused = '1';
    cards[next].style.outline = '2px solid #10b981';
    cards[next].scrollIntoView({ block: 'nearest' });
  }

  function approveFocused() {
    const focused = document.querySelector('#ss-sub-library .ss-card[data-focused="1"]');
    if (!focused) { SocialStudio.modules.toast.info('Press j/k to focus a Library item first'); return; }
    const approveBtn = focused.querySelector('button[onclick*="approved"]');
    if (approveBtn) approveBtn.click();
    else SocialStudio.modules.toast.info('No Approve action available (post not pending review)');
  }

  function renderCurrent() {
    if (SocialStudio.state.activeSub !== 'composer') {
      SocialStudio.modules.toast.info('Switch to Composer first');
      return;
    }
    const btn = document.querySelector('#ss-variant-panel .btn-primary');
    if (btn) btn.click();
  }

  function closeAny() {
    document.querySelectorAll('.modal-bg').forEach(m => {
      if (m.style.display !== 'none' && m.id.startsWith('social')) m.style.display = 'none';
    });
  }

  // Install once when the Social tab opens
  const origInit = SocialStudio.init;
  SocialStudio.init = function() {
    const r = origInit.apply(this, arguments);
    install();
    return r;
  };

  return { install, showHelp, bindings };
})();
</script>
```

- [ ] **Step 3: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open `/ahb123`, click Social, press `?` — overlay should appear listing 7 shortcuts. Press Esc to close. Press `j` then `k` while in Library to move focus across cards. Press `r` while in Composer to trigger render.

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: keyboard shortcuts + help overlay

SocialStudio.modules.keymap installs a global keydown handler when the
Social tab opens. Bindings: ?/help, //search-focus, j/k navigate Library,
a approve focused, r render in Composer, Esc close modal. Disabled when
focus is in input/textarea/select.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Render progress polling UI

**Files:**
- Modify: `dashboard/templates/ahb123.html` — Composer's renderPackage gets async + progress

- [ ] **Step 1: Append the progress IIFE**

After the keymap IIFE:

```html
<script>
SocialStudio.modules.progress = (function(){
  const POLL_MS = 1500;
  let poller = null;

  async function watch(jobId, label) {
    const toastId = SocialStudio.modules.toast.progress(label || 'Rendering…', {
      onCancel: async () => {
        await fetch('/api/ahb/social/jobs/' + jobId, { method: 'DELETE' });
        SocialStudio.modules.toast.update(toastId, { msg: 'Cancelling…' });
      },
    });
    let lastPct = 0;
    const tick = async () => {
      let job;
      try {
        const r = await fetch('/api/ahb/social/jobs/' + jobId);
        if (!r.ok) {
          SocialStudio.modules.toast.resolve(toastId, 'error', 'Job lookup failed');
          return;
        }
        job = await r.json();
      } catch (e) {
        SocialStudio.modules.toast.resolve(toastId, 'error', 'Network error');
        return;
      }
      // No real percent yet (v2.1 will add); fake monotonic creep to give feedback
      lastPct = Math.min(95, lastPct + 5);
      SocialStudio.modules.toast.update(toastId, { percent: lastPct });
      if (job.status === 'done') {
        SocialStudio.modules.toast.update(toastId, { percent: 100 });
        setTimeout(() => SocialStudio.modules.toast.resolve(toastId, 'success', 'Render complete'), 200);
        if (SocialStudio.modules.library) SocialStudio.modules.library.render();
      } else if (job.status === 'failed') {
        SocialStudio.modules.toast.resolve(toastId, 'error', 'Render failed: ' + (job.error || 'unknown'));
      } else if (job.status === 'cancelled') {
        SocialStudio.modules.toast.resolve(toastId, 'info', 'Render cancelled');
      } else {
        setTimeout(tick, POLL_MS);
      }
    };
    setTimeout(tick, POLL_MS);
  }

  return { watch };
})();
</script>
```

- [ ] **Step 2: Modify Composer's renderPackage to use async**

In the Composer module (search `grep -n "async function renderPackage" dashboard/templates/ahb123.html`), replace the function body:

```javascript
  async function renderPackage() {
    if (!state.shotList.length) {
      SocialStudio.modules.toast.info('Pick at least one source.');
      return;
    }
    const post = await fetch('/api/ahb/social/posts', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        platform: state.activePlatform, variant: state.activePlatform,
        source_media_ids: state.shotList,
        caption: document.getElementById('ss-caption-' + state.activePlatform).value,
        hashtags: document.getElementById('ss-hashtags-' + state.activePlatform).value,
      }),
    }).then(r => r.json());
    const ren = await fetch(`/api/ahb/social/posts/${post.id}/render-async`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ hook_text: document.getElementById('ss-hook-input').value || null }),
    }).then(r => r.json());
    if (ren.error) { SocialStudio.modules.toast.error('Render failed: ' + ren.error); return; }
    SocialStudio.modules.progress.watch(ren.job_id, 'Rendering ' + state.activePlatform);
  }
```

- [ ] **Step 3: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer, pick a source (any thumbnail), click Render. A progress toast should appear and animate; once render completes (within ~30s for a still), the toast resolves to success.

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: render progress polling UI with cancel

SocialStudio.modules.progress.watch(jobId) polls /api/ahb/social/jobs/<id>
every 1.5s, shows percent in a progress toast, calls library.render()
on success. Cancel button hits DELETE /jobs/<id>. Composer's
renderPackage now uses the async render endpoint from Task 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Drag-to-reorder shot list

**Files:**
- Modify: `dashboard/templates/ahb123.html` — composer's source rendering + new shot list rail

- [ ] **Step 1: Add shot list rail to composer**

In the Composer module's `render()` function (search `function render() {` inside the composer IIFE), replace the first column (Sources card) inner div structure. Find this section:

```javascript
          <div id="ss-source-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;max-height:520px;overflow-y:auto"></div>
```

Replace with:

```javascript
          <div id="ss-source-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;max-height:280px;overflow-y:auto"></div>
          <div style="margin-top:10px">
            <div style="font-weight:700;font-size:12px;color:#aaa;margin-bottom:6px">📋 Shot list (drag to reorder)</div>
            <div id="ss-shot-rail" style="display:flex;flex-direction:column;gap:4px;max-height:200px;overflow-y:auto;border:1px dashed #2a2a4a;border-radius:6px;padding:6px;min-height:60px"></div>
          </div>
```

- [ ] **Step 2: Add shotlist render + drag logic**

Inside the composer IIFE, after `renderPreview()` is defined, append:

```javascript
  function renderShotRail() {
    const rail = document.getElementById('ss-shot-rail');
    if (!rail) return;
    if (!state.shotList.length) {
      rail.innerHTML = '<div style="color:#444;font-size:11px;padding:8px;text-align:center">No clips picked yet.</div>';
      return;
    }
    // Normalize: each item is either {id, in_seconds, out_seconds} or a bare id
    state.shotList = state.shotList.map(item => typeof item === 'object' ? item : { id: item });
    rail.innerHTML = state.shotList.map((item, idx) => {
      const src = state.sources.find(s => s.id === item.id) || {};
      const trim = (item.in_seconds != null || item.out_seconds != null)
        ? ` <span style="color:#10b981">[${item.in_seconds||0}s–${item.out_seconds||'end'}s]</span>` : '';
      return `
        <div class="ss-shot-item" draggable="true" data-idx="${idx}"
             style="display:flex;align-items:center;gap:6px;background:#0a0a18;border:1px solid #2a2a4a;border-radius:6px;padding:4px;cursor:grab">
          <span style="color:#10b981;font-weight:800;width:18px;text-align:center">${idx+1}</span>
          <img src="${thumbUrl(src.sub_path || '')}" onerror="this.style.opacity='0.3'" style="width:36px;height:36px;object-fit:cover;border-radius:4px">
          <div style="flex:1;font-size:11px;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(src.sub_path || '?')}${trim}</div>
          <button onclick="SocialStudio.modules.composer.shotTrim(${idx})" title="Trim" style="background:none;border:none;color:#aaa;cursor:pointer;padding:0 4px">✂</button>
          <button onclick="SocialStudio.modules.composer.shotRemove(${idx})" title="Remove" style="background:none;border:none;color:#f87171;cursor:pointer;padding:0 4px">×</button>
        </div>
      `;
    }).join('');
    // Wire HTML5 drag/drop
    rail.querySelectorAll('.ss-shot-item').forEach(el => {
      el.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', el.dataset.idx);
        el.style.opacity = '0.5';
      });
      el.addEventListener('dragend', () => { el.style.opacity = '1'; });
      el.addEventListener('dragover', (e) => { e.preventDefault(); el.style.borderColor = '#10b981'; });
      el.addEventListener('dragleave', () => { el.style.borderColor = '#2a2a4a'; });
      el.addEventListener('drop', (e) => {
        e.preventDefault();
        el.style.borderColor = '#2a2a4a';
        const fromIdx = parseInt(e.dataTransfer.getData('text/plain'), 10);
        const toIdx = parseInt(el.dataset.idx, 10);
        if (fromIdx === toIdx) return;
        const [moved] = state.shotList.splice(fromIdx, 1);
        state.shotList.splice(toIdx, 0, moved);
        renderShotRail();
        renderPreview();
      });
    });
  }

  function shotRemove(idx) {
    state.shotList.splice(idx, 1);
    renderSourceGrid();
    renderShotRail();
    renderPreview();
  }

  function shotTrim(idx) {
    // Stub: full trim modal lands in Task 7. For now, show a prompt.
    const item = state.shotList[idx];
    const inS = window.prompt('In (seconds, blank for 0):', item.in_seconds || '');
    const outS = window.prompt('Out (seconds, blank for end):', item.out_seconds || '');
    item.in_seconds = inS ? parseFloat(inS) : null;
    item.out_seconds = outS ? parseFloat(outS) : null;
    renderShotRail();
  }
```

- [ ] **Step 3: Update `toggle()` to use object items and call renderShotRail**

In the composer IIFE, find the existing `toggle()` function and replace:

```javascript
  function toggle(id) {
    const existing = state.shotList.findIndex(item => (typeof item === 'object' ? item.id : item) === id);
    if (existing === -1) state.shotList.push({ id });
    else state.shotList.splice(existing, 1);
    renderSourceGrid();
    renderShotRail();
    renderPreview();
  }
```

- [ ] **Step 4: Update `renderSourceGrid()` to normalize the include check**

Find inside `renderSourceGrid()`:

```javascript
${state.shotList.includes(s.id) ? ' selected' : ''}
```

Replace with:

```javascript
${state.shotList.some(item => (typeof item === 'object' ? item.id : item) === s.id) ? ' selected' : ''}
```

- [ ] **Step 5: Update `renderPreview()` to use new format**

Find inside `renderPreview()`:

```javascript
const firstId = state.shotList[0];
const src = state.sources.find(s => s.id === firstId);
```

Replace with:

```javascript
const firstItem = state.shotList[0];
const firstId = typeof firstItem === 'object' ? (firstItem && firstItem.id) : firstItem;
const src = state.sources.find(s => s.id === firstId);
```

- [ ] **Step 6: Update `renderPackage()` to send only IDs (backend doesn't yet know about trims)**

In `renderPackage()`, change:

```javascript
source_media_ids: state.shotList,
```

to:

```javascript
source_media_ids: state.shotList.map(item => typeof item === 'object' ? item.id : item),
```

Trim values are stored in `state.shotList` items but ignored by the render endpoint until Task 7 wires per-clip trim into the render pipeline. For v2.0 this means trims display in the UI but don't affect output yet.

- [ ] **Step 7: Add the `shotRemove` and `shotTrim` to the IIFE's return object**

In the composer IIFE's `return` statement, add:

```javascript
  return { render, toggle, setPlatform, aiCaption, aiHashtags, aiHooks, aiScore,
           renderPackage, renderPreview, _reload, renderShotRail, shotRemove, shotTrim };
```

Also: after `loadSources()` call in `render()`, append `renderShotRail();` so the rail appears immediately.

- [ ] **Step 8: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer. Pick 3 thumbnails. Shot rail shows them. Drag #3 above #1 — order updates. Click ✂ on one — prompts for in/out (just close them for now). Click × — item removes.

- [ ] **Step 9: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: drag-reorder shot list rail in composer

Source picker becomes top half; new shot list rail below shows selected
clips with drag-to-reorder via HTML5 drag/drop. Each shot has a trim
stub (window.prompt for now; full modal in next task) and a remove
button. state.shotList items become {id, in_seconds, out_seconds}
objects; old usage normalized through the picker, preview, and render.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Per-clip trim modal + render-pipeline trim support

**Files:**
- Modify: `dashboard/templates/ahb123.html` — replace prompt-based trim with proper modal
- Modify: `dashboard/social_render.py` — accept per-clip trim values
- Modify: `dashboard/social_studio.py` — pass trim values through render endpoint

- [ ] **Step 1: Replace `shotTrim` with a modal**

Find `shotTrim` in the composer IIFE. Replace with:

```javascript
  function shotTrim(idx) {
    const item = state.shotList[idx];
    const src = state.sources.find(s => s.id === item.id);
    if (!src) return;
    const m = document.createElement('div');
    m.className = 'modal-bg';
    m.style.cssText = 'display:flex';
    document.body.appendChild(m);
    const isVideo = ['mp4','mov','webm','mkv'].includes((src.sub_path||'').toLowerCase().split('.').pop());
    m.innerHTML = `
      <div class="modal" style="max-width:520px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">✂ Trim clip ${idx+1}</div>
          <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        ${isVideo
          ? `<video src="${serveUrl(src.sub_path)}" id="trim-video" controls style="width:100%;border-radius:8px;background:#000"></video>
             <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">
               <label style="font-size:12px;color:#aaa">In (seconds)
                 <input id="trim-in" type="number" min="0" step="0.1" value="${item.in_seconds||''}" placeholder="0" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
               </label>
               <label style="font-size:12px;color:#aaa">Out (seconds)
                 <input id="trim-out" type="number" min="0" step="0.1" value="${item.out_seconds||''}" placeholder="end" style="width:100%;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:6px;color:#fff">
               </label>
             </div>
             <div style="display:flex;gap:6px;margin-top:8px">
               <button class="btn-secondary" data-set-in style="font-size:11px">Use video time as In</button>
               <button class="btn-secondary" data-set-out style="font-size:11px">Use video time as Out</button>
             </div>`
          : '<div style="color:#aaa;padding:24px;text-align:center">Stills don\\'t have time — trim doesn\\'t apply.</div>'}
        <div style="display:flex;justify-content:flex-end;gap:6px;margin-top:14px">
          <button class="btn-secondary" data-clear>Clear trim</button>
          <button class="btn-primary" data-save>Save</button>
        </div>
      </div>
    `;
    const close = () => { document.body.removeChild(m); };
    m.querySelector('[data-close]').addEventListener('click', close);
    const inEl = m.querySelector('#trim-in');
    const outEl = m.querySelector('#trim-out');
    const video = m.querySelector('#trim-video');
    if (m.querySelector('[data-set-in]')) {
      m.querySelector('[data-set-in]').addEventListener('click', () => { inEl.value = video.currentTime.toFixed(1); });
      m.querySelector('[data-set-out]').addEventListener('click', () => { outEl.value = video.currentTime.toFixed(1); });
    }
    m.querySelector('[data-clear]').addEventListener('click', () => {
      item.in_seconds = null;
      item.out_seconds = null;
      renderShotRail();
      close();
    });
    m.querySelector('[data-save]').addEventListener('click', () => {
      item.in_seconds = inEl && inEl.value ? parseFloat(inEl.value) : null;
      item.out_seconds = outEl && outEl.value ? parseFloat(outEl.value) : null;
      renderShotRail();
      close();
    });
  }
```

- [ ] **Step 2: Modify `social_render.py` to accept per-clip trims**

In `dashboard/social_render.py`, change the `render_video` signature and body. Find the current function and replace:

```python
def render_video(srcs, out: str, platform: str,
                 hook_text: Optional[str] = None,
                 brand_corner: bool = False,
                 fill_mode: str = "blurred",
                 max_seconds: int = 60) -> str:
    """Concat sources, re-encode to target dims, optional hook overlay.
    srcs may be a list of paths (legacy) or a list of dicts
    {path, in_seconds, out_seconds}. Trim values applied per-clip via
    ffmpeg's concat demuxer with -ss/-to."""
    if not srcs:
        raise ValueError("no sources")
    # Normalize to dicts
    clips = []
    for s in srcs:
        if isinstance(s, str):
            clips.append({"path": s, "in_seconds": None, "out_seconds": None})
        else:
            clips.append(s)
    if not clips:
        raise ValueError("no sources")
    w, h = _ffprobe(clips[0]["path"])
    g = build_filter_graph(w, h, platform, fill_mode, hook_text, brand_corner)
    tmpdir = os.path.dirname(out) or "."
    fd, list_path = tempfile.mkstemp(suffix=".concat.txt", dir=tmpdir, text=True)
    try:
        with os.fdopen(fd, "w") as f:
            for c in clips:
                # ffmpeg concat demuxer per-file inpoint/outpoint syntax
                f.write(f"file {shlex.quote(os.path.abspath(c['path']))}\n")
                if c.get("in_seconds") is not None:
                    f.write(f"inpoint {float(c['in_seconds'])}\n")
                if c.get("out_seconds") is not None:
                    f.write(f"outpoint {float(c['out_seconds'])}\n")
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
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)
    return out
```

- [ ] **Step 3: Modify `social_studio.py` to accept trim list and pass to render**

In `_kick_render_async` (added in Task 2), update the resolution logic. Find:

```python
            paths = _resolve_media_paths(_get_post_source_ids(post_id))
            if not paths:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
```

Replace with (also import the trim-aware variant):

```python
            source_ids = _get_post_source_ids(post_id)
            paths = _resolve_media_paths(source_ids)
            if not paths:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
            # If body includes trims, build a [{path, in_seconds, out_seconds}] list
            trims = (body or {}).get("trims") or {}  # {source_id: {in_seconds, out_seconds}}
            id_to_path = {sid: p for sid, p in zip(source_ids, paths) if os.path.exists(p)}
            if trims and not is_video:
                pass  # trims don't apply to stills
            elif trims:
                clip_list = []
                for sid in source_ids:
                    if sid not in id_to_path:
                        continue
                    t = trims.get(str(sid)) or {}
                    clip_list.append({
                        "path": id_to_path[sid],
                        "in_seconds": t.get("in_seconds"),
                        "out_seconds": t.get("out_seconds"),
                    })
                # Use the clip list instead of plain paths for render
                paths = clip_list  # render_video accepts either
```

(The `is_video` detection happens later; this preserves the existing flow but adds the trim-aware path.)

Actually rewrite more carefully — find the full block in `_kick_render_async` and update it cleanly. Here's the corrected full version:

```python
            source_ids = _get_post_source_ids(post_id)
            paths_list = _resolve_media_paths(source_ids)
            if not paths_list:
                _job_finish(job_id, "failed", error="no resolvable source media")
                return
            out_dir = os.path.join(
                DASHBOARD_DIR, "artifacts", "social",
                datetime.utcnow().strftime("%Y-%m-%d"), str(post_id),
            )
            os.makedirs(out_dir, exist_ok=True)
            is_video = any(
                p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")) for p in paths_list
            )
            post = _get_post(post_id)
            if post is None:
                _job_finish(job_id, "failed", error="post not found")
                return
            platform = post["platform"]
            ext = ".mp4" if is_video else ".jpg"
            out_path = os.path.join(out_dir, f"{platform}{ext}")
            hook = (body or {}).get("hook_text")
            fill = (body or {}).get("fill_mode", "blurred")
            trims = (body or {}).get("trims") or {}  # {str(source_id): {in_seconds, out_seconds}}
            try:
                if is_video:
                    if trims:
                        # Build trim-aware clip list
                        clip_list = []
                        for sid, p in zip(source_ids, paths_list):
                            t = trims.get(str(sid)) or {}
                            clip_list.append({
                                "path": p,
                                "in_seconds": t.get("in_seconds"),
                                "out_seconds": t.get("out_seconds"),
                            })
                        _render.render_video(clip_list, out_path, platform, hook_text=hook, fill_mode=fill)
                    else:
                        _render.render_video(paths_list, out_path, platform, hook_text=hook, fill_mode=fill)
                    cover_path = os.path.join(out_dir, "cover.jpg")
                    _render.extract_cover(out_path, cover_path)
                else:
                    _render.render_still(paths_list[0], out_path, platform, hook_text=hook, fill_mode=fill)
                    cover_path = out_path
            except (subprocess.CalledProcessError, ValueError, OSError) as e:
                _set_post_status(post_id, "failed")
                detail = (e.stderr.decode(errors='ignore')[-500:]
                          if isinstance(e, subprocess.CalledProcessError) and e.stderr
                          else str(e))
                _job_finish(job_id, "failed", error=detail, output_path=None)
                return
            _set_post_render_paths(post_id, out_path, cover_path)
            _job_finish(job_id, "done", output_path=out_path)
```

- [ ] **Step 4: Update Composer's renderPackage to send trims**

In the composer IIFE's `renderPackage()`, replace the second fetch body with:

```javascript
    const trims = {};
    state.shotList.forEach(item => {
      if (typeof item === 'object' && (item.in_seconds != null || item.out_seconds != null)) {
        trims[String(item.id)] = { in_seconds: item.in_seconds, out_seconds: item.out_seconds };
      }
    });
    const ren = await fetch(`/api/ahb/social/posts/${post.id}/render-async`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        hook_text: document.getElementById('ss-hook-input').value || null,
        trims: trims,
      }),
    }).then(r => r.json());
```

- [ ] **Step 5: Add a render test for trim support**

Append to `tests/test_social_v2_polish.py`:

```python
def test_render_video_accepts_trim_dicts(monkeypatch):
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    if "social_render" in sys.modules:
        del sys.modules["social_render"]
    import social_render
    # Stub subprocess.run to record what command was built
    captured = {}
    real_run = social_render.subprocess.run
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # Need to satisfy _ffprobe which is called BEFORE render
        class R:
            stdout = "1920x1080"
            stderr = b""
        if cmd[0] == "ffprobe":
            return R()
        # Write a dummy output file
        if "-i" in cmd:
            out = cmd[-1]
            open(out, "w").write("fake")
        return R()
    monkeypatch.setattr(social_render.subprocess, "run", fake_run)
    import tempfile
    src = tempfile.mktemp(suffix=".mp4")
    open(src, "w").write("fake")
    out = tempfile.mktemp(suffix=".mp4")
    social_render.render_video(
        [{"path": src, "in_seconds": 1.5, "out_seconds": 3.0}],
        out, "tiktok",
    )
    # The cmd should include the concat file; inpoint/outpoint were written to it.
    assert "-f" in captured["cmd"] and "concat" in captured["cmd"]
```

- [ ] **Step 6: Run tests + restart + smoke**

```
pytest tests/test_social_v2_polish.py -v
sudo systemctl restart baza-dashboard
```

Open Composer with a video source selected. Click ✂ on the shot rail item — trim modal opens, video plays inline. Set in/out, Save. Render — output should reflect the trim (if you have a video that can be trimmed).

- [ ] **Step 7: Commit**

```
git add dashboard/templates/ahb123.html dashboard/social_render.py dashboard/social_studio.py tests/test_social_v2_polish.py
git commit -m "social v2: per-clip trim modal + render pipeline trim support

Trim modal opens from the shot rail's ✂ button; inline video playback,
'use current time as in/out' buttons, in/out number inputs. render_video
now accepts either a list of paths (legacy) or a list of
{path, in_seconds, out_seconds} dicts; per-clip trims emit ffmpeg
concat demuxer inpoint/outpoint directives. Composer's renderPackage
forwards trims as {source_id: {in, out}} in the request body.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: "Render all platforms" one-click + A/B + Translate buttons

**Files:**
- Modify: `dashboard/templates/ahb123.html` — composer's variant panel buttons

- [ ] **Step 1: Add new buttons + functions**

In the composer IIFE's `render()` template, find the existing AI buttons row (a 2-column grid containing ✨ Caption, # Tags, 🪝 Hooks, 🎯 Score). Replace that grid AND the Render button below it with:

```javascript
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px">
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiCaption()">✨ Caption</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHashtags()"># Tags</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHooks()">🪝 Hooks</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiScore()">🎯 Score</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiAB()">🧪 A/B</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiTranslate()">🌐 ES</button>
          </div>
          <button class="btn-primary" style="width:100%;margin-top:10px" onclick="SocialStudio.modules.composer.renderPackage()">▶️ Render current platform</button>
          <button class="btn-secondary" style="width:100%;margin-top:6px" onclick="SocialStudio.modules.composer.renderAll()">▶️▶️ Render ALL selected platforms</button>
```

- [ ] **Step 2: Add the new functions to the composer IIFE**

After `renderPackage()`, append:

```javascript
  async function renderAll() {
    if (!state.shotList.length) {
      SocialStudio.modules.toast.info('Pick at least one source.');
      return;
    }
    // Iterate selected platforms (state.platforms)
    const platforms = Object.entries(state.platforms).filter(([_, on]) => on).map(([p]) => p);
    if (!platforms.length) {
      SocialStudio.modules.toast.info('Toggle at least one platform on.');
      return;
    }
    const batchToast = SocialStudio.modules.toast.progress('Rendering 0/' + platforms.length + ' platforms…');
    let done = 0;
    const trims = {};
    state.shotList.forEach(item => {
      if (typeof item === 'object' && (item.in_seconds != null || item.out_seconds != null)) {
        trims[String(item.id)] = { in_seconds: item.in_seconds, out_seconds: item.out_seconds };
      }
    });
    for (const platform of platforms) {
      const post = await fetch('/api/ahb/social/posts', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          platform, variant: platform,
          source_media_ids: state.shotList.map(i => typeof i === 'object' ? i.id : i),
          caption: (document.getElementById('ss-caption-' + platform) || {}).value || '',
          hashtags: (document.getElementById('ss-hashtags-' + platform) || {}).value || '',
        }),
      }).then(r => r.json());
      const ren = await fetch(`/api/ahb/social/posts/${post.id}/render-async`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          hook_text: document.getElementById('ss-hook-input').value || null,
          trims: trims,
        }),
      }).then(r => r.json());
      if (ren.error) {
        SocialStudio.modules.toast.error(platform + ' failed: ' + ren.error);
      }
      done++;
      SocialStudio.modules.toast.update(batchToast, {
        percent: Math.round(done / platforms.length * 100),
        msg: `Rendering ${done}/${platforms.length} platforms…`
      });
    }
    SocialStudio.modules.toast.resolve(batchToast, 'success', `${done}/${platforms.length} renders queued`);
    setTimeout(() => {
      if (SocialStudio.modules.library) SocialStudio.modules.library.render();
    }, 1500);
  }

  async function aiAB() {
    const platform = state.activePlatform;
    const body = (extra) => ({
      source_ids: state.shotList.map(i => typeof i === 'object' ? i.id : i),
      platform, tone: document.getElementById('ss-tone').value,
      length: document.getElementById('ss-length').value,
      style: document.getElementById('ss-style').value,
      ...extra,
    });
    SocialStudio.modules.toast.info('Generating A/B variants…');
    const [a, b] = await Promise.all([
      fetch('/api/ahb/social/ai/caption', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body({})),
      }).then(r => r.json()),
      fetch('/api/ahb/social/ai/caption', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body({})),
      }).then(r => r.json()),
    ]);
    // Show modal with side-by-side picks
    const m = document.createElement('div');
    m.className = 'modal-bg';
    m.style.cssText = 'display:flex';
    document.body.appendChild(m);
    const close = () => document.body.removeChild(m);
    m.innerHTML = `
      <div class="modal" style="max-width:780px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">🧪 A/B caption variations</div>
          <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <div style="background:#070712;border:1px solid #2a2a4a;border-radius:8px;padding:12px">
            <div style="font-weight:700;color:#10b981;margin-bottom:6px">Variant A</div>
            <div style="white-space:pre-wrap;font-size:13px;color:#ddd;min-height:120px">${_esc(a.caption||'')}</div>
            <button class="btn-primary" data-pick="A" style="width:100%;margin-top:10px">Use A</button>
          </div>
          <div style="background:#070712;border:1px solid #2a2a4a;border-radius:8px;padding:12px">
            <div style="font-weight:700;color:#60a5fa;margin-bottom:6px">Variant B</div>
            <div style="white-space:pre-wrap;font-size:13px;color:#ddd;min-height:120px">${_esc(b.caption||'')}</div>
            <button class="btn-primary" data-pick="B" style="width:100%;margin-top:10px">Use B</button>
          </div>
        </div>
      </div>
    `;
    m.querySelector('[data-close]').addEventListener('click', close);
    m.querySelectorAll('[data-pick]').forEach(btn => {
      btn.addEventListener('click', () => {
        const cap = btn.dataset.pick === 'A' ? a.caption : b.caption;
        document.getElementById('ss-caption-' + platform).value = cap || '';
        close();
        SocialStudio.modules.toast.success('Variant ' + btn.dataset.pick + ' applied');
      });
    });
  }

  async function aiTranslate() {
    const platform = state.activePlatform;
    const caption = document.getElementById('ss-caption-' + platform).value;
    if (!caption.trim()) {
      SocialStudio.modules.toast.info('Generate a caption first.');
      return;
    }
    const tid = SocialStudio.modules.toast.progress('Translating to ES…');
    const r = await fetch('/api/ahb/social/ai/translate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ text: caption, target_lang: 'es' }),
    });
    const j = await r.json();
    SocialStudio.modules.toast.resolve(tid, 'success', 'Translated');
    // Show in a small viewer
    const m = document.createElement('div');
    m.className = 'modal-bg';
    m.style.cssText = 'display:flex';
    document.body.appendChild(m);
    const close = () => document.body.removeChild(m);
    m.innerHTML = `
      <div class="modal" style="max-width:520px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div style="font-size:16px;font-weight:800">🌐 Spanish translation</div>
          <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
        </div>
        <textarea style="width:100%;height:160px;background:#070712;border:1px solid #2a2a4a;border-radius:6px;padding:8px;color:#fff;font-size:13px">${_esc(j.text||'')}</textarea>
        <div style="font-size:11px;color:#666;margin-top:8px">Copy this manually for now — multi-language post storage lands in v2.1.</div>
      </div>
    `;
    m.querySelector('[data-close]').addEventListener('click', close);
  }
```

- [ ] **Step 3: Add to the IIFE's return**

Update the composer IIFE's `return` statement to include the new functions:

```javascript
  return { render, toggle, setPlatform, aiCaption, aiHashtags, aiHooks, aiScore,
           renderPackage, renderAll, aiAB, aiTranslate,
           renderPreview, _reload, renderShotRail, shotRemove, shotTrim };
```

- [ ] **Step 4: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer. Render current platform button still works. Click "Render ALL selected platforms" — progresses toast counts up. Click 🧪 A/B — modal shows two captions; pick one applies. Click 🌐 ES — translation modal opens with Spanish text.

- [ ] **Step 5: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: render-all, A/B caption variants, translate button

▶️▶️ Render ALL iterates selected platforms with a single batched
progress toast. 🧪 A/B fires two parallel caption requests at the same
prompt + same temperature (Ollama's sampling gives natural variation),
shows side-by-side pick modal. 🌐 ES calls existing /ai/translate and
shows result in a copy-friendly viewer.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Device frames + light/dark + DPR toggles

**Files:**
- Modify: `dashboard/templates/ahb123.html` — append CSS + device module

- [ ] **Step 1: Add device frame CSS**

Append to the existing Social Studio style block:

```html
<style>
  .ss-device-iphone, .ss-device-pixel {
    position:relative; padding:14px; border-radius:38px;
    background:linear-gradient(135deg,#1a1a2e,#0a0a18);
    box-shadow:0 0 0 1px #2a2a4a, 0 12px 32px rgba(0,0,0,.6);
  }
  .ss-device-iphone::before {
    content:''; position:absolute; top:6px; left:50%; transform:translateX(-50%);
    width:120px; height:28px; background:#000; border-radius:0 0 18px 18px; z-index:2;
  }
  .ss-device-iphone::after {
    content:''; position:absolute; top:14px; left:50%; transform:translateX(-50%);
    width:90px; height:14px; background:#000; border-radius:8px; z-index:3;
  }
  .ss-device-pixel::before {
    content:''; position:absolute; top:14px; left:50%; transform:translateX(-50%);
    width:14px; height:14px; background:#000; border-radius:50%; z-index:3;
  }
  .ss-light-bg { background:#fafafa !important; }
  .ss-light-bg .ss-preview-shell { box-shadow:0 0 0 1px #ddd; }
  .ss-dpr-2 .ss-preview-shell img, .ss-dpr-2 .ss-preview-shell video { image-rendering:auto; }
  .ss-dpr-1 .ss-preview-shell img, .ss-dpr-1 .ss-preview-shell video { image-rendering:pixelated; }
  .ss-toolbar { display:flex; gap:6px; align-items:center; padding:6px 0; flex-wrap:wrap }
  .ss-toolbar select, .ss-toolbar button { background:#070712; border:1px solid #2a2a4a; border-radius:6px; padding:4px 8px; color:#aaa; font-size:11px; cursor:pointer }
  .ss-toolbar button.active { background:#10b981; color:#0a0a18; border-color:#10b981 }
</style>
```

- [ ] **Step 2: Append the device IIFE**

```html
<script>
SocialStudio.modules.device = (function(){
  function read(key, def) { return localStorage.getItem('ss_preview_' + key) || def; }
  function write(key, val) { localStorage.setItem('ss_preview_' + key, val); }

  function install() {
    // Inject the toolbar above the preview shell
    const shell = document.getElementById('ss-preview-shell');
    if (!shell) return;
    if (document.getElementById('ss-preview-toolbar')) return;
    const tb = document.createElement('div');
    tb.id = 'ss-preview-toolbar';
    tb.className = 'ss-toolbar';
    tb.innerHTML = `
      <select id="ss-device-pick">
        <option value="">No frame</option>
        <option value="iphone">📱 iPhone</option>
        <option value="pixel">📱 Pixel</option>
      </select>
      <button id="ss-light-toggle" title="Toggle light background">☀</button>
      <button id="ss-dpr-toggle" title="Toggle @1× / @2×">@2×</button>
    `;
    shell.parentElement.insertBefore(tb, shell);
    document.getElementById('ss-device-pick').value = read('device', '');
    apply();
    document.getElementById('ss-device-pick').addEventListener('change', (e) => {
      write('device', e.target.value); apply();
    });
    document.getElementById('ss-light-toggle').addEventListener('click', () => {
      const cur = read('light', '0');
      write('light', cur === '0' ? '1' : '0'); apply();
    });
    document.getElementById('ss-dpr-toggle').addEventListener('click', () => {
      const cur = read('dpr', '2');
      write('dpr', cur === '2' ? '1' : '2'); apply();
    });
  }

  function apply() {
    const shell = document.getElementById('ss-preview-shell');
    if (!shell) return;
    const parent = shell.parentElement;
    const device = read('device', '');
    const light = read('light', '0') === '1';
    const dpr = read('dpr', '2');
    // Reset
    parent.classList.remove('ss-device-iphone', 'ss-device-pixel', 'ss-light-bg', 'ss-dpr-1', 'ss-dpr-2');
    if (device === 'iphone') parent.classList.add('ss-device-iphone');
    if (device === 'pixel') parent.classList.add('ss-device-pixel');
    if (light) parent.classList.add('ss-light-bg');
    parent.classList.add('ss-dpr-' + dpr);
    // Visual button states
    const lb = document.getElementById('ss-light-toggle');
    const db = document.getElementById('ss-dpr-toggle');
    if (lb) lb.classList.toggle('active', light);
    if (db) { db.classList.toggle('active', dpr === '1'); db.textContent = dpr === '2' ? '@2×' : '@1×'; }
  }

  // Hook into the composer's render() to install our toolbar after the preview exists
  if (SocialStudio.modules.composer) {
    const origRender = SocialStudio.modules.composer.render;
    SocialStudio.modules.composer.render = function() {
      const r = origRender.apply(this, arguments);
      setTimeout(install, 0);
      return r;
    };
  }

  return { install, apply };
})();
</script>
```

- [ ] **Step 3: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer. Toolbar appears above preview with device picker + light/dark + DPR toggles. Pick iPhone — preview wraps in a phone frame with notch + dynamic island. Pick Pixel — hole-punch frame. Toggle light — background becomes off-white. Toggle DPR — switches between @1× pixelated and @2× smooth.

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: device frames (iPhone/Pixel) + light/DPR toggles

SocialStudio.modules.device adds a toolbar above the preview shell with
device frame picker (None / iPhone / Pixel — pure CSS pseudo-elements
for notch & hole-punch), light-bg toggle (shows preview on light
background for IG over-light themes), and @1×/@2× DPR toggle. State
persisted to localStorage. Hooks into composer.render() to install
the toolbar after preview renders.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Platform UI overlays + safe-zone + caption truncation

**Files:**
- Modify: `dashboard/templates/ahb123.html` — append CSS for overlays + module

- [ ] **Step 1: Add overlay CSS**

Append to the style block:

```html
<style>
  .ss-overlay-rail { position:absolute; right:6px; bottom:80px; display:flex; flex-direction:column; gap:14px; pointer-events:none; z-index:5 }
  .ss-overlay-rail .ss-or-icon { background:rgba(0,0,0,.4); color:#fff; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px; backdrop-filter:blur(8px) }
  .ss-overlay-rail .ss-or-label { color:#fff; font-size:9px; text-align:center; margin-top:2px; text-shadow:0 1px 2px rgba(0,0,0,.8) }
  .ss-overlay-caption { position:absolute; bottom:14px; left:14px; right:60px; color:#fff; font-size:11px; line-height:1.3; text-shadow:0 1px 2px rgba(0,0,0,.8); pointer-events:none; z-index:5; max-height:48px; overflow:hidden }
  .ss-overlay-topbar { position:absolute; top:0; left:0; right:0; padding:8px 14px; color:rgba(255,255,255,.85); font-size:11px; background:linear-gradient(180deg,rgba(0,0,0,.4),transparent); pointer-events:none; z-index:5; display:flex; justify-content:space-between }
  .ss-safe-zone { position:absolute; inset:0; pointer-events:none; z-index:6 }
  .ss-safe-zone::before, .ss-safe-zone::after { content:''; position:absolute; left:0; right:0; background:rgba(248,113,113,.18); border:1px dashed rgba(248,113,113,.5) }
  .ss-safe-zone::before { top:0; height:11%; }  /* Top status bar / hashtag bar */
  .ss-safe-zone::after { bottom:0; height:17%; }  /* Bottom caption area */
  .ss-trunc-marker { position:absolute; left:14px; right:14px; bottom:78px; pointer-events:none; z-index:5; color:rgba(255,255,255,.7); font-size:9px; font-style:italic; text-align:right }
</style>
```

- [ ] **Step 2: Append the overlay IIFE**

```html
<script>
SocialStudio.modules.overlay = (function(){
  function read(key, def) { return localStorage.getItem('ss_overlay_' + key) || def; }
  function write(key, val) { localStorage.setItem('ss_overlay_' + key, val); }

  function install() {
    const shell = document.getElementById('ss-preview-shell');
    if (!shell || document.getElementById('ss-overlay-toolbar')) return;
    const tb = document.createElement('div');
    tb.id = 'ss-overlay-toolbar';
    tb.className = 'ss-toolbar';
    tb.innerHTML = `
      <button id="ss-overlay-ui" title="Show platform UI overlay">📱 UI</button>
      <button id="ss-overlay-safe" title="Show safe zones">🛡 Safe</button>
      <button id="ss-overlay-trunc" title="Show caption truncation marker">✂ Trunc</button>
    `;
    // Insert at the end of the preview toolbar
    const preTb = document.getElementById('ss-preview-toolbar');
    if (preTb) preTb.appendChild(tb);
    else shell.parentElement.insertBefore(tb, shell);

    document.getElementById('ss-overlay-ui').addEventListener('click', () => {
      write('ui', read('ui', '0') === '0' ? '1' : '0'); apply();
    });
    document.getElementById('ss-overlay-safe').addEventListener('click', () => {
      write('safe', read('safe', '0') === '0' ? '1' : '0'); apply();
    });
    document.getElementById('ss-overlay-trunc').addEventListener('click', () => {
      write('trunc', read('trunc', '0') === '0' ? '1' : '0'); apply();
    });
    apply();
  }

  function _platformOverlayHTML(platform) {
    if (platform === 'tiktok') {
      return `
        <div class="ss-overlay-topbar"><span>Following | <strong>For You</strong></span></div>
        <div class="ss-overlay-rail">
          <div><div class="ss-or-icon">♥</div><div class="ss-or-label">12.3K</div></div>
          <div><div class="ss-or-icon">💬</div><div class="ss-or-label">421</div></div>
          <div><div class="ss-or-icon">🔖</div><div class="ss-or-label">567</div></div>
          <div><div class="ss-or-icon">↗</div><div class="ss-or-label">Share</div></div>
        </div>
        <div class="ss-overlay-caption">@allhomebuilding · <span style="opacity:.8">music — Original audio</span></div>
      `;
    }
    if (platform === 'ig_reel') {
      return `
        <div class="ss-overlay-topbar"><span>Reels</span><span>📷</span></div>
        <div class="ss-overlay-rail">
          <div><div class="ss-or-icon">♥</div><div class="ss-or-label">8.7K</div></div>
          <div><div class="ss-or-icon">💬</div><div class="ss-or-label">221</div></div>
          <div><div class="ss-or-icon">↗</div><div class="ss-or-label">Send</div></div>
          <div><div class="ss-or-icon">⋯</div></div>
        </div>
        <div class="ss-overlay-caption">@allhomebuilding · <span style="opacity:.8">3d</span></div>
      `;
    }
    if (platform === 'ig_story') {
      return `<div class="ss-overlay-topbar"><span>@allhomebuilding</span><span>×</span></div>`;
    }
    if (platform === 'ig_feed_square' || platform === 'ig_feed_portrait') {
      return `
        <div class="ss-overlay-topbar"><span>@allhomebuilding</span><span>⋯</span></div>
        <div style="position:absolute;bottom:8px;left:8px;right:8px;color:#fff;font-size:10px;pointer-events:none;z-index:5;display:flex;gap:14px">
          <span>♥</span><span>💬</span><span>↗</span>
        </div>
      `;
    }
    return '';
  }

  function apply() {
    const shell = document.getElementById('ss-preview-shell');
    if (!shell) return;
    // Strip prior overlays
    shell.querySelectorAll('.ss-overlay-rail, .ss-overlay-caption, .ss-overlay-topbar, .ss-safe-zone, .ss-trunc-marker').forEach(e => e.remove());
    const ui = read('ui', '0') === '1';
    const safe = read('safe', '0') === '1';
    const trunc = read('trunc', '0') === '1';
    if (ui) {
      const tmp = document.createElement('div');
      tmp.innerHTML = _platformOverlayHTML(SocialStudio.state.activePlatform);
      while (tmp.firstChild) shell.appendChild(tmp.firstChild);
    }
    if (safe) {
      const z = document.createElement('div');
      z.className = 'ss-safe-zone';
      shell.appendChild(z);
    }
    if (trunc) {
      const tm = document.createElement('div');
      tm.className = 'ss-trunc-marker';
      tm.textContent = '… more (TikTok truncates here at ~120 chars)';
      shell.appendChild(tm);
    }
    // Visual state on buttons
    ['ui', 'safe', 'trunc'].forEach(k => {
      const b = document.getElementById('ss-overlay-' + k);
      if (b) b.classList.toggle('active', read(k, '0') === '1');
    });
  }

  // Hook into composer.setPlatform so overlays re-apply on platform change
  if (SocialStudio.modules.composer) {
    const origSetPlatform = SocialStudio.modules.composer.setPlatform;
    SocialStudio.modules.composer.setPlatform = function(p) {
      const r = origSetPlatform.apply(this, arguments);
      setTimeout(apply, 0);
      return r;
    };
    const origRender = SocialStudio.modules.composer.render;
    SocialStudio.modules.composer.render = function() {
      const r = origRender.apply(this, arguments);
      setTimeout(install, 0);
      return r;
    };
  }

  return { install, apply };
})();
</script>
```

- [ ] **Step 3: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer. Three more buttons appear in the preview toolbar: 📱 UI, 🛡 Safe, ✂ Trunc. Toggle each on and off. Switching platforms updates the overlay (TikTok shows right-rail; IG Reel shows different right-rail; IG Story shows just a top bar).

- [ ] **Step 4: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: platform UI overlays + safe zones + truncation markers

SocialStudio.modules.overlay adds 3 toggles to the preview toolbar. UI
overlay draws the native right-rail (likes/comments/share/save) +
top-bar + caption-bar for the active platform (different layout per
TikTok / IG Reel / IG Feed / IG Story). Safe-zone overlay paints
translucent red bands at top (~11%) and bottom (~17%) marking areas
typically covered by platform UI. Truncation marker shows where the
caption gets cut.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: IG cover grid preview

**Files:**
- Modify: `dashboard/templates/ahb123.html` — gridpreview IIFE + sidebar slot

- [ ] **Step 1: Append the grid preview IIFE**

```html
<script>
SocialStudio.modules.gridpreview = (function(){
  function install() {
    const shell = document.getElementById('ss-preview-shell');
    if (!shell || document.getElementById('ss-grid-preview')) return;
    const wrap = shell.parentElement;
    // Add toggle button in the toolbar
    const tb = document.getElementById('ss-overlay-toolbar') || document.getElementById('ss-preview-toolbar');
    if (tb) {
      const btn = document.createElement('button');
      btn.id = 'ss-grid-toggle';
      btn.textContent = '⊞ Grid';
      btn.title = 'Show IG 3x3 grid preview';
      btn.addEventListener('click', toggle);
      tb.appendChild(btn);
    }
    // Container injected just below the preview shell, hidden by default
    const gp = document.createElement('div');
    gp.id = 'ss-grid-preview';
    gp.style.cssText = 'display:none;margin-top:10px;background:#070712;border:1px solid #2a2a4a;border-radius:8px;padding:10px';
    wrap.appendChild(gp);
  }

  let visible = false;
  function toggle() {
    visible = !visible;
    const el = document.getElementById('ss-grid-preview');
    if (!el) return;
    el.style.display = visible ? 'block' : 'none';
    document.getElementById('ss-grid-toggle').classList.toggle('active', visible);
    if (visible) refresh();
  }

  async function refresh() {
    const platform = SocialStudio.state.activePlatform;
    if (!['ig_feed_square', 'ig_feed_portrait'].includes(platform)) {
      document.getElementById('ss-grid-preview').innerHTML =
        '<div style="color:#666;font-size:12px;text-align:center;padding:20px">Grid preview is only relevant for IG Feed.</div>';
      return;
    }
    // Fetch last 8 posted IG-feed items
    const r = await fetch('/api/ahb/social/posts?status=posted&platform=' + platform + '&limit=8');
    const items = ((await r.json()).items || []);
    // Build a 3×3 grid: position 0 = current draft (placeholder), positions 1-8 = last 8
    const cells = [];
    cells.push({ current: true });
    items.forEach(p => cells.push({ id: p.id, cover: p.cover_path ? '/api/ahb/social/posts/' + p.id + '/cover' : '' }));
    while (cells.length < 9) cells.push({ empty: true });
    document.getElementById('ss-grid-preview').innerHTML = `
      <div style="font-size:11px;color:#aaa;margin-bottom:8px">Your IG feed with this post at the top-left:</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:2px">
        ${cells.slice(0, 9).map(c => {
          if (c.current) {
            const firstItem = SocialStudio.state.shotList[0];
            const id = typeof firstItem === 'object' ? (firstItem && firstItem.id) : firstItem;
            const src = SocialStudio.state.sources.find(s => s.id === id);
            const url = src ? '/api/cloud/thumb/' + encodeURIComponent(src.sub_path) + '?size=300' : '';
            return `<div style="aspect-ratio:1;background:#1a1a2e;border:2px solid #10b981;border-radius:4px;overflow:hidden"><img src="${url}" style="width:100%;height:100%;object-fit:cover" onerror="this.style.display='none'"></div>`;
          }
          if (c.empty) {
            return `<div style="aspect-ratio:1;background:#1a1a2e;border-radius:4px"></div>`;
          }
          return `<div style="aspect-ratio:1;background:#1a1a2e;border-radius:4px;overflow:hidden"><img src="${c.cover}" style="width:100%;height:100%;object-fit:cover" onerror="this.style.display='none'"></div>`;
        }).join('')}
      </div>
    `;
  }

  // Hook into composer
  if (SocialStudio.modules.composer) {
    const origRender = SocialStudio.modules.composer.render;
    SocialStudio.modules.composer.render = function() {
      const r = origRender.apply(this, arguments);
      setTimeout(install, 0);
      return r;
    };
    const origToggle = SocialStudio.modules.composer.toggle;
    SocialStudio.modules.composer.toggle = function() {
      const r = origToggle.apply(this, arguments);
      if (visible) setTimeout(refresh, 0);
      return r;
    };
    const origSetPlatform = SocialStudio.modules.composer.setPlatform;
    SocialStudio.modules.composer.setPlatform = function() {
      const r = origSetPlatform.apply(this, arguments);
      if (visible) setTimeout(refresh, 0);
      return r;
    };
  }

  return { install, toggle, refresh };
})();
</script>
```

- [ ] **Step 2: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open Composer with IG Feed Square selected. Click ⊞ Grid in the toolbar. A 3×3 grid appears below the preview: current draft (highlighted green) top-left, last 8 posted IG feed items in the other slots. Switch platform to TikTok — grid says "only relevant for IG Feed."

- [ ] **Step 3: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: IG 3x3 cover grid preview

SocialStudio.modules.gridpreview adds a ⊞ Grid toggle in the preview
toolbar. When ON and platform is IG Feed (square or portrait), shows
a 3x3 grid: current draft top-left (highlighted green), last 8 posted
IG-feed items in the other 8 slots. Helps visualize how the new cover
will fit the user's existing grid aesthetic. Re-renders on shot pick
or platform change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Mobile responsive + touch drag + tooltips + tour + empty CTAs + ARIA

**Files:**
- Modify: `dashboard/templates/ahb123.html` — append CSS + multiple small modules

This task bundles all the J.* items into one commit because each is small.

- [ ] **Step 1: Add mobile-responsive CSS**

Append to the style block:

```html
<style>
  /* Mobile-first responsive Composer */
  @media (max-width: 768px) {
    #tab-social .ss-grid {
      grid-template-columns: 1fr;
      gap: 8px;
    }
    #tab-social .ss-card {
      padding: 8px;
    }
    /* Source picker as horizontal scroll on mobile */
    #ss-source-grid {
      grid-template-columns: repeat(4, 100px) !important;
      grid-auto-flow: column;
      overflow-x: auto;
      max-height: 140px !important;
      padding-bottom: 6px;
    }
    #ss-source-grid::-webkit-scrollbar { height: 4px; }
    /* Variant panel as bottom-sheet trigger */
    #ss-variant-panel {
      position: fixed;
      bottom: 0; left: 0; right: 0;
      max-height: 60vh;
      overflow-y: auto;
      background: #0a0a18;
      border-top: 2px solid #10b981;
      border-radius: 14px 14px 0 0;
      z-index: 1000;
      transform: translateY(calc(100% - 56px));
      transition: transform .25s ease;
      box-shadow: 0 -8px 24px rgba(0,0,0,.5);
    }
    #ss-variant-panel.open { transform: translateY(0); }
    #ss-variant-panel::before {
      content: 'Tap to ✏ edit copy ▲';
      display: block;
      text-align: center;
      padding: 12px;
      font-size: 13px;
      color: #aaa;
      cursor: pointer;
    }
    /* Touch-friendly buttons */
    #tab-social button {
      min-height: 44px;
    }
    /* Toast position adjusts for bottom sheet */
    #socialToastStack {
      bottom: 80px !important;
    }
  }
  /* Tooltip CSS — works on all sizes */
  .ss-tip {
    position: relative;
  }
  .ss-tip:hover::after, .ss-tip:focus::after {
    content: attr(data-tip);
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    background: #0a0a18;
    border: 1px solid #2a2a4a;
    border-radius: 6px;
    padding: 6px 10px;
    color: #ddd;
    font-size: 11px;
    white-space: nowrap;
    z-index: 999;
    pointer-events: none;
  }
  /* Tour overlay */
  .ss-tour-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 99998;
    display: flex; align-items: center; justify-content: center;
  }
  .ss-tour-card {
    background: #0e0e1e; border: 1px solid #10b981; border-radius: 12px;
    padding: 20px; max-width: 420px; color: #fff;
  }
</style>
```

- [ ] **Step 2: Append the mobile bottom-sheet toggle IIFE**

```html
<script>
SocialStudio.modules.mobile = (function(){
  function install() {
    if (window.matchMedia && window.matchMedia('(min-width: 769px)').matches) return;
    const vp = document.getElementById('ss-variant-panel');
    if (!vp || vp.dataset.mobileWired) return;
    vp.dataset.mobileWired = '1';
    vp.addEventListener('click', (e) => {
      if (e.target === vp || (e.target.previousSibling === null && e.target.parentElement === vp)) {
        vp.classList.toggle('open');
      }
    });
  }
  if (SocialStudio.modules.composer) {
    const origRender = SocialStudio.modules.composer.render;
    SocialStudio.modules.composer.render = function() {
      const r = origRender.apply(this, arguments);
      setTimeout(install, 0);
      return r;
    };
  }
  return { install };
})();
</script>
```

- [ ] **Step 3: Add tooltips to existing buttons**

Sweep through the existing Social tab markup and add `class="ss-tip" data-tip="…"` attributes to the most-used buttons. Use grep + Edit. Critical buttons to tag (do these in `ahb123.html`):

- `✨ Caption` → `data-tip="Generate caption (local AI)"`
- `# Tags` → `data-tip="Suggest hashtags (forces brand floor tags)"`
- `🪝 Hooks` → `data-tip="3 hook variants — pattern-interrupt style"`
- `🎯 Score` → `data-tip="Rate this post 0–100 + 1-paragraph critique"`
- `🧪 A/B` → `data-tip="Generate 2 caption variants side-by-side"`
- `🌐 ES` → `data-tip="Translate caption to Spanish"`
- `▶️ Render current platform` → `data-tip="Render just the active platform"`
- `▶️▶️ Render ALL selected platforms` → `data-tip="Render all platforms with one click"`
- `📲` (Telegram drop in Library) → `data-tip="Send to your phone via Telegram"`
- `✅` (Approve) → `data-tip="Approve this draft"`
- `❌` (Reject) → `data-tip="Reject this draft"`

Add the attributes by Edit. Example for the Composer AI buttons row, find the existing:

```javascript
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiCaption()">✨ Caption</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHashtags()"># Tags</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiHooks()">🪝 Hooks</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiScore()">🎯 Score</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiAB()">🧪 A/B</button>
            <button class="btn-secondary" onclick="SocialStudio.modules.composer.aiTranslate()">🌐 ES</button>
```

Replace with:

```javascript
            <button class="btn-secondary ss-tip" data-tip="Generate caption (local AI)" onclick="SocialStudio.modules.composer.aiCaption()">✨ Caption</button>
            <button class="btn-secondary ss-tip" data-tip="Suggest hashtags (forces brand floor tags)" onclick="SocialStudio.modules.composer.aiHashtags()"># Tags</button>
            <button class="btn-secondary ss-tip" data-tip="3 hook variants — pattern-interrupt style" onclick="SocialStudio.modules.composer.aiHooks()">🪝 Hooks</button>
            <button class="btn-secondary ss-tip" data-tip="Rate this post 0–100 + critique" onclick="SocialStudio.modules.composer.aiScore()">🎯 Score</button>
            <button class="btn-secondary ss-tip" data-tip="Generate 2 caption variants" onclick="SocialStudio.modules.composer.aiAB()">🧪 A/B</button>
            <button class="btn-secondary ss-tip" data-tip="Translate caption to Spanish" onclick="SocialStudio.modules.composer.aiTranslate()">🌐 ES</button>
```

- [ ] **Step 4: Add empty-state CTAs**

In the Library module's `paint()`, find the empty-state fallback:

```javascript
      <div>${grid || '<div style="color:#444;padding:40px;text-align:center">No posts yet.</div>'}</div>
```

Replace with:

```javascript
      <div>${grid || '<div style="color:#444;padding:40px;text-align:center">No posts yet. <button class="btn-primary" style="margin-left:8px" onclick="SocialStudio.switchSub(\\'composer\\')">Pick media in Composer →</button></div>'}</div>
```

In the Scheduler module's `load()`, find the empty fallback:

```javascript
    root().innerHTML = html || '<div style="color:#444;padding:40px;text-align:center">No scheduled posts.</div>';
```

Replace with:

```javascript
    root().innerHTML = html || '<div style="color:#444;padding:40px;text-align:center">Nothing scheduled. <button class="btn-primary" style="margin-left:8px" onclick="SocialStudio.switchSub(\\'library\\')">Pick from Library →</button></div>';
```

In the Presets module's `paint()`, find:

```javascript
      <div>${list || '<div style="color:#444;padding:40px;text-align:center">No presets. Click "Install seed presets".</div>'}</div>
```

Replace with:

```javascript
      <div>${list || '<div style="color:#444;padding:40px;text-align:center">No presets. <button class="btn-primary" style="margin-left:8px" onclick="SocialStudio.modules.presets.installSeeds()">Install 8 seed presets →</button></div>'}</div>
```

- [ ] **Step 5: Add ARIA labels and Esc-to-close on all modals**

Add an aria sweep IIFE:

```html
<script>
SocialStudio.modules.a11y = (function(){
  function install() {
    // Tag all body-level social modals as role=dialog aria-modal=true
    document.querySelectorAll('.modal-bg[id^="social"]').forEach(m => {
      m.setAttribute('role', 'dialog');
      m.setAttribute('aria-modal', 'true');
    });
    // Global Esc handler — close any visible social modal
    if (!document.body.dataset.ssEscWired) {
      document.body.dataset.ssEscWired = '1';
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.modal-bg[id^="social"]').forEach(m => {
          if (m.style.display !== 'none') m.style.display = 'none';
        });
      });
    }
    // Label common interactive elements that may lack labels
    document.querySelectorAll('#tab-social button').forEach(b => {
      if (!b.getAttribute('aria-label')) b.setAttribute('aria-label', (b.textContent || '').trim() || 'button');
    });
  }
  const origInit = SocialStudio.init;
  SocialStudio.init = function() { const r = origInit.apply(this, arguments); install(); return r; };
  return { install };
})();
</script>
```

- [ ] **Step 6: First-time user tour**

```html
<script>
SocialStudio.modules.tour = (function(){
  const STEPS = [
    { sel: '#tab-social .page-header', text: '👋 Welcome to Social Studio. Make TikTok + Instagram content from your project media — all local AI.' },
    { sel: '#ss-sub-composer .ss-card:nth-child(1)', text: '🗂 Pick sources from your existing project media here.' },
    { sel: '#ss-preview-shell', text: '📱 Live preview shows exactly what your post will look like.' },
    { sel: '#ss-variant-panel', text: '✨ AI tools: caption, hashtags, hooks, score, A/B, translate.' },
    { sel: '.ss-subnav[data-sub="library"]', text: '📚 Library tracks all drafts, scheduled, and posted content.' },
    { sel: '.ss-subnav[data-sub="autopilot"]', text: '🤖 Auto-Pilot drafts posts on a schedule when you enable it.' },
    { sel: null, text: 'Press ? anytime to see keyboard shortcuts. Happy posting! 🚀' },
  ];

  function show(stepIdx) {
    document.querySelectorAll('.ss-tour-overlay').forEach(e => e.remove());
    if (stepIdx >= STEPS.length) {
      localStorage.setItem('ss_tour_done', '1');
      return;
    }
    const step = STEPS[stepIdx];
    const o = document.createElement('div');
    o.className = 'ss-tour-overlay';
    o.innerHTML = `
      <div class="ss-tour-card">
        <div style="font-size:14px;line-height:1.4;color:#fff;margin-bottom:14px">${step.text}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
          <button class="btn-secondary" data-skip>Skip tour</button>
          <div style="color:#aaa;font-size:11px">${stepIdx+1} / ${STEPS.length}</div>
          <button class="btn-primary" data-next>${stepIdx === STEPS.length - 1 ? 'Done' : 'Next'}</button>
        </div>
      </div>
    `;
    document.body.appendChild(o);
    o.querySelector('[data-skip]').addEventListener('click', () => {
      o.remove(); localStorage.setItem('ss_tour_done', '1');
    });
    o.querySelector('[data-next]').addEventListener('click', () => {
      o.remove(); show(stepIdx + 1);
    });
  }

  function maybeStart() {
    if (localStorage.getItem('ss_tour_done')) return;
    // Only start when Social tab is open
    if (!document.getElementById('tab-social').classList.contains('active')) return;
    setTimeout(() => show(0), 400);
  }

  function force() { localStorage.removeItem('ss_tour_done'); show(0); }

  const origInit = SocialStudio.init;
  SocialStudio.init = function() { const r = origInit.apply(this, arguments); maybeStart(); return r; };
  return { show, force };
})();
</script>
```

- [ ] **Step 7: Restart + smoke**

```
sudo systemctl restart baza-dashboard
```

Open `/ahb123` in a mobile-emulated viewport (Chrome devtools → toggle device toolbar → iPhone 14). Composer single-column, source grid scrolls horizontally, variant panel is a bottom-sheet you tap to open.

In normal viewport: hover the ✨ Caption button — tooltip "Generate caption (local AI)" appears above it.

Open an empty Library (no posts) — see "No posts yet. [Pick media in Composer →]".

Run `localStorage.removeItem('ss_tour_done'); SocialStudio.modules.tour.force()` in devtools — tour walks through 7 steps.

Press `?` — keyboard shortcuts overlay. Press Esc — closes.

- [ ] **Step 8: Commit**

```
git add dashboard/templates/ahb123.html
git commit -m "social v2: mobile responsive + tooltips + tour + empty CTAs + ARIA

@media (max-width:768px): composer collapses to single column, source
picker becomes horizontal scroll, variant panel becomes a slide-up
bottom sheet, all buttons get 44px min-height for touch. .ss-tip CSS
shows hover/focus tooltips with data-tip attribute. Empty-state CTAs
in Library/Scheduler/Presets link to next-step actions. First-time
tour walks through 7 highlights when ss_tour_done flag absent. ARIA
sweep tags modals as role=dialog aria-modal=true and adds Esc-close.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Plan self-review (completed inline)

**Spec coverage:**
- A.1 fonts → Task 1 ✓
- A.2 toasts → Task 3 ✓
- A.3 keyboard shortcuts → Task 4 ✓
- A.4 render progress polling → Task 2 (backend) + Task 5 (UI) ✓
- A.5 drag-reorder → Task 6 ✓
- A.6 trim handles → Task 7 ✓
- A.7 render-all → Task 8 ✓
- A.8 A/B variations → Task 8 ✓
- A.9 translate-in-composer → Task 8 ✓
- F.1 device frames → Task 9 ✓
- F.2 platform UI overlay → Task 10 ✓
- F.3 safe-zone indicators → Task 10 ✓
- F.4 caption truncation → Task 10 ✓
- F.5 IG cover grid → Task 11 ✓
- F.6 light/dark toggle → Task 9 ✓
- F.7 DPR toggle → Task 9 ✓
- J.1 mobile responsive → Task 12 ✓
- J.2 touch drag → Task 12 (via min-height + HTML5 drag from Task 6 which works for touch in most browsers — full PointerEvent rewrite deferred to v2.3 if needed) ✓
- J.3 tooltips → Task 12 ✓
- J.4 tour → Task 12 ✓
- J.5 empty CTAs → Task 12 ✓
- J.6 ARIA → Task 12 ✓
- J.7 keyboard nav for grids → Task 4 (j/k) ✓

**Placeholder scan:** searched for "TBD" / "TODO" / "implement later" / "fill in" — none found in plan body. The Score popup is intentionally left as alert() in Task 3 (multi-line, score modal is v2.1 scope) — that's an explicit deferral, not a placeholder.

**Type consistency:** `state.shotList` items are normalized to `{id, in_seconds, out_seconds}` objects in Task 6; Tasks 7, 8 use this shape consistently. `_kick_render_async` defined in Task 2 is updated in Task 7. `_resolve_media_paths` from Phase 1 reused unchanged. `SocialStudio.modules.toast` API (info/success/error/progress/update/resolve) defined in Task 3 and used in Tasks 5, 8.

**Carve-outs noted as intentional (NOT placeholders):**
- True ffmpeg subprocess kill on cancel — Task 2 stores dashboard's pid; v2.1 will pass child pid up from render module
- Multi-language storage on posts — Task 8 shows translation in modal but doesn't yet save to the new `translations` column; v2.1 schema migration adds that
- Score popup remains alert() — v2.1 adds a proper score modal
- Full PointerEvent touch-drag rewrite — HTML5 drag works on most mobile browsers; defer the rewrite if real users hit issues

---

## Execution

**Plan complete and saved to** `docs/superpowers/plans/2026-05-23-ahb123-social-studio-v2.0-polish-plan.md`.

This is Phase v2.0 of 3 (v2.1 and v2.2 plans coming next).

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration on issues
2. **Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints
