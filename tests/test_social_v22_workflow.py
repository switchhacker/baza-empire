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
    yield app.test_client(), db
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


def test_template_delete_unknown_returns_404(client):
    c, _ = client
    r = c.delete("/api/ahb/social/templates/999999")
    assert r.status_code == 404


def test_tag_create_list_uniqueness(client):
    c, _ = client
    r = c.post("/api/ahb/social/tags", json={"name": "launch", "color": "#ff0000"})
    assert r.status_code == 200
    tid = r.get_json()["id"]
    items = c.get("/api/ahb/social/tags").get_json()["items"]
    assert any(t["id"] == tid and t["name"] == "launch" for t in items)
    # duplicate name
    r2 = c.post("/api/ahb/social/tags", json={"name": "launch"})
    assert r2.status_code == 409


def test_tag_empty_name_rejected(client):
    c, _ = client
    r = c.post("/api/ahb/social/tags", json={"name": "  "})
    assert r.status_code == 400


def test_tag_assign_and_query(client):
    c, db = client
    # Seed a tag and a post
    tid = c.post("/api/ahb/social/tags", json={"name": "promo"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1, 'ig_reel', 'A', 'draft', 'p1')")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1, 'ig_reel', 'A', 'draft', 'p2')")
    con.commit()
    pid1 = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1 OFFSET 1").fetchone()[0]
    pid2 = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    # Assign tag to pid1 only
    r = c.post(f"/api/ahb/social/posts/{pid1}/tags", json={"tag_ids": [tid]})
    assert r.status_code == 200
    # Query: posts?tag=promo returns only pid1
    items = c.get("/api/ahb/social/posts?tag=promo").get_json()["items"]
    pids = [p["id"] for p in items]
    assert pid1 in pids
    assert pid2 not in pids
    # Per-post tag fetch
    j = c.get(f"/api/ahb/social/posts/{pid1}/tags").get_json()
    assert any(t["id"] == tid for t in j["tags"])


def test_tag_delete_cascades_to_post_tags(client):
    c, db = client
    tid = c.post("/api/ahb/social/tags", json={"name": "tmp"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1, 'ig_reel', 'A', 'draft', 'x')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [tid]})
    # Delete tag
    r = c.delete(f"/api/ahb/social/tags/{tid}")
    assert r.status_code == 200
    # Post-tags row should be gone
    import sqlite3 as s
    con = s.connect(db)
    n = con.execute("SELECT COUNT(*) FROM ahb_social_post_tags WHERE tag_id=?", (tid,)).fetchone()[0]
    con.close()
    assert n == 0


def test_tag_replace_set(client):
    c, db = client
    t1 = c.post("/api/ahb/social/tags", json={"name": "a"}).get_json()["id"]
    t2 = c.post("/api/ahb/social/tags", json={"name": "b"}).get_json()["id"]
    t3 = c.post("/api/ahb/social/tags", json={"name": "c"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1, 'ig_reel', 'A', 'draft', 'x')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    # Assign all three
    c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [t1, t2, t3]})
    # Replace with just t2
    c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [t2]})
    j = c.get(f"/api/ahb/social/posts/{pid}/tags").get_json()
    ids = sorted(t["id"] for t in j["tags"])
    assert ids == [t2]


def test_tag_create_invalid_color_rejected(client):
    c, _ = client
    r = c.post("/api/ahb/social/tags", json={"name": "bad", "color": "red;background:url(x)"})
    assert r.status_code == 400


def test_tag_update_invalid_color_rejected(client):
    c, _ = client
    tid = c.post("/api/ahb/social/tags", json={"name": "ok"}).get_json()["id"]
    r = c.put(f"/api/ahb/social/tags/{tid}", json={"color": "not-a-color"})
    assert r.status_code == 400


def test_post_tags_set_with_stale_ids_only_applies_valid(client):
    c, db = client
    tid = c.post("/api/ahb/social/tags", json={"name": "real"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1, 'ig_reel', 'A', 'draft', 'x')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    r = c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [tid, 999999]})
    j = r.get_json()
    assert r.status_code == 200
    assert j["applied"] == 1


def test_search_q_finds_fts_match(client):
    c, db = client
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, hashtags) VALUES (1,'ig_reel','A','draft','brooklyn renovation kitchen','#reno')")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, hashtags) VALUES (1,'ig_reel','A','draft','queens bathroom remodel','#bath')")
    con.commit()
    con.close()
    items = c.get("/api/ahb/social/posts?q=brooklyn").get_json()["items"]
    captions = [p["caption"] for p in items]
    assert any("brooklyn" in (cap or "") for cap in captions)
    assert not any("queens" in (cap or "") for cap in captions)


def test_search_q_short_string_returns_all(client):
    """q shorter than 3 chars should be ignored (no filter)."""
    c, db = client
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','one')")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','two')")
    con.commit()
    con.close()
    items = c.get("/api/ahb/social/posts?q=on").get_json()["items"]
    assert len(items) >= 2


def test_search_q_matches_hashtags(client):
    c, db = client
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, hashtags) VALUES (1,'ig_reel','A','draft','x','#poolconstruction #ahbco')")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption, hashtags) VALUES (1,'ig_reel','A','draft','x','#kitchen')")
    con.commit()
    con.close()
    items = c.get("/api/ahb/social/posts?q=poolconstruction").get_json()["items"]
    assert len(items) == 1


def test_search_q_combines_with_tag_filter(client):
    c, db = client
    tid = c.post("/api/ahb/social/tags", json={"name": "launch"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','brooklyn kitchen launch')")
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','brooklyn bathroom only')")
    con.commit()
    pid_kitchen = con.execute("SELECT id FROM ahb_social_posts WHERE caption LIKE '%kitchen%' ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    c.post(f"/api/ahb/social/posts/{pid_kitchen}/tags", json={"tag_ids": [tid]})
    items = c.get("/api/ahb/social/posts?q=brooklyn&tag=launch").get_json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == pid_kitchen


# ---- T5: bulk operations ------------------------------------------------


def test_bulk_set_status_updates_rows(client):
    c, db = client
    import sqlite3
    con = sqlite3.connect(db)
    for cap in ("a","b","c"):
        con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft',?)", (cap,))
    con.commit()
    ids = [r[0] for r in con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 3").fetchall()]
    con.close()
    r = c.post("/api/ahb/social/posts/bulk", json={"ids": ids, "action": "set_status", "params": {"status": "approved"}})
    assert r.status_code == 200
    j = r.get_json()
    assert j["affected"] == 3
    con = sqlite3.connect(db)
    statuses = [r[0] for r in con.execute(f"SELECT status FROM ahb_social_posts WHERE id IN ({','.join(['?']*len(ids))})", ids).fetchall()]
    con.close()
    assert all(s == "approved" for s in statuses)


def test_bulk_set_status_rejects_unknown_status(client):
    c, _ = client
    r = c.post("/api/ahb/social/posts/bulk", json={"ids": [1], "action": "set_status", "params": {"status": "garbage"}})
    assert r.status_code == 400


def test_bulk_delete_removes_rows_and_tags(client):
    c, db = client
    tid = c.post("/api/ahb/social/tags", json={"name": "del"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','to-del')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    c.post(f"/api/ahb/social/posts/{pid}/tags", json={"tag_ids": [tid]})
    r = c.post("/api/ahb/social/posts/bulk", json={"ids": [pid], "action": "delete"})
    assert r.status_code == 200
    assert r.get_json()["affected"] == 1
    con = sqlite3.connect(db)
    n_posts = con.execute("SELECT COUNT(*) FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()[0]
    n_tags = con.execute("SELECT COUNT(*) FROM ahb_social_post_tags WHERE post_id=?", (pid,)).fetchone()[0]
    con.close()
    assert n_posts == 0
    assert n_tags == 0


def test_bulk_schedule_sets_scheduled_at(client):
    c, db = client
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft','sched-me')")
    con.commit()
    pid = con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    r = c.post("/api/ahb/social/posts/bulk",
               json={"ids": [pid], "action": "schedule",
                     "params": {"scheduled_at": "2026-06-01T15:30:00"}})
    assert r.status_code == 200
    con = sqlite3.connect(db)
    row = con.execute("SELECT scheduled_at, status FROM ahb_social_posts WHERE id=?", (pid,)).fetchone()
    con.close()
    assert row[0] == "2026-06-01T15:30:00"
    assert row[1] == "scheduled"


def test_bulk_tag_replace_set_for_each_post(client):
    c, db = client
    t = c.post("/api/ahb/social/tags", json={"name": "blk"}).get_json()["id"]
    import sqlite3
    con = sqlite3.connect(db)
    for cap in ("p1","p2"):
        con.execute("INSERT INTO ahb_social_posts (project_id, platform, variant, status, caption) VALUES (1,'ig_reel','A','draft',?)", (cap,))
    con.commit()
    ids = [r[0] for r in con.execute("SELECT id FROM ahb_social_posts ORDER BY id DESC LIMIT 2").fetchall()]
    con.close()
    r = c.post("/api/ahb/social/posts/bulk",
               json={"ids": ids, "action": "tag", "params": {"tag_ids": [t]}})
    assert r.status_code == 200
    for pid in ids:
        j = c.get(f"/api/ahb/social/posts/{pid}/tags").get_json()
        assert any(tt["id"] == t for tt in j["tags"])


def test_bulk_unknown_action_returns_400(client):
    c, _ = client
    r = c.post("/api/ahb/social/posts/bulk", json={"ids": [1], "action": "ufo"})
    assert r.status_code == 400


def test_bulk_empty_ids_returns_400(client):
    c, _ = client
    r = c.post("/api/ahb/social/posts/bulk", json={"ids": [], "action": "set_status", "params": {"status": "draft"}})
    assert r.status_code == 400
