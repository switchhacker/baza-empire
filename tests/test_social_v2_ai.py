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


def test_translate_all_400_no_caption(client):
    r = client.post("/api/ahb/social/ai/translate-all", json={})
    assert r.status_code == 400


def test_translate_all_returns_per_lang(client, monkeypatch):
    import social_studio as ss
    calls = []

    def fake_chat(model, system, user, temperature=0.7):
        calls.append((system, user))
        return f"<<{user[:40]}>>"

    monkeypatch.setattr(ss, "_call_ollama_chat", fake_chat)
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post(
        "/api/ahb/social/ai/translate-all",
        json={"caption": "Hello world", "hashtags": "#tag", "targets": ["es", "fr"]},
    )
    assert r.status_code == 200
    j = r.get_json()
    assert set(j["translations"].keys()) == {"es", "fr"}
    assert j["translations"]["es"]["caption"].startswith("<<")
    assert j["translations"]["es"]["hashtags"].startswith("<<")
    # 2 langs × (caption + hashtags) = 4 model calls
    assert len(calls) == 4
    # Confirm system prompt names the target language
    assert any("into es" in c[0] for c in calls)
    assert any("into fr" in c[0] for c in calls)


def test_translate_all_caps_at_5(client, monkeypatch):
    import social_studio as ss
    monkeypatch.setattr(ss, "_call_ollama_chat", lambda *a, **k: "x")
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post(
        "/api/ahb/social/ai/translate-all",
        json={"caption": "hi", "targets": ["es", "fr", "de", "it", "pt", "zh", "ja"]},
    )
    assert r.status_code == 200
    assert len(r.get_json()["targets"]) == 5


def test_translate_all_defaults_from_settings(client, monkeypatch):
    import sys
    import social_studio as ss
    # social_studio loads social_settings via `from dashboard import …` so the
    # active module instance is registered under the qualified name. Patch
    # whichever instance Python actually resolves to at call time.
    settings_mod = sys.modules.get("dashboard.social_settings") or sys.modules.get("social_settings")
    assert settings_mod is not None, "social_settings not loaded"
    monkeypatch.setattr(settings_mod, "load_settings",
                        lambda: {"translation_targets": ["fr", "de"]})
    monkeypatch.setattr(ss, "_call_ollama_chat", lambda *a, **k: "x")
    monkeypatch.setattr(ss, "_pick_copy_model", lambda: "fake")
    r = client.post("/api/ahb/social/ai/translate-all", json={"caption": "hi"})
    assert r.status_code == 200
    assert r.get_json()["targets"] == ["fr", "de"]


def test_predict_returns_view_range(client):
    r = client.post(
        "/api/ahb/social/ai/predict",
        json={
            "caption": "How to caulk tile cleanly in 30 seconds — step by step.",
            "hashtags": "#tile #renovation #howto #home #diy #pro #ny #contractor",
            "hook": "Stop using the wrong caulk.",
            "platform": "tiktok",
        },
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j["view_range"]["low"] < j["view_range"]["mid"] < j["view_range"]["high"]
    assert j["confidence"] in ("low", "medium", "high")
    assert isinstance(j["improvements"], list) and len(j["improvements"]) <= 3


def test_predict_flags_missing_hook(client):
    r = client.post(
        "/api/ahb/social/ai/predict",
        json={
            "caption": "Tile caulk tutorial.",
            "hashtags": "#tile #diy",
            "hook": "",  # missing
            "platform": "ig_reel",
        },
    )
    j = r.get_json()
    assert any("hook" in i.lower() for i in j["improvements"])


def test_best_times_industry_defaults(client):
    r = client.get("/api/ahb/social/best-times?platform=tiktok")
    assert r.status_code == 200
    j = r.get_json()
    assert j["platform"] == "tiktok"
    assert j["source"] == "industry_defaults"
    assert len(j["slots"]) == 7  # 7-day grid
    for s in j["slots"]:
        assert 0 <= s["day_of_week"] <= 6
        assert 0 <= s["hour"] <= 23


def test_best_times_unknown_platform_falls_back(client):
    r = client.get("/api/ahb/social/best-times?platform=mystery")
    assert r.status_code == 200
    assert len(r.get_json()["slots"]) == 7
