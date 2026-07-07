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
