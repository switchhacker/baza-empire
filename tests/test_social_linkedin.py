"""Tests for Social Connections — LinkedIn (member + organization).

All LinkedIn network ops are monkeypatched; no credentials or network.
"""
import json
import os
import sqlite3
import sys
import time

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
    social_connect = (sys.modules.get("dashboard.social_connect")
                      or sys.modules.get("social_connect"))
    assert social_connect is not None, "social_connect not loaded"
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


def _li_creds(sc):
    sc._secure_write(sc._platform_creds_path("linkedin"),
                     json.dumps({"client_id": "cid123", "client_secret": "sec"}))


def test_linkedin_registered_as_oauth_platform(env):
    c, sc, _ = env
    assert "linkedin" in sc.PLATFORMS
    assert "linkedin" in sc.OAUTH_PLATFORMS
    j = c.get("/api/ahb/social/connections").get_json()
    assert "linkedin" in j["platforms"]
    assert "linkedin" in j["oauth_platforms"]
    creds = c.get("/api/ahb/social/connections/app-creds").get_json()["configured"]
    assert creds["linkedin"] is False


def test_li_client_creds_missing_raises(env):
    c, sc, _ = env
    with pytest.raises(RuntimeError):
        sc._li_client_creds()


def test_li_build_authorize_url(env):
    c, sc, _ = env
    _li_creds(sc)
    url = sc._li_build_authorize_url("st42")
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization")
    assert "client_id=cid123" in url
    assert "state=st42" in url
    assert "response_type=code" in url
    assert "w_member_social" in url  # scopes present (space-encoded)


def test_li_auth_start_needs_creds(env):
    c, sc, _ = env
    r = c.post("/api/ahb/social/connections/linkedin/auth/start", json={})
    assert r.status_code == 400
    assert "LinkedIn" in r.get_json()["error"]


def test_li_auth_start_returns_url(env):
    c, sc, _ = env
    _li_creds(sc)
    r = c.post("/api/ahb/social/connections/linkedin/auth/start", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["ok"] is True
    assert "linkedin.com" in j["auth_url"]
    assert j["flow_id"]


def test_li_auth_finish_returns_choices(env, monkeypatch):
    c, sc, _ = env
    _li_creds(sc)
    monkeypatch.setattr(sc, "_li_exchange_token", lambda code: {"access_token": "tok"})
    monkeypatch.setattr(sc, "_li_userinfo", lambda t: {
        "person_urn": "urn:li:person:abc", "name": "Serge T", "email": "s@x.z"})
    monkeypatch.setattr(sc, "_li_list_orgs", lambda t: [
        {"org_urn": "urn:li:organization:99", "name": "All Home Building"}])
    start = c.post("/api/ahb/social/connections/linkedin/auth/start",
                   json={}).get_json()
    fin = c.post("/api/ahb/social/connections/linkedin/auth/finish",
                 json={"flow_id": start["flow_id"],
                       "redirect_url": "http://localhost:8888/cb?code=thecode&state=x"})
    assert fin.status_code == 200, fin.get_data(as_text=True)
    j = fin.get_json()
    assert j["ok"] is True and j["ref"]
    assert j["member"]["person_urn"] == "urn:li:person:abc"
    assert j["orgs"][0]["org_urn"] == "urn:li:organization:99"
    # No connection created until the user picks a target.
    assert c.get("/api/ahb/social/connections").get_json()["items"] == []


def _seed_li_session(sc):
    ref = sc._flow_id()
    sc._linkedin_sessions[ref] = {
        "token": "tok",
        "member": {"person_urn": "urn:li:person:abc", "name": "Serge T"},
        "orgs": [{"org_urn": "urn:li:organization:99", "name": "AHB"}],
        "created": time.time()}
    return ref


def test_li_add_member(env):
    c, sc, _ = env
    ref = _seed_li_session(sc)
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": ref, "target": "member"})
    assert r.status_code == 200, r.get_data(as_text=True)
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "linkedin"
    assert items[0]["meta"]["person_urn"] == "urn:li:person:abc"
    tp = sc._token_path("linkedin", "urn:li:person:abc")
    assert os.path.exists(tp)
    assert oct(os.stat(tp).st_mode & 0o777) == "0o600"
    assert "token" not in json.dumps(items)  # never leak tokens


def test_li_add_org(env):
    c, sc, _ = env
    ref = _seed_li_session(sc)
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": ref, "target": "urn:li:organization:99"})
    assert r.status_code == 200, r.get_data(as_text=True)
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert items[0]["meta"]["org_urn"] == "urn:li:organization:99"
    assert items[0]["account_label"] == "AHB"


def test_li_add_expired_session(env):
    c, sc, _ = env
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": "nope", "target": "member"})
    assert r.status_code == 400


def _seed_li_conn(sc, urn, meta, label="LI"):
    cid = sc._upsert_connection("linkedin", label, urn, " ".join(sc.LI_SCOPES))
    sc._set_conn_meta(cid, meta)
    sc._secure_write(sc._token_path("linkedin", urn),
                     json.dumps({"access_token": "tok"}))
    return cid


def test_li_publish_member_image(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    img = tmp_path / "cover.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img),
                     caption="New bath", hashtags="#remodel")
    monkeypatch.setattr(sc, "_li_register_image",
                        lambda t, o: {"upload_url": "U", "asset_urn": "urn:li:image:1"})
    cap = {}
    monkeypatch.setattr(sc, "_li_put_bytes",
                        lambda u, p: cap.update(put=p) or "etag")
    monkeypatch.setattr(sc, "_li_create_post",
                        lambda t, a, c2, m, v: {"id": "P1",
                        "url": "https://www.linkedin.com/feed/update/P1"})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["url"].endswith("/P1")
    assert cap["put"] == str(img)
    con = sqlite3.connect(db)
    row = con.execute("SELECT status FROM ahb_social_posts WHERE id=?",
                      (pid,)).fetchone()
    con.close()
    assert row[0] == "posted"


def test_li_publish_org_video(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    vid = tmp_path / "r.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    pid = _make_post(db, asset_path=str(vid), caption="Job", hashtags="#build")
    monkeypatch.setattr(sc, "_li_register_video",
                        lambda t, o, s: {"upload_url": "U", "asset_urn": "urn:li:video:2"})
    monkeypatch.setattr(sc, "_li_put_bytes", lambda u, p: "etag")
    monkeypatch.setattr(sc, "_li_finalize_video", lambda t, a, e: None)
    seen = {}
    monkeypatch.setattr(sc, "_li_create_post",
                        lambda t, a, c2, m, v: seen.update(author=a, media=m, video=v)
                        or {"id": "P2", "url": "https://x/P2"})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert seen["author"] == "urn:li:organization:99"
    assert seen["media"] == "urn:li:video:2" and seen["video"] is True


def test_li_publish_no_asset(env, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    pid = _make_post(db, asset_path=None, cover_path=None)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "render" in r.get_json()["error"].lower()


def test_li_publish_org_pending_approval(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    img = tmp_path / "c.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img))

    def boom(t, o):
        resp = type("R", (), {"status_code": 403})()
        e = Exception("403 Forbidden"); e.response = resp
        raise e
    monkeypatch.setattr(sc, "_li_register_image", boom)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 403
    assert "Community Management" in r.get_json()["error"]
    assert "manual_export" in r.get_json()


def test_li_feed_org(env, monkeypatch):
    c, sc, _ = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    monkeypatch.setattr(sc, "_li_org_feed", lambda t, urn, lim: [
        {"id": "P1", "title": "hello", "url": "https://x/P1",
         "published_at": "1", "thumbnail": ""}])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["items"][0]["id"] == "P1"


def test_li_feed_member_unavailable(env):
    c, sc, _ = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 502
    assert "not available" in r.get_json()["error"].lower()


def test_li_publish_register_failure(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    img = tmp_path / "c.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img))
    monkeypatch.setattr(sc, "_li_register_image",
                        lambda t, o: {"upload_url": "", "asset_urn": ""})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 502
    assert "registration failed" in r.get_json()["error"].lower()


def test_li_publish_missing_author_urn(env, tmp_path):
    c, sc, db = env
    # connection with empty account_ref and no meta → no author URN resolvable
    cid = sc._upsert_connection("linkedin", "broken", "", " ".join(sc.LI_SCOPES))
    img = tmp_path / "c.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img))
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "author" in r.get_json()["error"].lower()
