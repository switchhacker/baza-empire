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


def test_url_import_400_no_url(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/url-import", json={})
    assert r.status_code == 400


def test_url_import_400_bad_scheme(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/url-import", json={"url": "ftp://example.com/x"})
    assert r.status_code == 400
    assert "http://" in r.get_json()["error"]


def test_url_import_rate_limit(client, monkeypatch):
    sources_mod = (sys.modules.get("dashboard.social_sources")
                   or sys.modules.get("social_sources"))
    # Reset timestamps & lower the limit so we can fill it predictably
    monkeypatch.setattr(sources_mod, "URL_IMPORT_RATE_LIMIT", 2)
    sources_mod._URL_IMPORT_TIMESTAMPS.clear()
    c, _ = client

    # Force yt-dlp to fail fast so we don't actually fetch — the rate limit
    # still increments BEFORE the download attempt.
    class FakeError(Exception):
        pass

    def _fail(*a, **k):
        raise FakeError("no network in tests")

    import yt_dlp
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", _fail)
    for i in range(2):
        r = c.post("/api/ahb/social/sources/url-import",
                   json={"url": "https://example.com/v" + str(i)})
        # 500 (yt-dlp error) is fine — the rate counter still ticked
        assert r.status_code in (200, 400, 500)
    # Third should be rate-limited (429)
    r = c.post("/api/ahb/social/sources/url-import",
               json={"url": "https://example.com/v3"})
    assert r.status_code == 429
    j = r.get_json()
    assert j["limit"] == 2
    assert j["retry_after_seconds"] > 0


def test_voice_memo_400_no_file(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/voice-memo")
    assert r.status_code == 400


def test_voice_memo_400_bad_extension(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/voice-memo",
        data={"file": (io.BytesIO(b"x"), "x.mp4")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_voice_memo_happy_path_with_mocked_whisper(client, monkeypatch):
    # Mock the whisper model so the test doesn't need real audio
    class FakeSegment:
        def __init__(self, text):
            self.text = text

    class FakeModel:
        def transcribe(self, path, beam_size=1):
            return ([FakeSegment("hello world from the test")], None)

    audio_mod = (sys.modules.get("dashboard.social_audio")
                 or sys.modules.get("social_audio"))
    monkeypatch.setattr(audio_mod, "_get_whisper", lambda: FakeModel())
    c, _ = client
    r = c.post(
        "/api/ahb/social/sources/voice-memo",
        data={"file": (io.BytesIO(b"fake-webm-bytes"), "memo.webm")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["transcript"] == "hello world from the test"
    assert j["transcribe_error"] is None
    # Row inserted in image_captions with the transcript as caption
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM image_captions WHERE id=?", (j["id"],)).fetchone()
    finally:
        con.close()
    assert "hello world" in row["caption"]
    assert row["tags"] == "voice-memo"
