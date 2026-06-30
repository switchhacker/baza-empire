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


def test_upload_rejects_oversize_content_length(es, monkeypatch, tmp_path):
    # Flask's test client recomputes Content-Length from the real multipart body,
    # so a spoofed header on c.post() can't reach the route — and a spoofed
    # CONTENT_LENGTH larger than the actual stream trips Werkzeug's form parser
    # (ClientDisconnected) before our guard. So drive the route directly with a
    # genuinely oversized in-memory multipart body: the guard must fast-reject on
    # request.content_length and stage NOTHING to disk (the bug was f.save()
    # flooding the partition before the size check).
    from flask import Flask
    from werkzeug.test import EnvironBuilder

    monkeypatch.setattr(es, "OUTBOX_DIR", str(tmp_path / "outbox"))
    os.makedirs(es.OUTBOX_DIR, exist_ok=True)
    app = Flask(__name__)
    app.register_blueprint(es.email_bp)

    # Spy on os.makedirs in the module: the fast guard must return BEFORE the
    # route creates the per-token staging dir (which precedes f.save). This
    # distinguishes the fast Content-Length reject from the post-save check,
    # which would have already created the dir and streamed the upload to disk.
    made_dirs = []
    real_makedirs = es.os.makedirs

    def _spy_makedirs(path, *a, **k):
        made_dirs.append(path)
        return real_makedirs(path, *a, **k)

    monkeypatch.setattr(es.os, "makedirs", _spy_makedirs)

    big_payload = b"\x00" * (26 * 1024 * 1024)  # >25MB, in memory only
    builder = EnvironBuilder(
        path="/api/email2/attachments/upload", method="POST",
        data={"file": (io.BytesIO(big_payload), "big.bin")},
    )
    env = builder.get_environ()

    with app.request_context(env):
        from flask import request
        assert request.content_length > es._MAX_ATTACH_BYTES
        resp = es.api_attachment_upload()
        body, status = resp if isinstance(resp, tuple) else (resp, 200)
        assert status == 400
        assert body.get_json()["error"] == "file exceeds the 25 MB limit"
    # guard fired before staging: no token dir created, nothing written to disk
    assert made_dirs == []
    assert not os.listdir(es.OUTBOX_DIR)
