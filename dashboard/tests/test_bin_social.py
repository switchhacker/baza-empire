# dashboard/tests/test_bin_social.py
import os, importlib
import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZA_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("BAZA_BIN_DB", str(tmp_path / "bin.db"))
    monkeypatch.setenv("BAZA_DASHBOARD_DB", str(tmp_path / "dash.db"))
    import dashboard.bin_store as bs
    import dashboard.social_studio as ss
    importlib.reload(bs)
    importlib.reload(ss)
    bs.init_bin_db()
    ss._ensure_social_tables()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ss.social_bp)
    c = app.test_client()
    c._bs = bs
    c._ss = ss
    return c


def test_asset_from_bin_sets_asset_path(client):
    # create a post row
    con = client._ss._conn()
    con.execute("INSERT INTO ahb_social_posts (platform, variant, status) VALUES ('instagram','feed','draft')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()

    item = client._bs.add_file(filename="hero.jpg", data=b"JPGDATA", source="upload")
    tok = client._bs.bin_token(item["stored_path"])
    r = client.post(f"/api/ahb/social/posts/{pid}/asset-from-bin", json={"token": tok})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and os.path.isfile(j["asset_path"])
    assert os.path.isfile(item["stored_path"])   # bin untouched

    con = client._ss._conn()
    row = con.execute("SELECT asset_path, cover_path FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    assert row["asset_path"] == j["asset_path"]
    assert row["cover_path"] == j["asset_path"]   # image -> cover set too


def test_asset_from_bin_bad_token(client):
    r = client.post("/api/ahb/social/posts/1/asset-from-bin", json={"token": "~Zm9v"})
    assert r.status_code in (404, 400)
