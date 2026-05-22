"""Tests for social_studio schema migrations."""
import os
import sqlite3
import sys
import tempfile

import pytest


@pytest.fixture()
def db_path(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="social_db_")
    p = os.path.join(tmp, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", p)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    import importlib
    if "social_studio" in sys.modules:
        del sys.modules["social_studio"]
    yield p
    if "social_studio" in sys.modules:
        del sys.modules["social_studio"]


def test_ensure_social_tables_creates_all_three(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"ahb_social_presets", "ahb_social_posts", "ahb_social_jobs"} <= names
    finally:
        con.close()


def test_ensure_social_tables_is_idempotent(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        posts_cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_posts)")}
        assert "preset_id" in posts_cols
        assert "first_comment" in posts_cols
    finally:
        con.close()


def test_indexes_exist(db_path):
    import social_studio
    social_studio._ensure_social_tables(db_path)
    con = sqlite3.connect(db_path)
    try:
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_social_posts_status" in idx
        assert "idx_social_posts_project" in idx
        assert "idx_social_posts_scheduled" in idx
        assert "idx_social_jobs_status" in idx
    finally:
        con.close()
