# AHB123 Share Docs via Email (PDF attachments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From AHB123, share a quote/invoice/estimate PDF (and project artifacts) by email with the real file bytes attached — no links.

**Architecture:** Extract a shared `render_ahb_doc_pdf(kind, id) -> (filename, mimetype, bytes)` in `app.py` reused by the three existing PDF routes. Extend `email_studio._mime_message` to `multipart/mixed` and `POST /api/email2/send` to accept an `attachments` list of server-side refs, resolving doc PDFs via a deferred `from app import render_ahb_doc_pdf` and artifacts from disk (path-guarded). Add a self-contained Share modal in `ahb123.html`.

**Tech Stack:** Flask (`app.py`, `email_studio.py`), WeasyPrint, `email.mime`, Gmail API, vanilla JS (`ahb123.html`).

**Spec:** `docs/superpowers/specs/2026-06-16-ahb123-share-docs-via-email-design.md`

**Repo note:** No manual git commits (auto-git timer owns the repo). Checkpoint = tests green. Restart `baza-dashboard` after `ahb123.html` edits.

**Test location:** `dashboard/tests/test_share_docs.py` (new), Flask test-client pattern; mock WeasyPrint and the Gmail `send`.

---

### Task 1: Extract `render_ahb_doc_pdf` and re-point the 3 PDF routes

**Files:**
- Modify: `dashboard/app.py` — invoice PDF route (~9908–10199), quote PDF route (~6006–6160), estimate PDF route (~7366–7460)
- Test: `dashboard/tests/test_share_docs.py`

- [ ] **Step 1: Write the failing test**

```python
import app as appmod

def test_render_ahb_doc_pdf_returns_bytes(monkeypatch):
    monkeypatch.setattr(appmod, "_invoice_html", lambda iid: ("<html>INV</html>", "invoice_42"))
    class FakeWeasy:
        def __init__(self, string=None): self.s = string
        def write_pdf(self): return b"%PDF-fake"
    monkeypatch.setattr(appmod, "WeasyHTML", FakeWeasy, raising=False)
    fn, mime, data = appmod.render_ahb_doc_pdf("invoice", 42)
    assert fn == "invoice_42.pdf"
    assert mime == "application/pdf"
    assert data == b"%PDF-fake"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py::test_render_ahb_doc_pdf_returns_bytes -v`
Expected: FAIL (`render_ahb_doc_pdf` / `_invoice_html` missing)

- [ ] **Step 3: Refactor — extract per-kind HTML builders**

For each of the three routes, move the body that **builds the `html` string** into a module-level function returning `(html: str, filename_base: str)`, leaving the route to call it then build its Response:
- `_invoice_html(iid) -> (html, f"invoice_{number}")` — move invoice route lines that build `html` (the f-string ending at `</html>'''`, ~10179) into the function; `filename_base` from `inv.get('invoice_number')`.
- `_quote_html(qid) -> (html, f"quote_{qid}")`
- `_estimate_html(eid) -> (html, f"estimate_{eid}")`

Add a module-level WeasyPrint import alias so it is monkeypatchable and add the shared helper + dispatcher:

```python
try:
    from weasyprint import HTML as WeasyHTML
except Exception:
    WeasyHTML = None

_DOC_HTML_BUILDERS = {
    "invoice":  lambda i: _invoice_html(i),
    "quote":    lambda i: _quote_html(i),
    "estimate": lambda i: _estimate_html(i),
}

def render_ahb_doc_pdf(kind, doc_id):
    """Return (filename, mimetype, data_bytes) for an AHB document.
    PDF when WeasyPrint is available, else HTML fallback bytes."""
    builder = _DOC_HTML_BUILDERS.get(kind)
    if not builder:
        raise ValueError(f"unknown doc kind: {kind}")
    html, base = builder(doc_id)
    if html is None:
        raise LookupError(f"{kind} {doc_id} not found")
    if WeasyHTML is not None:
        try:
            return f"{base}.pdf", "application/pdf", WeasyHTML(string=html).write_pdf()
        except Exception as e:
            print(f"[pdf] weasyprint failed for {kind} {doc_id}: {e}", flush=True)
    return f"{base}.html", "text/html", html.encode("utf-8")
```

Re-point each route to call its builder + `render_ahb_doc_pdf` (or build the Response from the builder html). Use a shared response wrapper to keep the existing inline/download behavior:

```python
def _pdf_response(kind, doc_id, download):
    fn, mime, data = render_ahb_doc_pdf(kind, doc_id)
    resp = make_response(data)
    resp.headers['Content-Type'] = mime
    disposition = 'attachment' if download else 'inline'
    resp.headers['Content-Disposition'] = f'{disposition}; filename="{fn}"'
    return resp
```

Route bodies become e.g. `return _pdf_response("invoice", iid, request.args.get('download','0')=='1')` (preserve each route's existing try/except + not-found behavior).

- [ ] **Step 4: Run test + regression**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py::test_render_ahb_doc_pdf_returns_bytes -v`
Expected: PASS
Manual: `GET /api/ahb/invoices/<existing>/pdf` still returns a PDF inline after `sudo systemctl restart baza-dashboard`; same for quote/estimate.

- [ ] **Step 5: Checkpoint** — tests green; 3 PDF routes verified unchanged.

---

### Task 2: `_mime_message` supports attachments (multipart/mixed)

**Files:**
- Modify: `dashboard/email_studio.py` (`_mime_message` ~670–692; add imports)
- Test: `dashboard/tests/test_share_docs.py`

- [ ] **Step 1: Write the failing test**

```python
import base64, email as emaillib
import email_studio

def test_mime_message_attaches_file():
    raw = email_studio._mime_message(
        "to@x.com", "Subj", "Body text",
        attachments=[{"filename": "invoice_1.pdf", "mimetype": "application/pdf", "data": b"%PDF-1"}])
    msg = emaillib.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/mixed"
    parts = list(msg.walk())
    pdfs = [p for p in parts if p.get_filename() == "invoice_1.pdf"]
    assert len(pdfs) == 1
    assert pdfs[0].get_payload(decode=True) == b"%PDF-1"

def test_mime_message_no_attachments_is_alternative():
    raw = email_studio._mime_message("to@x.com", "S", "B")
    msg = emaillib.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get_content_type() == "multipart/alternative"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py -k mime_message -v`
Expected: FAIL (`attachments` kwarg unexpected)

- [ ] **Step 3: Implement**

At top of `email_studio.py` add: `from email.mime.application import MIMEApplication`. Change `_mime_message` to accept `attachments=None` and wrap when present:

```python
def _mime_message(to, subject, body, cc="", bcc="", in_reply_to="", references="",
                  from_addr="", attachments=None):
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", _charset="utf-8"))
    html_body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + \
                body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"
    alt.attach(MIMEText(html_body, "html", _charset="utf-8"))

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        for a in attachments:
            maintype, _, subtype = (a.get("mimetype") or "application/octet-stream").partition("/")
            part = MIMEApplication(a["data"], _subtype=subtype or "octet-stream")
            part.add_header("Content-Disposition", "attachment", filename=a["filename"])
            msg.attach(part)
    else:
        msg = alt

    msg["To"] = to
    if cc: msg["Cc"] = cc
    if bcc: msg["Bcc"] = bcc
    if from_addr: msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to: msg["In-Reply-To"] = in_reply_to
    if references: msg["References"] = references
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py -k mime_message -v`
Expected: PASS

- [ ] **Step 5: Checkpoint** — tests green.

---

### Task 3: `/api/email2/send` accepts `attachments` refs + guards

**Files:**
- Modify: `dashboard/email_studio.py` (`api_send` ~695–756; add an attachment resolver + `ARTIFACTS_DIR` awareness)
- Test: `dashboard/tests/test_share_docs.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_send_attaches_invoice_pdf(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc(captured))
    import sys, types
    fake_app = types.ModuleType("app")
    fake_app.render_ahb_doc_pdf = lambda kind, i: ("invoice_7.pdf", "application/pdf", b"%PDF-7")
    monkeypatch.setitem(sys.modules, "app", fake_app)
    r = client.post("/api/email2/send", json={
        "mode": "compose", "to": "c@x.com", "subject": "Your invoice", "body": "hi",
        "attachments": [{"type": "invoice_pdf", "invoice_id": 7}]})
    assert r.get_json()["ok"] is True
    assert b"%PDF-7" in base64.urlsafe_b64decode(captured["raw"])

def test_send_rejects_oversize(monkeypatch, client):
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc({}))
    import sys, types
    fake_app = types.ModuleType("app")
    fake_app.render_ahb_doc_pdf = lambda kind, i: ("big.pdf", "application/pdf", b"x" * (26*1024*1024))
    monkeypatch.setitem(sys.modules, "app", fake_app)
    r = client.post("/api/email2/send", json={"to":"c@x.com","subject":"s","body":"b",
        "attachments":[{"type":"invoice_pdf","invoice_id":1}]})
    assert r.status_code == 400 and "25" in r.get_json()["error"]

def test_send_rejects_path_traversal(monkeypatch, client):
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc({}))
    r = client.post("/api/email2/send", json={"to":"c@x.com","subject":"s","body":"b",
        "attachments":[{"type":"artifact","project_id":"p1","path":"../../etc/passwd"}]})
    assert r.status_code == 400
```

(Add a `_fake_send_svc(captured)` helper at the top of the test file that records the `body['raw']` passed to `users().messages().send`.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py -k send_ -v`
Expected: FAIL (no attachment handling)

- [ ] **Step 3: Implement the resolver + wire into `api_send`**

Add near the top of `email_studio.py`:

```python
import os
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
_MAX_ATTACH_BYTES = 25 * 1024 * 1024
_DENY_ARTIFACT_DIRS = (".private-inbound", ".vault_meta")

def _resolve_attachments(refs):
    """refs: list of server-side refs -> list of {filename, mimetype, data}. Raises ValueError on bad ref."""
    out, total = [], 0
    for ref in refs or []:
        t = ref.get("type")
        if t in ("invoice_pdf", "quote_pdf", "estimate_pdf"):
            from app import render_ahb_doc_pdf  # deferred import avoids circular import
            kind = t.replace("_pdf", "")
            doc_id = ref.get(kind + "_id") or ref.get("id")
            fn, mime, data = render_ahb_doc_pdf(kind, doc_id)
        elif t == "artifact":
            pid = str(ref.get("project_id") or "")
            rel = str(ref.get("path") or "")
            base = os.path.realpath(os.path.join(ARTIFACTS_DIR, pid))
            full = os.path.realpath(os.path.join(base, rel))
            if not (full == base or full.startswith(base + os.sep)):
                raise ValueError("invalid artifact path")
            if any(seg in full.split(os.sep) for seg in _DENY_ARTIFACT_DIRS):
                raise ValueError("artifact is private and cannot be shared")
            if not os.path.isfile(full):
                raise ValueError("artifact not found")
            with open(full, "rb") as f:
                data = f.read()
            fn = os.path.basename(full)
            import mimetypes
            mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        else:
            raise ValueError(f"unknown attachment type: {t}")
        total += len(data)
        if total > _MAX_ATTACH_BYTES:
            raise ValueError("attachments exceed the 25 MB limit")
        out.append({"filename": fn, "mimetype": mime, "data": data})
    return out
```

In `api_send`, after reading the body, resolve attachments and pass to `_mime_message`:

```python
try:
    attach_objs = _resolve_attachments(data.get("attachments"))
except ValueError as e:
    return jsonify({"ok": False, "error": str(e)}), 400
except LookupError as e:
    return jsonify({"ok": False, "error": str(e)}), 404
...
raw = _mime_message(to, subject, body, cc=cc, bcc=bcc,
                    in_reply_to=in_reply_to, references=references,
                    attachments=attach_objs)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd dashboard && python -m pytest tests/test_share_docs.py -v`
Expected: PASS (all)

- [ ] **Step 5: Checkpoint** — tests green.

---

### Task 4: AHB123 Share modal + From picker + invoice/quote/estimate buttons

**Files:**
- Modify: `dashboard/templates/ahb123.html` (add body-level modal; add buttons near `viewInvoicePDF` ~6748, `pdViewQuotePDF` ~10561, estimate PDF button ~9770)

- [ ] **Step 1: Add a body-level Share modal** with To, a `Send from` `<select id="share-from">`, Subject, Body textarea, an attachment chip list `<div id="share-attach">`, and Send/Cancel buttons. Place it at body level (dashboard modal rule).

- [ ] **Step 2: Add the driver JS**

```javascript
let _shareCtx = {attachments: [], to: ''};
async function openShareEmail({to, subject, body, attachments}){
  _shareCtx = {attachments: attachments||[], to: to||''};
  document.getElementById('share-to').value = to||'';
  document.getElementById('share-subject').value = subject||'';
  document.getElementById('share-body').value = body||'';
  document.getElementById('share-attach').textContent =
    (attachments||[]).map(a=>a.label||a.type).join(', ') || 'No attachments';
  // populate From accounts
  const d = await fetch('/api/email2/accounts').then(r=>r.json());
  const sel = document.getElementById('share-from');
  sel.innerHTML = (d.accounts||[]).map(a=>`<option value="${a.id}" ${a.is_active?'selected':''}>${a.email}</option>`).join('');
  sel.disabled = !(d.accounts||[]).length;
  document.getElementById('shareModal').classList.add('open');
}
async function sendShareEmail(){
  const body = {
    account: document.getElementById('share-from').value,
    mode: 'compose',
    to: document.getElementById('share-to').value.trim(),
    subject: document.getElementById('share-subject').value,
    body: document.getElementById('share-body').value,
    attachments: _shareCtx.attachments.map(a=>a.ref),
  };
  if(!body.to){ alert('Recipient required'); return; }
  const r = await fetch('/api/email2/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d = await r.json();
  if(d.ok){ document.getElementById('shareModal').classList.remove('open'); }
  else alert('Send failed: ' + (d.error||'?'));
}
```

- [ ] **Step 3: Wire the three doc buttons** — add a "✉️ Share" button next to each existing PDF button:
- Invoice: `onclick="openShareEmail({to: currentClientEmail(), subject:'Invoice '+inv.invoice_number, body:'Please find your invoice attached.', attachments:[{type:'invoice_pdf', label:'Invoice PDF', ref:{type:'invoice_pdf', invoice_id: id}}]})"`
- Quote: `attachments:[{type:'quote_pdf', label:'Quote PDF', ref:{type:'quote_pdf', quote_id: q.id}}]`, subject `'Quote — '+projectTitle`.
- Estimate: `attachments:[{type:'estimate_pdf', label:'Estimate PDF', ref:{type:'estimate_pdf', estimate_id: e.id}}]`.

Add a `currentClientEmail()` helper reading `#project-client-email`.

- [ ] **Step 4: Manual verify** — `sudo systemctl restart baza-dashboard`; from a project's invoice click "✉️ Share" → modal opens with client email + From account → Send → the PDF arrives as an attachment in the recipient's mailbox. Repeat for quote and estimate.

- [ ] **Step 5: Checkpoint.**

---

### Task 5: AHB123 artifact multi-select share (photos/documents/receipts)

**Files:**
- Modify: `dashboard/templates/ahb123.html` (the artifacts/photos/documents/receipts tab rendering)

- [ ] **Step 1:** Add a checkbox per artifact tile and a "✉️ Share selected" button on the photos/documents/receipts views. Track selected `{project_id, path}` in a JS array.

- [ ] **Step 2:** On "Share selected", call `openShareEmail({to: currentClientEmail(), subject: projectTitle+' — files', body:'Please find the attached files.', attachments: selected.map(s=>({type:'artifact', label:s.name, ref:{type:'artifact', project_id:s.project_id, path:s.path}}))})`.

- [ ] **Step 3: Manual verify** — select 2 photos → "Share selected" → both arrive as attachments; selecting a `.private-inbound` file (if surfaced) is rejected with the privacy error; >25 MB selection rejected.

- [ ] **Step 4: Final checkpoint** — `cd dashboard && python -m pytest tests/test_share_docs.py -v` (all green) + manual flows above pass.
