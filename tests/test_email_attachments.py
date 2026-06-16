import base64, importlib, os, sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))


@pytest.fixture
def es():
    sys.modules.pop("email_studio", None)
    return importlib.import_module("email_studio")


# ---- Task 15: _mime_message multipart ----

import email as _email


def _parse(raw):
    return _email.message_from_bytes(base64.urlsafe_b64decode(raw))


def _bodies_and_atts(msg):
    """Return (plain_bodies, attachment_filenames) walking the MIME tree."""
    bodies, atts = [], []
    for part in msg.walk():
        disp = part.get("Content-Disposition", "") or ""
        if "attachment" in disp:
            atts.append(part.get_filename())
        elif part.get_content_type() == "text/plain":
            bodies.append(part.get_payload(decode=True).decode("utf-8", "replace"))
    return bodies, atts


def test_mime_message_attaches_pdf(es):
    raw = es._mime_message("a@b.com", "Subj", "hello",
                           attachments=[{"filename": "inv.pdf",
                                         "data": b"%PDF-1.4 fake",
                                         "mimetype": "application/pdf"}])
    msg = _parse(raw)
    assert msg.get_content_type() == "multipart/mixed"
    bodies, atts = _bodies_and_atts(msg)
    assert "inv.pdf" in atts
    assert any("hello" in b for b in bodies)


def test_mime_message_no_attachments_unchanged(es):
    raw = es._mime_message("a@b.com", "Subj", "hello")
    msg = _parse(raw)
    assert msg.get_content_type() == "multipart/alternative"
    bodies, atts = _bodies_and_atts(msg)
    assert atts == []
    assert any("hello" in b for b in bodies)


# ---- Task 16: /send resolves attachment refs ----
# NOTE: the attachment-resolution layer (_resolve_attachments) was implemented
# concurrently in email_studio.py and is broader than this plan's design
# (it also supports estimate_pdf + artifact refs, with a 25MB cap and path
# guards). These tests verify /send attaches a resolved file rather than
# re-asserting a particular resolver function name.

def test_send_attaches_resolved_pdf(es, monkeypatch):
    captured = {}

    class FakeSvc:
        def users(self): return self
        def messages(self): return self
        def send(self, userId, body): captured["raw"] = body["raw"]; return self
        def execute(self): return {"id": "x", "threadId": None}

    monkeypatch.setattr(es, "_req_account_id", lambda: "acct")
    monkeypatch.setattr(es, "_gmail", lambda a: FakeSvc())
    monkeypatch.setattr(es, "_resolve_attachments",
                        lambda refs: [{"filename": "inv.pdf", "data": b"%PDF fake",
                                       "mimetype": "application/pdf"}] if refs else [])

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)
    app.config["TESTING"] = True
    c = app.test_client()
    r = c.post("/api/email2/send", json={"to": "a@b.com", "subject": "S", "body": "hi",
                                         "attachments": [{"type": "invoice_pdf", "invoice_id": "i1"}]})
    assert r.status_code == 200
    raw = base64.urlsafe_b64decode(captured["raw"]).decode("utf-8", "replace")
    assert "inv.pdf" in raw
