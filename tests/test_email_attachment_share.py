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

    # The denied file must actually exist, so a 404 here is genuinely
    # attributable to the _DENY_ARTIFACT_DIRS privacy guard rather than
    # to the plain isfile() check (which would 404 on a missing file too).
    private_dir = os.path.join(es.ARTIFACTS_DIR, ".private-inbound", "phil")
    os.makedirs(private_dir, exist_ok=True)
    with open(os.path.join(private_dir, "x.jpg"), "wb") as fh:
        fh.write(b"SECRET-PHOTO-BYTES")

    r3 = client.post("/api/email2/attachments/restage",
                     json={"rel": ".private-inbound/phil/x.jpg"})
    assert r3.status_code in (400, 404)

    # Same guard must hold for the share endpoint: a real private file must
    # never be shareable via link/telegram/email.
    r4 = client.post("/api/email2/attachment/share",
                     json={"via": "link", "rel": ".private-inbound/phil/x.jpg"})
    assert r4.status_code == 404
