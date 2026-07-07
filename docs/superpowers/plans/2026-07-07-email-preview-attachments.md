# Email Studio Full Preview + Attachments Everywhere — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render full (sanitized HTML) emails, fix the 700–1100px navigation trap, surface attachments everywhere (list pane, cross-mailbox browse view, nested/inline parts, agent files), and give every attachment a unified action bar: preview / download / save (project · cloud · Desktop) / share (Telegram · link · email) / forward.

**Architecture:** All backend work lands in `dashboard/email_studio.py` (existing blueprint, `/api/email2/*`), reusing `share_service.py` for share channels and the existing outbox-staging token system for forwarding. All frontend work lands in `dashboard/templates/email.html` (single-file template, vanilla JS). Attachment metadata is cached in new `emails` table columns so browsing never hits the Gmail API.

**Tech Stack:** Flask blueprint, sqlite3 (baza_projects.db), Gmail API, stdlib-only HTML sanitization (regex), vanilla JS + sandboxed iframe. Tests: pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-email-preview-attachments-design.md`

## Global Constraints

- **No new pip dependencies** (stdlib sanitizer, no bleach).
- **25 MB cap** on any materialized/staged attachment (`_MAX_ATTACH_BYTES`).
- **`.private-inbound` and `.vault_meta` are never listed or shareable** (`_DENY_ARTIFACT_DIRS` in email_studio, `_DENY_DIRS` in share_service).
- **Local-first**: no cloud API calls beyond the existing Gmail integration.
- **Do NOT `git commit` manually** — `claw-auto-git.timer` commits this repo hourly.
- **Template edits are invisible until `sudo systemctl restart baza-dashboard`** (Jinja cache, debug=False). Restart once at the end (Task 9), not per-task.
- Run tests with `venv/bin/python -m pytest` from `/home/switchhacker/baza-empire/agent-framework-v3`.
- All `email_studio.py` line numbers below refer to the file BEFORE this plan's edits; anchor by the quoted code, not the number, as earlier tasks shift lines.

---

### Task 1: Attachment collector — nested rfc822 + inline/content-id parts

**Files:**
- Modify: `dashboard/email_studio.py:461-479` (`_collect_attachments`)
- Test: `tests/test_email_attachments2.py` (new file)

**Interfaces:**
- Produces: `_collect_attachments(payload) -> list[dict]` where each dict is `{"filename": str, "mime": str, "size": int, "attachment_id": str, "content_id": str, "inline": bool}`. Tasks 2, 3, 5 rely on `content_id`/`inline` keys existing on every entry.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_attachments2.py`:

```python
import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def _part(filename="", mime="", att_id=None, headers=None, parts=None, size=10):
    p = {"filename": filename, "mimeType": mime,
         "body": ({"attachmentId": att_id, "size": size} if att_id else {"size": size})}
    if headers:
        p["headers"] = [{"name": k, "value": v} for k, v in headers.items()]
    if parts:
        p["parts"] = parts
    return p


def test_collects_nested_rfc822_attachments(es):
    inner_pdf = _part("permit.pdf", "application/pdf", att_id="AID_inner")
    rfc822 = _part("fwd.eml", "message/rfc822", att_id="AID_eml",
                   parts=[_part(mime="multipart/mixed", parts=[
                       _part(mime="text/plain"), inner_pdf])])
    root = _part(mime="multipart/mixed", parts=[_part(mime="text/plain"), rfc822])
    atts = es._collect_attachments(root)
    names = [a["filename"] for a in atts]
    assert "permit.pdf" in names           # nested inside the forwarded email
    assert "fwd.eml" in names              # the forwarded email itself


def test_inline_cid_part_flagged_inline(es):
    img = _part("logo.png", "image/png", att_id="AID_img",
                headers={"Content-ID": "<logo123>", "Content-Disposition": "inline"})
    root = _part(mime="multipart/related", parts=[_part(mime="text/html"), img])
    atts = es._collect_attachments(root)
    assert len(atts) == 1
    assert atts[0]["content_id"] == "logo123"
    assert atts[0]["inline"] is True


def test_regular_attachment_not_inline_and_has_keys(es):
    pdf = _part("quote.pdf", "application/pdf", att_id="AID1",
                headers={"Content-Disposition": 'attachment; filename="quote.pdf"'})
    atts = es._collect_attachments(_part(mime="multipart/mixed", parts=[pdf]))
    assert atts[0]["inline"] is False
    assert atts[0]["content_id"] == ""
    assert set(atts[0]) >= {"filename", "mime", "size", "attachment_id", "content_id", "inline"}


def test_cid_part_without_filename_still_collected(es):
    img = {"filename": "", "mimeType": "image/jpeg",
           "body": {"attachmentId": "AIDX", "size": 5},
           "headers": [{"name": "Content-ID", "value": "<photo1>"}]}
    atts = es._collect_attachments({"mimeType": "multipart/related", "parts": [img]})
    assert len(atts) == 1
    assert atts[0]["inline"] is True
    assert atts[0]["filename"]  # synthesized, non-empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_email_attachments2.py -v`
Expected: FAIL — `KeyError: 'content_id'` / missing nested names (current code requires `fn and attachmentId` and returns no `content_id`/`inline` keys).

- [ ] **Step 3: Replace `_collect_attachments` implementation**

Replace the whole function (currently email_studio.py:461-479) with:

```python
def _collect_attachments(payload: dict) -> list[dict]:
    """Walk a payload tree collecting attachments.

    Recurses into nested parts (including forwarded message/rfc822 subtrees)
    and also collects inline content-id parts (embedded images), flagged
    ``inline=True`` so the UI can distinguish them from real attachments.
    """
    out: list[dict] = []

    def walk(p):
        fn = p.get("filename") or ""
        body = p.get("body") or {}
        hdrs = {(h.get("name") or "").lower(): (h.get("value") or "")
                for h in p.get("headers", []) or []}
        cid = (hdrs.get("content-id") or "").strip("<> ")
        disp = (hdrs.get("content-disposition") or "").lower()
        if body.get("attachmentId") and (fn or cid):
            mime = p.get("mimeType", "")
            if not fn:
                ext = (mime.split("/")[-1] or "bin") if "/" in mime else "bin"
                fn = f"inline-{cid or 'part'}.{ext}"
            out.append({
                "filename": fn,
                "mime": mime,
                "size": int(body.get("size") or 0),
                "attachment_id": body["attachmentId"],
                "content_id": cid,
                "inline": bool(cid) and "attachment" not in disp,
            })
        for sp in p.get("parts", []) or []:
            walk(sp)

    walk(payload or {})
    return out
```

- [ ] **Step 4: Run new tests + existing regression suite**

Run: `venv/bin/python -m pytest tests/test_email_attachments2.py tests/test_email_attachments.py tests/test_email_upload_preview.py -v`
Expected: ALL PASS.

---

### Task 2: HTML sanitizer + full-HTML message endpoint

**Files:**
- Modify: `dashboard/email_studio.py` — add `_sanitize_email_html()` right after `_collect_attachments`, add route `api_message_html` right after `api_attachment` (line ~760), add `import urllib.parse` near the existing `import urllib.request` (line 22).
- Test: `tests/test_email_html_render.py` (new file)

**Interfaces:**
- Consumes: `_collect_attachments` (Task 1 shape, needs `content_id`).
- Produces: `_sanitize_email_html(html: str, cid_map: dict[str, str] | None = None) -> str` and `GET /api/email2/message/<msg_id>/html?account=` → `text/html` response (CSP `script-src 'none'`) or JSON 404 `{"error": "no html part"}`. Task 7's iframe consumes this URL.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_html_render.py`:

```python
import importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


def test_sanitize_strips_scripts_and_handlers(es):
    dirty = ('<div onclick="steal()">hi</div>'
             '<script>alert(1)</script>'
             '<img src="x" onerror="alert(2)">'
             '<a href="javascript:evil()">c</a>'
             '<iframe src="https://evil"></iframe>'
             '<form action="https://evil"><input></form>')
    clean = es._sanitize_email_html(dirty)
    low = clean.lower()
    assert "<script" not in low and "alert(1)" not in low
    assert "onclick" not in low and "onerror" not in low
    assert "javascript:" not in low
    assert "<iframe" not in low and "<form" not in low
    assert "hi" in clean  # content survives


def test_sanitize_keeps_formatting(es):
    html = '<table><tr><td style="color:red">cell</td></tr></table><style>.x{color:blue}</style>'
    clean = es._sanitize_email_html(html)
    assert "<table>" in clean and 'style="color:red"' in clean and "<style>" in clean


def test_sanitize_rewrites_cid(es):
    html = '<img src="cid:logo123" alt="l">'
    clean = es._sanitize_email_html(html, {"logo123": "/api/email2/attachment/M1/A1?inline=1"})
    assert 'src="/api/email2/attachment/M1/A1?inline=1"' in clean
    assert "cid:" not in clean


def _client(es, monkeypatch, payload):
    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def get(self, userId, id, format): return self
        def execute(self): return {"id": "M1", "payload": payload}
    monkeypatch.setattr(es, "_req_account_id", lambda: None)
    monkeypatch.setattr(es, "_gmail", lambda a: FakeSvc())
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_html_endpoint_returns_sanitized_doc(es, monkeypatch):
    import base64
    raw = base64.urlsafe_b64encode(b"<p>Hello <b>world</b></p><script>x()</script>").decode()
    payload = {"mimeType": "text/html", "body": {"data": raw}}
    c = _client(es, monkeypatch, payload)
    r = c.get("/api/email2/message/M1/html")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert "script-src 'none'" in r.headers.get("Content-Security-Policy", "")
    body = r.get_data(as_text=True)
    assert "<b>world</b>" in body and "<script" not in body.lower()


def test_html_endpoint_404_when_plain_only(es, monkeypatch):
    import base64
    raw = base64.urlsafe_b64encode(b"just text").decode()
    payload = {"mimeType": "text/plain", "body": {"data": raw}}
    c = _client(es, monkeypatch, payload)
    r = c.get("/api/email2/message/M1/html")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_email_html_render.py -v`
Expected: FAIL — `AttributeError: module 'email_studio' has no attribute '_sanitize_email_html'`.

- [ ] **Step 3: Implement sanitizer + endpoint**

Add `import urllib.parse` after line 22 (`import urllib.request`). Then add after `_collect_attachments`:

```python
def _sanitize_email_html(html: str, cid_map: Optional[dict] = None) -> str:
    """Best-effort stdlib sanitizer for rendering email HTML in a sandboxed,
    script-less iframe. Removes active content; keeps formatting/styles."""
    if not html:
        return ""
    # scripts (with content) and other active/embedding elements (tags only)
    html = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", html)
    html = re.sub(r"(?is)<script\b[^>]*/?>", "", html)
    html = re.sub(r"(?is)</?(?:object|embed|iframe|frame|frameset|applet|base|form|input|button|select|textarea|meta|link)\b[^>]*>", "", html)
    # inline event handlers:  onload="..." / onclick='...' / onerror=x
    html = re.sub(r"(?is)\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html)
    # javascript:/vbscript: URLs in attributes and CSS url()
    html = re.sub(r"(?is)((?:href|src|action|formaction|background|poster)\s*=\s*[\"']?)\s*(?:javascript|vbscript):[^\"'>\s]*", r"\1#", html)
    html = re.sub(r"(?is)url\(\s*[\"']?\s*(?:javascript|vbscript):[^)]*\)", "url(#)", html)
    # cid: image rewrite to our inline attachment URLs
    if cid_map:
        def _cid(m):
            url = cid_map.get(m.group(2).strip("<> "))
            return (m.group(1) + url) if url else m.group(0)
        html = re.sub(r"(?is)(src\s*=\s*[\"']?)cid:([^\"'>\s]+)", _cid, html)
    return html
```

Add the route after `api_attachment` (after line ~760):

```python
@email_bp.route("/api/email2/message/<msg_id>/html", methods=["GET"])
def api_message_html(msg_id: str):
    """Full sanitized HTML body of one message, for the reader's sandboxed iframe."""
    from flask import Response
    try:
        acc = request.args.get("account") or ""
        svc = _gmail(_req_account_id())
        m = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = m.get("payload") or {}
        _plain, html = _decode_body(payload)
        if not (html or "").strip():
            return jsonify({"error": "no html part"}), 404
        cid_map = {}
        for a in _collect_attachments(payload):
            if a.get("content_id") and a.get("attachment_id"):
                cid_map[a["content_id"]] = (
                    "/api/email2/attachment/" + urllib.parse.quote(m.get("id", msg_id))
                    + "/" + urllib.parse.quote(a["attachment_id"])
                    + "?inline=1&name=" + urllib.parse.quote(a.get("filename") or "inline")
                    + "&mime=" + urllib.parse.quote(a.get("mime") or "")
                    + (("&account=" + urllib.parse.quote(acc)) if acc else ""))
        doc = ("<!doctype html><html><head><meta charset='utf-8'>"
               "<base target='_blank'>"
               "<style>body{margin:14px;font-family:system-ui,-apple-system,sans-serif;"
               "background:#fff;color:#111;word-wrap:break-word;overflow-wrap:break-word}"
               "img{max-width:100%;height:auto}table{max-width:100%}</style></head><body>"
               + _sanitize_email_html(html, cid_map) + "</body></html>")
        return Response(doc, mimetype="text/html", headers={
            "Content-Security-Policy": "script-src 'none'; object-src 'none'; frame-src 'none'",
            "X-Content-Type-Options": "nosniff",
        })
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_email_html_render.py tests/test_email_attachments2.py -v`
Expected: ALL PASS.

---

### Task 3: Attachment cache columns + browse + index + agent-files endpoints

**Files:**
- Modify: `dashboard/email_studio.py` — `_EXTRA_COLUMNS` (line 94), `api_thread` upsert (lines 707-727), `_hydrate_thread` (lines 522-575); add helper `_att_type_bucket` + routes `api_attachments_browse`, `api_attachments_index`, `api_agent_files` after `api_attachment_upload`.
- Test: `tests/test_email_attachments_browse.py` (new file)

**Interfaces:**
- Consumes: `_collect_attachments` (Task 1).
- Produces:
  - `emails` columns `has_attachments INTEGER DEFAULT 0`, `attachments_json TEXT` (JSON array of Task 1 dicts).
  - `_hydrate_thread` output gains `"has_attachments": bool` and `"attachments": list` keys (empty list when unknown).
  - `GET /api/email2/attachments/browse?account=&type=&q=&limit=&offset=` → `{"attachments": [{gmail_id, thread_id, subject, from_addr, received_at, account_id, filename, mime, size, attachment_id}], "total": int}` (inline parts excluded).
  - `POST /api/email2/attachments/index {max?}` → `{"ok": true, "indexed": int}` (fetches recent threads in full format to backfill the cache).
  - `GET /api/email2/attachments/agent-files?q=` → `{"files": [{name, rel, project_id, size, mtime, mime}]}` — never lists `.private-inbound`/`.vault_meta`.
  - `_att_type_bucket(mime: str, name: str) -> str` ∈ `{"image","pdf","doc","video","audio","other"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_attachments_browse.py`:

```python
import importlib, json, os, sqlite3, sys, uuid
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.modules.pop("email_studio", None)
    mod = importlib.import_module("email_studio")
    mod._ensure_email_schema(db)
    return mod


@pytest.fixture
def client(es):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _seed(es, gmail_id, subject, atts, account_id="acc1", received="2026-07-01"):
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    con.execute(
        """INSERT INTO emails (id, gmail_id, thread_id, from_addr, subject, received_at,
                               account_id, has_attachments, attachments_json)
           VALUES (?, ?, ?, 'v@x.com', ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, gmail_id, "T" + gmail_id, subject, received,
         account_id, 1 if atts else 0, json.dumps(atts)))
    con.commit(); con.close()


ATT = {"filename": "quote.pdf", "mime": "application/pdf", "size": 100,
       "attachment_id": "A1", "content_id": "", "inline": False}
INLINE = {"filename": "logo.png", "mime": "image/png", "size": 10,
          "attachment_id": "A2", "content_id": "c1", "inline": True}


def test_schema_has_new_columns(es):
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    cols = {r[1] for r in con.execute("PRAGMA table_info(emails)").fetchall()}
    con.close()
    assert {"has_attachments", "attachments_json"} <= cols


def test_browse_lists_and_excludes_inline(es, client):
    _seed(es, "M1", "Vendor quote", [ATT, INLINE])
    _seed(es, "M2", "No files", [])
    r = client.get("/api/email2/attachments/browse")
    data = r.get_json()
    names = [a["filename"] for a in data["attachments"]]
    assert names == ["quote.pdf"]
    assert data["total"] == 1
    assert data["attachments"][0]["gmail_id"] == "M1"


def test_browse_filters_by_type_query_account(es, client):
    _seed(es, "M1", "Vendor quote", [ATT], account_id="acc1")
    img = dict(ATT, filename="site.jpg", mime="image/jpeg", attachment_id="A3")
    _seed(es, "M3", "Photos", [img], account_id="acc2")
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?type=image").get_json()["attachments"]] == ["site.jpg"]
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?q=quote").get_json()["attachments"]] == ["quote.pdf"]
    assert [a["filename"] for a in client.get(
        "/api/email2/attachments/browse?account=acc2").get_json()["attachments"]] == ["site.jpg"]


def test_agent_files_excludes_private(es, client, tmp_path, monkeypatch):
    art = tmp_path / "artifacts"
    (art / "proj1").mkdir(parents=True)
    (art / "proj1" / "report.pdf").write_bytes(b"x")
    (art / ".private-inbound" / "phil").mkdir(parents=True)
    (art / ".private-inbound" / "phil" / "secret.jpg").write_bytes(b"x")
    monkeypatch.setattr(es, "ARTIFACTS_DIR", str(art))
    data = client.get("/api/email2/attachments/agent-files").get_json()
    rels = [f["rel"] for f in data["files"]]
    assert "proj1/report.pdf" in rels
    assert all(".private-inbound" not in r for r in rels)


def test_hydrate_thread_exposes_attachments(es):
    _seed(es, "M9", "With file", [ATT])
    con = es._conn()
    try:
        out = es._hydrate_thread(None, con, {"id": "TM9"}, "acc1", "a@b.com")
    finally:
        con.close()
    assert out["has_attachments"] is True
    assert out["attachments"][0]["filename"] == "quote.pdf"
    assert out["attachments"][0]["gmail_id"] == "M9"   # stamped for the list-pane chips


def test_att_type_bucket(es):
    assert es._att_type_bucket("application/pdf", "x.pdf") == "pdf"
    assert es._att_type_bucket("", "photo.JPG") == "image"
    assert es._att_type_bucket("video/mp4", "v.mp4") == "video"
    assert es._att_type_bucket("application/vnd.ms-excel", "s.xls") == "doc"
    assert es._att_type_bucket("application/zip", "a.zip") == "other"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_email_attachments_browse.py -v`
Expected: FAIL — missing columns / 404 routes / missing `_att_type_bucket`.

- [ ] **Step 3: Implement**

3a. Append to `_EXTRA_COLUMNS` (line 94-103):

```python
    ("has_attachments", "INTEGER DEFAULT 0"),
    ("attachments_json", "TEXT"),
```

3b. In `api_thread` (lines 686-727): after `plain, html = _decode_body(...)` add `atts = _collect_attachments(m.get("payload") or {})`; use `"attachments": atts,` in the msgs dict (replacing the inline `_collect_attachments` call at line 704); extend the upsert column list with `has_attachments, attachments_json`, the VALUES with two more `?`, the `DO UPDATE SET` with `has_attachments=excluded.has_attachments, attachments_json=excluded.attachments_json,` and the params tuple with `1 if atts else 0, json.dumps(atts)`.

3c. In `_hydrate_thread`: add `has_attachments, attachments_json` to the SELECT column list (line 530-533); in the cached branch add to `out`:

```python
            "has_attachments": bool(d["has_attachments"]),
            "attachments": (json.loads(d["attachments_json"]) if d["attachments_json"] else []),
```
(wrap the `json.loads` in `try/except Exception: []` via a small local; simplest:)

```python
        try:
            _atts = json.loads(d["attachments_json"]) if d["attachments_json"] else []
        except Exception:
            _atts = []
        for _a in _atts:
            _a.setdefault("gmail_id", d["gmail_id"])
```
and use `"attachments": _atts`. The `gmail_id` stamp is required by the list-pane chips (Task 8), which need to know which message owns each attachment. In the non-cached branch add `"has_attachments": False, "attachments": [],`.

3d. Add helper + three routes after `api_attachment_upload` (after line ~938):

```python
_DOC_EXTS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "csv", "txt", "md", "rtf"}


def _att_type_bucket(mime: str, name: str) -> str:
    mime = (mime or "").lower()
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if mime.startswith("image/") or ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "svg"):
        return "image"
    if mime == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime.startswith("video/") or ext in ("mp4", "mov", "m4v", "webm", "avi"):
        return "video"
    if mime.startswith("audio/") or ext in ("mp3", "wav", "m4a", "ogg", "flac"):
        return "audio"
    if ext in _DOC_EXTS or "word" in mime or "excel" in mime or "spreadsheet" in mime \
            or "presentation" in mime or mime.startswith("text/"):
        return "doc"
    return "other"


@email_bp.route("/api/email2/attachments/browse", methods=["GET"])
def api_attachments_browse():
    """Browse cached attachments across all mailboxes/accounts. Local cache only."""
    q = (request.args.get("q") or "").strip().lower()
    ftype = (request.args.get("type") or "").strip().lower()
    acc = (request.args.get("account") or "").strip()
    limit = max(1, min(int(request.args.get("limit", 100) or 100), 500))
    offset = max(0, int(request.args.get("offset", 0) or 0))
    con = _conn()
    try:
        sql = ("SELECT gmail_id, thread_id, subject, from_addr, received_at, account_id, "
               "attachments_json FROM emails WHERE has_attachments=1")
        params: list = []
        if acc and acc != "ALL":
            sql += " AND account_id=?"
            params.append(acc)
        sql += " ORDER BY received_at DESC LIMIT 1000"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            atts = json.loads(r["attachments_json"] or "[]")
        except Exception:
            atts = []
        for a in atts:
            if a.get("inline"):
                continue
            name = a.get("filename") or ""
            hay = " ".join([name, r["subject"] or "", r["from_addr"] or ""]).lower()
            if q and q not in hay:
                continue
            if ftype and _att_type_bucket(a.get("mime", ""), name) != ftype:
                continue
            out.append({
                "gmail_id": r["gmail_id"], "thread_id": r["thread_id"],
                "subject": r["subject"] or "", "from_addr": r["from_addr"] or "",
                "received_at": r["received_at"] or "", "account_id": r["account_id"] or "",
                "filename": name, "mime": a.get("mime", ""),
                "size": a.get("size") or 0, "attachment_id": a.get("attachment_id", ""),
            })
    return jsonify({"attachments": out[offset:offset + limit], "total": len(out)})


@email_bp.route("/api/email2/attachments/index", methods=["POST"])
def api_attachments_index():
    """Backfill attachments_json for recent threads (full-format fetch).
    Body: {max?: int, label?: str}. Returns {ok, indexed}."""
    body = request.get_json(silent=True) or {}
    max_threads = max(1, min(int(body.get("max", 50) or 50), 200))
    label = body.get("label") or "INBOX"
    try:
        acc = _pick_account(_req_account_id())
        acc_id = acc["id"] if acc else None
        svc = _gmail(acc_id)
        resp = svc.users().threads().list(
            userId="me", labelIds=[label], maxResults=max_threads).execute()
        indexed = 0
        con = _conn()
        try:
            for t in resp.get("threads", []) or []:
                full = svc.users().threads().get(userId="me", id=t["id"], format="full").execute()
                for m in full.get("messages", []) or []:
                    atts = _collect_attachments(m.get("payload") or {})
                    cur = con.execute("SELECT 1 FROM emails WHERE gmail_id=?", (m["id"],)).fetchone()
                    if cur:
                        con.execute(
                            "UPDATE emails SET has_attachments=?, attachments_json=? WHERE gmail_id=?",
                            (1 if atts else 0, json.dumps(atts), m["id"]))
                    else:
                        hdrs = _headers_map(m)
                        con.execute(
                            """INSERT INTO emails (id, gmail_id, thread_id, from_addr, subject,
                                   body_snippet, received_at, status, priority, account_id,
                                   has_attachments, attachments_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, 'new', 'normal', ?, ?, ?)""",
                            (str(uuid.uuid4()), m["id"], m.get("threadId", t["id"]),
                             hdrs.get("From", ""), hdrs.get("Subject", ""), m.get("snippet", ""),
                             hdrs.get("Date", ""), acc_id, 1 if atts else 0, json.dumps(atts)))
                    indexed += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "indexed": indexed})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/attachments/agent-files", methods=["GET"])
def api_agent_files():
    """List files produced by agents/scaffold runs under dashboard/artifacts/.
    Privacy: .private-inbound and .vault_meta are never listed."""
    import mimetypes
    q = (request.args.get("q") or "").strip().lower()
    base = os.path.realpath(ARTIFACTS_DIR)
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _DENY_ARTIFACT_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base)
            if any(seg in _DENY_ARTIFACT_DIRS for seg in rel.split(os.sep)):
                continue
            if q and q not in rel.lower():
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            files.append({
                "name": fn, "rel": rel.replace(os.sep, "/"),
                "project_id": rel.split(os.sep)[0],
                "size": st.st_size, "mtime": st.st_mtime,
                "mime": mimetypes.guess_type(fn)[0] or "application/octet-stream",
            })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"files": files[:500]})
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_email_attachments_browse.py dashboard/tests/test_email_unified.py -v`
Expected: ALL PASS (unified tests confirm `_hydrate_thread` changes didn't regress).

---

### Task 4: Desktop save destination

**Files:**
- Modify: `dashboard/email_studio.py` — constant near line 39, `api_attachment_save` (lines 828-913)
- Test: `tests/test_email_attachment_save_desktop.py` (new file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `POST /api/email2/attachment/save` accepts `to_desktop: bool`; response `saved` dict gains `"desktop": {"path": ...}`. Constant `DESKTOP_SAVE_DIR` (env `EMAIL_DESKTOP_SAVE_DIR`, default `/home/switchhacker/Desktop/Email-Attachments`). Task 8's save modal sends `to_desktop`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_attachment_save_desktop.py`:

```python
import base64, importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("EMAIL_DESKTOP_SAVE_DIR", str(tmp_path / "Desktop" / "Email-Attachments"))
    sys.modules.pop("email_studio", None)
    mod = importlib.import_module("email_studio")
    mod._ensure_email_schema(str(tmp_path / "t.db"))
    return mod


def test_save_to_desktop(es, monkeypatch, tmp_path):
    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self
        def get(self, userId, messageId, id): return self
        def execute(self):
            return {"data": base64.urlsafe_b64encode(b"PDFDATA").decode()}
    monkeypatch.setattr(es, "_req_account_id", lambda: None)
    monkeypatch.setattr(es, "_gmail", lambda a: FakeSvc())
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    c = app.test_client()
    r = c.post("/api/email2/attachment/save", json={
        "msg_id": "M1", "att_id": "A1", "name": "quote.pdf",
        "mime": "application/pdf", "to_desktop": True})
    data = r.get_json()
    assert r.status_code == 200 and data["success"], data
    path = data["saved"]["desktop"]["path"]
    assert path.startswith(str(tmp_path / "Desktop"))
    with open(path, "rb") as fh:
        assert fh.read() == b"PDFDATA"


def test_save_requires_some_destination(es):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    c = app.test_client()
    r = c.post("/api/email2/attachment/save",
               json={"msg_id": "M1", "att_id": "A1", "name": "x"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_email_attachment_save_desktop.py -v`
Expected: `test_save_to_desktop` FAILS with 400 ("pick a project and/or the cloud library"); the second test passes already.

- [ ] **Step 3: Implement**

Add near line 39 (after `OUTBOX_DIR`):

```python
DESKTOP_SAVE_DIR = os.environ.get(
    "EMAIL_DESKTOP_SAVE_DIR", "/home/switchhacker/Desktop/Email-Attachments"
)
```

Note: `DESKTOP_SAVE_DIR` is read at import time; the test sets the env var before importing, which is why the fixture pops `email_studio` from `sys.modules`.

In `api_attachment_save`:
- after `to_cloud = bool(body.get("to_cloud"))` add `to_desktop = bool(body.get("to_desktop"))`;
- change the guard to `if not project_id and not to_cloud and not to_desktop:` with error message `"pick a project, the cloud library, and/or Desktop"`;
- update the docstring's Body line to `{msg_id, att_id, name, mime, project_id?, file_type?, to_cloud?, to_desktop?}`;
- after the cloud block (line ~911) add:

```python
    # 3) Save to the Desktop folder (quick local grab)
    if to_desktop:
        try:
            os.makedirs(DESKTOP_SAVE_DIR, exist_ok=True)
            ddest = os.path.join(DESKTOP_SAVE_DIR, safe)
            if os.path.exists(ddest):
                stem, dot, e2 = safe.partition(".")
                ddest = os.path.join(DESKTOP_SAVE_DIR, f"{stem}_{uuid.uuid4().hex[:6]}{dot}{e2}")
            with open(ddest, "wb") as fh:
                fh.write(data)
            saved["desktop"] = {"path": ddest}
        except Exception as e:
            saved["desktop_error"] = str(e)
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_email_attachment_save_desktop.py -v`
Expected: ALL PASS.

---

### Task 5: Share + restage (forward) endpoints

**Files:**
- Modify: `dashboard/email_studio.py` — add `_materialize_attachment` helper + routes `api_attachment_share`, `api_attachment_restage` after `api_attachment_from_bin` (line ~970)
- Test: `tests/test_email_attachment_share.py` (new file)

**Interfaces:**
- Consumes: `share_service.create_link/share_telegram/share_email` (existing signatures, source=`"artifact"`), `share_service.resolve_share_path("artifact", rel)`, `OUTBOX_DIR` staging layout (`OUTBOX_DIR/<token>/<safe_name>`), `_MAX_ATTACH_BYTES`.
- Produces:
  - `POST /api/email2/attachment/share` body `{via: "link"|"telegram"|"email", msg_id?, att_id?, name?, rel?, to?, note?, account?}` → link: `{ok, url, token, expires_at}`; telegram/email: passthrough of share_service result.
  - `POST /api/email2/attachments/restage` body `{msg_id, att_id, name?, account?}` or `{rel}` → `{ok, token, filename, size, mime}` (same shape as `/attachments/upload`; Task 8's compose consumes it).
  - `_materialize_attachment(msg_id, att_id, name, account_id) -> str` returning an artifact-relative path `_email-shares/<uuid>/<safe>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_attachment_share.py`:

```python
import base64, importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


class FakeSvc:
    def users(self): return self
    def messages(self): return self
    def attachments(self): return self
    def get(self, userId, messageId, id): return self
    def execute(self):
        return {"data": base64.urlsafe_b64encode(b"FILEBYTES").decode()}


@pytest.fixture
def es(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("EMAIL_OUTBOX_DIR", str(tmp_path / "outbox"))
    sys.modules.pop("email_studio", None)
    mod = importlib.import_module("email_studio")
    mod._ensure_email_schema(str(tmp_path / "t.db"))
    monkeypatch.setattr(mod, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    os.makedirs(str(tmp_path / "artifacts"), exist_ok=True)
    monkeypatch.setattr(mod, "_req_account_id", lambda: None)
    monkeypatch.setattr(mod, "_gmail", lambda a: FakeSvc())
    return mod


@pytest.fixture
def client(es):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_materialize_writes_under_email_shares(es):
    rel = es._materialize_attachment("M1", "A1", "quote.pdf", None)
    assert rel.startswith("_email-shares/")
    full = os.path.join(es.ARTIFACTS_DIR, rel)
    with open(full, "rb") as fh:
        assert fh.read() == b"FILEBYTES"


def test_share_link(es, client, monkeypatch):
    import share_service
    calls = {}
    def fake_create_link(source, rel, days=7):
        calls["args"] = (source, rel, days)
        return {"token": "tok1", "url": "http://x/s/tok1", "expires_at": None}
    monkeypatch.setattr(share_service, "create_link", fake_create_link)
    r = client.post("/api/email2/attachment/share", json={
        "via": "link", "msg_id": "M1", "att_id": "A1", "name": "quote.pdf"})
    data = r.get_json()
    assert r.status_code == 200 and data["ok"] and data["url"].endswith("/s/tok1")
    src, rel, _ = calls["args"]
    assert src == "artifact" and rel.startswith("_email-shares/")


def test_share_telegram(es, client, monkeypatch):
    import share_service
    monkeypatch.setattr(share_service, "share_telegram",
                        lambda source, rel, chat_id="", caption="": {"ok": True, "method": "sendDocument"})
    r = client.post("/api/email2/attachment/share", json={
        "via": "telegram", "msg_id": "M1", "att_id": "A1", "name": "quote.pdf"})
    assert r.status_code == 200 and r.get_json()["ok"]


def test_share_unknown_via_400(es, client):
    r = client.post("/api/email2/attachment/share", json={
        "via": "carrier-pigeon", "msg_id": "M1", "att_id": "A1", "name": "x"})
    assert r.status_code == 400


def test_restage_gmail_attachment_roundtrip(es, client):
    r = client.post("/api/email2/attachments/restage", json={
        "msg_id": "M1", "att_id": "A1", "name": "quote.pdf"})
    data = r.get_json()
    assert r.status_code == 200 and data["ok"], data
    staged = os.path.join(es.OUTBOX_DIR, data["token"], data["filename"])
    with open(staged, "rb") as fh:
        assert fh.read() == b"FILEBYTES"
    assert data["size"] == len(b"FILEBYTES")


def test_restage_artifact_rel_with_traversal_denied(es, client, tmp_path):
    proj = os.path.join(es.ARTIFACTS_DIR, "proj1")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, "report.pdf"), "wb") as fh:
        fh.write(b"REPORT")
    r = client.post("/api/email2/attachments/restage", json={"rel": "proj1/report.pdf"})
    assert r.status_code == 200 and r.get_json()["ok"]
    r2 = client.post("/api/email2/attachments/restage", json={"rel": "../../etc/passwd"})
    assert r2.status_code in (400, 404)
    r3 = client.post("/api/email2/attachments/restage",
                     json={"rel": ".private-inbound/phil/x.jpg"})
    assert r3.status_code in (400, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_email_attachment_share.py -v`
Expected: FAIL — `_materialize_attachment` missing, routes 404.

- [ ] **Step 3: Implement**

Note: `share_service.resolve_share_path` uses its own module-level `ARTIFACTS_DIR` (same real path), so guarding artifact `rel`s locally in email_studio keeps the test monkeypatch simple — see `_artifact_abs` below. Add after `api_attachment_from_bin` (after line ~970):

```python
def _artifact_abs(rel: str) -> Optional[str]:
    """Resolve an artifacts-relative path with traversal + privacy guards."""
    base = os.path.realpath(ARTIFACTS_DIR)
    full = os.path.realpath(os.path.join(base, rel or ""))
    if not full.startswith(base + os.sep):
        return None
    if any(seg in _DENY_ARTIFACT_DIRS for seg in full[len(base) + 1:].split(os.sep)):
        return None
    if not os.path.isfile(full):
        return None
    return full


def _materialize_attachment(msg_id: str, att_id: str, name: str,
                            account_id: Optional[str]) -> str:
    """Download a Gmail attachment into artifacts/_email-shares/ so the
    share_service roots can serve it. Returns the artifact-relative path."""
    svc = _gmail(account_id)
    att = svc.users().messages().attachments().get(
        userId="me", messageId=msg_id, id=att_id).execute()
    data = base64.urlsafe_b64decode(att.get("data", ""))
    if len(data) > _MAX_ATTACH_BYTES:
        raise ValueError("attachment exceeds the 25 MB limit")
    safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(name or "attachment"))[:160] or "attachment"
    rel = os.path.join("_email-shares", uuid.uuid4().hex, safe)
    dest = os.path.join(ARTIFACTS_DIR, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    return rel.replace(os.sep, "/")


@email_bp.route("/api/email2/attachment/share", methods=["POST"])
def api_attachment_share():
    """Share an email attachment (or agent file) via link / telegram / email.

    Body: {via, msg_id?, att_id?, name?, rel?, to?, note?, account?}
      via=link     -> {ok, url, token, expires_at}
      via=telegram -> share_service.share_telegram result
      via=email    -> share_service.share_email result (requires `to`)
    """
    try:
        import share_service
    except ImportError:
        from dashboard import share_service  # type: ignore
    body = request.get_json(silent=True) or {}
    via = (body.get("via") or "").strip()
    if via not in ("link", "telegram", "email"):
        return jsonify({"ok": False, "error": f"unknown via: {via}"}), 400
    note = body.get("note") or ""
    try:
        if body.get("rel"):
            if _artifact_abs(body["rel"]) is None:
                return jsonify({"ok": False, "error": "file not found or not shareable"}), 404
            rel = body["rel"]
        else:
            if not body.get("msg_id") or not body.get("att_id"):
                return jsonify({"ok": False, "error": "msg_id and att_id (or rel) required"}), 400
            rel = _materialize_attachment(body["msg_id"], body["att_id"],
                                          body.get("name") or "attachment",
                                          body.get("account") or None)
        if via == "link":
            out = share_service.create_link("artifact", rel)
            return jsonify({"ok": True, **out})
        if via == "telegram":
            out = share_service.share_telegram("artifact", rel, caption=note)
        else:
            if not (body.get("to") or "").strip():
                return jsonify({"ok": False, "error": "missing 'to' address"}), 400
            out = share_service.share_email("artifact", rel, body["to"],
                                            body.get("subject") or "", note)
        return (jsonify(out), 200) if out.get("ok") else (jsonify(out), 400)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@email_bp.route("/api/email2/attachments/restage", methods=["POST"])
def api_attachment_restage():
    """Copy a Gmail attachment or agent file into the send-outbox staging so it
    can be attached to a new message (Forward-a-file).

    Body: {msg_id, att_id, name?, account?} or {rel}.
    Returns the same shape as /attachments/upload: {ok, token, filename, size, mime}.
    """
    import mimetypes, shutil
    body = request.get_json(silent=True) or {}
    try:
        if body.get("rel"):
            src = _artifact_abs(body["rel"])
            if src is None:
                return jsonify({"ok": False, "error": "file not found"}), 404
            if os.path.getsize(src) > _MAX_ATTACH_BYTES:
                return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
            safe = re.sub(r'[^\w.\- ()]', "_", os.path.basename(src))[:160] or "file"
            token = uuid.uuid4().hex
            d = os.path.join(OUTBOX_DIR, token)
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, safe)
            shutil.copy2(src, dest)
        else:
            if not body.get("msg_id") or not body.get("att_id"):
                return jsonify({"ok": False, "error": "msg_id and att_id (or rel) required"}), 400
            svc = _gmail(body.get("account") or None)
            att = svc.users().messages().attachments().get(
                userId="me", messageId=body["msg_id"], id=body["att_id"]).execute()
            data = base64.urlsafe_b64decode(att.get("data", ""))
            if len(data) > _MAX_ATTACH_BYTES:
                return jsonify({"ok": False, "error": "file exceeds the 25 MB limit"}), 400
            safe = re.sub(r'[^\w.\- ()]', "_",
                          os.path.basename(body.get("name") or "attachment"))[:160] or "attachment"
            token = uuid.uuid4().hex
            d = os.path.join(OUTBOX_DIR, token)
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, safe)
            with open(dest, "wb") as fh:
                fh.write(data)
        size = os.path.getsize(dest)
        mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        return jsonify({"ok": True, "token": token, "filename": safe,
                        "size": size, "mime": mime})
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
```

Also add a `_email-shares` sweep so materialized share copies don't pile up: in `_sweep_outbox` (line 42), after the existing loop, add:

```python
    try:
        shares = os.path.join(ARTIFACTS_DIR, "_email-shares")
        for name in os.listdir(shares):
            p = os.path.join(shares, name)
            if os.path.isdir(p) and now - os.path.getmtime(p) > 30 * 86400:
                shutil.rmtree(p, ignore_errors=True)
    except FileNotFoundError:
        pass
```
(30 days — share links default to 7-day expiry. Note `_sweep_outbox` runs before `ARTIFACTS_DIR` is used elsewhere but the constant is defined above it at line 35, so this is safe. `now` and `shutil` are already local there.)

- [ ] **Step 4: Run tests + full email backend suite**

Run: `venv/bin/python -m pytest tests/test_email_attachment_share.py tests/test_email_attachments.py tests/test_email_attachments2.py tests/test_email_upload_preview.py tests/test_share_service.py -v`
Expected: ALL PASS.

---

### Task 6: Frontend — navigation fix, sticky/visible actions, expand-all

**Files:**
- Modify: `dashboard/templates/email.html` — CSS media block (lines 256-272), `.reader-toolbar` CSS (line 126), `renderThread()` toolbar + message cards (lines 812-859), AI strip markup (lines 352-363)

No pytest here (pure template). Verification is Step 3.

- [ ] **Step 1: CSS fixes**

In the `@media(max-width:1100px)` block (lines 258-265), replace:

```css
      .mail-shell.show-reader{grid-template-columns:1fr}
```
with:
```css
      .mail-shell.show-reader{grid-template-columns:200px 1fr}
```

In the `@media(max-width:700px)` block (lines 266-272) add (inside the block):

```css
      .mail-shell.show-reader{grid-template-columns:1fr}
      .mail-shell.show-reader .sidebar{display:none}
```

This kills the reader-below-mailboxes wrap: ≤1100px shows sidebar + reader side by side; ≤700px shows reader alone with a working Back button.

Make the toolbar row stick and always reachable — change line 126 to:

```css
    .reader-toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px 16px;background:#0a0a18;border-bottom:1px solid #14142a;position:sticky;top:0;z-index:5}
```

Add new CSS after `.ai-strip` rules (after line ~140):

```css
    .ai-strip.collapsed{display:none}
    .ai-toggle{background:none;border:none;color:#7c6ba8;font-size:11px;cursor:pointer;font-weight:700;margin-left:auto}
    .msg-quick{display:flex;gap:4px;margin-left:10px;flex-shrink:0}
    .msg-quick button{background:none;border:1px solid #22224a;border-radius:5px;color:#889;font-size:11px;cursor:pointer;padding:3px 7px}
    .msg-quick button:hover{color:#cfd2e8;border-color:#3a3a6a}
```

- [ ] **Step 2: JS/markup changes**

In `renderThread()` toolbar (lines 821-832), after the Print button add:

```js
    <button class="tool-btn" onclick="expandAllMsgs()" title="Expand every message in this thread">⤢ Expand all</button>
    <button class="tool-btn ai" onclick="toggleAiStrip()" title="Show / hide AI helpers">⚡ AI</button>
```

In the AI strip markup (line 352 `<div class="ai-strip">`), give it an id: `<div class="ai-strip" id="aiStrip">`.

Add functions after `toggleMsg` (line ~859):

```js
function expandAllMsgs(){
  document.querySelectorAll('#messagesScroll .msg-card.collapsed').forEach(c=>c.classList.remove('collapsed'));
}
function toggleAiStrip(){
  const s = document.getElementById('aiStrip');
  s.classList.toggle('collapsed');
  try{ localStorage.setItem('email_ai_strip', s.classList.contains('collapsed')?'0':'1'); }catch(e){}
}
(function(){ try{ if(localStorage.getItem('email_ai_strip')==='0') document.getElementById('aiStrip').classList.add('collapsed'); }catch(e){} })();
```

Per-message quick actions — in the `msg-card` template inside `renderThread()` (lines 837-848), replace the `<div class="msg-date">…</div>` line with:

```js
        <div class="msg-date">${esc(m.date)}</div>
        <div class="msg-quick">
          <button title="Reply" onclick="event.stopPropagation();openReplyDraft('professional')">↩</button>
          <button title="Forward this message" onclick="event.stopPropagation();forwardThread()">→</button>
        </div>
```

- [ ] **Step 3: Verify**

Template syntax check: `venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('dashboard/templates/email.html').read())"` → no exception.
Visual verification happens in Task 9 after the dashboard restart (resize to ~900px, open a thread: sidebar stays left, reader beside it, Back works; ≤700px: reader alone + Back).

---

### Task 7: Frontend — sandboxed HTML body rendering with toggle

**Files:**
- Modify: `dashboard/templates/email.html` — `renderThread()` message body (line 846), new CSS + JS helpers

**Interfaces:**
- Consumes: `GET /api/email2/message/<gmail_id>/html?account=` (Task 2); `state.currentThread.messages[i].html` (already returned by `api_thread`, template line 699 of email_studio.py).

- [ ] **Step 1: CSS**

Add after `.msg-body` rules (line ~149):

```css
    .msg-html-frame{width:100%;border:none;background:#fff;border-radius:6px;min-height:120px;display:block}
    .msg-body-toggle{font-size:10.5px;color:#667;background:none;border:1px solid #22224a;border-radius:10px;padding:2px 8px;cursor:pointer;margin:8px 16px 0}
    .msg-body-toggle:hover{color:#aab}
```

- [ ] **Step 2: JS**

Add near `toggleMsg` (line ~859):

```js
const _bodyMode = {};   // gmail_id -> 'html' | 'text'
function bodyModeFor(m){ return _bodyMode[m.gmail_id] || (m.html ? 'html' : 'text'); }
function toggleBodyMode(gid){
  const m = (state.currentThread?.messages||[]).find(x=>x.gmail_id===gid);
  if(!m) return;
  _bodyMode[gid] = bodyModeFor(m)==='html' ? 'text' : 'html';
  renderThread();
}
function fitHtmlFrame(f){
  try{ f.style.height = Math.min(f.contentDocument.documentElement.scrollHeight + 24, 20000) + 'px'; }
  catch(e){ f.style.height = '70vh'; }
}
function renderMsgBody(m){
  const acc = threadAccount(state.currentThreadId);
  if(bodyModeFor(m)==='html'){
    const src = '/api/email2/message/' + encodeURIComponent(m.gmail_id) + '/html' +
                (acc ? ('?account=' + encodeURIComponent(acc)) : '');
    return `<button class="msg-body-toggle" onclick="event.stopPropagation();toggleBodyMode('${escAttr(m.gmail_id)}')">📄 View as plain text</button>
      <div style="padding:10px 16px 16px"><iframe class="msg-html-frame" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        src="${escAttr(src)}" onload="fitHtmlFrame(this)" onerror="toggleBodyMode('${escAttr(m.gmail_id)}')"></iframe></div>`;
  }
  const tgl = m.html ? `<button class="msg-body-toggle" onclick="event.stopPropagation();toggleBodyMode('${escAttr(m.gmail_id)}')">🎨 View formatted (HTML)</button>` : '';
  return tgl + `<div class="msg-body">${esc(m.body || '(empty body)')}</div>`;
}
```

In `renderThread()` replace line 846:

```js
      <div class="msg-body">${esc(m.body || '(empty body)')}</div>
```
with:
```js
      ${renderMsgBody(m)}
```

Note: `sandbox` without `allow-scripts` means no JS runs inside the frame even though the doc is same-origin; the parent can still read `contentDocument` for auto-height. `<base target="_blank">` (Task 2) plus `allow-popups` makes links open in a new tab.

- [ ] **Step 3: Verify**

Jinja parse check (same command as Task 6 Step 3). Functional check in Task 9: open an HTML newsletter → full formatted email, images render, no inner scrollbar, toggle to plain text works; a plain-text-only email shows no toggle.

---

### Task 8: Frontend — attachment action bar, list-pane 📎, Attachments view, save modal Desktop, forward-to-compose

**Files:**
- Modify: `dashboard/templates/email.html` — `renderAttachments()` (lines 868-885), `renderThreads()` (lines 757-773), sidebar (labelList area, line ~700-710), save modal markup (lines 574-602), new share modal markup, new JS section

**Interfaces:**
- Consumes: Task 3 (`browse`, `index`, `agent-files`, `_hydrate_thread.attachments`), Task 4 (`to_desktop`), Task 5 (`share`, `restage`), existing `_cmpAttachments` compose array (line 1124) + `renderCmpAttachments()` + `openCompose(pref)`.

- [ ] **Step 1: Unified action bar on message chips**

Replace `renderAttachments(m)` (lines 868-885) with:

```js
function attActionsHtml(ctx){
  // ctx: {msg_id, att_id, name, mime, rel} — rel set for agent files instead of msg/att ids
  const j = escAttr(JSON.stringify(ctx));
  return `<span class="att-actions">
    <button title="Save to project / cloud / Desktop" onclick='event.stopPropagation();attSave(${j})'>💾</button>
    <button title="Share (Telegram / link / email)" onclick='event.stopPropagation();attShare(${j})'>📤</button>
    <button title="Forward as new email" onclick='event.stopPropagation();attForward(${j})'>↪</button>
  </span>`;
}

function attDownloadBase(ctx){
  // Gmail contexts only (ctx has msg_id/att_id). Agent files (ctx.rel) have no
  // direct download URL — their chips omit ⬇/preview and use Share/Forward instead.
  const acc = state.activeAccount ? '&account=' + encodeURIComponent(state.activeAccount.id) : '';
  return '/api/email2/attachment/' + encodeURIComponent(ctx.msg_id) + '/' +
         encodeURIComponent(ctx.att_id) + '?name=' + encodeURIComponent(ctx.name) +
         '&mime=' + encodeURIComponent(ctx.mime||'') + acc;
}

function renderAttachments(m){
  const atts = (m.attachments || []).filter(a => !a.inline);
  const inline = (m.attachments || []).filter(a => a.inline);
  if(!atts.length && !inline.length) return '';
  const chip = a => {
    const ctx = {msg_id: m.gmail_id, att_id: a.attachment_id, name: a.filename, mime: a.mime||''};
    const base = attDownloadBase(ctx);
    return `<span class="att-chip-wrap">
      <a class="att-chip att-preview-link" href="#" title="${escAttr(a.filename)}"
         data-base="${escAttr(base)}" data-name="${escAttr(a.filename)}" data-mime="${escAttr(a.mime||'')}">${a.inline?'🖼':'📎'} ${esc(a.filename)}
         <span class="att-size">${fmtSize(a.size)}</span></a>
      <a class="att-save-btn" title="Download" href="${escAttr(base)}">⬇</a>
      ${attActionsHtml(ctx)}
    </span>`;
  };
  let html = '<div class="msg-attachments">' + atts.map(chip).join('');
  if(inline.length){
    html += `<span style="font-size:10.5px;color:#556;align-self:center">+ ${inline.length} embedded image${inline.length>1?'s':''}</span>`;
  }
  return html + '</div>';
}
```

Add CSS next to `.att-save-btn` (line ~156):

```css
    .att-actions{display:inline-flex;gap:4px}
    .att-actions button{background:#10102a;border:1px solid #2a2a55;border-radius:14px;color:#a0c0ff;font-size:12px;padding:6px 9px;cursor:pointer;line-height:1}
    .att-actions button:hover{border-color:#4a4a90;background:#16163a}
```

Keep the existing delegated click handler for `.att-preview-link` (it reads `data-base/name/mime` — search for `att-preview-link` listener; unchanged).

- [ ] **Step 2: attSave / attShare / attForward + share modal**

Add share modal markup after the save modal (after line 602):

```html
<!-- Share attachment -->
<div class="att-prev-bg" id="attShareBg" onclick="if(event.target===this)closeAttShare()">
  <div class="att-prev-card" style="max-width:440px">
    <div class="att-prev-head">
      <span>📤 Share file</span>
      <button class="modal-close" onclick="closeAttShare()">×</button>
    </div>
    <div style="padding:18px 20px;display:flex;flex-direction:column;gap:12px">
      <div style="font-size:13px;color:#aab;word-break:break-all" id="attShareName">file</div>
      <button class="send-btn" onclick="submitAttShare('telegram')">📨 Send to Telegram</button>
      <button class="send-btn" onclick="submitAttShare('link')">🔗 Copy 7-day link</button>
      <div style="display:flex;gap:8px">
        <input id="attShareTo" placeholder="email@address…" style="flex:1;background:#0a0a18;border:1px solid #2a2a4a;border-radius:6px;padding:9px;color:#e0e0e0;font-size:13px">
        <button class="send-btn" onclick="submitAttShare('email')">✉️ Email</button>
      </div>
      <div id="attShareMsg" style="font-size:12px;min-height:16px;color:#8b8"></div>
    </div>
  </div>
</div>
```

Add JS after `submitSaveAttachment` (line ~973):

```js
let _shareCtx = null;
function attShare(ctx){ _shareCtx = ctx; document.getElementById('attShareName').textContent = ctx.name; document.getElementById('attShareMsg').textContent=''; document.getElementById('attShareBg').classList.add('open'); }
function closeAttShare(){ document.getElementById('attShareBg').classList.remove('open'); _shareCtx = null; }
async function submitAttShare(via){
  if(!_shareCtx) return;
  const msg = document.getElementById('attShareMsg');
  msg.style.color='#aab'; msg.textContent='Sharing…';
  const body = {via, name:_shareCtx.name, mime:_shareCtx.mime,
                account: state.activeAccount ? state.activeAccount.id : undefined};
  if(_shareCtx.rel) body.rel = _shareCtx.rel;
  else { body.msg_id = _shareCtx.msg_id; body.att_id = _shareCtx.att_id; }
  if(via==='email'){
    body.to = document.getElementById('attShareTo').value.trim();
    if(!body.to){ msg.style.color='#e88'; msg.textContent='Enter an email address.'; return; }
  }
  try{
    const r = await api('/api/email2/attachment/share', {method:'POST', body});
    if(r.ok){
      if(via==='link'){
        try{ await navigator.clipboard.writeText(r.url); msg.textContent='Link copied ✓ ' + r.url; }
        catch(e){ msg.textContent = r.url; }
      } else { msg.style.color='#8b8'; msg.textContent = via==='telegram' ? 'Sent to Telegram ✓' : 'Emailed ✓'; setTimeout(closeAttShare, 900); }
    } else { msg.style.color='#e88'; msg.textContent='Failed: ' + (r.error||'unknown'); }
  }catch(e){ msg.style.color='#e88'; msg.textContent='Failed: '+e.message; }
}

function attSave(ctx){
  if(ctx.rel){ toast('Agent files are already on baza — use Share or Forward.'); return; }
  openSaveAttachment(ctx.msg_id, ctx.att_id, ctx.name, ctx.mime);
}

async function attForward(ctx){
  toast('Staging file…');
  const body = ctx.rel ? {rel: ctx.rel}
    : {msg_id: ctx.msg_id, att_id: ctx.att_id, name: ctx.name,
       account: state.activeAccount ? state.activeAccount.id : undefined};
  try{
    const r = await api('/api/email2/attachments/restage', {method:'POST', body});
    if(!r.ok){ toast('Failed: ' + (r.error||'unknown'), true); return; }
    openCompose({to:'', subject:'Fwd: ' + ctx.name, body:'', mode:'forward'});
    _cmpAttachments.push({token:r.token, filename:r.filename, size:r.size});
    renderCmpAttachments();
  }catch(e){ toast('Failed: '+e.message, true); }
}
```

- [ ] **Step 3: Save modal — Desktop checkbox**

In the save modal (after the cloud checkbox, line 592-594) add:

```html
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:#ccd;cursor:pointer">
        <input type="checkbox" id="saveAttDesktop"> Save to baza Desktop folder
      </label>
```

In `openSaveAttachment` add `document.getElementById('saveAttDesktop').checked = false;` next to the cloud reset (line 927). In `submitSaveAttachment`:
- `const to_desktop = document.getElementById('saveAttDesktop').checked;`
- guard: `if(!project_id && !to_cloud && !to_desktop){ … 'Pick a project, cloud, and/or Desktop.' … }`
- request body gains `to_desktop`;
- success message: `if(r.saved&&r.saved.desktop) where.push('Desktop');`

- [ ] **Step 4: List-pane 📎 badges + expandable chip row**

In `renderThreads()` (lines 757-773): inside the map callback add before `return`:

```js
    const attCount = (t.attachments||[]).filter(a=>!a.inline).length;
    const attBadge = (t.has_attachments && attCount)
      ? `<span class="thread-att-badge" onclick="event.stopPropagation();toggleThreadAtts('${t.thread_id}')">📎 ${attCount}</span>`
      : (t.has_attachments ? '<span class="thread-att-badge">📎</span>' : '');
```

and render it inside `thread-row1` next to the time:

```js
      <div class="thread-row1">
        <div class="thread-from">${star}${esc(fromDisp)}${badge}</div>
        <div class="thread-time">${attBadge}${fmtTime(t.received_at)}</div>
      </div>
```

after the `${cat…}` line, add the (initially hidden) chip row:

```js
      ${attCount ? `<div class="thread-atts" id="tatts-${t.thread_id}" style="display:none" onclick="event.stopPropagation()">
        ${(t.attachments||[]).filter(a=>!a.inline).map(a=>{
          const ctx = {msg_id: a.gmail_id, att_id: a.attachment_id, name: a.filename, mime: a.mime||''};
          const base = attDownloadBase(ctx);
          return `<span class="att-chip-wrap"><a class="att-chip att-preview-link" href="#" data-base="${escAttr(base)}" data-name="${escAttr(a.filename)}" data-mime="${escAttr(a.mime||'')}">📎 ${esc(a.filename)}</a>${attActionsHtml(ctx)}</span>`;
        }).join('')}
      </div>` : ''}
```

(The `gmail_id` on each cached attachment dict is stamped by Task 3's `_hydrate_thread` change.)

Add JS + CSS:

```js
function toggleThreadAtts(tid){
  const el = document.getElementById('tatts-' + tid);
  if(el) el.style.display = el.style.display==='none' ? 'flex' : 'none';
}
```

```css
    .thread-att-badge{font-size:10px;color:#8ab;background:#10102a;border:1px solid #22224a;border-radius:9px;padding:1px 6px;margin-right:6px;cursor:pointer}
    .thread-atts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
```

- [ ] **Step 5: Attachments sidebar view (email + agent files tabs)**

In `loadLabels()` (line 707), after `let html = '<div class="label-group-title">Mailboxes</div>' + part(data.system||[]);` add:

```js
  html += `<div class="label-item ${state.attView?'active':''}" onclick="openAttachmentsView()">
    <div class="label-emoji">📎</div><div class="label-name">Attachments</div></div>`;
```

Add state flag `attView: false` to the `state` object (line 650-659) and reset it at the top of `pickLabel`: `state.attView = false;`.

Add the view functions (new JS section after the save/share code):

```js
async function openAttachmentsView(){
  state.attView = true;
  state.currentThreadId = null;
  document.getElementById('mailShell').classList.add('show-reader');
  document.getElementById('listTitle').textContent = 'Attachments';
  document.getElementById('readerEmpty').style.display = 'none';
  const rc = document.getElementById('readerContent');
  rc.style.display = 'flex';
  document.getElementById('readerSubject').textContent = '📎 All attachments';
  document.getElementById('readerToolbar').innerHTML = `
    <button class="tool-btn ${!state.attTab||state.attTab==='email'?'primary':''}" onclick="setAttTab('email')">✉ Email files</button>
    <button class="tool-btn ${state.attTab==='agent'?'primary':''}" onclick="setAttTab('agent')">🤖 Agent files</button>
    <div class="tool-divider"></div>
    <select id="attTypeSel" class="ai-model-pick" style="margin-left:0" onchange="loadAttView()">
      <option value="">All types</option><option value="image">Images</option><option value="pdf">PDFs</option>
      <option value="doc">Documents</option><option value="video">Video</option><option value="audio">Audio</option><option value="other">Other</option>
    </select>
    <input id="attSearch" placeholder="🔍 filter…" oninput="clearTimeout(window.__ath);window.__ath=setTimeout(loadAttView,300)"
           style="background:#0a0a18;border:1px solid #22224a;border-radius:6px;padding:6px 9px;color:#e0e0e0;font-size:12px">
    <button class="tool-btn" onclick="indexAttachments()" title="Scan recent inbox threads for attachments">🔄 Index recent</button>`;
  loadAttView();
}

function setAttTab(t){ state.attTab = t; openAttachmentsView(); }

async function loadAttView(){
  const ms = document.getElementById('messagesScroll');
  ms.innerHTML = '<div style="padding:30px;text-align:center;color:#333">Loading…</div>';
  const q = encodeURIComponent(document.getElementById('attSearch')?.value || '');
  if(state.attTab === 'agent'){
    const data = await api('/api/email2/attachments/agent-files?q=' + q);
    const files = data.files || [];
    ms.innerHTML = files.length ? '<div class="msg-attachments" style="padding:10px 0">' + files.map(f=>{
      const ctx = {rel: f.rel, name: f.name, mime: f.mime||''};
      return `<span class="att-chip-wrap" title="${escAttr(f.rel)}">
        <span class="att-chip">🤖 ${esc(f.name)} <span class="att-size">${fmtSize(f.size)}</span></span>
        ${attActionsHtml(ctx)}</span>`;
    }).join('') + '</div>'
    : '<div style="padding:30px;color:#556">No agent files yet.</div>';
    return;
  }
  const acc = state.activeAccount?.all ? 'ALL' : (state.activeAccount?.id || '');
  const type = document.getElementById('attTypeSel')?.value || '';
  const data = await api(`/api/email2/attachments/browse?limit=200&q=${q}&type=${encodeURIComponent(type)}${acc?('&account='+encodeURIComponent(acc)):''}`);
  const atts = data.attachments || [];
  ms.innerHTML = atts.length ? '<div class="msg-attachments" style="padding:10px 0">' + atts.map(a=>{
    const ctx = {msg_id: a.gmail_id, att_id: a.attachment_id, name: a.filename, mime: a.mime||''};
    const base = attDownloadBase(ctx);
    return `<span class="att-chip-wrap" title="${escAttr(a.subject + ' — ' + a.from_addr)}">
      <a class="att-chip att-preview-link" href="#" data-base="${escAttr(base)}" data-name="${escAttr(a.filename)}" data-mime="${escAttr(a.mime||'')}">📎 ${esc(a.filename)} <span class="att-size">${fmtSize(a.size)}</span></a>
      <a class="att-save-btn" title="Download" href="${escAttr(base)}">⬇</a>
      <button class="att-save-btn" title="Open email" onclick="state.attView=false;openThread('${escAttr(a.thread_id)}')">✉</button>
      ${attActionsHtml(ctx)}</span>`;
  }).join('') + `</div><div style="padding:8px;color:#556;font-size:11px">${data.total} attachment(s) in the local cache. Not seeing one? Hit 🔄 Index recent, or open its email once.</div>`
  : '<div style="padding:30px;color:#556">No cached attachments yet — hit 🔄 Index recent to scan your inbox.</div>';
}

async function indexAttachments(){
  toast('Indexing recent threads…');
  const r = await api('/api/email2/attachments/index', {method:'POST', body:{max: 50,
    account: state.activeAccount ? state.activeAccount.id : undefined}});
  toast(r.ok ? `Indexed ${r.indexed} messages ✓` : ('Index failed: ' + (r.error||'unknown')), !r.ok);
  if(r.ok) loadAttView();
}
```

Also guard `openThread`/`renderThread` against the att view: at the top of `openThread` add `state.attView = false;`.

- [ ] **Step 6: Verify**

Jinja parse check again. Grep sanity: `grep -c "attActionsHtml\|attForward\|attShare\|openAttachmentsView" dashboard/templates/email.html` → all present. Full functional pass in Task 9.

---

### Task 9: Restart, full test suite, live verification, session log

**Files:** none (operations)

- [ ] **Step 1: Full backend test suite**

Run: `venv/bin/python -m pytest tests/test_email_attachments.py tests/test_email_attachments2.py tests/test_email_html_render.py tests/test_email_attachments_browse.py tests/test_email_attachment_save_desktop.py tests/test_email_attachment_share.py tests/test_email_upload_preview.py tests/test_share_service.py dashboard/tests/test_email_unified.py dashboard/tests/test_email_all_parallel.py dashboard/tests/test_email_threadsafe.py -v`
Expected: ALL PASS.

- [ ] **Step 2: Restart dashboard (template cache)**

Run: `sudo systemctl restart baza-dashboard && sleep 3 && systemctl is-active baza-dashboard && curl -s -o /dev/null -w '%{http_code}' http://localhost:8888/email`
Expected: `active` and `200`.

- [ ] **Step 3: Live API smoke**

```bash
curl -s http://localhost:8888/api/email2/attachments/browse | head -c 300
curl -s http://localhost:8888/api/email2/attachments/agent-files | head -c 300
curl -s -X POST http://localhost:8888/api/email2/attachments/index -H 'Content-Type: application/json' -d '{"max":25}'
```
Expected: JSON (no 500s); index returns `{"ok": true, "indexed": N}`; browse then returns entries.

- [ ] **Step 4: Manual verification checklist (with Serge or via browser)**

1. Open an HTML newsletter → fully formatted, images visible, no inner scrollbar, HTML⇄text toggle works.
2. Resize to ~900px, open a thread → reader beside mailboxes (NOT underneath), ‹ Back returns to the list; ≤700px single-pane also works.
3. Thread with attachment → chip shows ⬇ 💾 📤 ↪; save modal has the Desktop checkbox; share link copies; Telegram send arrives; ↪ opens compose with the file attached and it sends.
4. List pane shows 📎 badges; clicking expands chips without opening the email.
5. Sidebar → 📎 Attachments: email tab lists cached files with actions; Agent files tab lists artifacts and NOTHING from `.private-inbound`.

- [ ] **Step 5: Session log**

Append a timestamped entry to `~/Desktop/baza-session-log.md` (heading `### YYYY-MM-DD HH:MM | Email preview + attachments shipped`) summarizing files touched, endpoints added, test counts, restart done. Do NOT manually git commit (claw-auto-git).
