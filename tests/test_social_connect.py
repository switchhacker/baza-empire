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


def _make_post(db, asset_path=None, caption="hello", hashtags="#a #b",
               cover_path=None):
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_posts (platform, variant, caption, hashtags, "
            "asset_path, cover_path, source_media_ids, status) VALUES "
            "('tiktok','9x16',?,?,?,?,'[]','draft')",
            (caption, hashtags, asset_path, cover_path),
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


def test_publish_unknown_platform_is_501(env):
    c, sc, db = env
    cid = sc._upsert_connection("threads", "th", "th_1", "")
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


# ── Phase 3: TikTok ─────────────────────────────────────────────────────────
def _seed_tiktok(sc, ref="open_1", privacy_options=None):
    cid = sc._upsert_connection("tiktok", "Creator", ref, "video.publish")
    sc._set_conn_meta(cid, {"open_id": ref,
                            "privacy_options": privacy_options or []})
    sc._secure_write(sc._token_path("tiktok", ref),
                     '{"access_token": "TT_TOKEN", "open_id": "open_1"}')
    return cid


def test_tiktok_token_requires_token(env):
    c, _, _ = env
    assert c.post("/api/ahb/social/connections/tiktok/token",
                  json={}).status_code == 400


def test_tiktok_token_connects(env, monkeypatch):
    c, sc, _ = env
    monkeypatch.setattr(sc, "_tt_creator_info", lambda tok: {
        "creator_nickname": "AHB Co",
        "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]})
    r = c.post("/api/ahb/social/connections/tiktok/token",
               json={"access_token": "TT", "open_id": "open_xyz"})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["account_label"] == "AHB Co"
    assert "PUBLIC_TO_EVERYONE" in j["privacy_options"]
    tp = sc._token_path("tiktok", "open_xyz")
    assert os.path.exists(tp)
    assert oct(os.stat(tp).st_mode & 0o777) == "0o600"
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert any(i["platform"] == "tiktok" for i in items)


def test_publish_tiktok_self_only_draft(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_tiktok(sc, privacy_options=[])  # unaudited → SELF_ONLY only
    vid = tmp_path / "v.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    pid = _make_post(db, asset_path=str(vid), caption="hi", hashtags="#x")
    cap = {}

    def fake_pub(token, url, title, privacy):
        cap.update(token=token, url=url, privacy=privacy)
        return {"publish_id": "PUB1"}

    monkeypatch.setattr(sc, "_tt_publish_video", fake_pub)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["publish_id"] == "PUB1"
    assert j["privacy_level"] == "SELF_ONLY"
    assert "draft" in j["note"].lower()
    assert cap["privacy"] == "SELF_ONLY"
    assert cap["url"].endswith(f"/posts/{pid}/asset")


def test_publish_tiktok_public_when_allowed(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_tiktok(sc, privacy_options=["PUBLIC_TO_EVERYONE", "SELF_ONLY"])
    vid = tmp_path / "v.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    pid = _make_post(db, asset_path=str(vid))
    seen = {}
    monkeypatch.setattr(sc, "_tt_publish_video",
                        lambda token, url, title, privacy: seen.update(privacy=privacy)
                        or {"publish_id": "P2"})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200
    assert seen["privacy"] == "PUBLIC_TO_EVERYONE"


def test_publish_tiktok_requires_video(env, monkeypatch):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_tiktok(sc)
    pid = _make_post(db, asset_path=None)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "video" in r.get_json()["error"].lower()


def test_publish_tiktok_requires_public_base(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "")
    cid = _seed_tiktok(sc)
    vid = tmp_path / "v.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    pid = _make_post(db, asset_path=str(vid))
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "manual_export" in r.get_json()


def test_feed_tiktok(env, monkeypatch):
    c, sc, db = env
    cid = _seed_tiktok(sc)
    monkeypatch.setattr(sc, "_tt_video_list",
                        lambda token, limit: [{"id": "tv1", "title": "clip",
                                               "url": "u", "thumbnail": "t",
                                               "published_at": "0"}])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200
    assert r.get_json()["items"][0]["id"] == "tv1"


# ── Phase 2: Meta (Instagram + Facebook) ───────────────────────────────────
_FAKE_PAGES = [{
    "id": "PAGE1", "name": "All Home Building",
    "access_token": "PAGE_TOKEN_1",
    "instagram_business_account": {"id": "IG1", "username": "ahbco"},
}]


def _seed_meta(sc, platform, ref, meta):
    cid = sc._upsert_connection(platform, "lbl", ref, "")
    sc._set_conn_meta(cid, meta)
    sc._secure_write(sc._token_path(platform, ref),
                     '{"page_token": "PAGE_TOKEN_1"}')
    return cid


def test_connections_list_reports_token_platforms(env):
    c, _, _ = env
    j = c.get("/api/ahb/social/connections").get_json()
    assert "instagram" in j["token_platforms"]
    assert "facebook" in j["token_platforms"]
    assert j["public_base_set"] is False


def test_meta_token_lists_pages_without_leaking_tokens(env, monkeypatch):
    c, sc, _ = env
    monkeypatch.setattr(sc, "_meta_list_pages", lambda tok: _FAKE_PAGES)
    r = c.post("/api/ahb/social/connections/meta/token",
               json={"access_token": "USER_TOKEN"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ref"]
    assert j["pages"][0]["has_instagram"] is True
    assert j["pages"][0]["ig_username"] == "ahbco"
    # page access tokens never leak to the client
    assert "PAGE_TOKEN_1" not in json.dumps(j)


def test_meta_add_creates_facebook_and_instagram(env, monkeypatch):
    c, sc, _ = env
    monkeypatch.setattr(sc, "_meta_list_pages", lambda tok: _FAKE_PAGES)
    ref = c.post("/api/ahb/social/connections/meta/token",
                 json={"access_token": "U"}).get_json()["ref"]
    r = c.post("/api/ahb/social/connections/meta/add",
               json={"ref": ref, "page_id": "PAGE1", "connect_instagram": True})
    assert r.status_code == 200
    created = r.get_json()["created"]
    platforms = {x["platform"] for x in created}
    assert platforms == {"facebook", "instagram"}
    # token files written with 600 perms
    assert oct(os.stat(sc._token_path("facebook", "PAGE1")).st_mode & 0o777) == "0o600"
    assert os.path.exists(sc._token_path("instagram", "IG1"))


def test_meta_add_facebook_only(env, monkeypatch):
    c, sc, _ = env
    monkeypatch.setattr(sc, "_meta_list_pages", lambda tok: _FAKE_PAGES)
    ref = c.post("/api/ahb/social/connections/meta/token",
                 json={"access_token": "U"}).get_json()["ref"]
    r = c.post("/api/ahb/social/connections/meta/add",
               json={"ref": ref, "page_id": "PAGE1", "connect_instagram": False})
    assert [x["platform"] for x in r.get_json()["created"]] == ["facebook"]


def test_meta_add_expired_session(env):
    c, _, _ = env
    r = c.post("/api/ahb/social/connections/meta/add",
               json={"ref": "nope", "page_id": "PAGE1"})
    assert r.status_code == 400


def test_publish_meta_requires_public_base(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "")
    cid = _seed_meta(sc, "facebook", "PAGE1", {"page_id": "PAGE1"})
    img = tmp_path / "c.jpg"; img.write_bytes(b"x")
    pid = _make_post(db, cover_path=str(img))
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "manual_export" in r.get_json()


def test_publish_facebook_photo(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_meta(sc, "facebook", "PAGE1", {"page_id": "PAGE1"})
    img = tmp_path / "c.jpg"; img.write_bytes(b"jpg")
    pid = _make_post(db, cover_path=str(img), caption="hi", hashtags="#x")
    cap = {}

    def fake_photo(page_id, token, image_url, caption):
        cap.update(page_id=page_id, token=token, image_url=image_url)
        return {"id": "FB9", "url": "https://facebook.com/FB9"}

    monkeypatch.setattr(sc, "_meta_publish_photo", fake_photo)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["url"] == "https://facebook.com/FB9"
    assert cap["page_id"] == "PAGE1"
    assert cap["token"] == "PAGE_TOKEN_1"
    assert cap["image_url"] == f"https://ahb123.com/api/ahb/social/posts/{pid}/cover"


def test_publish_instagram_reel(env, monkeypatch, tmp_path):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_meta(sc, "instagram", "IG1", {"ig_user_id": "IG1", "page_id": "PAGE1"})
    vid = tmp_path / "r.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftyp")
    pid = _make_post(db, asset_path=str(vid), caption="reel", hashtags="#a")
    cap = {}

    def fake_ig(ig_id, token, media_url, caption, is_video):
        cap.update(ig_id=ig_id, media_url=media_url, is_video=is_video)
        return {"id": "IGM1", "url": "https://instagram.com/p/IGM1"}

    monkeypatch.setattr(sc, "_meta_ig_publish", fake_ig)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert cap["ig_id"] == "IG1"
    assert cap["is_video"] is True
    assert cap["media_url"].endswith(f"/posts/{pid}/asset")


def test_publish_meta_requires_asset(env, monkeypatch):
    c, sc, db = env
    monkeypatch.setattr(sc, "SOCIAL_PUBLIC_BASE_URL", "https://ahb123.com")
    cid = _seed_meta(sc, "facebook", "PAGE1", {"page_id": "PAGE1"})
    pid = _make_post(db, asset_path=None, cover_path=None)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "render" in r.get_json()["error"].lower()


def test_feed_facebook(env, monkeypatch):
    c, sc, db = env
    cid = _seed_meta(sc, "facebook", "PAGE1", {"page_id": "PAGE1"})
    monkeypatch.setattr(sc, "_meta_page_feed",
                        lambda pid, tok, lim: [{"id": "p1", "title": "post",
                                                "url": "u", "thumbnail": "t",
                                                "published_at": ""}])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200
    assert r.get_json()["items"][0]["id"] == "p1"


def test_feed_instagram(env, monkeypatch):
    c, sc, db = env
    cid = _seed_meta(sc, "instagram", "IG1", {"ig_user_id": "IG1"})
    monkeypatch.setattr(sc, "_meta_ig_media",
                        lambda ig, tok, lim: [{"id": "m1", "title": "cap",
                                               "url": "u", "thumbnail": "t",
                                               "published_at": ""}])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200
    assert r.get_json()["items"][0]["id"] == "m1"


def test_asset_serve(env, tmp_path):
    c, sc, db = env
    vid = tmp_path / "a.mp4"; vid.write_bytes(b"VIDEOBYTES")
    pid = _make_post(db, asset_path=str(vid))
    r = c.get(f"/api/ahb/social/posts/{pid}/asset")
    assert r.status_code == 200
    assert r.data == b"VIDEOBYTES"
    pid2 = _make_post(db, asset_path=None)
    assert c.get(f"/api/ahb/social/posts/{pid2}/asset").status_code == 404
