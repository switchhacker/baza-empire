"""Tests for Social Studio v2.1 — source acquisition (T20)."""
import io
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client(monkeypatch, tmp_path):
    d = tmp_path
    db = os.path.join(str(d), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", str(d))
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_audio",
              "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    for m in ("social_studio", "social_settings", "social_audio",
              "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_upload_400_no_file(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/upload")
    assert r.status_code == 400
    assert "file required" in r.get_json()["error"]


def test_upload_400_bad_extension(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/upload",
        data={"file": (io.BytesIO(b"hello"), "doc.txt")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert ".png" in r.get_json()["accepted"]


def test_upload_image_inserts_row(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/upload",
        data={"file": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake-png-bytes"), "frame.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["kind"] == "image"
    assert j["id"] > 0
    assert os.path.exists(j["path"])
    assert "uploads/social" in j["path"].replace(os.sep, "/")
    # Verify row landed in image_captions
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM image_captions WHERE id=?", (j["id"],)).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row["sub_path"] == j["path"]


def test_upload_respects_source_qs(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/upload?source=webcam",
        data={"file": (io.BytesIO(b"x" * 32), "vid.mp4")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["source"] == "webcam"


def test_upload_sanitizes_source(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/upload?source=evil%2F../../etc",
        data={"file": (io.BytesIO(b"x"), "frame.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    src = r.get_json()["source"]
    # No slashes or dots, capped at 32 chars
    assert "/" not in src and ".." not in src
    assert len(src) <= 32


def test_upload_size_cap_rejects(client, monkeypatch):
    # social_studio imports submodules via `from dashboard import …` so the
    # live instance the route uses is registered under that qualified name.
    sources_mod = (sys.modules.get("dashboard.social_sources")
                   or sys.modules.get("social_sources"))
    assert sources_mod is not None, "social_sources not loaded"
    monkeypatch.setattr(sources_mod, "MAX_UPLOAD_BYTES", 1024)
    c, _ = client
    big = io.BytesIO(b"\x00" * 2048)
    r = c.post(
        "/api/ahb/social/sources/upload",
        data={"file": (big, "big.mp4")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "exceeds" in r.get_json()["error"]


def test_uploaded_path_resolvable_via_post_render(client):
    """An uploaded source must be usable in a post (path inside allowed roots)."""
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/upload",
        data={"file": (io.BytesIO(b"fake-jpg"), "shot.jpg")},
        content_type="multipart/form-data",
    )
    sid = r.get_json()["id"]
    upload_path = r.get_json()["path"]
    # Create a post referencing that source ID
    import social_studio as ss
    paths = ss._resolve_media_paths([sid])
    assert paths == [upload_path]  # path is inside DASHBOARD_DIR/uploads/social
