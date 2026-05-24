"""Tests for Social Studio v2.2 — schema migration smoke + workflow routes."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_MODULES = (
    "social_studio", "social_settings", "social_workflow",
    "social_trends", "social_analytics",
    "social_ai", "social_audio", "social_sources",
)


def _flush_modules():
    for m in _MODULES:
        if m in sys.modules:
            del sys.modules[m]


@pytest.fixture()
def db_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv22_")
    p = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    _flush_modules()
    yield p
    _flush_modules()


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv22w_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    monkeypatch.setenv("BAZA_SOCIAL_EDITS_DIR", os.path.join(d, "edits"))
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    _flush_modules()
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    social_studio._ensure_social_v22_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    _flush_modules()


def test_v22_tables_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_v2_tables(db_path)
    social_studio._ensure_social_v22_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["ahb_social_post_templates", "ahb_social_tags",
                  "ahb_social_post_tags", "ahb_social_hashtag_snapshots",
                  "ahb_social_competitors", "ahb_social_sound_snapshots",
                  "ahb_social_analytics", "ahb_social_approval_events",
                  "ahb_social_post_versions"]:
            assert t in names, f"missing table: {t}"
        try:
            con.execute("SELECT count(*) FROM ahb_social_posts_fts")
            fts_ok = True
        except sqlite3.OperationalError:
            fts_ok = False
        preset_cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_presets)")}
        assert "requires_review" in preset_cols
        assert "schedule_dow" in preset_cols
        assert "schedule_time" in preset_cols
    finally:
        con.close()


def test_template_create_and_list(client):
    c, _ = client
    pid = c.post("/api/ahb/social/templates", json={
        "name": "AHB launch",
        "caption_template": "New project: {{project_name}}!",
        "hashtag_set": "#ahbco #renovation",
        "platform_targets": ["ig_reel"],
    }).get_json()["id"]
    items = c.get("/api/ahb/social/templates").get_json()["items"]
    assert any(t["id"] == pid and t["name"] == "AHB launch" for t in items)


def test_template_apply_returns_draft(client):
    c, _ = client
    tid = c.post("/api/ahb/social/templates", json={
        "name": "T", "caption_template": "Hello {{project_name}}",
        "hashtag_set": "#hi",
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/templates/{tid}/apply",
               json={"variables": {"project_name": "Brooklyn Reno"}})
    assert r.status_code == 200
    j = r.get_json()
    assert "Brooklyn Reno" in j["caption"]
