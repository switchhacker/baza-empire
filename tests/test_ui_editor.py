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


def test_stale_column_and_report_roundtrip(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/x", "selector": "#gone", "kind": "text", "value": "hi"})
    oid = r.get_json()["id"]
    r2 = client.post("/api/ui/overrides", json={
        "page": "/x", "selector": "#alive", "kind": "text", "value": "yo"})
    oid2 = r2.get_json()["id"]
    # fresh overrides are not stale
    rows = client.get("/api/ui/overrides?page=/x").get_json()["overrides"]
    assert all(o["stale"] == 0 for o in rows)
    # report one stale, one ok (oid2 stays stale=0, no transition, so cleared=0)
    rep = client.post("/api/ui/overrides/stale-report", json={
        "page": "/x", "stale_ids": [oid], "ok_ids": [oid2]})
    assert rep.status_code == 200
    j = rep.get_json()
    assert j["ok"] and j["marked"] == 1 and j["cleared"] == 0
    by_id = {o["id"]: o for o in
             client.get("/api/ui/overrides/history?page=/x").get_json()["overrides"]}
    assert by_id[oid]["stale"] == 1 and by_id[oid2]["stale"] == 0
    # summary carries a stale count
    pages = client.get("/api/ui/overrides/summary").get_json()["pages"]
    px = [p for p in pages if p["page"] == "/x"][0]
    assert px["count"] == 2 and px["stale"] == 1


def test_stale_report_validation_and_scoping(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/a", "selector": "#s", "kind": "text", "value": "v"})
    oid = r.get_json()["id"]
    # non-int ids rejected
    bad = client.post("/api/ui/overrides/stale-report", json={
        "page": "/a", "stale_ids": ["x"], "ok_ids": []})
    assert bad.status_code == 422
    # wrong page does not mark
    client.post("/api/ui/overrides/stale-report", json={
        "page": "/other", "stale_ids": [oid], "ok_ids": []})
    row = client.get("/api/ui/overrides?page=/a").get_json()["overrides"][0]
    assert row["stale"] == 0


def test_resave_clears_stale(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/y", "selector": "#s", "kind": "text", "value": "v1"})
    oid = r.get_json()["id"]
    client.post("/api/ui/overrides/stale-report", json={
        "page": "/y", "stale_ids": [oid], "ok_ids": []})
    # upsert (same page+selector+kind) resets stale to 0 — the element was just edited live
    client.post("/api/ui/overrides", json={
        "page": "/y", "selector": "#s", "kind": "text", "value": "v2"})
    row = client.get("/api/ui/overrides?page=/y").get_json()["overrides"][0]
    assert row["stale"] == 0 and row["value"] == "v2"


def test_stale_report_counts_only_transitions(client):
    r = client.post("/api/ui/overrides", json={
        "page": "/z", "selector": "#s", "kind": "text", "value": "v"})
    oid = r.get_json()["id"]
    first = client.post("/api/ui/overrides/stale-report", json={
        "page": "/z", "stale_ids": [oid], "ok_ids": []}).get_json()
    assert first["marked"] == 1
    second = client.post("/api/ui/overrides/stale-report", json={
        "page": "/z", "stale_ids": [oid], "ok_ids": []}).get_json()
    assert second["marked"] == 0  # already stale — nothing changed


def test_stale_report_rejects_booleans(client):
    r = client.post("/api/ui/overrides/stale-report", json={
        "page": "/z", "stale_ids": [True], "ok_ids": []})
    assert r.status_code == 422
