"""Tests for Social Connections — Phase 1 (framework + YouTube + manual export).

All Google/YouTube network ops are monkeypatched; no credentials or network.
"""
import json
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOCIAL_MODS = (
    "social_studio", "social_settings", "social_audio", "social_ai",
    "social_sources", "social_workflow", "social_trends", "social_analytics",
    "social_connect",
)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", str(tmp_path))
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in _SOCIAL_MODS:
        sys.modules.pop(m, None)
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    # social_studio registers submodules under their qualified name; the live
    # module the routes use is whichever import path won. Patch THAT object.
    social_connect = (sys.modules.get("dashboard.social_connect")
                      or sys.modules.get("social_connect"))
    assert social_connect is not None, "social_connect not loaded"
    # Isolate token / app-creds storage to the temp dir.
    monkeypatch.setattr(social_connect, "ACCOUNTS_DIR",
                        os.path.join(str(tmp_path), "accounts"))
    monkeypatch.setattr(social_connect, "CREDS_DIR",
                        os.path.join(str(tmp_path), "creds"))
    monkeypatch.setattr(social_connect, "EMAIL_CREDENTIALS_PATH",
                        os.path.join(str(tmp_path), "no-such-email-creds.json"))
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_connect, db
    for m in _SOCIAL_MODS:
        sys.modules.pop(m, None)


def _make_post(db, asset_path=None, caption="hello", hashtags="#a #b"):
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_posts (platform, variant, caption, hashtags, "
            "asset_path, source_media_ids, status) VALUES "
            "('tiktok','9x16',?,?,?,'[]','draft')",
            (caption, hashtags, asset_path),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def test_connections_list_empty(env):
    c, _, _ = env
    r = c.get("/api/ahb/social/connections")
    assert r.status_code == 200
    j = r.get_json()
    assert j["items"] == []
    assert "youtube" in j["platforms"]
    assert j["oauth_platforms"] == ["youtube"]


def test_appcreds_status_and_set(env):
    c, sc, _ = env
    r = c.get("/api/ahb/social/connections/app-creds")
    assert r.status_code == 200
    assert r.get_json()["configured"]["youtube"] is False
    # Set a YouTube client secret
    r2 = c.put("/api/ahb/social/connections/app-creds",
               json={"platform": "youtube",
                     "client_json": {"installed": {"client_id": "x"}}})
    assert r2.status_code == 200
    assert os.path.exists(sc._platform_creds_path("youtube"))
    # perms are 600
    mode = oct(os.stat(sc._platform_creds_path("youtube")).st_mode & 0o777)
    assert mode == "0o600"
    # Now status reflects it
    assert c.get("/api/ahb/social/connections/app-creds").get_json()["configured"]["youtube"] is True


def test_appcreds_rejects_bad_platform_and_bad_json(env):
    c, _, _ = env
    assert c.put("/api/ahb/social/connections/app-creds",
                 json={"platform": "myspace", "client_json": "{}"}).status_code == 400
    assert c.put("/api/ahb/social/connections/app-creds",
                 json={"platform": "youtube", "client_json": "{not json"}).status_code == 400


def test_auth_start_unavailable_platform(env):
    c, _, _ = env
    r = c.post("/api/ahb/social/connections/instagram/auth/start", json={})
    assert r.status_code == 400
    assert "Phase 2/3" in r.get_json()["error"]


def test_auth_start_youtube_returns_url(env, monkeypatch):
    c, sc, _ = env

    class FakeFlow:
        def authorization_url(self, **kw):
            return ("https://accounts.google.com/o/oauth2/auth?x=1", "state123")

    monkeypatch.setattr(sc, "_yt_build_flow", lambda: FakeFlow())
    r = c.post("/api/ahb/social/connections/youtube/auth/start", json={})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["auth_url"].startswith("https://accounts.google.com")
    assert j["flow_id"]


def test_auth_finish_creates_connection(env, monkeypatch):
    c, sc, _ = env

    class FakeCreds:
        def to_json(self):
            return json.dumps({"token": "abc", "refresh_token": "r"})

    class FakeFlow:
        def __init__(self):
            self.credentials = FakeCreds()

        def authorization_url(self, **kw):
            return ("https://accounts.google.com/o/oauth2/auth", "st")

        def fetch_token(self, code=None):
            assert code == "thecode"

    monkeypatch.setattr(sc, "_yt_build_flow", lambda: FakeFlow())
    monkeypatch.setattr(sc, "_yt_channel_label",
                        lambda creds: ("All Home Building", "UC_chan_1"))

    start = c.post("/api/ahb/social/connections/youtube/auth/start", json={}).get_json()
    fin = c.post("/api/ahb/social/connections/youtube/auth/finish",
                 json={"flow_id": start["flow_id"],
                       "redirect_url": "http://localhost:8888/cb?code=thecode&state=st"})
    assert fin.status_code == 200, fin.get_data(as_text=True)
    j = fin.get_json()
    assert j["ok"] is True
    assert j["account_label"] == "All Home Building"
    # Token persisted with 600 perms
    tp = sc._token_path("youtube", "UC_chan_1")
    assert os.path.exists(tp)
    assert oct(os.stat(tp).st_mode & 0o777) == "0o600"
    # Connection row visible
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "youtube"
    assert items[0]["account_ref"] == "UC_chan_1"
    # List never leaks tokens
    assert "token" not in json.dumps(items)


def test_publish_requires_confirm(env, monkeypatch):
    c, sc, db = env
    pid = _make_post(db)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish", json={"connection_id": 1})
    assert r.status_code == 400
    assert "confirm" in r.get_json()["error"]


def test_publish_requires_video_asset(env, monkeypatch, tmp_path):
    c, sc, db = env
    # Seed a youtube connection directly
    cid = sc._upsert_connection("youtube", "Chan", "UC1", " ".join(sc.YT_SCOPES))
    pid = _make_post(db, asset_path=None)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "video" in r.get_json()["error"].lower()


def test_publish_youtube_success(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = sc._upsert_connection("youtube", "Chan", "UC1", " ".join(sc.YT_SCOPES))
    vid = tmp_path / "render.mp4"
    vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    pid = _make_post(db, asset_path=str(vid), caption="Bathroom remodel",
                     hashtags="#remodel #tile")
    monkeypatch.setattr(sc, "_load_creds", lambda p, ref: object())
    captured = {}

    def fake_upload(creds, path, title, desc, tags):
        captured.update(path=path, title=title, tags=tags)
        return {"id": "VID123", "url": "https://youtu.be/VID123"}

    monkeypatch.setattr(sc, "_yt_upload", fake_upload)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["url"] == "https://youtu.be/VID123"
    assert captured["path"] == str(vid)
    assert "remodel" in captured["tags"] and "tile" in captured["tags"]
    # Post marked posted
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, posted_url FROM ahb_social_posts WHERE id=?",
                      (pid,)).fetchone()
    con.close()
    assert row[0] == "posted"
    assert row[1] == "https://youtu.be/VID123"


def test_publish_non_youtube_is_501_with_manual_hint(env):
    c, sc, db = env
    cid = sc._upsert_connection("instagram", "ig", "ig_1", "")
    pid = _make_post(db)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 501
    assert "manual-export" in r.get_json()["manual_export"]


def test_manual_export(env, tmp_path):
    c, sc, db = env
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"x")
    pid = _make_post(db, asset_path=str(vid), caption="cap", hashtags="#h")
    r = c.get(f"/api/ahb/social/posts/{pid}/manual-export")
    assert r.status_code == 200
    j = r.get_json()
    assert j["caption"] == "cap"
    assert j["hashtags"] == "#h"
    assert j["has_asset"] is True
    assert j["asset_filename"] == "a.mp4"
    assert j["bundle_url"].endswith(f"/posts/{pid}/bundle")


def test_disconnect_removes_row_and_token(env, monkeypatch):
    c, sc, db = env
    # Create connection + a token file
    cid = sc._upsert_connection("youtube", "Chan", "UC1", "")
    sc._secure_write(sc._token_path("youtube", "UC1"), '{"token":"t"}')
    assert os.path.exists(sc._token_path("youtube", "UC1"))
    r = c.delete(f"/api/ahb/social/connections/{cid}")
    assert r.status_code == 200
    assert not os.path.exists(sc._token_path("youtube", "UC1"))
    assert c.get("/api/ahb/social/connections").get_json()["items"] == []


def test_feed_youtube(env, monkeypatch):
    c, sc, db = env
    cid = sc._upsert_connection("youtube", "Chan", "UC1", "")
    monkeypatch.setattr(sc, "_load_creds", lambda p, ref: object())
    monkeypatch.setattr(sc, "_yt_recent_uploads", lambda creds, limit: [
        {"id": "v1", "title": "Reel 1", "url": "https://youtu.be/v1",
         "thumbnail": "t", "published_at": "2026-06-01"},
    ])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert items[0]["id"] == "v1"


def test_feed_non_youtube_returns_phase_note(env, monkeypatch):
    c, sc, db = env
    cid = sc._upsert_connection("tiktok", "tt", "tt_1", "")
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200
    assert r.get_json()["items"] == []
    assert "Phase" in r.get_json()["note"]
