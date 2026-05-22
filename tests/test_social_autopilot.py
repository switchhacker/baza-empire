import os
import sqlite3
import sys
import tempfile
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="ap_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE image_captions (
            id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
            caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
        )""")
        con.execute(
            "INSERT INTO image_captions VALUES (1,42,'a.jpg','wall','work','ok',?)",
            (datetime.utcnow().isoformat(),),
        )
        con.commit()
    finally:
        con.close()
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_studio
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]


def test_autopilot_tick_master_off_is_noop(client):
    c, ss = client
    r = c.post("/api/ahb/social/autopilot/tick")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ran"] == 0


def test_autopilot_toggle_persists(client):
    c, ss = client
    c.post("/api/ahb/social/autopilot/toggle", json={"on": True})
    r = c.get("/api/ahb/social/autopilot/status").get_json()
    assert r["master"] is True


def test_autopilot_tick_with_master_on_and_due_preset(client, monkeypatch):
    c, ss = client
    c.put("/api/ahb/social/settings", json={"autopilot_master": True})
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        con.execute("""INSERT INTO ahb_social_presets
            (name, cadence, active, max_per_day, next_run_at, platform_targets, source_filter)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("T", "daily", 1, 5, (datetime.utcnow() - timedelta(hours=1)).isoformat(),
             json.dumps(["ig_feed_square"]), json.dumps({"project_ids": [42]})))
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(ss, "_call_ollama_chat", lambda *a, **kw: "test caption")
    r = c.post("/api/ahb/social/autopilot/tick")
    j = r.get_json()
    assert j["ran"] >= 1
    posts = c.get("/api/ahb/social/posts").get_json()["items"]
    assert len(posts) >= 1
    assert posts[0]["status"] == "pending_review"


def test_preset_run_returns_post_id(client, monkeypatch):
    c, ss = client
    pid = c.post("/api/ahb/social/presets", json={
        "name": "R", "cadence": "off",
        "source_filter": {"project_ids": [42]},
    }).get_json()["id"]
    monkeypatch.setattr(ss, "_call_ollama_chat", lambda *a, **kw: "manual caption")
    r = c.post(f"/api/ahb/social/presets/{pid}/run")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("post_id") is not None


def test_preset_run_no_sources(client):
    c, ss = client
    pid = c.post("/api/ahb/social/presets", json={
        "name": "Empty", "cadence": "off",
        "source_filter": {"project_ids": [99999]},  # no matches
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/presets/{pid}/run")
    assert r.status_code == 400


def test_telegram_endpoint_502_when_bridge_down(client):
    c, ss = client
    pid = c.post("/api/ahb/social/posts", json={
        "platform": "tiktok", "variant": "9x16", "source_media_ids": [1]
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/posts/{pid}/telegram")
    # Specter bridge is not running in tests — expect 502
    assert r.status_code == 502
