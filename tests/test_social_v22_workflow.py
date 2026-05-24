"""Tests for Social Studio v2.2 — schema migration smoke."""
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def db_path(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv22_")
    p = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_workflow",
              "social_trends", "social_analytics",
              "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]
    yield p
    for m in ("social_studio", "social_settings", "social_workflow",
              "social_trends", "social_analytics",
              "social_ai", "social_audio", "social_sources"):
        if m in sys.modules:
            del sys.modules[m]


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
