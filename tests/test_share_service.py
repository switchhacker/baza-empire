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
    msg = _emaillib.message_from_string(_sent_raw(captured))
    body_texts = [p.get_payload(decode=True).decode("utf-8", "replace")
                  for p in msg.walk() if p.get_content_type() == "text/plain"]
    assert any("https://share.example/s/" in b for b in body_texts)
    assert "pinout.png" not in [p.get_filename() for p in msg.walk()]
    # subject must NOT contain the URL
    assert "https://" not in (msg.get("Subject") or "")


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


def test_share_email_no_account_returns_error(ss_db, monkeypatch):
    import email_studio as es
    monkeypatch.setattr(es, "_active_account", lambda: None)
    out = ss_db.share_email("artifact", "p1/pinout.png", "to@x.com", "Subj", "note")
    assert out["ok"] is False
    assert "account" in out["error"].lower()
