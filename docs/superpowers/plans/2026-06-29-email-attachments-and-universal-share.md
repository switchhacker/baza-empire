# Email Attachments/Preview + Universal Share — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add file-upload + inline preview to the Email tab, and a single reusable Share sheet (Link / Email / Telegram) backed by one service, wired into Data Hub, Projects, and Cloud.

**Architecture:** Part A extends `email_studio.py` (a Flask blueprint mounted at `/api/email2/*`) and its template `email.html`. Part B adds a self-contained `share_service.py` blueprint (`/api/share`) plus a body-level `_share_sheet.html` partial included by each tab template. Link-sharing generalizes the existing `cloud_shares` token table from cloud-only to multiple file roots via a new `root` column; `/s/<token>` already supports `?inline=1`.

**Tech Stack:** Python 3 / Flask blueprints, SQLite (`baza_projects.db`), Gmail API (existing OAuth), Telegram Bot API (Phil's bot), pytest, vanilla-JS Jinja templates.

**Spec:** `docs/superpowers/specs/2026-06-29-email-attachments-and-universal-share-design.md`

---

## Conventions (read once)

- **Tests** live in top-level `tests/`, run with `venv/bin/python -m pytest`. They put `dashboard/` on `sys.path` and `importlib.import_module(...)`. See `tests/test_email_attachments.py` for the exact pattern (fixture `es` re-imports `email_studio`). `pytest.ini` sets `testpaths = tests`.
- **Run tests:** from repo root `/home/switchhacker/baza-empire/agent-framework-v3`, use `venv/bin/python -m pytest tests/<file>::<test> -v`.
- **Dashboard caches templates** (`debug=False`). After ANY `templates/*.html` edit: `sudo systemctl restart baza-dashboard.service`, then verify with `curl`.
- **Do NOT manually `git commit`** in this repo — `claw-auto-git.timer` commits hourly. The "Commit" steps below are written for completeness; in this repo, **skip the commit step** and instead note progress in `~/Desktop/baza-session-log.md`. (If a worktree is used per subagent-driven-development, commit normally there.)
- **Modals MUST be body-level**, never nested in a `#tab-*` div (ancestor `display:none` hides them).
- **Privacy hard-rule:** `.private-inbound/` and `.vault_meta/` files are never shareable; the resolver rejects them.

## File Structure

**New files**
- `dashboard/share_service.py` — roots registry, `resolve_source` / `resolve_share_path`, channel handlers (`create_link`, `share_email`, `share_telegram`), `/api/share` blueprint `share_bp`, schema helper `_ensure_share_schema`.
- `dashboard/templates/_share_sheet.html` — body-level Share modal (Link/Email/Telegram sub-tabs) + `openShareSheet()` JS.
- `tests/test_share_service.py` — unit tests for resolver + channels + dispatch.
- `tests/test_email_upload_preview.py` — unit tests for upload staging, resolve-upload ref, inline serve.

**Modified files**
- `dashboard/email_studio.py` — `?inline=1` on `api_attachment`; `/api/email2/attachments/upload`; `_resolve_attachments` `upload` ref; `_cleanup_uploads` + `_sweep_outbox`; call cleanup in `api_send`.
- `dashboard/templates/email.html` — compose file picker + chips; inbound preview modal + clickable chips.
- `dashboard/app.py` — `cloud_shares.root` migration in `init_cloud_tables`; generalize `public_share`; register `share_bp`.
- `dashboard/templates/datahub.html`, `templates/projects.html`, `templates/cloud.html` — Share buttons → `openShareSheet`; `{% include '_share_sheet.html' %}` at body level.

---

# PART A — Email tab: attachments & preview

### Task 1: Inline serve for inbound attachments

**Files:**
- Modify: `dashboard/email_studio.py:716-736` (`api_attachment`)
- Test: `tests/test_email_upload_preview.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_upload_preview.py`:

```python
import base64, importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def _client(es):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


class _FakeAttSvc:
    def users(self): return self
    def messages(self): return self
    def attachments(self): return self
    def get(self, userId, messageId, id): return self
    def execute(self): return {"data": base64.urlsafe_b64encode(b"hello-bytes").decode()}


def test_attachment_inline_disposition(es, monkeypatch):
    monkeypatch.setattr(es, "_req_account_id", lambda: "acct")
    monkeypatch.setattr(es, "_gmail", lambda a: _FakeAttSvc())
    c = _client(es)
    r_inline = c.get("/api/email2/attachment/m1/a1?inline=1&name=x.txt&mime=text/plain")
    r_dl = c.get("/api/email2/attachment/m1/a1?name=x.txt&mime=text/plain")
    assert r_inline.status_code == 200
    assert "inline" in r_inline.headers["Content-Disposition"]
    assert "attachment" in r_dl.headers["Content-Disposition"]
    assert r_inline.data == b"hello-bytes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_email_upload_preview.py::test_attachment_inline_disposition -v`
Expected: FAIL — current code always sends `attachment` disposition.

- [ ] **Step 3: Implement inline disposition**

In `dashboard/email_studio.py`, replace the `return Response(...)` block inside `api_attachment` (currently lines ~729-732):

```python
        data = base64.urlsafe_b64decode(att.get("data", ""))
        inline = request.args.get("inline") in ("1", "true", "yes")
        disp = "inline" if inline else "attachment"
        return Response(data, mimetype=mime, headers={
            "Content-Disposition": f'{disp}; filename="{safe_name}"',
            "Content-Length": str(len(data)),
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_email_upload_preview.py::test_attachment_inline_disposition -v`
Expected: PASS

- [ ] **Step 5: Commit** (skip in this repo — auto-git; log to session log instead)

```bash
git add dashboard/email_studio.py tests/test_email_upload_preview.py
git commit -m "feat(email): inline disposition for attachment preview"
```

---

### Task 2: Outbound upload staging + resolve `upload` ref + cleanup

**Files:**
- Modify: `dashboard/email_studio.py` — add constants + `_sweep_outbox` near line 37; add `api_attachment_upload` route after `api_attachment` (~line 737); add `upload` branch to `_resolve_attachments` (~line 826); add `_cleanup_uploads` after `_resolve_attachments`; call cleanup in `api_send` (~line 938).
- Test: `tests/test_email_upload_preview.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_upload_preview.py`:

```python
import io


def test_upload_then_resolve_roundtrip(es, monkeypatch, tmp_path):
    monkeypatch.setattr(es, "OUTBOX_DIR", str(tmp_path / "outbox"))
    os.makedirs(es.OUTBOX_DIR, exist_ok=True)
    c = _client(es)
    r = c.post("/api/email2/attachments/upload",
               data={"file": (io.BytesIO(b"PNGDATA"), "pic.png")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["filename"] == "pic.png" and j["size"] == 7
    resolved = es._resolve_attachments([{"type": "upload", "token": j["token"]}])
    assert resolved[0]["filename"] == "pic.png"
    assert resolved[0]["data"] == b"PNGDATA"


def test_resolve_bad_upload_token_raises(es, monkeypatch, tmp_path):
    monkeypatch.setattr(es, "OUTBOX_DIR", str(tmp_path / "outbox"))
    os.makedirs(es.OUTBOX_DIR, exist_ok=True)
    with pytest.raises(ValueError):
        es._resolve_attachments([{"type": "upload", "token": "deadbeef"}])


def test_cleanup_uploads_removes_staged(es, monkeypatch, tmp_path):
    monkeypatch.setattr(es, "OUTBOX_DIR", str(tmp_path / "outbox"))
    os.makedirs(es.OUTBOX_DIR, exist_ok=True)
    c = _client(es)
    r = c.post("/api/email2/attachments/upload",
               data={"file": (io.BytesIO(b"x"), "f.bin")},
               content_type="multipart/form-data")
    tok = r.get_json()["token"]
    assert os.path.isdir(os.path.join(es.OUTBOX_DIR, tok))
    es._cleanup_uploads([{"type": "upload", "token": tok}])
    assert not os.path.isdir(os.path.join(es.OUTBOX_DIR, tok))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_email_upload_preview.py -v -k "upload or cleanup"`
Expected: FAIL — `OUTBOX_DIR`, route, `_cleanup_uploads` not defined.

- [ ] **Step 3a: Add staging dir constant + sweep**

In `dashboard/email_studio.py`, after line 37 (`EMAIL_PIPELINE_DIR = ...`) add:

```python
OUTBOX_DIR = os.path.join(EMAIL_PIPELINE_DIR, ".outbox_uploads")


def _sweep_outbox(max_age=6 * 3600):
    """Delete staged upload dirs older than max_age seconds (orphans)."""
    import shutil
    try:
        now = time.time()
        for name in os.listdir(OUTBOX_DIR):
            p = os.path.join(OUTBOX_DIR, name)
            try:
                if os.path.isdir(p) and now - os.path.getmtime(p) > max_age:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except FileNotFoundError:
        pass
```

Then near the bottom of the module's top-level setup (after the constants block, before the first route is fine — place it right after the `_sweep_outbox` def):

```python
os.makedirs(OUTBOX_DIR, exist_ok=True)
_sweep_outbox()
```

- [ ] **Step 3b: Add the upload route**

In `dashboard/email_studio.py`, immediately after the `api_attachment` function (after line 736) add:

```python
@email_bp.route("/api/email2/attachments/upload", methods=["POST"])
def api_attachment_upload():
    """Stage an uploaded file for sending. Returns {ok, token, filename, size, mime}."""
    import mimetypes
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    token = uuid.uuid4().hex
    safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(f.filename))[:160] or "file"
    d = os.path.join(OUTBOX_DIR, token)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, safe)
    f.save(dest)
    size = os.path.getsize(dest)
    if size > _MAX_ATTACH_BYTES:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return jsonify({"ok": True, "token": token, "filename": safe, "size": size, "mime": mime})
```

- [ ] **Step 3c: Add the `upload` branch to `_resolve_attachments`**

In `_resolve_attachments` (line ~810), add a new `elif` BEFORE the final `else: raise ValueError(...)`:

```python
        elif t == "upload":
            import mimetypes
            token = re.sub(r'[^0-9a-f]', "", str(ref.get("token") or ""))[:32]
            d = os.path.join(OUTBOX_DIR, token)
            if not token or not os.path.isdir(d):
                raise ValueError("upload not found")
            files = [x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x))]
            if not files:
                raise ValueError("upload not found")
            full = os.path.join(d, files[0])
            with open(full, "rb") as f:
                data = f.read()
            fn = os.path.basename(full)
            mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
```

- [ ] **Step 3d: Add `_cleanup_uploads` + call it on send**

After `_resolve_attachments` (after line 832) add:

```python
def _cleanup_uploads(refs):
    """Delete staged upload dirs for the given send refs (best-effort)."""
    import shutil
    for ref in refs or []:
        if (ref or {}).get("type") == "upload":
            token = re.sub(r'[^0-9a-f]', "", str(ref.get("token") or ""))[:32]
            if token:
                shutil.rmtree(os.path.join(OUTBOX_DIR, token), ignore_errors=True)
```

In `api_send`, just after the successful send (right after `result = svc.users().messages().send(...).execute()`, line ~925) add:

```python
        _cleanup_uploads(data.get("attachments"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_email_upload_preview.py -v`
Expected: PASS (all, incl. Task 1).

- [ ] **Step 5: Commit** (skip — auto-git; log instead)

```bash
git add dashboard/email_studio.py tests/test_email_upload_preview.py
git commit -m "feat(email): upload staging + resolve/cleanup upload attachments"
```

---

### Task 3: Compose attachment UI (email.html)

**Files:**
- Modify: `dashboard/templates/email.html` — compose modal (496-535), `openCompose`/`closeCompose` (945-958), `sendCompose` (960-973).

No unit test (template JS). Verify manually at the end.

- [ ] **Step 1: Add CSS for attachment chips**

In `email.html`, inside the `<style>` block near the compose-modal CSS (after line 56), add:

```css
    .cmp-attach-bar{display:flex;flex-wrap:wrap;gap:8px;padding:8px 0 0;align-items:center}
    .cmp-attach-chip{display:inline-flex;align-items:center;gap:6px;background:#13132a;border:1px solid #22224a;border-radius:7px;padding:5px 9px;font-size:12px;color:#cdd}
    .cmp-attach-chip button{background:none;border:none;color:#e94560;cursor:pointer;font-size:14px;line-height:1;padding:0}
    .cmp-attach-add{background:#0d0d1e;border:1px dashed #33335a;border-radius:7px;padding:5px 10px;font-size:12px;color:#8ab;cursor:pointer}
```

- [ ] **Step 2: Add file input + chip bar to the compose modal**

In `email.html`, after the `modal-body-area` div (after line 522, before the closing `</div>` of `.modal-body` at 523), add:

```html
        <div class="cmp-attach-bar" id="cmpAttachBar">
          <label class="cmp-attach-add">📎 Attach<input type="file" id="cmpFileInput" multiple style="display:none"></label>
        </div>
```

- [ ] **Step 3: Add attachment JS state + handlers**

In `email.html`, replace the `openCompose`/`closeCompose` functions (lines 945-958) with:

```javascript
let _cmpAttachments = [];  // [{token, filename, size}]
function openCompose(pref){
  _composePrefill = pref || {};
  document.getElementById('cmpTo').value = pref?.to || '';
  document.getElementById('cmpCc').value = pref?.cc || '';
  document.getElementById('cmpBcc').value = pref?.bcc || '';
  document.getElementById('cmpSubject').value = pref?.subject || '';
  document.getElementById('cmpBody').value = pref?.body || '';
  document.getElementById('composeTitle').textContent = pref?.mode === 'forward' ? '→ Forward' : '✉️ New Message';
  _cmpAttachments = [];
  renderCmpAttachments();
  document.getElementById('composeModal').classList.add('open');
  setTimeout(()=>document.getElementById('cmpTo').focus(), 50);
}
function closeCompose(){
  document.getElementById('composeModal').classList.remove('open');
  _cmpAttachments = [];
  renderCmpAttachments();
}
function renderCmpAttachments(){
  const bar = document.getElementById('cmpAttachBar');
  bar.querySelectorAll('.cmp-attach-chip').forEach(n=>n.remove());
  _cmpAttachments.forEach((a,i)=>{
    const chip = document.createElement('span');
    chip.className = 'cmp-attach-chip';
    chip.innerHTML = `📎 ${esc(a.filename)} <span class="att-size">${fmtSize(a.size)}</span> <button title="Remove">×</button>`;
    chip.querySelector('button').onclick = ()=>{ _cmpAttachments.splice(i,1); renderCmpAttachments(); };
    bar.insertBefore(chip, bar.firstChild);
  });
}
document.getElementById('cmpFileInput').addEventListener('change', async e=>{
  for(const file of Array.from(e.target.files||[])){
    const fd = new FormData(); fd.append('file', file);
    try{
      const resp = await fetch('/api/email2/attachments/upload', {method:'POST', body: fd});
      const j = await resp.json();
      if(j.ok){ _cmpAttachments.push({token:j.token, filename:j.filename, size:j.size}); renderCmpAttachments(); }
      else { toast('Upload failed: '+(j.error||'?'), true); }
    }catch(err){ toast('Upload failed', true); }
  }
  e.target.value = '';
});
```

- [ ] **Step 4: Include attachments in the send payload**

In `sendCompose` (line 969), change the send body to include attachment refs:

```javascript
  const attachments = _cmpAttachments.map(a => ({type:'upload', token:a.token}));
  const d = await api('/api/email2/send',{method:'POST', body:{mode:'compose', to, cc, bcc, subject, body, attachments, account: composeAcct || undefined}});
```

- [ ] **Step 5: Verify manually**

```bash
sudo systemctl restart baza-dashboard.service
curl -s http://localhost:8888/email | grep -c "cmpFileInput"   # expect 1
```
Then in the browser: Compose → 📎 Attach a small file → chip appears → Send to yourself → confirm the email arrives with the attachment. (No git commit — auto-git.)

---

### Task 4: Inbound attachment preview modal (email.html)

**Files:**
- Modify: `dashboard/templates/email.html` — add a body-level preview modal near the compose modal (after line 535); make chips clickable in `renderAttachments` (782-794); add `openAttachmentPreview`/`closeAttachmentPreview` JS.

- [ ] **Step 1: Add preview modal CSS**

In `email.html` `<style>`, after the chip CSS from Task 3, add:

```css
    .att-prev-bg{position:fixed;inset:0;background:rgba(0,0,0,.78);display:none;align-items:center;justify-content:center;z-index:9000}
    .att-prev-bg.open{display:flex}
    .att-prev-card{background:#0b0b1c;border:1px solid #22224a;border-radius:12px;max-width:90vw;max-height:90vh;width:860px;display:flex;flex-direction:column;overflow:hidden}
    .att-prev-head{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid #1a1a3a;font-size:13px;color:#cdd}
    .att-prev-body{padding:14px;overflow:auto;background:#020208}
    .att-prev-body img{max-width:100%;max-height:70vh;display:block;margin:0 auto}
    .att-prev-body iframe{width:100%;height:70vh;border:none;background:#fff}
    .att-prev-body pre{font-family:'Courier New',monospace;color:#a0f0a0;white-space:pre-wrap;margin:0}
    .att-prev-dl{background:#e94560;color:#fff;border:none;border-radius:7px;padding:7px 12px;font-size:12px;cursor:pointer;text-decoration:none}
```

- [ ] **Step 2: Add the body-level preview modal markup**

In `email.html`, immediately after the compose modal's closing `</div>` (after line 535), add:

```html
<!-- Attachment preview modal — BODY LEVEL -->
<div class="att-prev-bg" id="attPrevBg" onclick="if(event.target===this)closeAttachmentPreview()">
  <div class="att-prev-card">
    <div class="att-prev-head">
      <span id="attPrevName">Attachment</span>
      <span style="display:flex;gap:10px;align-items:center">
        <a class="att-prev-dl" id="attPrevDl" href="#">⬇ Download</a>
        <button class="modal-close" onclick="closeAttachmentPreview()">×</button>
      </span>
    </div>
    <div class="att-prev-body" id="attPrevBody"></div>
  </div>
</div>
```

- [ ] **Step 3: Make chips clickable**

Replace `renderAttachments` (lines 782-794) with:

```javascript
function renderAttachments(m){
  const atts = m.attachments || [];
  if(!atts.length) return '';
  const acc = state.activeAccount ? '&account=' + encodeURIComponent(state.activeAccount.id) : '';
  return '<div class="msg-attachments">' + atts.map(a => {
    const base = '/api/email2/attachment/' + encodeURIComponent(m.gmail_id) + '/' +
                encodeURIComponent(a.attachment_id) +
                '?name=' + encodeURIComponent(a.filename) +
                '&mime=' + encodeURIComponent(a.mime || '') + acc;
    return `<a class="att-chip" href="#" title="${esc(a.filename)}"
              onclick="openAttachmentPreview('${encodeURIComponent(base)}','${esc(a.filename)}','${esc(a.mime||'')}');return false;">📎 ${esc(a.filename)}
            <span class="att-size">${fmtSize(a.size)}</span></a>`;
  }).join('') + '</div>';
}
```

- [ ] **Step 4: Add the preview open/close JS**

In `email.html`, after `renderAttachments`, add:

```javascript
async function openAttachmentPreview(encBase, name, mime){
  const base = decodeURIComponent(encBase);
  const inlineUrl = base + '&inline=1';
  const dlUrl = base;
  const body = document.getElementById('attPrevBody');
  document.getElementById('attPrevName').textContent = name;
  const dl = document.getElementById('attPrevDl'); dl.href = dlUrl;
  body.innerHTML = '<div style="color:#667;padding:30px;text-align:center">Loading…</div>';
  document.getElementById('attPrevBg').classList.add('open');
  const ext = (name.split('.').pop()||'').toLowerCase();
  const isImg = mime.startsWith('image/') || ['jpg','jpeg','png','gif','webp','bmp','svg'].includes(ext);
  const isPdf = mime === 'application/pdf' || ext === 'pdf';
  const isVid = mime.startsWith('video/') || ['mp4','webm','mov','m4v'].includes(ext);
  const isAud = mime.startsWith('audio/') || ['mp3','wav','ogg','flac','m4a'].includes(ext);
  const isTxt = mime.startsWith('text/') || ['txt','md','csv','log','json','yaml','yml','py','js','html','css'].includes(ext);
  if(isImg){ body.innerHTML = `<img src="${inlineUrl}" alt="${esc(name)}">`; }
  else if(isPdf){ body.innerHTML = `<iframe src="${inlineUrl}"></iframe>`; }
  else if(isVid){ body.innerHTML = `<video controls style="max-width:100%;max-height:70vh"><source src="${inlineUrl}"></video>`; }
  else if(isAud){ body.innerHTML = `<audio controls style="width:100%"><source src="${inlineUrl}"></audio>`; }
  else if(isTxt){
    try{ const t = await (await fetch(inlineUrl)).text(); body.innerHTML = `<pre>${esc(t.slice(0,500000))}</pre>`; }
    catch(e){ body.innerHTML = '<div style="color:#778;padding:30px;text-align:center">Cannot preview — use Download.</div>'; }
  }
  else { body.innerHTML = '<div style="color:#778;padding:30px;text-align:center">No preview for this file type. Use Download.</div>'; }
}
function closeAttachmentPreview(){ document.getElementById('attPrevBg').classList.remove('open'); document.getElementById('attPrevBody').innerHTML=''; }
```

- [ ] **Step 5: Verify manually**

```bash
sudo systemctl restart baza-dashboard.service
curl -s http://localhost:8888/email | grep -c "attPrevBg"   # expect 1
```
In the browser: open a thread that has an image/PDF attachment → click the chip → preview opens inline → Download button works. (No git commit.)

---

# PART B — Universal Share

### Task 5: `share_service.py` — roots + resolver (pure, TDD)

**Files:**
- Create: `dashboard/share_service.py`
- Test: `tests/test_share_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_service.py`:

```python
import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def ss(monkeypatch, tmp_path):
    cloud = tmp_path / "cloud"; art = tmp_path / "art"
    (cloud).mkdir(); (art).mkdir()
    (cloud / "doc.pdf").write_bytes(b"%PDF cloud")
    (art / "p1").mkdir(); (art / "p1" / "pinout.png").write_bytes(b"PNG art")
    (art / ".vault_meta").mkdir(); (art / ".vault_meta" / "secret.txt").write_bytes(b"no")
    sys.modules.pop("share_service", None)
    mod = importlib.import_module("share_service")
    monkeypatch.setattr(mod, "ROOTS", {"cloud": str(cloud), "artifact": str(art)})
    return mod


def test_resolve_source_cloud_and_artifact(ss):
    assert ss.resolve_source("cloud", "doc.pdf").endswith("/cloud/doc.pdf")
    assert ss.resolve_source("artifact", "p1/pinout.png").endswith("/art/p1/pinout.png")
    assert ss.resolve_source("datahub", "p1/pinout.png").endswith("/art/p1/pinout.png")


def test_resolve_source_rejects_traversal_private_missing(ss):
    assert ss.resolve_source("cloud", "../etc/passwd") is None
    assert ss.resolve_source("artifact", ".vault_meta/secret.txt") is None
    assert ss.resolve_source("artifact", "p1/nope.png") is None
    assert ss.resolve_source("bogus", "x") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `share_service.py` with roots + resolver**

Create `dashboard/share_service.py`:

```python
"""Universal Share service — Link / Email / Telegram for any non-private file.

Mounts /api/share. Generalizes the cloud_shares token table (cloud-only) to
multiple file roots via the `root` column. Email reuses email_studio's Gmail
send; Telegram reuses Phil's bot.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
import sqlite3
from typing import Optional

from flask import Blueprint, jsonify, request

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_DEFAULT = os.path.join(DASHBOARD_DIR, "baza_projects.db")
ARTIFACTS_DIR = os.path.join(DASHBOARD_DIR, "artifacts")
CLOUD_STORAGE = os.environ.get("BAZA_CLOUD_STORAGE", "/mnt/empirepool/cloud")
FAMILY_USER_ID = int(os.environ.get("BAZA_FAMILY_USER_ID", "1"))
_DENY_DIRS = (".private-inbound", ".vault_meta")
_MAX_ATTACH_BYTES = 25 * 1024 * 1024

# Logical file roots. resolve_source maps a UI "source" to one of these.
ROOTS = {
    "cloud": os.path.join(CLOUD_STORAGE, str(FAMILY_USER_ID)),
    "artifact": ARTIFACTS_DIR,
}
# UI source name -> root key
_SOURCE_ROOT = {"cloud": "cloud", "artifact": "artifact", "datahub": "artifact"}

share_bp = Blueprint("share", __name__)


def _db_path() -> str:
    return os.environ.get("BAZA_DASHBOARD_DB", DB_PATH_DEFAULT)


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=5)
    con.row_factory = sqlite3.Row
    return con


def resolve_share_path(root: str, rel: str) -> Optional[str]:
    """Resolve (root, rel) to an absolute path inside the root, or None if
    invalid (traversal, private dir, missing, or unknown root)."""
    base = ROOTS.get(root)
    if not base:
        return None
    base_real = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base_real, rel or ""))
    if not (full == base_real or full.startswith(base_real + os.sep)):
        return None
    if any(seg in _DENY_DIRS for seg in full.split(os.sep)):
        return None
    if not os.path.isfile(full):
        return None
    return full


def resolve_source(source: str, id: str) -> Optional[str]:
    """Map a UI source descriptor to an absolute path (guarded)."""
    root = _SOURCE_ROOT.get(source)
    if not root:
        return None
    return resolve_share_path(root, id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (skip — auto-git)

```bash
git add dashboard/share_service.py tests/test_share_service.py
git commit -m "feat(share): roots registry + guarded source resolver"
```

---

### Task 6: `share_service` link channel + schema (TDD)

**Files:**
- Modify: `dashboard/share_service.py` — add `_ensure_share_schema`, `_public_base_url`, `create_link`.
- Test: `tests/test_share_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_share_service.py`:

```python
@pytest.fixture
def ss_db(ss, monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(db))
    ss._ensure_share_schema()
    return ss


def test_create_link_inserts_row(ss_db, monkeypatch):
    monkeypatch.setenv("BAZA_PUBLIC_URL", "https://share.example")
    out = ss_db.create_link("artifact", "p1/pinout.png", days=7)
    assert out["url"] == "https://share.example/s/" + out["token"]
    con = ss_db._conn()
    row = con.execute("SELECT root, path FROM cloud_shares WHERE token=?", (out["token"],)).fetchone()
    con.close()
    assert row["root"] == "artifact" and row["path"] == "p1/pinout.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_share_service.py::test_create_link_inserts_row -v`
Expected: FAIL — `_ensure_share_schema` / `create_link` not defined.

- [ ] **Step 3: Implement schema + link**

Append to `dashboard/share_service.py`:

```python
def _ensure_share_schema():
    """Create cloud_shares if missing and ensure the `root` column exists."""
    con = _conn()
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cloud_shares (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT '1',
                path TEXT NOT NULL,
                expires_at TEXT,
                created_by TEXT DEFAULT 'serge',
                created_at TEXT DEFAULT (datetime('now')),
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                root TEXT DEFAULT 'cloud'
            )
        """)
        cols = [r[1] for r in con.execute("PRAGMA table_info(cloud_shares)").fetchall()]
        if "root" not in cols:
            con.execute("ALTER TABLE cloud_shares ADD COLUMN root TEXT DEFAULT 'cloud'")
        con.commit()
    finally:
        con.close()


def _public_base_url() -> str:
    env = os.environ.get("BAZA_PUBLIC_URL", "").rstrip("/")
    if env:
        return env
    try:
        return request.host_url.rstrip("/")
    except Exception:
        return ""


def create_link(source_or_root: str, rel: str, days: int = 7) -> dict:
    """Mint a share token for (root, rel). Accepts a UI source or a root key."""
    root = _SOURCE_ROOT.get(source_or_root, source_or_root)
    if root not in ROOTS:
        raise ValueError("unknown source")
    token = secrets.token_urlsafe(18)
    expires_at = ((_dt.datetime.utcnow() + _dt.timedelta(days=days)).isoformat()
                  if days and days > 0 else None)
    _ensure_share_schema()
    con = _conn()
    try:
        con.execute(
            "INSERT INTO cloud_shares (token, user_id, path, expires_at, created_by, root) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, str(FAMILY_USER_ID), rel, expires_at, "serge", root),
        )
        con.commit()
    finally:
        con.close()
    return {"token": token, "url": f"{_public_base_url()}/s/{token}", "expires_at": expires_at}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (skip — auto-git)

```bash
git add dashboard/share_service.py tests/test_share_service.py
git commit -m "feat(share): link channel + cloud_shares.root schema"
```

---

### Task 7: `cloud_shares.root` migration + `public_share` generalization (app.py)

**Files:**
- Modify: `dashboard/app.py` — `init_cloud_tables` (~line 570-583); `public_share` (~14276-14309).

`app.py` is too heavy to import in the unit suite, so this task is verified manually (the resolver itself is covered by Task 5). Keep the change minimal and delegate to `share_service.resolve_share_path`.

- [ ] **Step 1: Add the `root` column migration**

In `dashboard/app.py`, inside `init_cloud_tables`, after the `CREATE TABLE ... cloud_shares (...)` statement and before `conn.commit()` (line ~581), add:

```python
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(cloud_shares)").fetchall()]
            if "root" not in cols:
                conn.execute("ALTER TABLE cloud_shares ADD COLUMN root TEXT DEFAULT 'cloud'")
        except Exception:
            pass
```

- [ ] **Step 2: Generalize `public_share` to resolve per-root**

In `dashboard/app.py`, replace the body of `public_share` from the `row = conn.execute(...)` SELECT through the `send_from_directory(...)` return (lines ~14281-14309) with:

```python
        row = conn.execute(
            "SELECT user_id, path, expires_at, root FROM cloud_shares WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            conn.close()
            return "Share link not found or revoked", 404
        if row['expires_at']:
            try:
                if _dt.datetime.fromisoformat(row['expires_at']) < _dt.datetime.utcnow():
                    conn.close()
                    return "Share link expired", 410
            except Exception:
                pass
        root = (row['root'] if 'root' in row.keys() else None) or 'cloud'
        try:
            from share_service import resolve_share_path
        except ImportError:
            from dashboard.share_service import resolve_share_path
        target = resolve_share_path(root, row['path'])
        if not target:
            conn.close()
            return "File no longer available", 404
        conn.execute(
            "UPDATE cloud_shares SET access_count = access_count + 1, "
            "last_accessed_at = datetime('now') WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        inline = request.args.get('inline') in ('1', 'true', 'yes')
        return send_from_directory(os.path.dirname(target),
                                   os.path.basename(target),
                                   as_attachment=not inline)
```

Note: `share_service.ROOTS['cloud']` is `CLOUD_STORAGE/<FAMILY_USER_ID>`, which matches the existing cloud `user_dir`, so legacy cloud tokens (root NULL→'cloud') keep resolving identically.

- [ ] **Step 3: Verify manually** (after Task 10 registers `share_bp`)

```bash
sudo systemctl restart baza-dashboard.service
# create an artifact-root link and fetch it (replace <pid>/<file> with a real artifact)
TOK=$(curl -s -X POST http://localhost:8888/api/share -H 'Content-Type: application/json' \
  -d '{"source":"artifact","id":"<pid>/<file>","channel":"link"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['url'].split('/s/')[1])")
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8888/s/$TOK"   # expect 200
```

- [ ] **Step 4: Commit** (skip — auto-git)

---

### Task 8: `share_service` email channel (TDD)

**Files:**
- Modify: `dashboard/share_service.py` — add `share_email`.
- Test: `tests/test_share_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_service.py`:

```python
import base64 as _b64
import email as _emaillib


def _sent_raw(captured):
    return _b64.urlsafe_b64decode(captured["raw"]).decode("utf-8", "replace")


def test_share_email_attaches_small_file(ss_db, monkeypatch):
    captured = {}
    import email_studio as es
    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def send(self, userId, body): captured["raw"] = body["raw"]; return self
        def execute(self): return {"id": "x"}
    monkeypatch.setattr(es, "_gmail", lambda a=None: FakeSvc())
    monkeypatch.setattr(es, "_active_account", lambda: {"id": "a", "email": "me@x.com"})
    out = ss_db.share_email("artifact", "p1/pinout.png", "to@x.com", "Subj", "note")
    assert out["ok"] is True
    raw = _sent_raw(captured)
    assert "pinout.png" in raw


def test_share_email_big_file_falls_back_to_link(ss_db, monkeypatch, tmp_path):
    # Make the artifact look huge without writing 25MB: patch getsize.
    captured = {}
    import email_studio as es
    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def send(self, userId, body): captured["raw"] = body["raw"]; return self
        def execute(self): return {"id": "x"}
    monkeypatch.setattr(es, "_gmail", lambda a=None: FakeSvc())
    monkeypatch.setattr(es, "_active_account", lambda: {"id": "a", "email": "me@x.com"})
    monkeypatch.setattr(ss_db.os.path, "getsize", lambda p: 30 * 1024 * 1024)
    monkeypatch.setenv("BAZA_PUBLIC_URL", "https://share.example")
    out = ss_db.share_email("artifact", "p1/pinout.png", "to@x.com", "Subj", "note")
    assert out["ok"] is True and out.get("via") == "link"
    raw = _sent_raw(captured)
    assert "https://share.example/s/" in raw
    assert "pinout.png" not in [p.get_filename() for p in _emaillib.message_from_string(raw).walk()]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v -k share_email`
Expected: FAIL — `share_email` not defined.

- [ ] **Step 3: Implement `share_email`**

Append to `dashboard/share_service.py`:

```python
def share_email(source: str, id: str, to: str, subject: str = "", note: str = "") -> dict:
    """Email a shared file. Attaches the file if <= 25 MB, else sends a link."""
    try:
        import email_studio as es
    except ImportError:
        from dashboard import email_studio as es  # type: ignore
    abs_path = resolve_source(source, id)
    if not abs_path:
        return {"ok": False, "error": "file not found or not shareable"}
    if not (to or "").strip():
        return {"ok": False, "error": "missing 'to' address"}
    fname = os.path.basename(abs_path)
    subject = (subject or f"Shared: {fname}").strip()
    note = note or ""
    acc = es._active_account()
    from_addr = acc["email"] if acc else ""
    size = os.path.getsize(abs_path)
    via = "attachment"
    if size > _MAX_ATTACH_BYTES:
        via = "link"
        link = create_link(source, id, days=7)
        body = (note + "\n\n" if note else "") + \
               f"{fname} is too large to attach ({size // (1024*1024)} MB). " \
               f"Download it here:\n{link['url']}\n"
        raw = es._mime_message(to, subject, body, from_addr=from_addr)
    else:
        import mimetypes
        with open(abs_path, "rb") as f:
            data = f.read()
        mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body = (note + "\n\n" if note else "") + f"Sharing {fname} (attached)."
        raw = es._mime_message(to, subject, body, from_addr=from_addr,
                               attachments=[{"filename": fname, "mimetype": mime, "data": data}])
    svc = es._gmail(acc["id"] if acc else None)
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"ok": True, "via": via, "filename": fname}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (skip — auto-git)

```bash
git add dashboard/share_service.py tests/test_share_service.py
git commit -m "feat(share): email channel with >25MB link fallback"
```

---

### Task 9: `share_service` Telegram channel (TDD)

**Files:**
- Modify: `dashboard/share_service.py` — add `share_telegram`.
- Test: `tests/test_share_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_service.py`:

```python
def test_share_telegram_picks_photo_for_image(ss_db, monkeypatch):
    calls = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"ok": True}
    def fake_post(url, data=None, files=None, timeout=None):
        calls["url"] = url; calls["chat_id"] = data.get("chat_id"); return FakeResp()
    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_PHIL_HASS", "BOTTOKEN")
    out = ss_db.share_telegram("artifact", "p1/pinout.png", chat_id="999")
    assert out["ok"] is True
    assert calls["url"].endswith("/sendPhoto")
    assert calls["chat_id"] == "999"


def test_share_telegram_no_token_errors(ss_db, monkeypatch):
    monkeypatch.delenv("TELEGRAM_PHIL_HASS", raising=False)
    monkeypatch.delenv("CLOUD_TELEGRAM_BOT", raising=False)
    out = ss_db.share_telegram("artifact", "p1/pinout.png", chat_id="999")
    assert out["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v -k telegram`
Expected: FAIL — `share_telegram` not defined.

- [ ] **Step 3: Implement `share_telegram`**

Append to `dashboard/share_service.py`:

```python
def share_telegram(source: str, id: str, chat_id: str = "", caption: str = "") -> dict:
    """Send a shared file to Telegram via Phil's bot (file-type aware)."""
    import requests
    abs_path = resolve_source(source, id)
    if not abs_path:
        return {"ok": False, "error": "file not found or not shareable"}
    token = os.environ.get("CLOUD_TELEGRAM_BOT") or os.environ.get("TELEGRAM_PHIL_HASS")
    if not token:
        return {"ok": False, "error": "No Telegram bot token (set TELEGRAM_PHIL_HASS)"}
    chat_id = str(chat_id or os.environ.get("SERGE_CHAT_ID") or "").strip()
    if not chat_id:
        return {"ok": False, "error": "No chat_id and SERGE_CHAT_ID not set"}
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        method, field = "sendPhoto", "photo"
    elif ext in (".mp4", ".mov", ".m4v", ".webm"):
        method, field = "sendVideo", "video"
    elif ext in (".mp3", ".m4a", ".wav", ".ogg"):
        method, field = "sendAudio", "audio"
    else:
        method, field = "sendDocument", "document"
    try:
        with open(abs_path, "rb") as fh:
            files = {field: (os.path.basename(abs_path), fh)}
            payload = {"chat_id": chat_id}
            if caption:
                payload["caption"] = caption[:1024]
            resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                                 data=payload, files=files, timeout=120)
        try:
            result = resp.json()
        except Exception:
            result = {}
        if resp.status_code == 200 and result.get("ok"):
            return {"ok": True, "method": method, "chat_id": chat_id,
                    "filename": os.path.basename(abs_path)}
        return {"ok": False, "error": result.get("description") or "telegram send failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (skip — auto-git)

```bash
git add dashboard/share_service.py tests/test_share_service.py
git commit -m "feat(share): telegram channel via Phil bot"
```

---

### Task 10: `/api/share` dispatch blueprint + register in app.py (TDD)

**Files:**
- Modify: `dashboard/share_service.py` — add the `/api/share` route on `share_bp`.
- Modify: `dashboard/app.py` — register `share_bp` (near email blueprint, ~line 15929).
- Test: `tests/test_share_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_service.py`:

```python
def _share_client(ss):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(ss.share_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_dispatch_link(ss_db, monkeypatch):
    monkeypatch.setattr(ss_db, "create_link", lambda s, i, days=7: {"token": "t", "url": "u", "expires_at": None})
    c = _share_client(ss_db)
    r = c.post("/api/share", json={"source": "artifact", "id": "p1/pinout.png", "channel": "link"})
    assert r.status_code == 200 and r.get_json()["url"] == "u"


def test_dispatch_bad_channel(ss_db):
    c = _share_client(ss_db)
    r = c.post("/api/share", json={"source": "artifact", "id": "p1/pinout.png", "channel": "carrier-pigeon"})
    assert r.status_code == 400


def test_dispatch_private_file_403(ss_db):
    c = _share_client(ss_db)
    r = c.post("/api/share", json={"source": "artifact", "id": ".vault_meta/secret.txt", "channel": "link"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v -k dispatch`
Expected: FAIL — `/api/share` route not defined.

- [ ] **Step 3: Implement the dispatch route**

Append to `dashboard/share_service.py`:

```python
@share_bp.route("/api/share", methods=["POST"])
def api_share():
    """Dispatch a share. Body: {source, id, channel, ...channel_args}.
      channel=link:     {expires_days?}             -> {ok, token, url, expires_at}
      channel=email:    {to, subject?, note?}       -> {ok, via, filename}
      channel=telegram: {chat_id?, caption?}        -> {ok, method, ...}
    """
    data = request.get_json(silent=True) or {}
    source = data.get("source", "")
    id = data.get("id", "")
    channel = data.get("channel", "")
    if resolve_source(source, id) is None:
        return jsonify({"ok": False, "error": "file not found or not shareable"}), 403
    if channel == "link":
        try:
            out = create_link(source, id, days=int(data.get("expires_days", 7)))
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, **out})
    if channel == "email":
        out = share_email(source, id, data.get("to", ""), data.get("subject", ""), data.get("note", ""))
        return (jsonify(out), 200) if out.get("ok") else (jsonify(out), 400)
    if channel == "telegram":
        out = share_telegram(source, id, str(data.get("chat_id", "")), data.get("caption", ""))
        return (jsonify(out), 200) if out.get("ok") else (jsonify(out), 400)
    return jsonify({"ok": False, "error": f"unknown channel: {channel}"}), 400
```

- [ ] **Step 4: Register the blueprint in app.py**

In `dashboard/app.py`, after `app.register_blueprint(_email_bp)` (line 15929) add:

```python
try:
    from dashboard.share_service import share_bp as _share_bp, _ensure_share_schema as _ensure_share
except ImportError:
    from share_service import share_bp as _share_bp, _ensure_share_schema as _ensure_share
_ensure_share()
app.register_blueprint(_share_bp)
```

- [ ] **Step 5: Run tests + verify import**

Run: `venv/bin/python -m pytest tests/test_share_service.py -v`
Expected: PASS

```bash
sudo systemctl restart baza-dashboard.service
journalctl --user -u baza-dashboard.service -n 20 --no-pager 2>/dev/null || sudo journalctl -u baza-dashboard.service -n 20 --no-pager
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8888/api/share \
  -H 'Content-Type: application/json' -d '{"source":"artifact","id":"nope/none","channel":"link"}'  # expect 403
```

- [ ] **Step 6: Commit** (skip — auto-git)

```bash
git add dashboard/share_service.py dashboard/app.py tests/test_share_service.py
git commit -m "feat(share): /api/share dispatch + register blueprint"
```

---

### Task 11: Share sheet partial `_share_sheet.html`

**Files:**
- Create: `dashboard/templates/_share_sheet.html`

Frontend — verified manually. This partial is self-contained (scoped CSS + JS) so it can be `{% include %}`-ed by any tab template at body level.

- [ ] **Step 1: Create the partial**

Create `dashboard/templates/_share_sheet.html`:

```html
<!-- Universal Share sheet — BODY LEVEL. Include once per tab template. -->
<style>
  .shr-bg{position:fixed;inset:0;background:rgba(0,0,0,.78);display:none;align-items:center;justify-content:center;z-index:9500}
  .shr-bg.open{display:flex}
  .shr-card{background:#0b0b1c;border:1px solid #22224a;border-radius:12px;width:440px;max-width:92vw;max-height:90vh;display:flex;flex-direction:column;overflow:hidden;color:#dde}
  .shr-head{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;border-bottom:1px solid #1a1a3a;font-size:14px;font-weight:700}
  .shr-head button{background:none;border:none;color:#889;font-size:20px;cursor:pointer}
  .shr-file{padding:8px 16px;font-size:12px;color:#8ab;border-bottom:1px solid #14142e}
  .shr-tabs{display:flex;gap:6px;padding:10px 16px 0}
  .shr-tab{flex:1;padding:8px;text-align:center;background:#13132a;border:1px solid #22224a;border-radius:8px 8px 0 0;cursor:pointer;font-size:12.5px}
  .shr-tab.active{background:#1c1c3a;border-bottom-color:#1c1c3a;color:#fff}
  .shr-pane{display:none;padding:14px 16px}
  .shr-pane.active{display:block}
  .shr-pane input,.shr-pane textarea{width:100%;background:#070718;border:1px solid #22224a;border-radius:7px;padding:9px;color:#e0e0e0;font-size:13px;margin-bottom:9px;font-family:inherit}
  .shr-pane textarea{min-height:70px;resize:vertical}
  .shr-pane label{font-size:11px;color:#778;display:block;margin-bottom:3px}
  .shr-go{background:linear-gradient(135deg,#e94560,#c33049);color:#fff;border:none;padding:9px 14px;border-radius:8px;font-weight:700;cursor:pointer;font-size:13px;width:100%}
  .shr-result{margin-top:10px;font-size:12px;color:#9d9;word-break:break-all}
  .shr-linkrow{display:flex;gap:6px;margin-top:8px}
  .shr-linkrow input{margin:0}
  .shr-copy{background:#1c1c3a;border:1px solid #33335a;color:#cdd;border-radius:7px;padding:0 12px;cursor:pointer;font-size:12px}
</style>
<div class="shr-bg" id="shrBg" onclick="if(event.target===this)closeShareSheet()">
  <div class="shr-card">
    <div class="shr-head"><span>🔗 Share</span><button onclick="closeShareSheet()">×</button></div>
    <div class="shr-file" id="shrFile">file</div>
    <div class="shr-tabs">
      <div class="shr-tab active" data-pane="link" onclick="shrTab('link')">Link</div>
      <div class="shr-tab" data-pane="email" onclick="shrTab('email')">Email</div>
      <div class="shr-tab" data-pane="telegram" onclick="shrTab('telegram')">Telegram</div>
    </div>
    <div class="shr-pane active" id="shrPaneLink">
      <label>Expires (days, 0 = never)</label>
      <input type="number" id="shrExpiry" value="7" min="0">
      <button class="shr-go" onclick="shrCreateLink()">Create link</button>
      <div class="shr-linkrow" id="shrLinkRow" style="display:none">
        <input type="text" id="shrLinkUrl" readonly>
        <button class="shr-copy" onclick="shrCopyLink()">Copy</button>
      </div>
      <div class="shr-result" id="shrLinkMsg"></div>
    </div>
    <div class="shr-pane" id="shrPaneEmail">
      <label>To</label><input type="text" id="shrEmailTo" placeholder="recipient@example.com">
      <label>Subject</label><input type="text" id="shrEmailSubj">
      <label>Note (optional)</label><textarea id="shrEmailNote"></textarea>
      <button class="shr-go" onclick="shrSendEmail()">Send email</button>
      <div class="shr-result" id="shrEmailMsg"></div>
    </div>
    <div class="shr-pane" id="shrPaneTelegram">
      <label>Chat ID (blank = me)</label><input type="text" id="shrTgChat" placeholder="default: Serge">
      <label>Caption (optional)</label><input type="text" id="shrTgCaption">
      <button class="shr-go" onclick="shrSendTelegram()">Send to Telegram</button>
      <div class="shr-result" id="shrTgMsg"></div>
    </div>
  </div>
</div>
<script>
let _shrCtx = {source:'', id:'', filename:''};
function openShareSheet(ctx){
  _shrCtx = ctx || {};
  document.getElementById('shrFile').textContent = _shrCtx.filename || _shrCtx.id || '';
  document.getElementById('shrEmailSubj').value = 'Shared: ' + (_shrCtx.filename || _shrCtx.id || '');
  ['shrLinkMsg','shrEmailMsg','shrTgMsg'].forEach(i=>document.getElementById(i).textContent='');
  document.getElementById('shrLinkRow').style.display='none';
  shrTab('link');
  document.getElementById('shrBg').classList.add('open');
}
function closeShareSheet(){ document.getElementById('shrBg').classList.remove('open'); }
function shrTab(name){
  document.querySelectorAll('.shr-tab').forEach(t=>t.classList.toggle('active', t.dataset.pane===name));
  document.querySelectorAll('.shr-pane').forEach(p=>p.classList.remove('active'));
  document.getElementById('shrPane'+name.charAt(0).toUpperCase()+name.slice(1)).classList.add('active');
}
async function _shrPost(channel, extra){
  const body = Object.assign({source:_shrCtx.source, id:_shrCtx.id, channel}, extra||{});
  const r = await fetch('/api/share', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  return await r.json();
}
async function shrCreateLink(){
  const days = parseInt(document.getElementById('shrExpiry').value||'7',10);
  const msg = document.getElementById('shrLinkMsg'); msg.textContent='Creating…';
  const j = await _shrPost('link', {expires_days: days});
  if(j.ok){ document.getElementById('shrLinkUrl').value=j.url; document.getElementById('shrLinkRow').style.display='flex'; msg.textContent = j.expires_at?('Expires '+j.expires_at.slice(0,10)):'Never expires'; }
  else { msg.textContent = '⚠ '+(j.error||'failed'); }
}
function shrCopyLink(){ const i=document.getElementById('shrLinkUrl'); i.select(); navigator.clipboard.writeText(i.value); document.getElementById('shrLinkMsg').textContent='Copied ✓'; }
async function shrSendEmail(){
  const to = document.getElementById('shrEmailTo').value.trim();
  const msg = document.getElementById('shrEmailMsg');
  if(!to){ msg.textContent='⚠ enter a recipient'; return; }
  msg.textContent='Sending…';
  const j = await _shrPost('email', {to, subject:document.getElementById('shrEmailSubj').value, note:document.getElementById('shrEmailNote').value});
  msg.textContent = j.ok ? ('✓ Sent'+(j.via==='link'?' (as link — file >25MB)':' with attachment')) : ('⚠ '+(j.error||'failed'));
}
async function shrSendTelegram(){
  const msg = document.getElementById('shrTgMsg'); msg.textContent='Sending…';
  const j = await _shrPost('telegram', {chat_id:document.getElementById('shrTgChat').value.trim(), caption:document.getElementById('shrTgCaption').value});
  msg.textContent = j.ok ? '✓ Sent to Telegram' : ('⚠ '+(j.error||'failed'));
}
</script>
```

- [ ] **Step 2: Commit** (skip — auto-git). No restart needed until a tab includes it (Task 12+).

---

### Task 12: Wire Data Hub (datahub.html)

**Files:**
- Modify: `dashboard/templates/datahub.html` — add `{% include '_share_sheet.html' %}` at body level; add a Share button per file + in select mode that calls `openShareSheet`.

- [ ] **Step 1: Include the partial at body level**

In `dashboard/templates/datahub.html`, just before the closing `</body>` tag, add:

```html
{% include '_share_sheet.html' %}
```

- [ ] **Step 2: Add the Share action to the file UI**

Find the existing per-file action handler in `datahub.html` (the share/select logic around lines 917-990 that currently calls `navigator.share()`). Add a Share button/menu item that calls `openShareSheet` with the file's existing project_id/relative path. Use the SAME path field the file's existing serve/download link uses (search the file's row object for the property already passed to `/api/artifacts/serve/<project_id>/<path>` or `/api/artifacts/download/...`). Concretely, where a file object `f` exposes `f.project_id` and `f.path` (or equivalent), add:

```javascript
function dhShareFile(f){
  openShareSheet({source:'datahub', id: (f.project_id || f.proj) + '/' + (f.path || f.name), filename: f.name || (f.path||'').split('/').pop()});
}
```

and bind a "🔗 Share" button to `dhShareFile(f)` in the file card/menu and in the multi-select bar (for single selection). Replace the bare `navigator.share()` call with `dhShareFile(...)`.

- [ ] **Step 3: Verify manually**

```bash
sudo systemctl restart baza-dashboard.service
curl -s http://localhost:8888/datahub | grep -c "openShareSheet"   # expect >=1
```
In the browser: Data Hub → a non-private file → 🔗 Share → Link/Email/Telegram each work. Confirm a `.vault_meta`/private file is NOT offered (or returns the 403 error in the sheet). (No git commit.)

---

### Task 13: Wire Projects (projects.html)

**Files:**
- Modify: `dashboard/templates/projects.html` — include partial at body level; add Share button on project files/artifacts.

- [ ] **Step 1: Include the partial**

In `dashboard/templates/projects.html`, before `</body>`, add:

```html
{% include '_share_sheet.html' %}
```

- [ ] **Step 2: Add the Share button to project files**

In the project file list rendering (where each artifact links to `/api/artifacts/serve/<project_id>/<filename>` or `/download/...`), add a "🔗 Share" button calling:

```javascript
function projShareFile(projectId, filename){
  openShareSheet({source:'artifact', id: projectId + '/' + filename, filename: filename.split('/').pop()});
}
```

Bind it next to each file's existing download/view control, passing the project id and the file's relative path.

- [ ] **Step 3: Verify manually**

```bash
sudo systemctl restart baza-dashboard.service
curl -s http://localhost:8888/projects | grep -c "openShareSheet"   # expect >=1
```
Browser: open a project with files → 🔗 Share → create a link, hit it (200), and email yourself a small file. (No git commit.)

---

### Task 14: Wire Cloud (cloud.html) to the unified sheet

**Files:**
- Modify: `dashboard/templates/cloud.html` — include partial at body level; point the existing Share action at `openShareSheet({source:'cloud', ...})` so Cloud gains the Email channel. Keep Cloud's existing endpoints intact (they still back link/telegram via the unified service, which uses the same `cloud_shares` table and Phil bot).

- [ ] **Step 1: Include the partial**

In `dashboard/templates/cloud.html`, before `</body>`, add:

```html
{% include '_share_sheet.html' %}
```

- [ ] **Step 2: Route the Cloud share button to the unified sheet**

In `cloud.html`, the existing `shareFile(path)` (line ~1366) creates a link via `/api/cloud/files/share` and copies it. Replace its body to open the unified sheet instead (Cloud paths are relative to the cloud user dir, which is `share_service` root `cloud`):

```javascript
function shareFile(path){
  openShareSheet({source:'cloud', id: path, filename: (path||'').split('/').pop()});
}
```

Leave `telegramFile(path)` and the bulk handlers as-is (existing endpoints keep working); optionally also route `telegramFile` through the sheet later. Do not remove `/api/cloud/files/*` endpoints — other code and the bulk/memory share paths still use them.

- [ ] **Step 3: Verify manually**

```bash
sudo systemctl restart baza-dashboard.service
curl -s http://localhost:8888/cloud | grep -c "openShareSheet"   # expect >=1
```
Browser: Cloud → a file → Share → confirm Link still works (same `/s/<token>`), Email now available, Telegram works. Verify a previously-created legacy cloud share link still resolves (root defaults to 'cloud'). (No git commit.)

---

### Task 15: Full regression + session log

- [ ] **Step 1: Run the whole suite**

Run: `venv/bin/python -m pytest tests/test_email_upload_preview.py tests/test_share_service.py tests/test_email_attachments.py -v`
Expected: ALL PASS.

- [ ] **Step 2: Restart + smoke-check every touched tab**

```bash
sudo systemctl restart baza-dashboard.service
for tab in email datahub projects cloud; do
  echo -n "$tab: "; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8888/$tab
done
```
Expected: all 200.

- [ ] **Step 3: Log to the session continuity log**

Append a `### YYYY-MM-DD HH:MM | ...` entry (timestamp via `date '+%Y-%m-%d %H:%M'`) to `~/Desktop/baza-session-log.md` summarizing the feature, files touched, and test results. Do NOT manually git commit (auto-git).

---

## Self-Review

**Spec coverage:**
- A1 outbound upload → Tasks 2, 3 ✓
- A2 inbound preview → Tasks 1, 4 ✓
- B1 `share_service` resolver + channels + `/api/share` → Tasks 5, 6, 8, 9, 10 ✓
- B1 `/s/<token>` generalization → Task 7 ✓
- B2 share sheet (Link/Email/Telegram) → Task 11 ✓
- B3 wiring Data Hub / Projects / Cloud → Tasks 12, 13, 14 ✓
- Privacy rule (reject `.private-inbound`/`.vault_meta`) → Task 5 tests + resolver ✓
- 25 MB email link-fallback → Task 8 ✓

**Placeholder scan:** Frontend wiring in Tasks 12–14 references "the file's existing project_id/path field" because the exact JS property name must be read from each template at implementation time; the resolver contract (`source` + `<project_id>/<relpath>`) and the helper function bodies are fully specified, so this is a bounded lookup, not an unspecified blank. All backend steps contain complete code.

**Type consistency:** `resolve_source(source, id)`, `resolve_share_path(root, rel)`, `create_link(source_or_root, rel, days)`, `share_email(source, id, to, subject, note)`, `share_telegram(source, id, chat_id, caption)`, and the `/api/share` body `{source, id, channel, ...}` are used identically across Tasks 5–14. `ROOTS` keys (`cloud`, `artifact`) and `_SOURCE_ROOT` (`cloud`/`artifact`/`datahub`) are consistent. Email send refs use `{type:'upload', token}` in both `email.html` (Task 3) and `_resolve_attachments` (Task 2).
