"""Tests for the AHB123 public profile-link directory. No network."""
import os
import sqlite3
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    sys.modules.pop("profile_links", None)
    import profile_links
    profile_links._ensure_tables(db)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(profile_links.profile_bp)
    yield app.test_client(), profile_links, db
    sys.modules.pop("profile_links", None)


def test_list_empty(env):
    c, pl, _ = env
    r = c.get("/api/ahb/profile-links")
    assert r.status_code == 200
    assert r.get_json()["items"] == []


def test_table_exists(env):
    c, pl, db = env
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "ahb_profile_links" in names


def test_create_normalizes_scheme(env):
    c, pl, db = env
    r = c.post("/api/ahb/profile-links",
               json={"platform": "thumbtack", "label": "Thumbtack",
                     "url": "thumbtack.com/ahb", "icon": "🛠️"})
    assert r.status_code == 200, r.get_data(as_text=True)
    row = r.get_json()
    assert row["url"] == "https://thumbtack.com/ahb"
    assert row["platform"] == "thumbtack" and row["visible"] == 1


def test_create_rejects_non_http_scheme(env):
    c, pl, db = env
    r = c.post("/api/ahb/profile-links",
               json={"platform": "x", "url": "javascript:alert(1)"})
    assert r.status_code == 400
    assert "http" in r.get_json()["error"].lower()


def test_create_requires_platform_and_url(env):
    c, pl, db = env
    assert c.post("/api/ahb/profile-links", json={"url": "x.com"}).status_code == 400
    assert c.post("/api/ahb/profile-links", json={"platform": "x"}).status_code == 400


def _seed(c, **over):
    body = {"platform": over.get("platform", "linkedin"),
            "label": over.get("label", "LinkedIn"),
            "url": over.get("url", "https://linkedin.com/company/ahb"),
            "icon": over.get("icon", "💼")}
    return c.post("/api/ahb/profile-links", json=body).get_json()["id"]


def test_update_fields(env):
    c, pl, db = env
    lid = _seed(c)
    r = c.put(f"/api/ahb/profile-links/{lid}",
              json={"label": "AHB LinkedIn", "visible": False, "url": "x.com/new"})
    assert r.status_code == 200
    con = sqlite3.connect(db)
    row = con.execute("SELECT label, visible, url FROM ahb_profile_links WHERE id=?",
                      (lid,)).fetchone()
    con.close()
    assert row[0] == "AHB LinkedIn" and row[1] == 0
    assert row[2] == "https://x.com/new"


def test_update_unknown_is_404(env):
    c, pl, db = env
    assert c.put("/api/ahb/profile-links/9999", json={"label": "z"}).status_code == 404


def test_update_rejects_bad_url(env):
    c, pl, db = env
    lid = _seed(c)
    assert c.put(f"/api/ahb/profile-links/{lid}",
                 json={"url": "ftp://x"}).status_code == 400


def test_delete(env):
    c, pl, db = env
    lid = _seed(c)
    assert c.delete(f"/api/ahb/profile-links/{lid}").status_code == 200
    assert c.delete(f"/api/ahb/profile-links/{lid}").status_code == 404
    assert c.get("/api/ahb/profile-links").get_json()["items"] == []


def test_public_only_visible_ordered_minimal(env):
    c, pl, db = env
    a = _seed(c, platform="linkedin", url="https://lnkd.in/ahb")
    b = _seed(c, platform="thumbtack", url="https://thumbtack.com/ahb")
    # hide b, order a after via display_order
    c.put(f"/api/ahb/profile-links/{b}", json={"visible": False})
    c.put(f"/api/ahb/profile-links/{a}", json={"display_order": 5})
    r = c.get("/api/ahb/profile-links/public")
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert len(items) == 1                       # b hidden
    assert items[0]["platform"] == "linkedin"
    assert set(items[0].keys()) == {"platform", "label", "url", "icon"}  # no id/visible


def test_app_registers_profile_bp():
    import importlib
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    app_mod = importlib.import_module("app")
    rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
    assert "/api/ahb/profile-links" in rules
    assert "/api/ahb/profile-links/public" in rules
