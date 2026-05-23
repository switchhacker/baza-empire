"""Tests for Social Studio v2.1 audio pipeline."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv21a_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_audio", "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    for m in ("social_studio", "social_settings", "social_audio", "social_ai", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_music_list_empty(client):
    c, _ = client
    r = c.get("/api/ahb/social/music")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_music_search_by_mood(client):
    c, _ = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        con.execute("INSERT INTO ahb_social_music_library (path, title, bpm, mood) VALUES (?, ?, ?, ?)",
                    ("/tmp/calm.mp3", "Calm Track", 80, "calm"))
        con.execute("INSERT INTO ahb_social_music_library (path, title, bpm, mood) VALUES (?, ?, ?, ?)",
                    ("/tmp/hype.mp3", "Hype Track", 150, "energetic"))
        con.commit()
    finally:
        con.close()
    r = c.get("/api/ahb/social/music?mood=calm")
    items = r.get_json()["items"]
    assert len(items) == 1 and items[0]["mood"] == "calm"


def test_music_reindex_endpoint_exists(client, monkeypatch):
    c, ss = client
    import social_audio
    monkeypatch.setattr(social_audio, "_index_music_dir", lambda d: {"indexed": 0, "skipped": 0})
    r = c.post("/api/ahb/social/music/reindex")
    assert r.status_code == 200


def test_subtitles_400_no_asset(client):
    c, _ = client
    pid = c.post(
        "/api/ahb/social/posts",
        json={"platform": "tiktok", "variant": "9x16", "source_media_ids": [1]},
    ).get_json()["id"]
    r = c.post(f"/api/ahb/social/posts/{pid}/subtitles")
    assert r.status_code == 400
    assert "no rendered asset" in r.get_json()["error"]


def test_subtitles_400_unknown_post(client):
    c, _ = client
    r = c.post("/api/ahb/social/posts/999999/subtitles")
    assert r.status_code == 400


def test_voiceover_400_missing_text(client):
    c, _ = client
    r = c.post("/api/ahb/social/ai/voiceover", json={})
    assert r.status_code == 400
    assert "text required" in r.get_json()["error"]


def test_voiceover_400_invalid_voice_name(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/ai/voiceover",
        json={"text": "hello", "voice": "../../etc/passwd"},
    )
    assert r.status_code == 400


def test_voiceover_400_unknown_voice(client):
    c, _ = client
    r = c.post(
        "/api/ahb/social/ai/voiceover",
        json={"text": "hello", "voice": "nonexistent-voice"},
    )
    assert r.status_code == 400
    assert "voice not installed" in r.get_json()["error"]


def test_voiceover_preview_rejects_traversal(client):
    c, _ = client
    r = c.get("/api/ahb/social/ai/voiceover/preview?path=/etc/passwd")
    assert r.status_code == 400


def test_bundle_includes_per_language_captions(client, tmp_path):
    c, ss = client
    # Create a post with translations and a fake rendered asset
    asset = tmp_path / "asset.mp4"
    asset.write_bytes(b"fake-mp4-bytes")
    pid = c.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16",
        "source_media_ids": [1], "caption": "Hello",
        "hashtags": "#hi",
    }).get_json()["id"]
    import json as _json
    con = ss._conn()
    try:
        con.execute(
            "UPDATE ahb_social_posts SET asset_path=?, translations=? WHERE id=?",
            (str(asset),
             _json.dumps({
                 "es": {"caption": "Hola", "hashtags": "#hola"},
                 "fr": {"caption": "Bonjour", "hashtags": "#bonjour"},
             }),
             pid),
        )
        con.commit()
    finally:
        con.close()
    r = c.get(f"/api/ahb/social/posts/{pid}/bundle")
    assert r.status_code == 200
    # The response is a zip; check its byte contents for the per-lang filenames
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.data))
    names = z.namelist()
    assert "caption_tiktok.es.txt" in names
    assert "caption_tiktok.fr.txt" in names
    assert z.read("caption_tiktok.es.txt").decode().startswith("Hola")
    assert z.read("caption_tiktok.fr.txt").decode().startswith("Bonjour")
