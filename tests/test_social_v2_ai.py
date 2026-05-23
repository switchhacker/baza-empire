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
