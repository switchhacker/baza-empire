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


def test_sources_union_includes_media_tab(client, tmp_path, monkeypatch):
    """Media-tab DB rows (image_captions.db) should surface with origin='media'."""
    c, _ = client
    # Create a Media-tab-style image_captions.db
    media_db = tmp_path / "image_captions.db"
    con = sqlite3.connect(str(media_db))
    con.execute(
        """CREATE TABLE image_captions (
            abs_path TEXT PRIMARY KEY, project_id TEXT, sub_path TEXT,
            caption TEXT, tags TEXT, mtime REAL, indexed_at TEXT,
            model TEXT, status TEXT, error TEXT
        )"""
    )
    media_file = tmp_path / "from_media.jpg"
    media_file.write_bytes(b"fake-jpg-data")
    con.execute(
        "INSERT INTO image_captions (abs_path, sub_path, caption, tags, status, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(media_file), "from_media.jpg", "Bathroom remodel — tile closeup",
         "tile,bathroom,trim", "ok", "2026-05-23T10:00:00"),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("BAZA_MEDIA_CAPTIONS_DB", str(media_db))

    r = c.get("/api/ahb/social/sources?origins=media")
    assert r.status_code == 200
    j = r.get_json()
    assert any(i["origin"] == "media" for i in j["items"])
    media_row = next(i for i in j["items"] if i["origin"] == "media")
    assert media_row["abs_path"] == str(media_file)
    assert "tile" in (media_row["tags"] or "")


def test_sources_union_includes_data_hub(client, tmp_path):
    """ahb_files rows should surface with origin='data-hub'."""
    c, _ = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    # ahb_files schema (best-effort; matches dashboard/app.py)
    con.execute(
        """CREATE TABLE IF NOT EXISTS ahb_files (
            id TEXT PRIMARY KEY, name TEXT, file_type TEXT, file_path TEXT,
            size INTEGER, tags TEXT, category TEXT, year TEXT,
            project_id TEXT, created_at TEXT DEFAULT (datetime('now')),
            photo_section TEXT, document_type TEXT
        )"""
    )
    dh_file = tmp_path / "blueprint.jpg"
    dh_file.write_bytes(b"fake-jpg")
    con.execute(
        "INSERT INTO ahb_files (id, name, file_type, file_path, tags, category) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("uuid-1", "blueprint.jpg", "Image", str(dh_file), "blueprint", "permit"),
    )
    con.commit()
    con.close()

    r = c.get("/api/ahb/social/sources?origins=data-hub")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert any(i["origin"] == "data-hub" for i in items)
    dh = next(i for i in items if i["origin"] == "data-hub")
    assert dh["abs_path"] == str(dh_file)
    assert dh["data_hub_id"] == "uuid-1"


def test_sources_origins_filter(client):
    c, _ = client
    r_default = c.get("/api/ahb/social/sources")
    assert "composer" in r_default.get_json()["origins_returned"]
    r_composer = c.get("/api/ahb/social/sources?origins=composer")
    assert r_composer.get_json()["origins_returned"] == ["composer"]


def test_import_by_path_400_missing(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/import-by-path", json={})
    assert r.status_code == 400


def test_import_by_path_404_nonexistent(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/import-by-path",
               json={"abs_path": "/tmp/no-such-file.jpg"})
    assert r.status_code == 404


def test_import_by_path_creates_row_and_is_idempotent(client, tmp_path):
    c, _ = client
    f = tmp_path / "shot.jpg"
    f.write_bytes(b"fake")
    r1 = c.post("/api/ahb/social/sources/import-by-path",
                json={"abs_path": str(f), "caption": "test", "tags": "test"})
    assert r1.status_code == 200
    j1 = r1.get_json()
    assert j1["id"] > 0
    assert j1["already_imported"] is False
    # Second call reuses the row
    r2 = c.post("/api/ahb/social/sources/import-by-path", json={"abs_path": str(f)})
    j2 = r2.get_json()
    assert j2["id"] == j1["id"]
    assert j2["already_imported"] is True


def test_imported_abs_path_resolvable(client, tmp_path, monkeypatch):
    # Allow this temp path as a media root
    monkeypatch.setenv("BAZA_MEDIA_EXTRA_ROOT", str(tmp_path))
    c, _ = client
    f = tmp_path / "shot.jpg"
    f.write_bytes(b"fake")
    r = c.post("/api/ahb/social/sources/import-by-path", json={"abs_path": str(f)})
    sid = r.get_json()["id"]
    import social_studio as ss
    paths = ss._resolve_media_paths([sid])
    assert paths == [str(f)]


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


def test_sd_status_returns_running_flag(client):
    c, _ = client
    r = c.get("/api/ahb/social/sources/sd-status")
    assert r.status_code == 200
    j = r.get_json()
    assert "running" in j and isinstance(j["running"], bool)


def test_sd_generate_400_no_subject(client):
    c, _ = client
    r = c.post("/api/ahb/social/sources/sd-generate", json={})
    assert r.status_code == 400
    assert "subject required" in r.get_json()["error"]


def test_sd_generate_503_when_sd_unreachable(client, monkeypatch):
    c, _ = client
    import requests
    class FakeConnError(requests.exceptions.ConnectionError):
        pass

    def boom(*a, **k):
        raise FakeConnError("refused")

    monkeypatch.setattr(requests, "post", boom)
    r = c.post("/api/ahb/social/sources/sd-generate",
               json={"subject": "a contractor", "style": "photorealistic"})
    assert r.status_code == 503
    assert "not reachable" in r.get_json()["error"]


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
