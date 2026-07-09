# tests/test_ui_editor.py — overrides store + API (spec B2)
import io, json, os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
import pytest
from flask import Flask
import ui_editor as u


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "DB_PATH", str(tmp_path / "ov.db"))
    monkeypatch.setattr(u, "UPLOAD_DIR", str(tmp_path / "uploads"))
    u.init_db()
    app = Flask("t")
    app.register_blueprint(u.ui_bp)
    return app.test_client()


def _save(client, **kw):
    body = {"page": "/ahb123", "selector": "#x", "kind": "text", "value": "Hi"}
    body.update(kw)
    return client.post("/api/ui/overrides", json=body)


def test_normalize_page_strips_query_hash_and_trailing_slash():
    assert u.normalize_page("/ahb123?tab=email#x") == "/ahb123"
    assert u.normalize_page("ahb123/") == "/ahb123"
    assert u.normalize_page("") == "/"
    assert u.normalize_page("/") == "/"


def test_save_and_list_roundtrip(client):
    r = _save(client, value="New Label",
              fingerprint={"tag": "div", "text": "Old", "cls": "sub-tab"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    got = client.get("/api/ui/overrides?page=/ahb123?tab=email").get_json()
    assert got["page"] == "/ahb123"
    assert len(got["overrides"]) == 1
    ov = got["overrides"][0]
    assert ov["value"] == "New Label" and ov["kind"] == "text"
    assert ov["fingerprint"]["tag"] == "div"


def test_upsert_same_key_updates_not_duplicates(client):
    _save(client, value="one")
    _save(client, value="two")
    ovs = client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"]
    assert len(ovs) == 1 and ovs[0]["value"] == "two"


def test_bad_kind_and_bad_selector_rejected(client):
    assert _save(client, kind="explode").status_code == 422
    assert _save(client, selector="").status_code == 422
    assert _save(client, selector="x" * 1001).status_code == 422


def test_revert_removes_from_active_keeps_in_history(client):
    oid = _save(client).get_json()["id"]
    assert client.post(f"/api/ui/overrides/{oid}/revert").status_code == 200
    assert client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"] == []
    hist = client.get("/api/ui/overrides/history?page=/ahb123").get_json()["overrides"]
    assert len(hist) == 1 and hist[0]["active"] == 0


def test_reset_page_and_reset_selector(client):
    _save(client, selector="#a")
    _save(client, selector="#b")
    _save(client, selector="#b", kind="style", value={"color": "red"})
    r = client.post("/api/ui/overrides/reset", json={"page": "/ahb123", "selector": "#b"})
    assert r.get_json()["reverted"] == 2
    left = client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"]
    assert [o["selector"] for o in left] == ["#a"]
    r = client.post("/api/ui/overrides/reset", json={"page": "/ahb123"})
    assert r.get_json()["reverted"] == 1


def test_summary_counts_active_per_page(client):
    _save(client, page="/ahb123", selector="#a")
    _save(client, page="/datahub", selector="#a")
    _save(client, page="/datahub", selector="#b")
    pages = {p["page"]: p["count"]
             for p in client.get("/api/ui/overrides/summary").get_json()["pages"]}
    assert pages == {"/ahb123": 1, "/datahub": 2}


def test_upload_rejects_bad_extension_and_saves_good(client):
    bad = {"file": (io.BytesIO(b"x"), "evil.py")}
    assert client.post("/api/ui/upload", data=bad,
                       content_type="multipart/form-data").status_code == 422
    good = {"file": (io.BytesIO(b"\x89PNG fake"), "pic.PNG")}
    r = client.post("/api/ui/upload", data=good, content_type="multipart/form-data")
    url = r.get_json()["url"]
    assert url.startswith("/static/uploads/") and url.endswith(".png")
    assert os.path.exists(os.path.join(u.UPLOAD_DIR, os.path.basename(url)))


def test_non_string_fields_rejected(client):
    assert _save(client, kind=["text"]).status_code == 422
    assert _save(client, selector=["x"]).status_code == 422
    assert _save(client, page=["x"]).status_code == 422


def test_upload_size_cap(client, monkeypatch):
    monkeypatch.setattr(u, "MAX_UPLOAD_BYTES", 10)
    big = {"file": (io.BytesIO(b"x" * 11), "pic.png")}
    r = client.post("/api/ui/upload", data=big, content_type="multipart/form-data")
    assert r.status_code == 422


def test_revert_missing_id_404(client):
    assert client.post("/api/ui/overrides/9999/revert").status_code == 404


def test_explicit_empty_fingerprint_updates(client):
    _save(client, fingerprint={"tag": "div"})
    r = _save(client, fingerprint=None)
    assert r.status_code == 200
    ovs = client.get("/api/ui/overrides?page=/ahb123").get_json()["overrides"]
    assert len(ovs) == 1 and ovs[0]["fingerprint"] is None


def test_reset_non_string_fields_rejected(client):
    r = client.post("/api/ui/overrides/reset", json={"page": "/ahb123", "selector": ["x"]})
    assert r.status_code == 422
    r = client.post("/api/ui/overrides/reset", json={"page": ["x"]})
    assert r.status_code == 422
