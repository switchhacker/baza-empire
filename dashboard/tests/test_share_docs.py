"""Tests for AHB123 Share Docs via Email (PDF attachments) — Tasks 1–3."""
import base64
import email as emaillib
import os
import sys
import types

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_DIR = os.path.dirname(DASHBOARD_DIR)
for _p in (DASHBOARD_DIR, PARENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import email_studio


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fake_send_svc(captured):
    """Return a fake Gmail service that records body['raw'] in `captured`."""
    class FakeExec:
        def execute(self_inner):
            return {"id": "msg123", "threadId": "thr456"}
    class FakeSend:
        def send(self_inner, userId, body):
            captured["raw"] = body.get("raw", "")
            return FakeExec()
    class FakeMessages:
        def messages(self_inner):
            return FakeSend()
    class FakeUsers:
        def users(self_inner):
            return FakeMessages()
    return FakeUsers()


# ── Task 1: render_ahb_doc_pdf ────────────────────────────────────────────────

def test_render_ahb_doc_pdf_returns_bytes(monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "_invoice_html", lambda iid: ("<html>INV</html>", "invoice_42"))
    class FakeWeasy:
        def __init__(self, string=None): self.s = string
        def write_pdf(self): return b"%PDF-fake"
    monkeypatch.setattr(appmod, "WeasyHTML", FakeWeasy, raising=False)
    fn, mime, data = appmod.render_ahb_doc_pdf("invoice", 42)
    assert fn == "invoice_42.pdf"
    assert mime == "application/pdf"
    assert data == b"%PDF-fake"


# ── Task 2: _mime_message with attachments ────────────────────────────────────

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


# ── Task 3: /api/email2/send with attachments ─────────────────────────────────

def test_send_attaches_invoice_pdf(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc(captured))
    fake_app = types.ModuleType("app")
    fake_app.render_ahb_doc_pdf = lambda kind, i: ("invoice_7.pdf", "application/pdf", b"%PDF-7")
    monkeypatch.setitem(sys.modules, "app", fake_app)
    r = client.post("/api/email2/send", json={
        "mode": "compose", "to": "c@x.com", "subject": "Your invoice", "body": "hi",
        "attachments": [{"type": "invoice_pdf", "invoice_id": 7}]})
    assert r.get_json()["ok"] is True
    # The MIME message encodes attachment data with standard base64;
    # check for the base64-encoded form of b"%PDF-7" which is "JVBERi03"
    decoded_mime = base64.urlsafe_b64decode(captured["raw"])
    assert b"JVBERi03" in decoded_mime or b"%PDF-7" in decoded_mime


def test_send_rejects_oversize(monkeypatch, client):
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc({}))
    fake_app = types.ModuleType("app")
    fake_app.render_ahb_doc_pdf = lambda kind, i: ("big.pdf", "application/pdf", b"x" * (26 * 1024 * 1024))
    monkeypatch.setitem(sys.modules, "app", fake_app)
    r = client.post("/api/email2/send", json={"to": "c@x.com", "subject": "s", "body": "b",
        "attachments": [{"type": "invoice_pdf", "invoice_id": 1}]})
    assert r.status_code == 400 and "25" in r.get_json()["error"]


def test_send_rejects_path_traversal(monkeypatch, client):
    monkeypatch.setattr(email_studio, "_gmail", lambda aid=None: _fake_send_svc({}))
    r = client.post("/api/email2/send", json={"to": "c@x.com", "subject": "s", "body": "b",
        "attachments": [{"type": "artifact", "project_id": "p1", "path": "../../etc/passwd"}]})
    assert r.status_code == 400
