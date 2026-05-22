import json
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="ss_bp_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    for m in ("social_studio", "social_settings"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    # Seed a couple of media-like rows for the source picker
    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE image_captions (
            id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
            caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
        )""")
        con.execute("INSERT INTO image_captions VALUES (1,42,'a.jpg','x','work','ok','2026-05-22')")
        con.commit()
    finally:
        con.close()

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client()
    for m in ("social_studio", "social_settings"):
        if m in sys.modules:
            del sys.modules[m]


def test_presets_create_and_list(client):
    r = client.post("/api/ahb/social/presets", json={"name": "Test Preset"})
    assert r.status_code == 200
    pid = r.get_json()["id"]
    r2 = client.get("/api/ahb/social/presets")
    items = r2.get_json()["items"]
    assert any(p["id"] == pid for p in items)


def test_presets_update_and_delete(client):
    pid = client.post("/api/ahb/social/presets", json={"name": "Tmp"}).get_json()["id"]
    r = client.put(f"/api/ahb/social/presets/{pid}", json={"tone": "hype", "active": 0})
    assert r.status_code == 200
    item = client.get("/api/ahb/social/presets").get_json()["items"]
    assert any(p["id"] == pid and p["tone"] == "hype" for p in item)
    r = client.delete(f"/api/ahb/social/presets/{pid}")
    assert r.status_code == 200


def test_sources_returns_media(client):
    r = client.get("/api/ahb/social/sources?project_id=42")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert any(i["sub_path"] == "a.jpg" for i in items)


def test_preset_create_requires_name(client):
    r = client.post("/api/ahb/social/presets", json={})
    assert r.status_code == 400


def test_preset_update_unknown_field_ignored(client):
    pid = client.post("/api/ahb/social/presets", json={"name": "T"}).get_json()["id"]
    r = client.put(f"/api/ahb/social/presets/{pid}", json={"nope": "x"})
    # No writable fields → 400 per the spec
    assert r.status_code == 400


def test_preset_update_mixed_valid_invalid_fields(client):
    pid = client.post("/api/ahb/social/presets", json={"name": "T"}).get_json()["id"]
    r = client.put(f"/api/ahb/social/presets/{pid}", json={"tone": "hype", "unknown_field": "x"})
    assert r.status_code == 200
    item = client.get("/api/ahb/social/presets").get_json()["items"]
    assert any(p["id"] == pid and p["tone"] == "hype" for p in item)


def test_posts_create_and_list(client):
    r = client.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16",
        "source_media_ids": [1], "caption": "hi"
    })
    assert r.status_code == 200
    pid = r.get_json()["id"]
    items = client.get("/api/ahb/social/posts").get_json()["items"]
    assert any(p["id"] == pid and p["caption"] == "hi" for p in items)


def test_posts_patch_status(client):
    pid = client.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16", "source_media_ids": [1]
    }).get_json()["id"]
    r = client.patch(f"/api/ahb/social/posts/{pid}", json={"status": "approved"})
    assert r.status_code == 200
    items = client.get("/api/ahb/social/posts?status=approved").get_json()["items"]
    assert any(p["id"] == pid for p in items)


def test_posts_filter_invalid_status(client):
    r = client.patch("/api/ahb/social/posts/9999", json={"status": "bogus"})
    assert r.status_code == 400


def test_jobs_get_404(client):
    r = client.get("/api/ahb/social/jobs/9999")
    assert r.status_code == 404


def test_posts_create_invalid_platform(client):
    r = client.post("/api/ahb/social/posts", json={
        "platform": "invalid_platform", "variant": "9x16"
    })
    assert r.status_code == 400
