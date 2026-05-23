"""Tests for Social Studio v2.1 — schema migration smoke."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def db_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv21_")
    p = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    yield p
    for m in ("social_studio", "social_settings", "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


def test_v2_1_tables_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_v2_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ahb_social_music_library" in names
        cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_posts)")}
        assert "translations" in cols
        assert "music_id" in cols
        assert "voiceover_path" in cols
        assert "subtitles_path" in cols
        assert "lut_name" in cols
    finally:
        con.close()


def test_v2_1_blueprint_imports_clean(db_path):
    import social_ai, social_audio, social_sources
    assert hasattr(social_ai, "register")
    assert hasattr(social_audio, "register")
    assert hasattr(social_sources, "register")


@pytest.fixture()
def client(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_v2_tables(db_path)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    return app.test_client()


def test_hook_pattern_rejects_unknown(client):
    r = client.post("/api/ahb/social/ai/hook", json={"pattern": "garbage"})
    assert r.status_code == 400
    body = r.get_json()
    assert "patterns" in body
    assert "curiosity_gap" in body["patterns"]


def test_hook_pattern_calls_ollama(client, monkeypatch):
    import social_studio as ss
    calls = {}

    def fake_chat(model, system, user, temperature=0.7):
        calls["user"] = user
        calls["system"] = system
        return '["hook one", "hook two", "hook three"]'

    monkeypatch.setattr(ss, "_call_ollama_chat", fake_chat)
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake-model")
    r = client.post(
        "/api/ahb/social/ai/hook",
        json={"pattern": "contrarian", "n": 3, "source_ids": []},
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j["pattern"] == "contrarian"
    assert j["hooks"] == ["hook one", "hook two", "hook three"]
    assert "Pattern: contrarian" in calls["user"]


def test_hook_pattern_n_clamped(client, monkeypatch):
    import social_studio as ss
    monkeypatch.setattr(ss, "_call_ollama_chat",
                        lambda *a, **k: '["a","b","c","d","e","f","g","h","i","j"]')
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post("/api/ahb/social/ai/hook", json={"pattern": "personal", "n": 100})
    assert r.status_code == 200
    assert len(r.get_json()["hooks"]) <= 8  # clamped to 8


def test_cta_400_no_caption(client):
    r = client.post("/api/ahb/social/ai/cta", json={})
    assert r.status_code == 400
    assert "caption required" in r.get_json()["error"]


def test_cta_returns_3(client, monkeypatch):
    import social_studio as ss
    monkeypatch.setattr(ss, "_call_ollama_chat",
                        lambda *a, **k: '["save this", "DM tile", "tag a friend"]')
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post("/api/ahb/social/ai/cta",
                    json={"caption": "How to caulk tile", "platform": "ig_reel"})
    assert r.status_code == 200
    assert r.get_json()["ctas"] == ["save this", "DM tile", "tag a friend"]


def test_comment_bait_400_no_caption(client):
    r = client.post("/api/ahb/social/ai/comment-bait", json={})
    assert r.status_code == 400


def test_comment_bait_returns_prompts(client, monkeypatch):
    import social_studio as ss
    monkeypatch.setattr(ss, "_call_ollama_chat",
                        lambda *a, **k: '["q1", "q2", "q3"]')
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post("/api/ahb/social/ai/comment-bait",
                    json={"caption": "test", "platform": "tiktok"})
    assert r.status_code == 200
    assert r.get_json()["prompts"] == ["q1", "q2", "q3"]


def test_voiceover_script_400_no_caption(client):
    r = client.post("/api/ahb/social/ai/voiceover-script", json={})
    assert r.status_code == 400


def test_voiceover_script_returns_text(client, monkeypatch):
    import social_studio as ss
    monkeypatch.setattr(ss, "_call_ollama_chat",
                        lambda *a, **k: "[emphasis: this] is the trick. [pause] Try it.")
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post("/api/ahb/social/ai/voiceover-script",
                    json={"caption": "Caulking trick", "source_ids": []})
    assert r.status_code == 200
    j = r.get_json()
    assert "[emphasis:" in j["script"]
    assert "[pause]" in j["script"]
