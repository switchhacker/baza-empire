"""Tests for Social Studio v2.2 — analytics."""
import io
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
    d = tempfile.mkdtemp(prefix="sv22a_")
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


def _seed_post(db, caption="x", hashtags="#a"):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, hashtags) VALUES (1,'ig_reel','A','posted',?,?)", (caption, hashtags))
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    return pid


def test_analytics_get_defaults_when_no_row(client):
    c, db = client
    pid = _seed_post(db)
    j = c.get(f"/api/ahb/social/posts/{pid}/analytics").get_json()
    assert j["post_id"] == pid
    assert j["views"] == 0


def test_analytics_put_upsert(client):
    c, db = client
    pid = _seed_post(db)
    r = c.put(f"/api/ahb/social/posts/{pid}/analytics",
              json={"views": 100, "likes": 10, "comments": 2, "saves": 1, "shares": 0})
    assert r.status_code == 200
    j = c.get(f"/api/ahb/social/posts/{pid}/analytics").get_json()
    assert j["views"] == 100 and j["likes"] == 10


def test_analytics_put_rejects_negative(client):
    c, db = client
    pid = _seed_post(db)
    r = c.put(f"/api/ahb/social/posts/{pid}/analytics", json={"views": -1})
    assert r.status_code == 400


def test_analytics_summary_aggregates(client):
    c, db = client
    p1 = _seed_post(db)
    p2 = _seed_post(db)
    c.put(f"/api/ahb/social/posts/{p1}/analytics", json={"views": 100, "likes": 10})
    c.put(f"/api/ahb/social/posts/{p2}/analytics", json={"views": 200, "likes": 20})
    j = c.get("/api/ahb/social/analytics/summary?window=all").get_json()
    assert j["totals"]["views"] == 300
    assert j["totals"]["likes"] == 30


def test_analytics_heatmap_returns_grid(client):
    c, db = client
    pid = _seed_post(db)
    c.put(f"/api/ahb/social/posts/{pid}/analytics",
          json={"views": 100, "likes": 10, "posted_at": "2026-05-15T14:30:00"})
    j = c.get("/api/ahb/social/analytics/heatmap").get_json()
    # Accept either flat 168 or nested 7x24
    cells = j["cells"]
    if isinstance(cells[0], list):
        assert len(cells) == 7 and len(cells[0]) == 24
    else:
        assert len(cells) == 168


def test_analytics_hashtags_aggregates(client):
    c, db = client
    p1 = _seed_post(db, hashtags="#pool #ahb")
    p2 = _seed_post(db, hashtags="#pool")
    c.put(f"/api/ahb/social/posts/{p1}/analytics", json={"views": 100})
    c.put(f"/api/ahb/social/posts/{p2}/analytics", json={"views": 200})
    j = c.get("/api/ahb/social/analytics/hashtags").get_json()
    items = {it["tag"]: it for it in j["items"]}
    assert items["#pool"]["total_views"] == 300
    assert items["#ahb"]["total_views"] == 100


def test_analytics_csv_import(client):
    c, db = client
    p1 = _seed_post(db)
    p2 = _seed_post(db)
    csv = (
        "post_id,views,likes,comments,saves,shares,posted_at,post_url\n"
        f"{p1},500,50,5,5,5,2026-05-10T12:00:00,https://x.com/a\n"
        f"{p2},1000,100,10,10,10,2026-05-11T12:00:00,\n"
    )
    r = c.post("/api/ahb/social/analytics/import-csv",
               data={"file": (io.BytesIO(csv.encode("utf-8")), "stats.csv")},
               content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200
    assert j["inserted"] == 2
    j1 = c.get(f"/api/ahb/social/posts/{p1}/analytics").get_json()
    assert j1["views"] == 500


def test_csv_import_partial_errors_dont_block_good_rows(client):
    """CSV with one bad row + one good row: good row commits, bad row reports error."""
    c, db = client
    p1 = _seed_post(db)
    csv = (
        "post_id,views,likes,comments,saves,shares,posted_at,post_url\n"
        f"{p1},500,50,5,5,5,2026-05-10T12:00:00,\n"
        "999999,1,1,1,1,1,2026-05-10T12:00:00,\n"  # bad: unknown post_id
        "abc,1,1,1,1,1,2026-05-10T12:00:00,\n"     # bad: non-int post_id
    )
    r = c.post("/api/ahb/social/analytics/import-csv",
               data={"file": (io.BytesIO(csv.encode("utf-8")), "stats.csv")},
               content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200
    assert j["inserted"] == 1
    assert len(j["errors"]) == 2
    # Good row landed
    j1 = c.get(f"/api/ahb/social/posts/{p1}/analytics").get_json()
    assert j1["views"] == 500


def test_analytics_hashtags_case_insensitive_merge(client):
    c, db = client
    p1 = _seed_post(db, hashtags="#Pool #AHB")
    p2 = _seed_post(db, hashtags="#pool")
    c.put(f"/api/ahb/social/posts/{p1}/analytics", json={"views": 100})
    c.put(f"/api/ahb/social/posts/{p2}/analytics", json={"views": 200})
    j = c.get("/api/ahb/social/analytics/hashtags").get_json()
    items = {it["tag"]: it for it in j["items"]}
    # Both #Pool and #pool should aggregate to a single key (lowercase)
    assert "#pool" in items
    assert items["#pool"]["total_views"] == 300


def test_cleanup_list_filters_old_posted_posts(client):
    c, db = client
    import sqlite3
    from datetime import datetime, timedelta
    con = sqlite3.connect(db)
    # Old posted post (180 days ago)
    old = (datetime.utcnow() - timedelta(days=180)).isoformat(timespec="seconds")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, posted_at) VALUES (1,'ig_reel','A','posted','old', ?)", (old,))
    pid_old = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    # Recent posted post (10 days ago)
    recent = (datetime.utcnow() - timedelta(days=10)).isoformat(timespec="seconds")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, posted_at) VALUES (1,'ig_reel','A','posted','recent', ?)", (recent,))
    pid_recent = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    # Draft (not eligible)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','draft')")
    con.commit()
    con.close()
    items = c.get("/api/ahb/social/analytics/cleanup?older_than_days=90").get_json()["items"]
    ids = [it["id"] for it in items]
    assert pid_old in ids
    assert pid_recent not in ids
    assert all(it["status"] == "posted" for it in items)


def test_cleanup_archive_stamps_archived_at(client):
    c, db = client
    import sqlite3
    from datetime import datetime, timedelta
    con = sqlite3.connect(db)
    old = (datetime.utcnow() - timedelta(days=200)).isoformat(timespec="seconds")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, posted_at) VALUES (1,'ig_reel','A','posted','x', ?)", (old,))
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    r = c.post("/api/ahb/social/analytics/cleanup/archive", json={"ids": [pid]})
    assert r.status_code == 200
    assert r.get_json()["archived"] == 1
    con = sqlite3.connect(db)
    a = con.execute("SELECT archived_at FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()[0]
    con.close()
    assert a is not None
    # Already archived: cleanup list excludes
    items = c.get("/api/ahb/social/analytics/cleanup?older_than_days=90").get_json()["items"]
    assert pid not in [it["id"] for it in items]


def test_cleanup_delete_removes_post_and_related(client):
    c, db = client
    tid = c.post("/api/ahb/social/tags", json={"name": "old"}).get_json()["id"]
    import sqlite3
    from datetime import datetime, timedelta
    con = sqlite3.connect(db)
    old = (datetime.utcnow() - timedelta(days=200)).isoformat(timespec="seconds")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, posted_at) VALUES (1,'ig_reel','A','posted','del', ?)", (old,))
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [tid]})
    c.put(f"/api/ahb/social/posts/{pid}/analytics", json={"views": 100})
    r = c.post("/api/ahb/social/analytics/cleanup/delete", json={"ids": [pid]})
    assert r.status_code == 200
    assert r.get_json()["deleted"] == 1
    con = sqlite3.connect(db)
    n_posts = con.execute("SELECT COUNT(*) FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()[0]
    n_tags = con.execute("SELECT COUNT(*) FROM ahb_social_post_tags WHERE post_id=?", (pid,)).fetchone()[0]
    n_an = con.execute("SELECT COUNT(*) FROM ahb_social_analytics WHERE post_id=?", (pid,)).fetchone()[0]
    con.close()
    assert n_posts == 0
    assert n_tags == 0
    assert n_an == 0


def test_cleanup_empty_ids_returns_400(client):
    c, _ = client
    r = c.post("/api/ahb/social/analytics/cleanup/archive", json={"ids": []})
    assert r.status_code == 400
    r = c.post("/api/ahb/social/analytics/cleanup/delete", json={"ids": []})
    assert r.status_code == 400


def test_upsert_analytics_rejects_unknown_column(client):
    """Defense-in-depth: the helper rejects disallowed column names."""
    import social_analytics as sa
    c, db = client
    con = sqlite3.connect(db)
    try:
        try:
            sa._upsert_analytics(con, 1, {"views": 100, "evil": "x"})
            assert False, "expected ValueError"
        except ValueError as e:
            assert "disallowed" in str(e)
    finally:
        con.close()


def test_cleanup_days_zero_rejected(client):
    c, _ = client
    r = c.get("/api/ahb/social/analytics/cleanup?older_than_days=0")
    assert r.status_code == 400
    r = c.get("/api/ahb/social/analytics/cleanup?older_than_days=-5")
    assert r.status_code == 400


def test_cleanup_archive_refuses_unsafe_path(client, tmp_path):
    """A post whose asset_path points outside the allowed roots is skipped, not touched."""
    import sqlite3, os
    from datetime import datetime, timedelta
    c, db = client
    # Create a real file far outside the dashboard tree
    bad = tmp_path / "evil.bin"
    bad.write_bytes(b"x")
    con = sqlite3.connect(db)
    old = (datetime.utcnow() - timedelta(days=200)).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, posted_at, asset_path) "
        "VALUES (1,'ig_reel','A','posted','evil', ?, ?)",
        (old, str(bad)),
    )
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    r = c.post("/api/ahb/social/analytics/cleanup/archive", json={"ids": [pid]})
    j = r.get_json()
    assert r.status_code == 200
    # archived_at should NOT be stamped because move was refused; the row should report an error
    assert any("unsafe" in e.lower() for e in j.get("errors", []))
    # The evil file should still exist
    assert bad.exists()
