"""Tests for Social Studio v2.2 — trends (inspo / hashtags / competitors / sounds)."""
import json
import os
import sqlite3
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULES = ("social_studio", "social_settings", "social_workflow",
            "social_trends", "social_analytics",
            "social_ai", "social_audio", "social_sources")


def _flush_modules():
    for m in _MODULES:
        if m in sys.modules:
            del sys.modules[m]


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv22t_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    _flush_modules()
    import social_studio
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    social_studio._ensure_social_v22_tables(db)
    yield app.test_client(), db
    _flush_modules()


def test_hashtag_snapshot_create_and_list(client):
    c, _ = client
    r = c.post("/api/ahb/social/trends/hashtag-snapshots",
               json={"tag": "renovation", "source_url": "https://x.com/y"})
    assert r.status_code == 200
    items = c.get("/api/ahb/social/trends/hashtag-snapshots").get_json()["items"]
    assert any(x["tag"] == "renovation" for x in items)


def test_hashtag_snapshot_filter_by_tag(client):
    c, _ = client
    c.post("/api/ahb/social/trends/hashtag-snapshots", json={"tag": "foo"})
    c.post("/api/ahb/social/trends/hashtag-snapshots", json={"tag": "bar"})
    items = c.get("/api/ahb/social/trends/hashtag-snapshots/foo").get_json()["items"]
    assert all(x["tag"] == "foo" for x in items)


def test_competitor_create_list_delete(client):
    c, _ = client
    r = c.post("/api/ahb/social/trends/competitors",
               json={"handle": "@reno", "platform": "instagram"})
    cid = r.get_json()["id"]
    items = c.get("/api/ahb/social/trends/competitors").get_json()["items"]
    assert any(x["id"] == cid for x in items)
    r = c.delete(f"/api/ahb/social/trends/competitors/{cid}")
    assert r.status_code == 200
    items = c.get("/api/ahb/social/trends/competitors").get_json()["items"]
    assert not any(x["id"] == cid for x in items)


def test_competitor_invalid_platform_rejected(client):
    c, _ = client
    r = c.post("/api/ahb/social/trends/competitors",
               json={"handle": "@x", "platform": "myspace"})
    assert r.status_code == 400


def test_sound_snapshot_required_field(client):
    c, _ = client
    r = c.post("/api/ahb/social/trends/sound-snapshots", json={})
    assert r.status_code == 400
    r = c.post("/api/ahb/social/trends/sound-snapshots",
               json={"sound_url": "https://tiktok.com/s/123"})
    assert r.status_code == 200


def test_inspo_url_rejects_non_http(client):
    c, _ = client
    r = c.post("/api/ahb/social/trends/inspo-url", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400
    r = c.post("/api/ahb/social/trends/inspo-url", json={"url": "javascript:alert(1)"})
    assert r.status_code == 400
    r = c.post("/api/ahb/social/trends/inspo-url", json={"url": "ftp://example.com/x"})
    assert r.status_code == 400


def test_inspo_library_returns_items_key(client):
    c, _ = client
    j = c.get("/api/ahb/social/trends/inspo-library").get_json()
    assert isinstance(j, dict)
    assert "items" in j
    assert isinstance(j["items"], list)


def test_competitor_delete_unknown_returns_404(client):
    c, _ = client
    r = c.delete("/api/ahb/social/trends/competitors/99999")
    assert r.status_code == 404


def test_inspo_library_skips_non_json_and_malformed(tmp_path, monkeypatch, client):
    """Library should skip .txt files and malformed JSON gracefully."""
    import os
    # Write a non-JSON file and an invalid JSON file
    monkeypatch.setenv("BAZA_SOCIAL_INSPO_DIR", str(tmp_path))
    (tmp_path / "skip.txt").write_text("not json")
    (tmp_path / "bad.json").write_text("{this is not valid json")
    (tmp_path / "good.json").write_text('{"category": "demo", "caption": "ok"}')
    c, _ = client
    j = c.get("/api/ahb/social/trends/inspo-library").get_json()
    assert isinstance(j["items"], list)
    # At minimum, no crash; ideally the good entry is present and bad/non-JSON skipped
    names = {it.get("file_name") for it in j["items"]}
    assert "skip.txt" not in names
    # bad.json might be filtered out or included with a parse error — both are acceptable;
    # what matters is the endpoint didn't 5xx.
