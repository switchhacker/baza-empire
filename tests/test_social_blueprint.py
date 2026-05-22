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


def test_ai_caption(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: "Built like a tank.\nFraming this week. #ahbco")
    r = client.post("/api/ahb/social/ai/caption", json={
        "source_ids": [1], "platform": "ig_reel", "tone": "pro", "length": "short"
    })
    assert r.status_code == 200
    assert "tank" in r.get_json()["caption"].lower()


def test_ai_hashtags_parses_json_array(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '["#brooklyn", "#renovation", "#ahbco"]')
    r = client.post("/api/ahb/social/ai/hashtags", json={
        "caption": "framing day", "platform": "ig_reel"
    })
    assert r.status_code == 200
    tags = r.get_json()["hashtags"]
    assert "#renovation" in tags


def test_ai_hooks_returns_3(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '["Hook A","Hook B","Hook C"]')
    r = client.post("/api/ahb/social/ai/hooks", json={"source_ids": [1], "n": 3})
    assert r.status_code == 200
    assert len(r.get_json()["hooks"]) == 3


def test_ai_score_returns_score_and_notes(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '{"score": 82, "notes": "Strong hook, weak CTA."}')
    r = client.post("/api/ahb/social/ai/score", json={
        "caption": "x", "hashtags": "#x", "platform": "ig_reel"
    })
    assert r.status_code == 200
    j = r.get_json()
    assert j["score"] == 82 and "CTA" in j["notes"]


def test_extract_json_array_handles_nested(client):
    import social_studio
    assert social_studio._extract_json_array('[["a","b"],["c"]]') == [["a","b"],["c"]]


def test_extract_json_array_handles_code_fence(client):
    import social_studio
    assert social_studio._extract_json_array('```json\n["x","y"]\n```') == ["x", "y"]


def test_ai_caption_returns_empty_on_ollama_failure(client, monkeypatch):
    import social_studio
    def boom(*a, **kw):
        raise ConnectionError("Ollama down")
    # Replace the inner urlopen call by patching the wrapper itself
    monkeypatch.setattr(social_studio.urllib.request, "urlopen", boom)
    r = client.post("/api/ahb/social/ai/caption", json={"source_ids": [], "platform": "ig_reel"})
    assert r.status_code == 200
    assert r.get_json()["caption"] == ""


def test_ai_hashtags_dedupes(client, monkeypatch):
    import social_studio
    monkeypatch.setattr(social_studio, "_call_ollama_chat",
                        lambda *a, **kw: '["#ahbco", "#ahbco", "#renovation"]')
    r = client.post("/api/ahb/social/ai/hashtags",
                    json={"caption": "x", "platform": "ig_reel"})
    tags = r.get_json()["hashtags"]
    assert tags.count("#ahbco") == 1
    assert "#renovation" in tags
