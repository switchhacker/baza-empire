"""Tests for Social Studio v2.0 polish phase."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_inter_bold_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Bold.ttf")
    assert os.path.exists(path), f"{path} missing"
    size = os.path.getsize(path)
    assert size > 50_000, f"Inter-Bold.ttf too small ({size} bytes) — still a placeholder?"
    with open(path, "rb") as f:
        head = f.read(4)
    assert head in (b"\x00\x01\x00\x00", b"OTTO", b"true"), f"Not a real TTF: head={head!r}"


def test_inter_regular_is_real_ttf():
    path = os.path.join(REPO_ROOT, "dashboard", "static", "fonts", "Inter-Regular.ttf")
    assert os.path.exists(path)
    assert os.path.getsize(path) > 50_000
    with open(path, "rb") as f:
        assert f.read(4) in (b"\x00\x01\x00\x00", b"OTTO", b"true")


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="sv2_")
    db = os.path.join(d, "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", d)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in ("social_studio", "social_settings", "social_render"):
        if m in sys.modules:
            del sys.modules[m]
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE image_captions (
            id INTEGER PRIMARY KEY, project_id INTEGER, sub_path TEXT,
            caption TEXT, tags TEXT, status TEXT, indexed_at TEXT
        )""")
        con.execute("INSERT INTO image_captions VALUES (1,42,'a.jpg','x','work','ok',?)",
                    (datetime.utcnow().isoformat(),))
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


def test_jobs_pid_column_exists(client):
    c, ss = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(ahb_social_jobs)")}
        assert "pid" in cols
    finally:
        con.close()


def test_render_async_returns_job_id(client, monkeypatch):
    c, ss = client
    monkeypatch.setattr(ss, "_resolve_media_paths", lambda ids: ["/tmp/fake.jpg"])
    pid = c.post("/api/ahb/social/posts", json={
        "platform": "ig_feed_square", "variant": "1x1", "source_media_ids": [1],
    }).get_json()["id"]
    r = c.post(f"/api/ahb/social/posts/{pid}/render-async", json={})
    assert r.status_code == 200
    j = r.get_json()
    assert "job_id" in j
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        row = con.execute("SELECT status, kind, post_id FROM ahb_social_jobs WHERE id=?",
                          (j["job_id"],)).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[1] == "render"
    assert row[2] == pid


def test_job_cancel_marks_cancelled(client, monkeypatch):
    c, ss = client
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_jobs (post_id, kind, status, pid) VALUES (?, ?, ?, ?)",
            (1, "render", "running", None),
        )
        con.commit()
        jid = cur.lastrowid
    finally:
        con.close()
    r = c.delete(f"/api/ahb/social/jobs/{jid}")
    assert r.status_code == 200
    con = sqlite3.connect(os.environ["BAZA_DASHBOARD_DB"])
    try:
        status = con.execute("SELECT status FROM ahb_social_jobs WHERE id=?", (jid,)).fetchone()[0]
    finally:
        con.close()
    assert status == "cancelled"


def test_job_cancel_404_for_unknown(client):
    c, _ = client
    r = c.delete("/api/ahb/social/jobs/999999")
    assert r.status_code == 404
