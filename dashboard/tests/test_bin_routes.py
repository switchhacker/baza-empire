# dashboard/tests/test_bin_routes.py
import io, os, importlib
import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    import dashboard.bin_store as bs
    import dashboard.bin_routes as br
    importlib.reload(bs)
    importlib.reload(br)
    bs.init_bin_db()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(br.bin_bp)
    return app.test_client()


def test_upload_then_list_and_serve(client):
    r = client.post("/api/bin/upload", data={
        "file": (io.BytesIO(b"%PDF fake"), "permit.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    item = r.get_json()["item"]
    assert item["kind"] == "document" and item["token"].startswith("~")

    lst = client.get("/api/bin/list").get_json()
    assert lst["ok"] and lst["count"] == 1 and lst["items"][0]["name"] == "permit.pdf"

    served = client.get(f"/api/bin/serve/{item['token']}")
    assert served.status_code == 200 and served.data == b"%PDF fake"


def test_serve_bad_token_404(client):
    assert client.get("/api/bin/serve/~Zm9vYmFy").status_code == 404
    assert client.get("/api/bin/serve/notatoken").status_code == 404


def test_delete(client):
    item = client.post("/api/bin/upload", data={
        "file": (io.BytesIO(b"x"), "a.txt")},
        content_type="multipart/form-data").get_json()["item"]
    assert client.post("/api/bin/delete", json={"id": item["id"]}).status_code == 200
    assert client.get("/api/bin/list").get_json()["count"] == 0
    assert client.post("/api/bin/delete", json={"id": "nope"}).status_code == 404
